"""Sözleşme testleri — frontend'in Zod şemalarıyla birebir uyum.

Bu dosyanın varlık sebebi: sözleşme ihlalleri SESSİZ hatalardır. Backend
HTTP 200 döner, frontend `safeParse` ile reddeder ve kullanıcı boş ekran
görür. Ne pytest ne `npm test` bunu kendiliğinden yakalar.

Karşılaştırma kaynağı: `apps/web/src/lib/api/schemas/common.ts` ve
`upload.ts`. Buradaki iddialar o dosyalardan ELLE türetilmiştir; şema
değişirse bu testler de güncellenmelidir.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.errors import (
    ERROR_STATUS,
    ERROR_TITLES,
    ApiError,
    build_problem,
    error_type_uri,
)
from app.schemas.common import to_iso_z
from app.schemas.upload import UploadCreated, UploadProfile, UploadRead, UploadStatus

#: `apps/web/src/lib/api/schemas/common.ts` → `errorCodeSchema`.
FRONTEND_ERROR_CODES = {
    "UPLOAD_TOO_LARGE",
    "UPLOAD_INVALID_TYPE",
    "UPLOAD_CORRUPT_OR_ENCRYPTED",
    "SHEET_OR_COLUMN_NOT_FOUND",
    "PROVIDER_AUTH_FAILED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_BAD_RESPONSE",
    "PROVIDER_TIMEOUT",
    "JOB_NOT_FOUND",
    "JOB_CONFLICT",
    "INTERNAL_ERROR",
}

#: `apps/web/src/lib/api/schemas/upload.ts` → `uploadStatusSchema`.
FRONTEND_UPLOAD_STATUSES = {"queued", "validating", "ready", "failed"}

#: Zod'un `z.iso.datetime()` varsayılanı: yalnızca `Z` ile biten UTC damgası.
ISO_Z_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# ------------------------------------------------------------- hata kodları


def test_hata_kodlari_frontend_ile_birebir() -> None:
    assert set(ERROR_STATUS) == FRONTEND_ERROR_CODES
    assert set(ERROR_TITLES) == FRONTEND_ERROR_CODES


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("UPLOAD_TOO_LARGE", 413),
        ("UPLOAD_INVALID_TYPE", 415),
        ("UPLOAD_CORRUPT_OR_ENCRYPTED", 422),
        ("SHEET_OR_COLUMN_NOT_FOUND", 422),
        ("PROVIDER_AUTH_FAILED", 422),
        ("PROVIDER_RATE_LIMITED", 429),
        ("PROVIDER_BAD_RESPONSE", 502),
        ("PROVIDER_TIMEOUT", 504),
        ("JOB_NOT_FOUND", 404),
        ("JOB_CONFLICT", 409),
        ("INTERNAL_ERROR", 500),
    ],
)
def test_http_durumlari_adr_ile_uyumlu(code: str, status: int) -> None:
    """ADR §7'deki tablo. Sapma frontend'de yanlış hata ekranı demektir."""
    assert ERROR_STATUS[code] == status  # type: ignore[index]


def test_type_uri_mock_ile_ayni_bicimde() -> None:
    assert error_type_uri("UPLOAD_TOO_LARGE") == "/errors/upload-too-large"
    assert error_type_uri("INTERNAL_ERROR") == "/errors/internal-error"


# ------------------------------------------------- problem details gövdesi


def test_problem_govdesi_zorunlu_alanlari_tasir() -> None:
    payload = build_problem("JOB_NOT_FOUND", "Kayıt bulunamadı.").to_payload()

    assert set(payload) == {
        "type",
        "title",
        "status",
        "code",
        "detail",
        "trace_id",
        "errors",
    }
    assert payload["errors"] == []


def test_status_alani_tamsayi() -> None:
    """Frontend `z.int()` bekliyor; float serileştirme reddedilir."""
    payload = build_problem("UPLOAD_TOO_LARGE", "Sınır aşıldı.").to_payload()
    raw = json.loads(json.dumps(payload))

    assert isinstance(raw["status"], int)
    assert not isinstance(raw["status"], bool)


def test_retry_after_yoksa_alan_hic_gonderilmez() -> None:
    """KRİTİK: frontend `retry_after`'ı `.optional()` ilan ediyor, `.nullable()` DEĞİL.

    `null` gönderilirse `problemDetailsSchema.safeParse` başarısız olur ve
    kullanıcı gerçek hata mesajı yerine "beklenmeyen hata biçimi" görür.
    """
    payload = build_problem("INTERNAL_ERROR", "Hata.").to_payload()
    assert "retry_after" not in payload


def test_retry_after_varsa_gonderilir() -> None:
    payload = build_problem("PROVIDER_RATE_LIMITED", "Sınır aşıldı.", retry_after=60).to_payload()
    assert payload["retry_after"] == 60


def test_gomulu_problem_da_retry_after_icermez() -> None:
    """`UploadRead.error` içindeki problem gövdesi de aynı kurala tabidir.

    `exclude_none=True` bu durumu çözemezdi: aynı bayrak `profile: null`
    alanını da silerdi, oysa frontend orada null BEKLİYOR.
    """
    upload = UploadRead(
        upload_id=uuid4(),
        status=UploadStatus.FAILED,
        filename="bozuk.xlsx",
        size_bytes=1024,
        created_at=datetime.now(UTC),
        profile=None,
        error=build_problem("UPLOAD_CORRUPT_OR_ENCRYPTED", "Dosya okunamadı."),
    )
    payload = upload.model_dump(mode="json")

    assert "retry_after" not in payload["error"]
    # profile null OLARAK KALMALI — frontend `.nullable()` ilan ediyor.
    assert payload["profile"] is None
    assert "profile" in payload


def test_api_error_problem_uretir() -> None:
    error = ApiError("UPLOAD_TOO_LARGE", "Sınır aşıldı.")
    assert error.status_code == 413

    problem = error.to_problem()
    assert problem.code == "UPLOAD_TOO_LARGE"
    assert problem.status == 413
    assert problem.trace_id


def test_hata_govdesinde_stack_trace_veya_sir_yok() -> None:
    """ADR §9: hata cevabına dosya içeriği, anahtar veya iz düşmez."""
    payload = build_problem("INTERNAL_ERROR", "Beklenmeyen bir hata oluştu.").to_payload()
    serialized = json.dumps(payload)

    for leak in ("Traceback", 'File "', "sk-", "postgresql://", "minioadmin"):
        assert leak not in serialized


# ---------------------------------------------------------- zaman damgaları


def test_created_at_z_ile_biter() -> None:
    """KRİTİK: Zod'un `z.iso.datetime()` varsayılanı offset KABUL ETMEZ.

    Python `isoformat()` tz-aware UTC değerde `+00:00` üretir ve bu Zod
    doğrulamasından geçmez. Kontrol edilen şey tam olarak bu dönüşümdür.
    """
    upload = UploadRead(
        upload_id=uuid4(),
        status=UploadStatus.QUEUED,
        filename="veri.xlsx",
        size_bytes=10,
        created_at=datetime.now(UTC),
    )
    payload = upload.model_dump(mode="json")

    assert ISO_Z_PATTERN.match(payload["created_at"]), payload["created_at"]
    assert payload["created_at"].endswith("Z")
    assert "+00:00" not in payload["created_at"]


def test_naive_datetime_de_z_ile_biter() -> None:
    """Veritabanından zaman dilimsiz gelen değer de doğru serileşmeli."""
    naive = datetime(2026, 8, 11, 15, 30, 45)
    assert ISO_Z_PATTERN.match(to_iso_z(naive))


def test_offsetli_datetime_utc_ye_cevrilir() -> None:
    from datetime import timedelta, timezone

    istanbul = datetime(2026, 8, 11, 18, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    result = to_iso_z(istanbul)

    assert result.startswith("2026-08-11T15:00:00")
    assert result.endswith("Z")


# ------------------------------------------------------------ upload gövdesi


def test_upload_status_degerleri_frontend_ile_ayni() -> None:
    assert {status.value for status in UploadStatus} == FRONTEND_UPLOAD_STATUSES


def test_upload_created_govdesi() -> None:
    created = UploadCreated(upload_id=uuid4(), status=UploadStatus.QUEUED)
    payload = created.model_dump(mode="json")

    assert set(payload) == {"upload_id", "status"}
    assert payload["status"] == "queued"


def test_upload_read_alanlari_sozlesmeyle_ayni() -> None:
    upload = UploadRead(
        upload_id=uuid4(),
        status=UploadStatus.READY,
        filename="veri.xlsx",
        size_bytes=2048,
        created_at=datetime.now(UTC),
        profile=UploadProfile(sheets=[], total_row_count=0),
    )
    payload = upload.model_dump(mode="json")

    assert set(payload) == {
        "upload_id",
        "status",
        "filename",
        "size_bytes",
        "created_at",
        "profile",
        "error",
    }
    assert payload["error"] is None
    assert payload["profile"]["exceeds_row_limit"] is False


def test_upload_id_uuid_metni_olarak_serilesir() -> None:
    """Frontend `z.uuid()` bekliyor; UUID nesnesi değil metin gönderilmeli."""
    upload_id = uuid4()
    created = UploadCreated(upload_id=upload_id, status=UploadStatus.QUEUED)
    payload = created.model_dump(mode="json")

    assert payload["upload_id"] == str(upload_id)
    assert isinstance(payload["upload_id"], str)
