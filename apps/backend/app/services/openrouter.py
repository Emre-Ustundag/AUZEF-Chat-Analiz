"""OpenRouter istemcisi — ADR §2 (`httpx`), §7 (hata modeli), §10 (risk 4).

Bu modül TEK BİR ŞEY yapar: verilen sistem/kullanıcı mesajını OpenRouter'a
gönderip, verilen JSON Schema'ya uyan ve verilen Pydantic modeliyle
DOĞRULANMIŞ bir gövde döndürür. Sınıflandırma mantığı burada değil
(`pipeline/llm_classifier.py`), prompt metni burada değil
(`app/prompts/faq_analysis/`).

ÜÇ AYRI HATA SINIFI, ÜÇ AYRI DAVRANIŞ — karıştırılırlarsa iş ya boşuna
başarısız olur ya da sonsuza kadar döner:

* **Kalıcı hatalar** (401/403 → geçersiz anahtar, 404 → model yok):
  yeniden denemenin faydası yok, HEMEN sonlanır.
* **Geçici hatalar** (429, 5xx, bağlantı hatası, zaman aşımı):
  exponential backoff + jitter ile SINIRLI sayıda yeniden denenir
  (ADR §10 risk 4). Kota gerçekten dolmuşsa en sonunda
  `PROVIDER_RATE_LIMITED` + `retry_after` döner.
* **Şema hataları** (model geçerli JSON döndürmedi ya da şemaya uymadı):
  bu bir AĞ sorunu değil, MODEL sorunu. Aynı isteği tekrarlamak yerine
  modele ne yanlış yaptığı söylenip ONARIM istenir; en fazla iki deneme,
  sonra `PROVIDER_BAD_RESPONSE` (ADR §8).

ADR §9 — SIZINTI YOK:
* Anahtar yalnızca `Authorization` başlığında taşınır; hiçbir log satırına,
  hiçbir istisna metnine girmez. (`core/logging.py` ayrıca `Bearer ...`
  desenini maskeliyor — ikinci savunma katmanı.)
* HAM YANIT GÖVDESİ istisna metnine KONMAZ. `OpenRouterError.detail`
  kullanıcıya gidecek sabit bir metindir; ham gövde en fazla uzunluğuyla
  loglanır.
* `tools` alanı isteğe HİÇ KONMAZ — tool/function çağrıları kapalı (ADR §9).
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

#: Yeniden denemenin ANLAMLI olduğu HTTP durumları.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

#: Yeniden denemenin ANLAMSIZ olduğu, doğrudan sonlandıran durumlar.
_AUTH_STATUS = frozenset({401, 403})


class OpenRouterError(Exception):
    """Sözleşmedeki bir hata koduna çevrilebilen sağlayıcı hatası.

    `detail` KULLANICIYA GÖSTERİLEBİLİR olmak zorundadır: ham model yanıtı,
    anahtar parçası veya mesaj içeriği taşımaz (ADR §9).
    """

    def __init__(
        self,
        code: ErrorCode | str,
        detail: str,
        *,
        retry_after: float | None = None,
    ) -> None:
        resolved = ErrorCode(code)
        super().__init__(f"{resolved}: {detail}")
        self.code = resolved
        self.detail = detail
        self.retry_after = retry_after


@dataclass(frozen=True)
class Usage:
    """Bir veya birden çok çağrının token tüketimi.

    Bu sayılar MODELİN SINIFLANDIRMA ÇIKTISI DEĞİL, sağlayıcının faturalama
    ölçümüdür (ADR §4 modelin ürettiği ADET/ORAN'ları yasaklıyor; token
    sayacı o kapsamda değil). Yine de rapora `token_usage` olarak girerken
    tek kaynağı sağlayıcının `usage` bloğudur, modelin metni değil.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


@dataclass(frozen=True)
class Completion[TModel: BaseModel]:
    """Doğrulanmış model çıktısı + o çıktıyı üretmenin token maliyeti."""

    data: TModel
    usage: Usage
    #: Kaç onarım denemesi gerekti (0 = ilk yanıt geçerliydi). İzlenebilirlik
    #: için: sürekli onarım gerektiren bir model/prompt sessizce pahalıdır.
    repair_attempts: int = 0


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


