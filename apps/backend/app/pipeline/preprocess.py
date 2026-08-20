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
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.config import Settings
from app.schemas.analysis import ChatbotLogConfig
from app.services.redaction import (
    EMAIL_MASK,
    ID_MASK,
    NUMBER_MASK,
    PHONE_MASK,
    URL_MASK,
    redact_pii,
)

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


#: Maskelerin normalize edilmiş hâlleri: `[TELEFON]` → `telefon`.
#: `normalize` köşeli parantezleri sildiği için maske sıradan bir sözcüğe
#: dönüşüyor; bu liste olmadan yalnızca bir telefon numarasından ibaret bir
#: satır "telefon" adlı sahte bir SSS kategorisi üretirdi.
_MASK_TOKENS = frozenset(
    normalize(mask) for mask in (EMAIL_MASK, PHONE_MASK, ID_MASK, NUMBER_MASK, URL_MASK)
)


def is_only_masks(normalized: str) -> bool:
    """Kayıt maskelendikten sonra geriye anlamlı içerik kalmış mı?"""
    tokens = normalized.split()
    return bool(tokens) and all(token in _MASK_TOKENS for token in tokens)


@dataclass(frozen=True)
class SourceRecord:
    """Ön işlemeye giren tek satır.

    `GENERIC` ön ayarda yalnızca `text` doludur. `CHATBOT_LOG` ön ayarında
    `session_id` ve `date` (UTC `YYYY-MM-DD`) satır filtresinden geçen
    kullanıcı mesajları için taşınır; rol/tip filtresine takılan satırlar
    `text=None` ile gelir ve `discarded_count`'a sayılır — böylece
    `analyzed_count + discarded_count == total_rows` değişmezi ön ayardan
    bağımsız korunur.
    """

    text: str | None
    session_id: str | None = None
    date: str | None = None


#: `message_time_tr` benzeri kolonlarda görülen, timezone'suz yaygın biçimler.
_NAIVE_TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y")


def utc_date_of(raw: str | None) -> str | None:
    """Zaman damgasını UTC gün anahtarına (`YYYY-MM-DD`) indirger.

    Timezone taşıyan değerler UTC'ye çevrilir; naive değerler olduğu gibi gün
    alınır (kaynağın yerel saatini bilmiyoruz ve uydurmak günü kaydırabilir).
    Çözümlenemeyen değer `None` döner: satır analize girmeye devam eder,
    yalnızca zaman serisine katılmaz — bozuk bir tarih yüzünden mesaj atmak
    sayımları sessizce eksiltirdi.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    parsed: datetime | None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
        for fmt in _NAIVE_TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.date().isoformat()


def _normalize_filter_value(value: str) -> str:
    """Rol/tip karşılaştırma anahtarı: kırpılmış + Türkçe küçük harf."""
    return value.strip().translate(_TR_LOWER).lower()


def chatbot_records(
    rows: Iterable[tuple[str | None, ...]],
    config: ChatbotLogConfig,
) -> Iterator[SourceRecord]:
    """`CHATBOT_LOG` satır filtresi — PII ve tekilleştirmeden ÖNCE (plan B2).

    Satır sırası SABİT sözleşmedir (`workers/tasks.py` kurar):
    `(text, role, message_type?, session_id?, timestamp?)` — seçilmemiş
    kolonlar tuple'da hiç yer almaz.

    Rol değeri izin listesinde olmayan satırlar (bot cevapları, sistem
    olayları) ve tip filtresi seçiliyken tipi uymayan satırlar `text=None`
    olarak üretilir: toplam satır sayısına girerler ama analiz edilmezler.
    Gerçek dökümde bu filtre tek başına satırların ~%87'sini eler.
    """
    role_values = frozenset(_normalize_filter_value(value) for value in config.role_user_values)
    allowed_types = (
        frozenset(_normalize_filter_value(value) for value in config.allowed_message_types)
        if config.allowed_message_types is not None
        else None
    )
    has_type = config.message_type_column is not None
    has_session = config.session_id_column is not None
    has_timestamp = config.timestamp_column is not None

    for row in rows:
        text = row[0]
        role = row[1]
        cursor = 2
        message_type = None
        if has_type:
            message_type = row[cursor]
            cursor += 1
        session_id = None
        if has_session:
            session_id = row[cursor]
            cursor += 1
        timestamp = row[cursor] if has_timestamp else None

        if role is None or _normalize_filter_value(role) not in role_values:
            yield SourceRecord(text=None)
            continue
        if allowed_types is not None and (
            message_type is None or _normalize_filter_value(message_type) not in allowed_types
        ):
            yield SourceRecord(text=None)
            continue

        yield SourceRecord(
            text=text,
            session_id=session_id.strip() if session_id else None,
            date=utc_date_of(timestamp),
        )


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
    #: Bu grubun görüldüğü benzersiz oturumlar (`CHATBOT_LOG` + oturum kolonu).
    session_ids: set[str] = field(default_factory=set)
    #: UTC gün anahtarı -> bu grubun o günkü frekansı (`CHATBOT_LOG` + zaman
    #: kolonu). Toplamı `count`'tan küçük olabilir: tarihi çözümlenemeyen
    #: kayıtlar seriye girmez.
    daily_counts: Counter[str] = field(default_factory=Counter)


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
    #: Analize giren kayıtlardaki benzersiz oturum sayısı; oturum takibi
    #: kapalıyken `None` (rapordaki `session_count` doğrudan buradan gelir).
    session_count: int | None = None
    #: Zaman kolonu seçildi mi? Raporun `time_series` üretip üretmeyeceğini
    #: belirler — "takip açıktı ama hiçbir tarih çözümlenemedi" ile "takip
    #: hiç yoktu" ayrımı bu bayrak olmadan yapılamazdı.
    dates_tracked: bool = False

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

    def __init__(
        self,
        settings: Settings,
        *,
        track_sessions: bool = False,
        track_dates: bool = False,
    ) -> None:
        self._settings = settings
        self._result = PreprocessResult(
            session_count=0 if track_sessions else None,
            dates_tracked=track_dates,
        )
        self._index: dict[str, RecordGroup] = {}
        self._variants: dict[str, Counter[str]] = {}
        self._track_sessions = track_sessions
        self._track_dates = track_dates
        self._sessions: set[str] = set()

    @property
    def rows_seen(self) -> int:
        return self._result.total_rows

    def consume(self, values: Iterable[str | None]) -> None:
        """Düz metin kısayolu (`GENERIC`); satırlar `SourceRecord`'a sarılır."""
        self.consume_records(SourceRecord(text=value) for value in values)

    def consume_records(self, records: Iterable[SourceRecord]) -> None:
        settings = self._settings
        result = self._result

        for record in records:
            result.total_rows += 1

            raw = record.text
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
            if not normalized or is_only_masks(normalized):
                # Kayıt tamamen PII'dan ibaretmiş (örn. yalnız bir telefon
                # numarası). Maskelendikten sonra soru olarak anlamı kalmıyor
                # ve analize girseydi "telefon" adlı sahte bir kategori
                # üretirdi.
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

            if self._track_sessions and record.session_id:
                group.session_ids.add(record.session_id)
                self._sessions.add(record.session_id)
            if self._track_dates and record.date:
                group.daily_counts[record.date] += 1

    def finish(self) -> PreprocessResult:
        if self._track_sessions:
            self._result.session_count = len(self._sessions)
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
