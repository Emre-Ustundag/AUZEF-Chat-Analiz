"""OpenRouter istemcisi testleri — GERÇEK AĞ YOK.

Tamamı `httpx.MockTransport` üzerinden koşar. Bu bir eksiklik değil bilinçli
bir tasarım: elimizde OpenRouter anahtarı yok ve olsaydı bile testlerin
sağlayıcının o günkü davranışına, kotasına ve faturasına bağlı olması
istenmezdi. Sahte transport, ADR §7/§10'un tarif ettiği hata yollarını
(rate limit, zaman aşımı, bozuk yanıt) DETERMİNİSTİK olarak üretebiliyor —
gerçek sağlayıcıda bunları tetiklemek çok daha zor olurdu.

⚠️ Bu testler sözleşmenin BİZİM TARAFIMIZI doğrular: doğru gövdeyi
gönderdiğimizi, dönen gövdeyi doğru yorumladığımızı ve hataları doğru
kodlara çevirdiğimizi. OpenRouter'ın gerçekten bu gövdeyi kabul ettiğini
DOĞRULAMAZ; o, anahtar geldiğinde yapılacak duman testinin işi.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.services.openrouter import OpenRouterClient, OpenRouterError, Usage


class _Answer(BaseModel):
    """Testlerin beklediği küçük şema."""

    label: str


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"label": {"type": "string"}},
    "required": ["label"],
    "additionalProperties": False,
}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        openrouter_base_url="https://openrouter.test/api/v1",
        openrouter_max_retries=2,
        openrouter_max_repair_attempts=2,
        openrouter_backoff_base_seconds=0.01,
        openrouter_backoff_max_seconds=0.02,
    )


def _ok(content: str, *, prompt: int = 100, completion: int = 20) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
        },
    )


def _client(
    settings: Settings,
    handler: Any,
    *,
    slept: list[float] | None = None,
) -> OpenRouterClient:
    import random

    return OpenRouterClient(
        api_key="sk-test-ANAHTAR-SIZMAMALI",
        model="google/gemini-2.5-flash",
        settings=settings,
        transport=httpx.MockTransport(handler),
        # Gerçekten uyumuyoruz; süreler yalnızca kaydediliyor.
        sleeper=(slept.append if slept is not None else (lambda _s: None)),
        rng=random.Random(0),
    )


# ------------------------------------------------------------- mutlu yol


def test_basarili_yanit_ayristirilir_ve_token_toplanir(settings: Settings) -> None:
    with _client(settings, lambda _r: _ok(json.dumps({"label": "sınav"}))) as client:
        result = client.complete_structured(
            system="s",
            user="u",
            schema=_SCHEMA,
            schema_name="answer",
            model_type=_Answer,
        )

    assert result.data.label == "sınav"
    assert result.repair_attempts == 0
    assert result.usage == Usage(prompt_tokens=100, completion_tokens=20)
    assert result.usage.total_tokens == 120


def test_istek_govdesi_sozlesmeye_uyar_ve_tool_cagrilari_kapali(settings: Settings) -> None:
    """ADR §9: tool/function çağrıları kapalı, structured output zorunlu."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return _ok(json.dumps({"label": "x"}))

    with _client(settings, handler) as client:
        client.complete_structured(
            system="s", user="u", schema=_SCHEMA, schema_name="answer", model_type=_Answer
        )

    body = seen["body"]
    # Tool çağrıları: alan HİÇ gönderilmemeli (boş liste bile değil).
    assert "tools" not in body
    assert "functions" not in body
    # Structured output gerçekten isteniyor mu?
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == _SCHEMA
    # Whitelist garantisinin çalışma anında kaybolmaması için:
    assert body["provider"]["require_parameters"] is True
    assert body["model"] == "google/gemini-2.5-flash"
    assert seen["url"] == "https://openrouter.test/api/v1/chat/completions"
    # Anahtar yalnızca Authorization başlığında.
    assert seen["auth"] == "Bearer sk-test-ANAHTAR-SIZMAMALI"
    assert "ANAHTAR-SIZMAMALI" not in json.dumps(body)


def test_kod_blogu_ile_sarilmis_json_kabul_edilir(settings: Settings) -> None:
    """Bazı modeller şemaya rağmen ```json ... ``` sarıyor."""
    wrapped = '```json\n{"label": "harç"}\n```'
    with _client(settings, lambda _r: _ok(wrapped)) as client:
        result = client.complete_structured(
            system="s", user="u", schema=_SCHEMA, schema_name="answer", model_type=_Answer
        )
    assert result.data.label == "harç"


# ------------------------------------------------- onarım (ADR §8: en fazla 2)


def test_bozuk_json_iki_onarim_denemesinden_sonra_duzelir(settings: Settings) -> None:
    responses = [
        _ok("bu JSON değil"),
        _ok(json.dumps({"yanlis_alan": 1})),
        _ok(json.dumps({"label": "kayıt"})),
    ]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[len(calls) - 1]

    with _client(settings, handler) as client:
        result = client.complete_structured(
            system="s", user="u", schema=_SCHEMA, schema_name="answer", model_type=_Answer
        )

    assert result.data.label == "kayıt"
    assert result.repair_attempts == 2
    # Token'lar ÜÇ çağrının toplamı: onarım bedava değil, rapora yansımalı.
    assert result.usage.prompt_tokens == 300

    # Onarım turu modele KENDİ bozuk çıktısını geri vermeli.
    son_mesajlar = json.loads(calls[-1].content)["messages"]
    assert son_mesajlar[1]["role"] == "user"
    assert son_mesajlar[2]["role"] == "assistant"
    assert son_mesajlar[2]["content"] == "bu JSON değil"
    assert "şemasına uymuyordu" in son_mesajlar[3]["content"]


