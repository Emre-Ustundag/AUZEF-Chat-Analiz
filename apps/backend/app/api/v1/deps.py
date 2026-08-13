"""Route bağımlılıkları."""

from typing import Annotated

from fastapi import Depends, Header

from app.core.errors import ProviderAuthFailedError

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
