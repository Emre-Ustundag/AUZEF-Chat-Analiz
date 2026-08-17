"""Route bağımlılıkları."""

from typing import Annotated

from fastapi import Depends, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import ProviderAuthFailedError, RequestValidationFailedError
from app.core.tracing import TRACE_ID_HEADER
from app.services import idempotency
from app.services.idempotency import IDEMPOTENCY_KEY_HEADER, IDEMPOTENCY_KEY_MAX_LENGTH

OPENROUTER_KEY_HEADER = "X-OpenRouter-Key"


def require_openrouter_key(
    x_openrouter_key: Annotated[str | None, Header(alias=OPENROUTER_KEY_HEADER)] = None,
) -> str:
    """BYOK anahtarını okur; yoksa PROVIDER_AUTH_FAILED üretir.

    Header'ı `Header(...)` ile zorunlu yapmak REQUEST_VALIDATION üretirdi ve
    teknik olarak doğru olurdu. Yine de PROVIDER_AUTH_FAILED tercih ediliyor:
    boş anahtar alanı için Türkçe mesajı ("OpenRouter API anahtarı
    doğrulanamadı. Anahtarı kontrol edip tekrar deneyin.") kullanıcıya jenerik
    doğrulama metninden belirgin şekilde daha yardımcı. Mock da aynı kodu
    döner; gerekçe ADR-0002'de kayıtlı.

    Anahtarın DEĞERİ hiçbir yere loglanmaz ve döndürülen problem gövdesine
    girmez.
    """
    if not x_openrouter_key or not x_openrouter_key.strip():
        raise ProviderAuthFailedError("X-OpenRouter-Key header'ı zorunludur.")
    return x_openrouter_key


OpenRouterKey = Annotated[str, Depends(require_openrouter_key)]


def optional_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_KEY_HEADER)] = None,
) -> str | None:
    """`Idempotency-Key` header'ını okur ve normalize eder (ADR-0002 #3).

    Header opsiyoneldir: göndermeyen istemci bugünkü davranışı aynen alır.
    Boş/whitespace bir değer "gönderilmemiş" sayılır — aksi hâlde tüm bu
    istemcilerin istekleri TEK bir saklama anahtarında toplanır ve ikincisi
    409 alırdı.

    255 karakter sınırı OpenAPI'deki `maxLength` ile aynı ve mock da aynı 422
    gövdesini üretiyor (`apps/web/src/mocks/validation.ts`); alan adı dâhil
    birebir eşleşmeleri bilinçli.
    """
    if idempotency_key is None or not idempotency_key.strip():
        return None

    if len(idempotency_key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise RequestValidationFailedError(
            f"{IDEMPOTENCY_KEY_HEADER} en fazla {IDEMPOTENCY_KEY_MAX_LENGTH} karakter olabilir.",
            errors=[
                (
                    f"header.{IDEMPOTENCY_KEY_HEADER}",
                    f"En fazla {IDEMPOTENCY_KEY_MAX_LENGTH} karakter olabilir.",
                )
            ],
        )

    return idempotency_key


IdempotencyKey = Annotated[str | None, Depends(optional_idempotency_key)]


async def claim_idempotency(
    request: Request,
    key: str,
    fingerprint: str,
    settings: Settings,
) -> idempotency.Claim | idempotency.StoredResponse:
    """Hak talebini kurar. `redis-py` senkron; event loop bloklanmaz."""
    return await run_in_threadpool(
        idempotency.claim,
        request.method,
        request.url.path,
        key,
        fingerprint,
        settings,
    )


async def remember_idempotency(
    claimed: idempotency.Claim,
    body: BaseModel,
    trace_id: str,
    settings: Settings,
) -> None:
    """İlk 202'yi saklar.

    Gövde `mode="json"` ile dökülüyor: replay'in TEL BİÇİMİ ilk cevabınkiyle
    aynı olmalı, `UtcDateTime` gibi alanların Python temsili değil.
    """
    await run_in_threadpool(
        idempotency.remember,
        claimed,
        status_code=status.HTTP_202_ACCEPTED,
        body=body.model_dump(mode="json"),
        trace_id=trace_id,
        settings=settings,
    )


async def release_idempotency(claimed: idempotency.Claim, settings: Settings) -> None:
    await run_in_threadpool(idempotency.release, claimed, settings)


def replayed_response(stored: idempotency.StoredResponse) -> JSONResponse:
    """Saklanan ilk 202'yi status, gövde ve trace id'siyle aynen döndürür.

    Trace id BİLEREK yeniden üretilmiyor (ADR-0002 #3: "ilk 202'nin
    status/body/header metadata'sı"). `TraceIdMiddleware` bu header'ı
    ezmiyor; gerekçe `core/tracing.py` içinde.
    """
    return JSONResponse(
        status_code=stored.status_code,
        content=stored.body,
        headers={TRACE_ID_HEADER: stored.trace_id},
    )
