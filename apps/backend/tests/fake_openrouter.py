"""Sahte OpenRouter sağlayıcısı — `httpx.MockTransport` için handler.

ELİMİZDE GERÇEK OPENROUTER ANAHTARI YOK. Bu bir eksiklik değil, testler için
avantaj: gerçek sağlayıcıda tetiklemesi zor olan yolları (rate limit, bozuk
JSON, kayıt atlama) burada DETERMİNİSTİK olarak üretebiliyoruz.

`FakeOpenRouter` gerçek bir modelin yaptığı işi taklit eder: gelen istekteki
`<kayit id="...">` etiketlerini okur, metindeki anahtar sözcüğe göre
kategorilere ayırır ve şemaya uyan bir yanıt döndürür. Böylece uçtan uca
testler "her şey sabit" olmadan, gerçek prompt gövdesi üzerinden koşar.

⚠️ Bu sınıf OpenRouter'ın gerçekten böyle davrandığını KANITLAMAZ. Yalnızca
bizim tarafımızı — gövdeyi doğru kurduğumuzu ve yanıtı doğru yorumladığımızı
— doğrular.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

_KAYIT = re.compile(r'<kayit id="([^"]+)">(.*?)</kayit>', re.DOTALL)
_KATEGORI = re.compile(r'<kategori id="([^"]+)" tema="([^"]*)">(.*?)</kategori>', re.DOTALL)

#: Metindeki anahtar sözcüğe göre tema. Gerçek bir modelin anlamsal
#: gruplamasının kaba taklidi; testler için yeterli ve DETERMİNİSTİK.
_TEMALAR: tuple[tuple[str, str, str], ...] = (
    ("sınav", "Sınav", "Sınav ne zaman yapılacak?"),
    ("final", "Sınav", "Sınav ne zaman yapılacak?"),
    ("harç", "Harç ve Ödeme", "Harç ücreti ne kadar?"),
    ("ödeme", "Harç ve Ödeme", "Harç ücreti ne kadar?"),
    ("ders", "Ders Materyali", "Ders materyallerine nereden ulaşılır?"),
    ("materyal", "Ders Materyali", "Ders materyallerine nereden ulaşılır?"),
    ("kayıt", "Kayıt İşlemleri", "Kayıt işlemleri nasıl yapılır?"),
)


def _tema(text: str) -> tuple[str, str]:
    lowered = text.lower()
    for anahtar, tema, soru in _TEMALAR:
        if anahtar in lowered:
            return tema, soru
    return "Diğer", "Diğer sorular"


class FakeOpenRouter:
    """Şemaya uyan yanıtlar üreten sahte sağlayıcı.

    Parametreler senaryo kurmak içindir:

    * `drop_records` — modelin kayıt ATLAMASINI taklit eder (en sinsi hata).
    * `bad_json_first` — ilk N yanıtı bozuk döndürür; onarım yolunu tetikler.
    * `always_bad_json` — hiç düzelmez; `PROVIDER_BAD_RESPONSE` yolunu test eder.
    * `rate_limit_first` — ilk N isteğe 429 döner; backoff yolunu test eder.
    """

    def __init__(
        self,
        *,
        drop_records: int = 0,
        bad_json_first: int = 0,
        always_bad_json: bool = False,
        rate_limit_first: int = 0,
        prompt_tokens: int = 400,
        completion_tokens: int = 60,
    ) -> None:
        self.drop_records = drop_records
        self.bad_json_first = bad_json_first
        self.always_bad_json = always_bad_json
        self.rate_limit_first = rate_limit_first
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

        self.requests: list[dict[str, Any]] = []
        #: Gönderilen Authorization başlıkları — anahtar sızıntısı testleri için.
        self.auth_headers: list[str] = []
        self._bad_sent = 0
        self._rate_sent = 0

    # ------------------------------------------------------------------ handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.auth_headers.append(request.headers.get("authorization", ""))

        if self._rate_sent < self.rate_limit_first:
            self._rate_sent += 1
            return httpx.Response(
                429,
                headers={"retry-after": "3"},
                json={"error": {"message": "rate limited"}},
            )

        if self.always_bad_json or self._bad_sent < self.bad_json_first:
            self._bad_sent += 1
            return self._wrap("bu geçerli JSON değil")

        name = body["response_format"]["json_schema"]["name"]
        prompt = body["messages"][1]["content"]
        payload = self._map(prompt) if name == "faq_map" else self._reduce(prompt)
        return self._wrap(json.dumps(payload, ensure_ascii=False))

    # -------------------------------------------------------------------- üretim

    def _map(self, prompt: str) -> dict[str, Any]:
        kayitlar = _KAYIT.findall(prompt)
        if self.drop_records:
            kayitlar = kayitlar[: max(0, len(kayitlar) - self.drop_records)]

        kategoriler: dict[str, tuple[str, str]] = {}
        eslemeler: list[dict[str, str]] = []

        for record_id, text in kayitlar:
            tema, soru = _tema(text)
            cid = (
                f"c-{len(kategoriler)}" if tema not in {t for t, _ in kategoriler.values()} else ""
            )
            if not cid:
                cid = next(k for k, (t, _) in kategoriler.items() if t == tema)
            kategoriler.setdefault(cid, (tema, soru))
            eslemeler.append({"record_id": record_id, "category_id": cid})

        return {
            "categories": [
                {"category_id": cid, "canonical_question": soru, "theme": tema}
                for cid, (tema, soru) in kategoriler.items()
            ],
            "assignments": eslemeler,
        }

    def _reduce(self, prompt: str) -> dict[str, Any]:
        kovalar = _KATEGORI.findall(prompt)
        gruplar: dict[str, dict[str, Any]] = {}
        for key, tema, soru in kovalar:
            grup = gruplar.setdefault(
                tema,
                {"canonical_question": soru.strip(), "theme": tema, "member_category_ids": []},
            )
            grup["member_category_ids"].append(key)
        return {"groups": list(gruplar.values())}

    def _wrap(self, content: str) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": self.prompt_tokens,
                    "completion_tokens": self.completion_tokens,
                },
            },
        )