def test_surekli_bozuk_yanit_provider_bad_response_ile_biter(settings: Settings) -> None:
    """ADR §8: iki onarım denemesi, sonra PROVIDER_BAD_RESPONSE."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _ok("asla geçerli JSON olmayacak")

    with _client(settings, handler) as client, pytest.raises(OpenRouterError) as excinfo:
        client.complete_structured(
            system="s", user="u", schema=_SCHEMA, schema_name="answer", model_type=_Answer
        )

    assert excinfo.value.code == "PROVIDER_BAD_RESPONSE"
    # 1 ilk deneme + 2 onarım = 3. Daha fazlası ADR §8'i çiğnerdi.
    assert len(calls) == 3
    # ADR §9: ham model yanıtı hata metnine SIZMAZ.
    assert "asla geçerli JSON" not in excinfo.value.detail
    assert "asla geçerli JSON" not in str(excinfo.value)


# ------------------------------------------------ rate limit / backoff / retry


def test_rate_limit_backoff_ile_yeniden_denenir_ve_duzelir(settings: Settings) -> None:
    slept: list[float] = []
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0.02"}, json={"error": "slow"})
        return _ok(json.dumps({"label": "ok"}))

    with _client(settings, handler, slept=slept) as client:
        result = client.complete_structured(
            system="s", user="u", schema=_SCHEMA, schema_name="answer", model_type=_Answer
        )

    assert result.data.label == "ok"
    assert len(calls) == 2
    # Tam olarak bir kez ve POZİTİF bir süre beklenmiş olmalı.
    assert len(slept) == 1
    assert slept[0] > 0


def test_surekli_rate_limit_retry_after_ile_sonlanir(settings: Settings) -> None:
    calls: list[httpx.Request] = []
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "7"}, json={"error": "rate"})

    with (
        _client(settings, handler, slept=slept) as client,
        pytest.raises(OpenRouterError) as excinfo,
    ):
        client.complete_structured(
            system="s", user="u", schema=_SCHEMA, schema_name="answer", model_type=_Answer
        )

    assert excinfo.value.code == "PROVIDER_RATE_LIMITED"
    assert excinfo.value.retry_after == 7.0
    # openrouter_max_retries=2 → 3 çağrı, 2 bekleme. SINIRLI retry (ADR §10/4).
    assert len(calls) == 3
    assert len(slept) == 2


def test_backoff_jitterli_ve_tavanla_sinirli(settings: Settings) -> None:
    """Jitter olmadan paralel worker'lar aynı anda uyanıp 429'u yeniden üretir."""
    slept: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    with _client(settings, handler, slept=slept) as client, pytest.raises(OpenRouterError):
        client.complete_structured(
            system="s", user="u", schema=_SCHEMA, schema_name="answer", model_type=_Answer
        )

    assert len(slept) == 2
    for delay in slept:
        # Tam jitter aralığı [tavan/2, tavan]; tavanı aşmamalı.
        assert 0 < delay <= settings.openrouter_backoff_max_seconds


# ----------------------------------------------------- kalıcı hatalar (retry yok)


@pytest.mark.parametrize(
    ("status", "beklenen"),
    [
        (401, "PROVIDER_AUTH_FAILED"),
        (403, "PROVIDER_AUTH_FAILED"),
        (402, "PROVIDER_AUTH_FAILED"),
        (404, "PROVIDER_BAD_RESPONSE"),
    ],
)
def test_kalici_hatalar_yeniden_denenmez(settings: Settings, status: int, beklenen: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"error": "no"})

    with _client(settings, handler) as client, pytest.raises(OpenRouterError) as excinfo:
        client.complete_structured(
            system="s", user="u", schema=_SCHEMA, schema_name="answer", model_type=_Answer
        )

    assert excinfo.value.code == beklenen
    # Geçersiz anahtarı 5 kez denemek kotayı yakar ve kullanıcıyı bekletir.
    assert len(calls) == 1


def test_zaman_asimi_provider_timeout_olur(settings: Settings) -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("timed out")

    with _client(settings, handler) as client, pytest.raises(OpenRouterError) as excinfo:
        client.complete_structured(
            system="s", user="u", schema=_SCHEMA, schema_name="answer", model_type=_Answer
        )

    assert excinfo.value.code == "PROVIDER_TIMEOUT"
    assert len(calls) == 3


def test_200_icinde_gelen_hata_govdesi_yakalanir(settings: Settings) -> None:
    """OpenRouter 200 durumuyla da hata döndürebiliyor."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"message": "model overloaded"}})

    with _client(settings, handler) as client, pytest.raises(OpenRouterError) as excinfo:
        client.complete_structured(
            system="s", user="u", schema=_SCHEMA, schema_name="answer", model_type=_Answer
        )

    assert excinfo.value.code == "PROVIDER_BAD_RESPONSE"
    assert "overloaded" not in excinfo.value.detail
