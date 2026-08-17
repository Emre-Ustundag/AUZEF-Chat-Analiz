"""`Idempotency-Key` desteği — ADR-0001 §6, ADR-0002 #3.

Sözleşme dört cümle:

* aynı anahtar + **aynı** gövde → ilk isteğin `202` cevabı aynen döner (replay)
* aynı anahtar + **farklı** gövde → `409 JOB_CONFLICT`
* kayıt 24 saat saklanır (`Settings.idempotency_ttl_seconds`)
* replay, istekle gelen yeni `X-OpenRouter-Key` header'ını YOK SAYAR: anahtar
  orijinal job'a bağlıdır

## Neden gerçekten gerekli

İki POST ucu da PAHALI ve GERİ ALINAMAZ yan etki üretiyor: `/uploads` object
storage'a ~130 MB yazıp bir worker job'ı açıyor, `/analyses` ise
KULLANICININ KENDİ PARASINI harcayan bir LLM koşusu başlatıyor (BYOK). Ağ
kesintisinden sonra yeniden denenen bir istek, bu ikisini sessizce ikinci kez
yapardı.

## Referans implementasyon frontend mock'udur

`apps/web/src/mocks/idempotency.ts` ve `store.ts` bu kuralları zaten
uyguluyor. Buradaki kod onunla AYNI davranmak zorunda: aynı saklama anahtarı,
aynı canonical JSON, aynı fingerprint. Mock ile gerçek backend aynı anda
kullanılmadığı için fingerprint'ler tel üstünde karşılaşmaz — ama ayrışmaları,
mock'a karşı geliştirilen bir istemcinin gerçek backend'de başka davranması
demektir. `tests/fixtures/contract/idempotency.fingerprints.json` bu yüzden
üretiliyor.

## Yarış koşulu

Kayıt İKİ FAZLI: önce `SET NX EX` ile "pending" hak talebi, iş 202 ile
bitince `keepttl` ile cevabın kendisi. Tek fazlı olsaydı aynı anahtarla
eşzamanlı gelen iki istek de kontrolü geçer ve ikisi de job açardı — korumanın
var olma sebebi tam olarak bu.

TTL yalnızca ilk talepte kurulur (`keepttl=True`): sözleşme "kayıt 24 saat
saklanır" diyor, "son replay'den 24 saat sonra" demiyor. Aksi hâlde düzenli
retry eden bir istemci kaydı süresiz canlı tutardı.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Final, cast

from pydantic import BaseModel
from redis import Redis

from app.core.config import Settings, get_settings
from app.core.errors import JobConflictError
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Header adı sözleşmede sabit (ADR-0002 #3, `core/openapi.py`).
IDEMPOTENCY_KEY_HEADER: Final = "Idempotency-Key"

#: OpenAPI'deki `maxLength` ile aynı; mock da bu sınırı uyguluyor
#: (`apps/web/src/mocks/validation.ts`).
IDEMPOTENCY_KEY_MAX_LENGTH: Final = 255

_KEY_PREFIX: Final = "idempotency"

_STATE_PENDING: Final = "pending"
_STATE_COMPLETED: Final = "completed"


# ------------------------------------------------------------ canonical JSON


def _number(value: float | int) -> str:
    """Sayıyı JavaScript `JSON.stringify` ile AYNI biçimde yazar.

    Python `json` modülü `5.0` yazdığı yerde JS `5` yazar; `max_cost_usd`
    tam sayı değerli bir float olduğu için bu fark sözleşmedeki en tipik
    gövdede ortaya çıkardı. Her iki dil de kısa-tur (shortest round-trip)
    float gösterimi kullandığından geri kalan durumlarda `repr` zaten aynı.

    Sınır: 1e21 ve üstü JS'te üstel gösterime geçer. Sözleşmedeki sayısal
    alanlar (`top_n` ≤ 100, `max_cost_usd` ≤ 100, `size` bayt) bu aralığın
    çok altında; buraya bir değer gelirse fingerprint iki dilde ayrışır ve
    parity fixture'ı bunu yakalar.
    """
    if isinstance(value, bool):  # pragma: no cover - bool, int'ten önce elenir
        raise TypeError("bool sayı olarak kodlanamaz.")
    if isinstance(value, int):
        return str(value)
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return repr(value)


def canonical_json(value: object) -> str:
    """Sözleşme gövdeleri için canonical JSON üretir.

    Genel amaçlı bir RFC 8785 uygulaması DEĞİL; mock'un `canonicalJson`'ıyla
    (`apps/web/src/mocks/idempotency.ts`) bilinçli olarak aynı dar alt kümeyi
    kapsıyor: object anahtarları sıralı, gereksiz boşluk yok.

    Anahtar sıralaması: mock JS code-unit (UTF-16) sırası kullanıyor, Python
    code point sırası. Sözleşmedeki tüm alan adları ASCII olduğu için ikisi
    aynı sonucu verir — ASCII dışı bir alan adı eklenirse bu varsayım düşer.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return _number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list | tuple):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        entries = sorted(cast("dict[str, object]", value).items())
        body = ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{canonical_json(item)}" for key, item in entries
        )
        return "{" + body + "}"
    raise TypeError(f"Canonical JSON yalnızca JSON değerlerini kabul eder: {type(value)!r}")


