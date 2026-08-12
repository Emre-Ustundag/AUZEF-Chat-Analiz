"""OpenAPI şemasının sözleşmeye göre düzeltilmesi.

`docs/api/openapi.json` kayıt artefaktıdır (ADR-0002 #8); frontend'in drift
testleri onu okur. Bu yüzden FastAPI'nin ürettiği ham şema olduğu gibi
bırakılmaz.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.core.config import get_settings
from app.core.handlers import PROBLEM_MEDIA_TYPE
from app.core.tracing import TRACE_ID_HEADER
from app.schemas.examples import build_cases

#: `Idempotency-Key` yalnızca bu iki uçta anlamlı (ADR-0002 #3).
IDEMPOTENT_POST_PATHS = ("/api/v1/uploads", "/api/v1/analyses")

OPENROUTER_KEY_SCHEME = "OpenRouterKey"
OPENROUTER_KEY_HEADER = "X-OpenRouter-Key"

_IDEMPOTENCY_DESCRIPTION = (
    "Opsiyonel. Aynı anahtar + aynı gövde ile tekrarlanan istek, ilk isteğin "
    "202 cevabını aynen döndürür (replay). Aynı anahtar + FARKLI gövde ise "
    "409 `JOB_CONFLICT` üretir. Kayıt 24 saat saklanır.\n\n"
    "Replay, orijinal 202'yi döndürür ve istekle gelen yeni `X-OpenRouter-Key` "
    "header'ını YOK SAYAR: anahtar orijinal job'a bağlıdır. Aksi hâlde anahtar "
    "rotasyonundan sonraki bir retry, ölmüş bir job'ı sessizce diriltirdi.\n\n"
    "Fingerprint ve saklama anahtarı ADR-0002 #3'te byte seviyesinde "
    "tanımlıdır; secret/header değerleri fingerprint'e girmez."
)


def _examples_by_case_id() -> dict[str, Any]:
    """Fixture örneklerini id -> JSON gövde olarak döndürür.

    OpenAPI örnekleri fixture'larla AYNI kaynaktan geliyor
    (`app/schemas/examples.py`); ayrı yazılsalardı belge ile testler
    ayrışabilirdi ve kimse fark etmezdi.
    """
    return {
        case.id: case.payload.model_dump(mode="json")
        for case in build_cases()
        if case.payload is not None
    }


#: (path, method, status) -> o cevaba iliştirilecek örneklerin case id'leri.
_RESPONSE_EXAMPLES: dict[tuple[str, str, str], dict[str, str]] = {
    ("/api/v1/health/live", "get", "200"): {"Process çalışıyor": "health.live.200"},
    ("/api/v1/health/ready", "get", "200"): {"Trafiğe hazır": "health.ready.200"},
    ("/api/v1/uploads", "post", "202"): {"Kuyruğa alındı": "uploads.create.202"},
    ("/api/v1/uploads/{upload_id}", "get", "200"): {
        "Doğrulanıyor": "uploads.get.200.queued",
        "Hazır (profil dolu)": "uploads.get.200.ready",
        "Satır sınırı aşıldı": "uploads.get.200.row-limit",
        "Başarısız": "uploads.get.200.failed",
    },
    ("/api/v1/models", "get", "200"): {"Whitelist": "models.list.200"},
    ("/api/v1/analyses", "post", "202"): {"Kuyruğa alındı": "analyses.create.202"},
    ("/api/v1/analyses/{analysis_id}", "get", "200"): {
        "Analiz sürüyor": "analyses.get.200.analyzing",
        "Sağlayıcı hatasıyla bitti": "analyses.get.200.failed",
        "İptal edildi": "analyses.get.200.cancelled",
    },
    ("/api/v1/analyses/{analysis_id}/result", "get", "200"): {
        "Tam rapor": "analyses.result.200",
        "Satır sınırı kırpılmış": "analyses.result.200.truncated",
    },
}

#: 422 gövdesinde hangi kodların çıkabileceğini örnekle göster.
_VALIDATION_EXAMPLE_CODES = (
    "REQUEST_VALIDATION",
    "INVALID_MODEL",
    "INVALID_PROMPT",
    "COST_LIMIT_EXCEEDED",
    "PROVIDER_AUTH_FAILED",
    "SHEET_OR_COLUMN_NOT_FOUND",
    "UPLOAD_CORRUPT_OR_ENCRYPTED",
)


def _error_examples(
    status: str,
    allowed_codes: list[str],
    examples: dict[str, Any],
) -> dict[str, Any]:
    """Bir operation/status için yalnızca o uçta çıkabilen örnekler."""
    selected: dict[str, Any] = {}
    for code in allowed_codes:
        if status == "422" and code not in _VALIDATION_EXAMPLE_CODES:
            continue
        case_id = f"errors.{code.lower().replace('_', '-')}.{status}"
        if case_id in examples:
            selected[code] = {"value": examples[case_id]}

    if (
        status == "422"
        and "REQUEST_VALIDATION" in allowed_codes
        and "errors.request-validation.422.no-field" in examples
    ):
        # Alan yolu üretilemeyen (`field: null`) validation hatası.
        selected["REQUEST_VALIDATION (alan yolu olmayan hata)"] = {
            "value": examples["errors.request-validation.422.no-field"]
        }
    return selected


def _trace_id_header() -> dict[str, Any]:
    # Fabrika, paylaşılan sabit değil: aynı dict onlarca cevaba iliştirilseydi
    # birinde yapılacak bir düzenleme sessizce hepsini değiştirirdi.
    return {
        "description": "Bu isteğin izleme kimliği; hata gövdesindeki trace_id ile aynı.",
        "schema": {"type": "string", "format": "uuid"},
        "example": "9d8c7b6a-5e4f-4321-8abc-0123456789ab",
    }


def build_openapi(app: FastAPI) -> dict[str, Any]:
    schema = get_openapi(
        title="AUZEF Chat Analiz API",
        version=get_settings().contract_version,
        description=(
            "AUZEF chatbot mesajlarından sık sorulan soru ve tema analizi.\n\n"
            "Tüm hata cevapları RFC 9457 Problem Details biçimindedir ve her "
            "zaman `type`, `title`, `status`, `code`, `detail`, `trace_id` "
            "alanlarını taşır. `retry_after` YALNIZCA 429 cevaplarında bulunur "
            "ve başka hiçbir cevapta `null` olarak dahi yer almaz.\n\n"
            "Tüm tarihler UTC ISO 8601'dir ve `Z` ile biter "
            "(`YYYY-MM-DDTHH:MM:SS.sssZ`)."
        ),
        routes=app.routes,
    )

    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})

    # FastAPI'nin otomatik 422 modelleri: sunucunun ASLA üretmediği bir gövdeyi
    # belgeliyorlar (RequestValidationError handler'ı hepsini ProblemDetails'e
    # çeviriyor). Bırakılırsa kayıt artefaktına daha ilk gün drift gömülür.
    for orphan in ("HTTPValidationError", "ValidationError"):
        schemas.pop(orphan, None)

    components.setdefault("securitySchemes", {})[OPENROUTER_KEY_SCHEME] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-OpenRouter-Key",
        "description": (
            "BYOK OpenRouter anahtarı. Yalnızca bu header'da taşınır; gövdeye "
            "veya sorgu parametresine konmaz, loglarda redakte edilir ve "
            "kalıcı olarak saklanmaz."
        ),
    }

    examples = _examples_by_case_id()

    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue

            responses = operation.setdefault("responses", {})

            # BE-01 stub'larının döndürdüğü 501, dondurulmuş sözleşmenin
            # parçası değil; route iş mantıkları tamamlandığında kaybolacak
            # geçici bir geliştirme davranışı. Public belgede yer almamalı.
            responses.pop("501", None)

            for status_code, response in responses.items():
                if status_code.isdigit() and int(status_code) >= 400:
                    # FastAPI `model=` verildiğinde `application/json` üretiyor;
                    # handler'lar ise YALNIZCA problem+json döndürüyor. İkisini
                    # birden belgelemek, sunucunun asla yapmadığı bir şeyi
                    # sözleşmeye yazmak demek.
                    content = {
                        PROBLEM_MEDIA_TYPE: {
                            "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                        }
                    }
                    error_examples = _error_examples(
                        status_code,
                        response.get("x-error-codes", []),
                        examples,
                    )
                    if error_examples:
                        content[PROBLEM_MEDIA_TYPE]["examples"] = error_examples
                    response["content"] = content
                else:
                    named = _RESPONSE_EXAMPLES.get((path, method, status_code))
                    if named:
                        for media in response.get("content", {}).values():
                            media["examples"] = {
                                label: {"value": examples[case_id]}
                                for label, case_id in named.items()
                            }

                response.setdefault("headers", {})[TRACE_ID_HEADER] = _trace_id_header()

            # 204'te gövde üretilmemeli.
            for no_content in ("204",):
                if no_content in responses:
                    responses[no_content].pop("content", None)

            if method == "post" and path == "/api/v1/analyses":
                body = operation.get("requestBody", {})
                for media in body.get("content", {}).values():
                    media["examples"] = {
                        "Tipik analiz isteği": {"value": examples["analyses.request"]}
                    }

            if method == "post" and path == "/api/v1/uploads":
                for media in operation.get("requestBody", {}).get("content", {}).values():
                    media["examples"] = {
                        "XLSX dosyası": {
                            "summary": "file alanında binary .xlsx içeriği",
                            "value": {"file": "(binary .xlsx content)"},
                        }
                    }

            if method == "post" and path in IDEMPOTENT_POST_PATHS:
                operation.setdefault("parameters", []).append(
                    {
                        "name": "Idempotency-Key",
                        "in": "header",
                        "required": False,
                        "description": _IDEMPOTENCY_DESCRIPTION,
                        "schema": {"type": "string", "maxLength": 255},
                        "example": "8f14e45f-ceea-467a-9f6b-2c1d3e4a5b6c",
                    }
                )

            if method == "post" and path == "/api/v1/analyses":
                operation["security"] = [{OPENROUTER_KEY_SCHEME: []}]
                # Anahtar `require_openrouter_key` dependency'sinden geliyor ve
                # FastAPI onu ayrıca opsiyonel bir header parametresi olarak
                # belgeliyor. İki kayıt çelişir: security scheme "kimlik
                # doğrulama böyle yapılır" derken parametre `required: false`
                # ve nullable görünür — oysa header'sız istek 422 alır.
                # Dependency'nin `str | None` imzası ise zorunlu: `Header(...)`
                # ile zorunlu kılmak REQUEST_VALIDATION üretir, biz
                # PROVIDER_AUTH_FAILED istiyoruz (ADR-0002 §5). Bu yüzden
                # çelişkiyi belgeden siliyoruz, imzadan değil.
                operation["parameters"] = [
                    parameter
                    for parameter in operation.get("parameters", [])
                    if parameter.get("name") != OPENROUTER_KEY_HEADER
                ]

            if path.endswith("/export") and method == "get":
                responses.setdefault("200", {})["content"] = {
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                        "schema": {"type": "string", "format": "binary"},
                        "examples": {
                            "XLSX export": {
                                "summary": "Binary XLSX dosyası",
                                "value": "PK...(binary xlsx content)",
                            }
                        },
                    },
                    "application/json": {
                        "schema": {"type": "string", "format": "binary"},
                        "examples": {
                            "JSON export": {
                                "summary": "Attachment olarak indirilen JSON raporu",
                                "value": '{"schema_version":"1.0","...":"..."}',
                            }
                        },
                    },
                }
                responses["200"].setdefault("headers", {})["Content-Disposition"] = {
                    "description": (
                        'Her zaman `attachment; filename="analiz-{analysis_id}.{format}"`. '
                        "Kullanıcının dosya adı kullanılmadığı için tanım gereği ASCII'dir; "
                        "RFC 5987 `filename*` üretilmez."
                    ),
                    "schema": {"type": "string"},
                    "example": (
                        'attachment; filename="analiz-6b1cf3d2-0a44-4f1b-9d64-1c2a7e5f8b90.xlsx"'
                    ),
                }

    return schema


def install_openapi(app: FastAPI) -> None:
    def _openapi() -> dict[str, Any]:
        if not app.openapi_schema:
            app.openapi_schema = build_openapi(app)
        return app.openapi_schema

    app.openapi = _openapi  # type: ignore[method-assign]
