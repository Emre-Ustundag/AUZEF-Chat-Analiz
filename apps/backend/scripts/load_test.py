"""130 MB yük testi — ADR §10 risk 1.

    docker compose up -d
    uv run python scripts/load_test.py            # veya: make loadtest

ADR §10 risk 1 aynen şunu istiyor: "streaming upload, ZIP bomb kontrolü,
`openpyxl` `read_only`, worker memory limiti/process recycling ve gerçek
130 MB fixture ile yük testi. Parser bir adapter arkasındadır; load test
başarısızsa API sözleşmesini değiştirmeden alternatif streaming parser
kullanılabilir."

Yani bu bir MİMARİ KARAR NOKTASI, tekrarlanan bir regresyon testi değil. Bu
yüzden `make check`'in dışında: dakikalar sürüyor, ~130 MB geçici dosya
yazıyor ve çalışan bir yığın istiyor.

## Ne ölçülüyor

Akışın tamamı GERÇEK yolundan geçiyor — proxy dâhil (:3000):

1. `POST /api/v1/uploads` — Caddy ve FastAPI gövdeyi tamponlamadan geçiriyor
   mu, dosya MinIO'ya stream ediliyor mu
2. Celery worker profilleme — `openpyxl read_only` gerçekten sabit bellekte mi
   kalıyor, yoksa dosya boyutuyla mı büyüyor
3. Satır sınırı davranışı — 100.000 aşıldığında dosya REDDEDİLMİYOR,
   `profile.exceeds_row_limit` işaretleniyor (ADR-0002 #2)

Worker'ın bellek tepesi `docker stats` ile ölçülüyor: process içinden
ölçmek yalnızca API container'ını görürdü, oysa asıl risk worker'da.

Analiz BAŞLATILMIYOR: orası gerçek bir OpenRouter koşusu ve kullanıcının
parası. Bu testin sorusu parser ve I/O katmanı.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.make_large_xlsx import generate

DEFAULT_BASE_URL = "http://127.0.0.1:3000"
WORKER_CONTAINER = "auzef-worker"
API_CONTAINER = "auzef-api"


def container_memory_mb(container: str) -> float | None:
    """`docker stats` tek atışlık okuma. Docker yoksa `None`."""
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

    # "123.4MiB / 7.653GiB" -> 123.4
    raw = out.split("/")[0].strip()
    for suffix, factor in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024.0)):
        if raw.endswith(suffix):
            return float(raw[: -len(suffix)]) * factor
    return None


def post_upload(base_url: str, path: Path) -> tuple[int, dict[str, object], float]:
    """Multipart upload'ı DİSKTEN AKITARAK gönderir.

    Dosyayı `read()` ile belleğe almak, testin ölçmek istediği şeyi
    (istemciden sunucuya kadar tamponsuz yol) istemci tarafında bozardı.
    """
    boundary = f"----auzef{uuid.uuid4().hex}"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n"
        "\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    total = len(prefix) + path.stat().st_size + len(suffix)

    def body() -> Iterator[bytes]:
        yield prefix
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                yield chunk
        yield suffix

    class _Stream:
        """`urllib`'in beklediği dosya benzeri arayüz, üreteç üzerinde.

        `urllib` `data` olarak bir iterable kabul ediyor ama o yolda
        `Content-Length` yerine chunked encoding kullanıyor. Sunucu tarafında
        beyan edilen uzunluk kontrolünü (`core/body_limit.py` katman 1) de
        ölçmek istediğimiz için `read()` arayüzü gerekiyor.
        """

        def __init__(self) -> None:
            self._chunks = body()
            self._buffer = b""

        def read(self, size: int = -1) -> bytes:
            while size < 0 or len(self._buffer) < size:
                try:
                    self._buffer += next(self._chunks)
                except StopIteration:
                    break
            if size < 0:
                data, self._buffer = self._buffer, b""
                return data
            data, self._buffer = self._buffer[:size], self._buffer[size:]
            return data

    request = urllib.request.Request(
        f"{base_url}/api/v1/uploads",
        data=_Stream(),
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(total),
        },
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            payload = json.loads(response.read())
            status = response.status
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read())
        status = exc.code
    return status, payload, time.monotonic() - started


def poll_upload(
    base_url: str, upload_id: str, timeout_s: float
) -> tuple[dict[str, object], float, float]:
    """`ready`/`failed` olana kadar poll eder; worker bellek TEPESİNİ döndürür."""
    started = time.monotonic()
    peak = 0.0
    while time.monotonic() - started < timeout_s:
        memory = container_memory_mb(WORKER_CONTAINER)
        if memory is not None:
            peak = max(peak, memory)

        with urllib.request.urlopen(
            f"{base_url}/api/v1/uploads/{upload_id}", timeout=30
        ) as response:
            body = json.loads(response.read())

        if body["status"] in {"ready", "failed"}:
            return body, time.monotonic() - started, peak
        time.sleep(2)

    raise TimeoutError(f"{timeout_s} sn içinde profilleme bitmedi.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-mb", type=int, default=130)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--fixture", type=Path, default=Path("/tmp/auzef-yuk-testi.xlsx"))
    parser.add_argument("--rows", type=int, default=0, help="0 ise boyuta göre kalibre edilir.")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--keep", action="store_true", help="Fixture'ı silme.")
    args = parser.parse_args()

    target_bytes = args.target_mb * 1024 * 1024

    # ---- 1. fixture ----
    if args.fixture.exists() and args.fixture.stat().st_size >= target_bytes * 0.9:
        print(f"var olan fixture kullanılıyor: {args.fixture}")
        rows, build_s = -1, 0.0
    else:
        print(f"fixture üretiliyor (~{args.target_mb} MB)…")
        rows_target = args.rows
        if not rows_target:
            probe = args.fixture.with_suffix(".probe.xlsx")
            generate(target_bytes, probe, 20_000)
            rows_target = int(target_bytes / (probe.stat().st_size / 20_000))
            probe.unlink()
        rows, build_s = generate(target_bytes, args.fixture, rows_target)

    size_mb = args.fixture.stat().st_size / 1024 / 1024
    print(f"fixture: {size_mb:.1f} MB, {rows:,} satır, {build_s:.1f} sn")

    api_before = container_memory_mb(API_CONTAINER)
    worker_before = container_memory_mb(WORKER_CONTAINER)

    # ---- 2. upload ----
    print(f"yükleniyor -> {args.base_url}/api/v1/uploads")
    status, payload, upload_s = post_upload(args.base_url, args.fixture)
    if status != 202:
        print(f"UPLOAD BAŞARISIZ: {status} {payload}")
        return 1

    upload_id = str(payload["upload_id"])
    throughput = size_mb / upload_s if upload_s else float("inf")
    api_after = container_memory_mb(API_CONTAINER)
    print(f"upload: {upload_s:.1f} sn, {throughput:.1f} MB/sn, 202 {upload_id}")

    # ---- 3. profilleme ----
    print("worker profilliyor…")
    body, profile_s, worker_peak = poll_upload(args.base_url, upload_id, args.timeout)

    print()
    print("=" * 68)
    print(f"durum                : {body['status']}")
    if body["status"] == "failed":
        print(f"hata                 : {body.get('error')}")
        return 1

    profile = body["profile"]
    assert isinstance(profile, dict)
    sheet = profile["sheets"][0]
    print(f"dosya boyutu         : {size_mb:.1f} MB")
    print(f"upload süresi        : {upload_s:.1f} sn ({throughput:.1f} MB/sn)")
    print(f"profilleme süresi    : {profile_s:.1f} sn")
    print(f"satır (sheet)        : {sheet['row_count']:,}")
    print(f"kolon                : {len(sheet['columns'])}")
    print(f"satır sınırı aşıldı  : {profile['exceeds_row_limit']}")
    print(f"api bellek           : {api_before:.0f} -> {api_after:.0f} MB")
    print(f"worker bellek tepesi : {worker_peak:.0f} MB (başlangıç {worker_before:.0f} MB)")
    print("=" * 68)

    if not args.keep:
        args.fixture.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