class OpenRouterClient:
    """OpenRouter `chat/completions` üzerinden structured output istemcisi.

    SENKRONDUR ve öyle kalmalıdır. `workers/tasks.py` sınıflandırıcıyı zaten
    `asyncio.to_thread` içinde çağırıyor; async'e çevirmek `RecordClassifier`
    protokolünü ve Faz 2'de doğrulanmış `DeterministicClassifier`'ı da
    değiştirmeyi gerektirirdi.

    Testler `transport=httpx.MockTransport(...)` ve `sleeper=lambda s: None`
    vererek ağ ve gerçek bekleme olmadan tüm yolları koşturabilir.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = _sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._settings = settings
        self._sleeper = sleeper
        self._rng = rng or random.Random()
        self._client = httpx.Client(
            base_url=settings.openrouter_base_url,
            timeout=settings.openrouter_timeout_seconds,
            transport=transport,
            headers={
                # Anahtar YALNIZCA burada. Log filtresi `Bearer ...` desenini
                # ayrıca maskeliyor (core/logging.py).
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # OpenRouter'ın atıf başlıkları; anahtar içermez.
                "X-Title": "AUZEF Chat Analiz",
            },
        )

    def __enter__(self) -> OpenRouterClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------- kamu API

    def complete_structured[TModel: BaseModel](
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        model_type: type[TModel],
    ) -> Completion[TModel]:
        """Şemaya uyan, Pydantic ile doğrulanmış bir yanıt döndürür.

        Geçersiz yanıtta modele hatasını söyleyip ONARIM ister; ADR §8 gereği
        en fazla `openrouter_max_repair_attempts` deneme, sonra
        `PROVIDER_BAD_RESPONSE`.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        usage = Usage()
        max_repairs = self._settings.openrouter_max_repair_attempts

        for attempt in range(max_repairs + 1):
            raw_text, call_usage = self._call(messages, schema, schema_name)
            usage = usage + call_usage

            try:
                parsed = self._parse(raw_text, model_type)
            except _SchemaViolationError as violation:
                if attempt >= max_repairs:
                    # ADR §9: ham yanıt hata cevabına GİRMEZ; yalnızca
                    # uzunluğu ve deneme sayısı loglanır.
                    logger.warning(
                        "openrouter_bad_response_exhausted",
                        extra={
                            "model": self._model,
                            "attempts": attempt + 1,
                            "raw_length": len(raw_text),
                            "reason": violation.reason,
                        },
                    )
                    raise OpenRouterError(
                        "PROVIDER_BAD_RESPONSE",
                        "Model geçerli bir sonuç üretemedi.",
                    ) from None

                logger.info(
                    "openrouter_repair_attempt",
                    extra={
                        "model": self._model,
                        "attempt": attempt + 1,
                        "reason": violation.reason,
                    },
                )
                # Onarım turu: modelin KENDİ çıktısını geri veriyoruz ki neyi
                # düzelteceğini bilsin. Aynı isteği körlemesine tekrarlamak
                # büyük ihtimalle aynı bozuk yanıtı üretirdi.
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw_text},
                    {
                        "role": "user",
                        "content": (
                            "Önceki yanıtın JSON şemasına uymuyordu. Sorun: "
                            f"{violation.reason}. "
                            "Yalnızca şemaya uyan geçerli JSON döndür; "
                            "açıklama, kod bloğu işareti veya ek metin ekleme."
                        ),
                    },
                ]
                continue

            return Completion(data=parsed, usage=usage, repair_attempts=attempt)

        # Döngü `return` veya `raise` ile bitiyor; buraya düşülemez.
        raise AssertionError("unreachable")

    # ------------------------------------------------------------ iç yardım

    def _parse(self, raw_text: str, model_type: type[T]) -> T:
        """Ham metni JSON'a ve oradan Pydantic modeline çevirir."""
        text = raw_text.strip()
        if not text:
            raise _SchemaViolationError("yanıt boş")

        # Bazı modeller şema zorlanmasına rağmen ```json ... ``` sarmalıyor.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]
            text = text.strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            # `exc` metni girdinin bir parçasını taşıyabilir; zincire
            # BAĞLANMIYOR ve yalnızca konumu aktarılıyor.
            raise _SchemaViolationError(f"geçersiz JSON (konum {exc.pos})") from None

        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            # Pydantic hata metni `input` alanıyla birlikte kullanıcı verisi
            # taşıyabilir. Yalnızca ALAN ADI ve hata TİPİ alınıyor.
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['type']}" for err in exc.errors()[:5]
            )
            raise _SchemaViolationError(f"şema uyuşmazlığı ({problems})") from None

    def _call(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        schema_name: str,
    ) -> tuple[str, Usage]:
        """Tek bir chat/completions çağrısı; geçici hatalarda yeniden dener."""
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            # Structured output: modelin şema dışına çıkma yolu kapalı.
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            # OpenRouter yönlendirmesini yalnızca istenen parametreleri
            # GERÇEKTEN destekleyen sağlayıcılarla sınırlar. Bu olmadan istek
            # structured output desteklemeyen bir sağlayıcıya düşebilir ve
            # whitelist'in doğruladığı garanti çalışma anında kaybolurdu.
            "provider": {"require_parameters": True},
            # ADR §9: tool/function çağrıları kapalı. `tools` alanı isteğe
            # HİÇ konmuyor — boş liste göndermek yerine hiç göndermemek,
            # sağlayıcı farklarına karşı daha güvenli.
        }

        last_error: OpenRouterError | None = None

        for attempt in range(self._settings.openrouter_max_retries + 1):
            try:
                response = self._client.post("/chat/completions", json=body)
            except httpx.TimeoutException:
                last_error = OpenRouterError(
                    "PROVIDER_TIMEOUT", "Model sağlayıcısı zamanında yanıt vermedi."
                )
            except httpx.HTTPError:
                # Bağlantı/aktarım hatası. İstisna metni URL taşıyabildiği
                # için ZİNCİRE BAĞLANMIYOR.
                last_error = OpenRouterError("PROVIDER_TIMEOUT", "Model sağlayıcısına ulaşılamadı.")
            else:
                if response.status_code == 200:
                    return self._extract(response)

                last_error = self._classify_status(response)
                if response.status_code not in _RETRYABLE_STATUS:
                    raise last_error

            if attempt >= self._settings.openrouter_max_retries:
                break
            self._sleeper(self._backoff(attempt, last_error))

        assert last_error is not None
        logger.warning(
            "openrouter_retries_exhausted",
            extra={
                "model": self._model,
                "code": last_error.code,
                "attempts": self._settings.openrouter_max_retries + 1,
            },
        )
        raise last_error

    def _classify_status(self, response: httpx.Response) -> OpenRouterError:
        """HTTP durumunu sözleşmedeki hata koduna çevirir. Gövdeyi TAŞIMAZ."""
        status = response.status_code

        if status in _AUTH_STATUS:
            return OpenRouterError(
                "PROVIDER_AUTH_FAILED",
                "OpenRouter anahtarı reddedildi.",
            )
        if status == 402:
            return OpenRouterError(
                "PROVIDER_AUTH_FAILED",
                "OpenRouter hesabında yeterli kredi yok.",
            )
        if status == 404:
            return OpenRouterError(
                "PROVIDER_BAD_RESPONSE",
                "Seçilen model sağlayıcıda bulunamadı.",
            )
        if status == 429:
            return OpenRouterError(
                "PROVIDER_RATE_LIMITED",
                "Model sağlayıcısının istek sınırına ulaşıldı.",
                retry_after=_retry_after(response),
            )
        if status in {408, 504}:
            return OpenRouterError(
                "PROVIDER_TIMEOUT",
                "Model sağlayıcısı zamanında yanıt vermedi.",
            )
        return OpenRouterError(
            "PROVIDER_BAD_RESPONSE",
            "Model sağlayıcısı beklenmeyen bir yanıt döndürdü.",
        )

    def _backoff(self, attempt: int, error: OpenRouterError | None) -> float:
        """Exponential backoff + jitter (ADR §10 risk 4).

        Sağlayıcı `Retry-After` söylediyse ONA UYULUR — kendi hesabımızı
        dayatmak kotayı daha da geciktirirdi. Jitter her koşulda eklenir:
        aynı anda 429 yiyen worker'lar aynı anda uyanmamalı.
        """
        settings = self._settings
        ceiling: float = settings.openrouter_backoff_max_seconds
        base: float = min(ceiling, settings.openrouter_backoff_base_seconds * (2.0**attempt))
        if error is not None and error.retry_after is not None:
            base = min(ceiling, max(base, error.retry_after))
        # Tam jitter: [base/2, base]. Alt sınır korunuyor ki bekleme
        # anlamsız derecede kısalmasın.
        jitter: float = self._rng.random()
        return base / 2 + jitter * (base / 2)

    def _extract(self, response: httpx.Response) -> tuple[str, Usage]:
        """200 yanıtından mesaj metnini ve token sayacını çıkarır."""
        try:
            payload = response.json()
        except ValueError:
            raise OpenRouterError(
                "PROVIDER_BAD_RESPONSE",
                "Model sağlayıcısı okunamayan bir yanıt döndürdü.",
            ) from None

        # OpenRouter 200 içinde de hata döndürebiliyor.
        if isinstance(payload, dict) and payload.get("error"):
            raise OpenRouterError(
                "PROVIDER_BAD_RESPONSE",
                "Model sağlayıcısı isteği işleyemedi.",
            )

        try:
            choices = payload["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise OpenRouterError(
                "PROVIDER_BAD_RESPONSE",
                "Model sağlayıcısı beklenen biçimde yanıt vermedi.",
            ) from None

        if not isinstance(content, str):
            raise OpenRouterError(
                "PROVIDER_BAD_RESPONSE",
                "Model sağlayıcısı beklenen biçimde yanıt vermedi.",
            )

        raw_usage = payload.get("usage") or {}
        usage = Usage(
            prompt_tokens=_as_int(raw_usage.get("prompt_tokens")),
            completion_tokens=_as_int(raw_usage.get("completion_tokens")),
        )
        return content, usage


class _SchemaViolationError(Exception):
    """Model yanıtı JSON/şema doğrulamasından geçmedi (iç kullanım).

    `reason` KULLANICI VERİSİ TAŞIMAZ: yalnızca alan adları ve hata tipleri.
    Onarım turunda modele geri verildiği için bu önemli — aksi hâlde
    maskelenmiş bir PII parçası prompt'a geri dolaşabilirdi.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _as_int(value: object) -> int:
    """Sağlayıcı sayacını güvenle int'e çevirir; saçmalarsa 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _retry_after(response: httpx.Response) -> float | None:
    """`Retry-After` başlığını saniyeye çevirir; yoksa/bozuksa `None`.

    Yalnızca saniye biçimi destekleniyor. HTTP-date biçimi de geçerlidir ama
    OpenRouter saniye gönderiyor; yanlış ayrıştırıp uydurma bir sayı
    döndürmektense `None` dönmek doğru — çağıran kendi backoff'unu uygular.
    """
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