def _sha256(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


# -------------------------------------------------------------- fingerprint


def analysis_fingerprint(body: BaseModel) -> str:
    """`POST /analyses` fingerprint'i — ADR-0002 #3.

    Girdi Pydantic ile DOĞRULANMIŞ gövdedir; ham istek değil. Header'lar ve
    özellikle `X-OpenRouter-Key` fingerprint'e GİRMEZ: anahtar rotasyonundan
    sonraki bir retry aynı isteği tekrar göndermiş sayılmalı.

    `mode="json"` zorunlu: UUID ve enum alanları tel üstündeki metin
    biçimleriyle hash'lenmeli, Python nesneleri olarak değil.
    """
    return _sha256(canonical_json(body.model_dump(mode="json")))


def upload_fingerprint(*, file_sha256: str, filename: str, mime_type: str, size: int) -> str:
    """`POST /uploads` fingerprint'i — ADR-0002 #3'teki iki aşamalı hash.

    Dosyanın SHA-256'sı tek başına yetmez: aynı içerik farklı bir adla
    yüklendiğinde raporun `source_summary`'si farklı olur, yani istek gerçekten
    farklıdır.
    """
    return _sha256(
        canonical_json(
            {
                "file_sha256": file_sha256,
                "filename": filename,
                "mime_type": mime_type,
                "size": size,
            }
        )
    )


# ------------------------------------------------------------- saklama anahtarı


def normalized_path(path: str) -> str:
    """Çoklu `/` daraltılır, sondaki `/` atılır (mock `normalizedPath` ile aynı).

    `/uploads` ve `/uploads/` aynı uçtur; normalize edilmeseydi aynı anahtarla
    yapılan iki isteğin biri korumanın dışında kalırdı.
    """
    collapsed = "/" + "/".join(part for part in path.split("/") if part)
    return collapsed


def storage_key(method: str, path: str, key: str) -> str:
    tuple_json = json.dumps(
        [method.upper(), normalized_path(path), key],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"{_KEY_PREFIX}:{tuple_json}"


# ------------------------------------------------------------------ kayıtlar


@dataclass(frozen=True)
class StoredResponse:
    """Saklanan ilk `202`: durum, gövde ve trace metadata'sı (ADR-0002 #3)."""

    status_code: int
    body: dict[str, Any]
    trace_id: str


@dataclass(frozen=True)
class Claim:
    """Kazanılmış hak talebi. Çağıran ya `remember` ya da `release` etmeli."""

    storage_key: str
    fingerprint: str


def _redis_client(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url)


def _conflict(detail: str) -> JobConflictError:
    return JobConflictError(detail)


def claim(
    method: str,
    path: str,
    key: str,
    fingerprint: str,
    settings: Settings | None = None,
) -> Claim | StoredResponse:
    """Hak talebi kurar; kayıt varsa replay gövdesini döndürür.

    Üç sonuç:

    * `Claim` — ilk istek, çağıran işe devam eder
    * `StoredResponse` — replay, çağıran hiçbir yan etki üretmeden döner
    * `JobConflictError` — fingerprint farklı ya da aynı istek HÂLÂ işleniyor
    """
    settings = settings or get_settings()
    redis_key = storage_key(method, path, key)
    client = _redis_client(settings)

    pending = json.dumps({"fingerprint": fingerprint, "state": _STATE_PENDING})
    if client.set(redis_key, pending, nx=True, ex=settings.idempotency_ttl_seconds):
        return Claim(storage_key=redis_key, fingerprint=fingerprint)

    raw = cast("bytes | None", client.get(redis_key))
    if raw is None:
        # NX ile SET arasında TTL doldu. Kayıp bir yarış: kaydı yeniden
        # kurmak yerine isteği normal (korumasız) akışına bırakmak yanlış
        # olurdu; ikinci kez denemek yeterli.
        raise _conflict(
            "Bu Idempotency-Key ile yapılan istek işlenirken kayıt süresi doldu. "
            "İsteği tekrar gönderin."
        )

    record = json.loads(raw.decode("utf-8"))
    if record.get("fingerprint") != fingerprint:
        raise _conflict(
            "Bu Idempotency-Key daha önce FARKLI bir istek gövdesiyle kullanıldı. "
            "Yeni bir istek için yeni bir anahtar üretin."
        )

    if record.get("state") != _STATE_COMPLETED:
        raise _conflict("Aynı Idempotency-Key ile gönderilen istek şu anda işleniyor.")

    return StoredResponse(
        status_code=record["status_code"],
        body=record["body"],
        trace_id=record["trace_id"],
    )


def remember(
    claimed: Claim,
    *,
    status_code: int,
    body: dict[str, Any],
    trace_id: str,
    settings: Settings | None = None,
) -> None:
    """İlk `202`'yi kaydeder. TTL ilk talepten sayılır, uzamaz."""
    settings = settings or get_settings()
    record = json.dumps(
        {
            "fingerprint": claimed.fingerprint,
            "state": _STATE_COMPLETED,
            "status_code": status_code,
            "body": body,
            "trace_id": trace_id,
        }
    )
    client = _redis_client(settings)
    client.set(claimed.storage_key, record, keepttl=True)
    logger.info("idempotency_recorded", extra={"trace_id": trace_id})


def release(claimed: Claim, settings: Settings | None = None) -> None:
    """Hak talebini bırakır — istek `202` DIŞINDA bir sonuçla bittiğinde.

    Sözleşme yalnızca "ilk 202"yi saklamayı söylüyor. Başarısız isteği de
    saklasaydık, maliyet tavanına takılan kullanıcı `max_cost_usd`'yi
    düzeltip tekrar denediğinde gövdesi değiştiği için 409 alırdı — yani
    hatanın kendi mesajının önerdiği şeyi yapamazdı.

    İstisna FIRLATMAZ: talebi bırakamamak isteğin sonucunu değiştirmemeli.
    TTL zaten üst sınır güvencesidir.
    """
    settings = settings or get_settings()
    try:
        _redis_client(settings).delete(claimed.storage_key)
    except Exception:
        logger.exception("idempotency_release_failed")
