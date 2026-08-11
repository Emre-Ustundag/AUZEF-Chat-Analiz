"""Ön işleme — ADR §5 "Aşama B" madde 1-4, plan §4.

Sıra ZORUNLUDUR ve keyfi değildir:

1. **Eleme** — boş, çok kısa ve sistem kayıtları atılır. Bunlar analize
   girseydi hem `analyzed_count` şişerdi hem de "ok", "teşekkürler" gibi
   satırlar sahte bir SSS kategorisi üretirdi.
2. **PII maskeleme** — sınıflandırıcıya GİTMEDEN ÖNCE (ADR §9 / plan §5.3).
   Faz 3'te sınıflandırıcının yerini OpenRouter alacak; maskeleme burada
   olduğu için o değişiklik PII yolunu etkilemez. `services/redaction.py`
   yeniden kullanılır, yeniden yazılmaz.
3. **Normalizasyon** — küçük harfe indirme (Türkçe kurallarıyla),
   noktalama temizliği, boşluk sadeleştirme. Yalnızca tekilleştirme ANAHTARI
   için; kullanıcıya gösterilecek örnek metin normalize EDİLMEZ.
4. **Exact hash tekilleştirme** — normalize metnin SHA-256'sı. Gerçek
   frekanslar `RecordGroup.count` içinde KORUNUR; ADR §4'ün "sayılar gerçek
   frekanslardan hesaplanır" kuralı bu sayacın doğruluğuna dayanır.

Neden ayrı bir modül: bu adımların hiçbiri LLM'e bağlı değil ve Faz 3'te
tek satırı değişmeyecek. Sınıflandırıcının değişmesi ön işlemeyi etkilemez.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.core.config import Settings
from app.services.redaction import redact_pii

#: Analiz dışı bırakılan sistem/otomasyon kayıtları. Chatbot dökümlerinde
#: sık görülen, kullanıcı sorusu OLMAYAN satırlar.
SYSTEM_MESSAGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^\s*\[?(sistem|system|bot|otomatik)\b"),
    re.compile(r"(?i)^\s*(görüşme|gorusme|oturum|session)\s+(başladı|basladi|bitti|sonlandı)"),
    re.compile(r"(?i)^\s*(null|n/?a|-{2,}|\.{2,})\s*$"),
)

#: Yalnızca nezaket bildiren, soru içermeyen kayıtlar. Tek başına bir SSS
#: kategorisi oluşturmaları anlamsız olurdu.
COURTESY_ONLY = frozenset(
    {
        "merhaba",
        "selam",
        "teşekkürler",
        "tesekkurler",
        "teşekkür ederim",
        "sağolun",
        "sagolun",
        "tamam",
        "ok",
        "okey",
        "peki",
        "iyi günler",
        "iyi gunler",
        "günaydın",
        "gunaydin",
        "eyvallah",
    }
)

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

#: Türkçe'de `str.lower()` "I" harfini "i" yapar ama doğrusu "ı"dır.
#: Normalizasyon anahtarı bu yüzden elle düzeltilir: "İSTANBUL" ve "istanbul"
#: aynı kovaya düşmezse tekilleştirme sessizce eksik çalışır.
_TR_LOWER = str.maketrans({"I": "ı", "İ": "i", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç"})


def normalize(text: str) -> str:
    """Tekilleştirme ve sınıflandırma için kanonik biçim.

    Kullanıcıya GÖSTERİLEN metin bu değildir — örnekler ham (ama redakte
    edilmiş) hâlleriyle saklanır.
    """
    lowered = text.translate(_TR_LOWER).lower()
    stripped = _PUNCTUATION.sub(" ", lowered)
    return _WHITESPACE.sub(" ", stripped).strip()


def is_system_message(text: str) -> bool:
    return any(pattern.search(text) for pattern in SYSTEM_MESSAGE_PATTERNS)


@dataclass
class RecordGroup:
    """Aynı normalize metne sahip kayıtların birleşimi.

    `count` bu grubun GERÇEK frekansıdır: aggregation'daki her sayı bunların
    toplamıdır, sınıflandırıcının ürettiği bir değer değildir (ADR §4).
    """

    #: Normalize metnin SHA-256'sının ilk 16 hex hanesi. Kayıt kimliği olarak
    #: kullanılır; sınıflandırıcı bu kimlikleri kategoriye eşler.
    record_id: str
    normalized: str
    #: Redakte edilmiş temsilci metin (grubun ilk görülen hâli).
    redacted_text: str
    count: int = 0
    #: Grup içinde PII maskelenmiş kayıt sayısı.
    redacted_count: int = 0
    #: Rapordaki `redacted_examples` için farklı yazımlar.
    examples: list[str] = field(default_factory=list)


@dataclass
class PreprocessResult:
    """Ön işlemenin çıktısı.

    `total_rows` ile `analyzed_count + discarded_count` HER ZAMAN eşittir;
    `test_aggregate.py` bunu doğruluyor.
    """

    total_rows: int = 0
    analyzed_count: int = 0
    discarded_count: int = 0
    redacted_count: int = 0
    groups: list[RecordGroup] = field(default_factory=list)

    @property
    def unique_count(self) -> int:
        return len(self.groups)

    @property
    def duplicate_count(self) -> int:
        """Tekilleştirmeyle elenen kayıt sayısı."""
        return self.analyzed_count - self.unique_count


def _record_id(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class Preprocessor:
    """Kayıtları PARÇA PARÇA tüketen biriktirici.

    Neden tek bir fonksiyon değil: worker, aşama içinde anlamlı ilerleme
    yazabilmek için (ADR §2) okumayı parti parti yapmak zorunda. Tek bir
    çağrıda tüm dosyayı işleyen bir fonksiyon, iş bitene kadar hiçbir
    ilerleme yazamazdı ve kullanıcı 40 saniye boyunca donmuş bir çubuk
    görürdü.

    Bellek: satırlar biriktirilmez, yalnızca BENZERSİZ gruplar tutulur.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._result = PreprocessResult()
        self._index: dict[str, RecordGroup] = {}
        self._variants: dict[str, Counter[str]] = {}

    @property
    def rows_seen(self) -> int:
        return self._result.total_rows

    def consume(self, values: Iterable[str | None]) -> None:
        settings = self._settings
        result = self._result

        for raw in values:
            result.total_rows += 1

            if raw is None:
                result.discarded_count += 1
                continue

            text = _WHITESPACE.sub(" ", raw).strip()
            if (
                not text
                or len(text) < settings.preprocess_min_message_length
                or is_system_message(text)
                or normalize(text) in COURTESY_ONLY
            ):
                result.discarded_count += 1
                continue

            # ---- PII maskeleme: sınıflandırıcıya gitmeden ÖNCE (ADR §9) ----
            redacted = redact_pii(text)
            was_redacted = redacted != text

            normalized = normalize(redacted)
            if not normalized:
                # Kayıt tamamen PII'dan ibaretmiş (örn. yalnız bir telefon
                # numarası). Maskelendikten sonra soru olarak anlamı kalmıyor.
                result.discarded_count += 1
                continue

            result.analyzed_count += 1
            if was_redacted:
                result.redacted_count += 1

            record_id = _record_id(normalized)
            group = self._index.get(record_id)
            if group is None:
                group = RecordGroup(
                    record_id=record_id,
                    normalized=normalized,
                    redacted_text=redacted,
                )
                self._index[record_id] = group
                self._variants[record_id] = Counter()

            group.count += 1
            if was_redacted:
                group.redacted_count += 1
            self._variants[record_id][redacted] += 1

    def finish(self) -> PreprocessResult:
        for record_id, group in self._index.items():
            # En sık görülen yazım temsilci olur; örnekler de oradan gelir.
            variants = self._variants[record_id].most_common()
            group.redacted_text = variants[0][0]
            group.examples = [
                variant for variant, _ in variants[: self._settings.report_examples_per_question]
            ]

        # Sıralama DETERMİNİSTİK: önce frekans, eşitlikte kayıt kimliği. Aynı
        # girdi her çalıştırmada aynı raporu üretmeli.
        self._result.groups = sorted(self._index.values(), key=lambda g: (-g.count, g.record_id))
        return self._result


def preprocess(values: Iterable[str | None], settings: Settings) -> PreprocessResult:
    """Tek seferde ön işleme. Testlerin ve küçük girdilerin kısayolu."""
    preprocessor = Preprocessor(settings)
    preprocessor.consume(values)
    return preprocessor.finish()
