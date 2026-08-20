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
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol

from app.core.config import Settings
from app.schemas.analysis import ConversationConfig
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

#: Soru İÇERMEYEN, tek başına bir SSS kategorisi oluşturması anlamsız olan
#: kayıtlar. İki grup var: nezaket kalıpları ve içeriksiz doldurma
#: ("evet", "cevap bekliyorum" gibi cevap/onay satırları).
#:
#: Liste TAHMİN DEĞİL, ölçüm: 67.036 gerçek kullanıcı mesajı üzerinde
#: normalize edilmiş tam eşleşme sayıldı. Genişletme öncesi 1.278 kayıt
#: eleniyordu, sonrasında 3.300 (%4,9) — yani ~2.000 kayıt boşuna
#: sınıflandırılıp para harcıyordu.
#:
#: İKİ KURAL:
#:
#: 1. Buraya yazılan her değer `normalize()` çıktısı biçiminde olmalı
#:    (Türkçe küçük harf, noktalama yok, tek boşluk). Aksi hâlde eşleşme
#:    SESSİZCE hiç kurulmaz.
#: 2. Eşleşme TAM METİN üzerindedir. Bu yüzden "evet" burada olsa da
#:    "evet mezun oldum mu" elenmez. Ama liste büyüdükçe gerçek soruları
#:    yutma riski artar: "mezuniyet", "diploma", "staj" gibi tek kelimelik
#:    MEŞRU konular veride sık geçiyor (464 / 320 / 100 kez) ve buraya
#:    asla girmemeli. Regresyon testi ikisini birlikte doğrular
#:    (`test_pipeline.py::test_nezaket_listesi_gercek_konulari_yutmaz`).
COURTESY_ONLY = frozenset(
    {
        # ---- selamlama (merhabalar 146, hey 34, hadi 24, alo 18) ----
        "merhaba",
        "merhabalar",
        "selam",
        "selamlar",
        "selamün aleyküm",
        "selamunaleykum",
        "hey",
        "alo",
        "hadi",
        "hoşbuldum",
        "hosbuldum",
        "hoş buldum",
        "hos buldum",
        "hoşbulduk",
        "hosbulduk",
        "hoş bulduk",
        "hoşgeldiniz",
        # ---- teşekkür / vedalaşma ----
        "teşekkürler",
        "tesekkurler",
        "teşekkür ederim",
        "teşekkur ederim",
        "tesekkur ederim",
        "teşekkürler ederim",
        "çok teşekkürler",
        "cok tesekkurler",
        "çok teşekkür ederim",
        "teşekkürler iyi günler",
        "rica ederim",
        "sağolun",
        "sagolun",
        "sağ olun",
        "sag olun",
        "sağol",
        "sagol",
        "eyvallah",
        "iyi günler",
        "iyi gunler",
        "günaydın",
        "gunaydin",
        "iyi akşamlar",
        "iyi aksamlar",
        "iyi geceler",
        "iyi çalışmalar",
        "iyi calismalar",
        "kolay gelsin",
        # ---- onay / ret (hayır 456+42, evet 257, okudum anladım 325+36) ----
        "evet",
        "hayır",
        "hayir",
        "olur",
        "olmaz",
        "tamam",
        "tamamdır",
        "tamamdir",
        "ok",
        "okey",
        "peki",
        "tabi",
        "tabii",
        "lütfen",
        "lutfen",
        "okudum",
        "okudum anladım",
        "okudum anladim",
        "anladım",
        "anladim",
        "anlamadım",
        "anlamadim",
        # ---- cevap bekleme (cevap 165, cevap bekliyorum 71) ----
        # Soru değil, önceki soruyu tekrar dürtme. Kendi başına kategori
        # olursa raporun tepesine "cevap bekliyorum" diye anlamsız bir
        # başlık çıkar.
        "cevap",
        "cevap ver",
        "cevap verin",
        "cevap verir misiniz",
        "cevap bekliyorum",
        "cevap alamadım",
        "cevap alamadim",
        "cevap yok",
        "bekliyorum",
        # ---- deneme satırları ----
        "test",
        "deneme",
        "asdf",
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
class ContextTurn:
    """Bir hedef kullanıcı mesajından önce görülen, sayılmayan bağlam turn'ü."""

    role: Literal["user", "assistant"]
    redacted_text: str


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
    #: Yalnızca contextual modda doludur; bu turn'ler sınıflandırılırken
    #: kanıt olarak görünür fakat `count` veya rapor örneklerine girmez.
    context_turns: tuple[ContextTurn, ...] = ()
    #: Boş geçmişli ilk kullanıcı turn'ü de contextual V4 zarfıyla render
    #: edilmelidir; yalnız `context_turns` boşluğuna bakmak bunu ayıramaz.
    contextual: bool = False


@dataclass
class PreprocessResult:
    """Ön işlemenin çıktısı.

    `total_rows`, `analyzed_count + context_only_count + discarded_count`
    toplamına HER ZAMAN eşittir. Legacy modda `context_only_count` sıfırdır.
    """

    total_rows: int = 0
    analyzed_count: int = 0
    #: Contextual modda bağlam için uygun olan hedef-dışı fiziksel satırlar.
    #: Turn/token tavanı veya session sonunda kalması nedeniyle her biri bir
    #: prompt'a girmeyebilir; hedef değildir ve kategori/yüzde paydasına girmez.
    context_only_count: int = 0
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


class ConversationOrderError(Exception):
    """Bir session'ın mesaj sırası kaynak akışında geriye gitti."""

    def __init__(self, source_row: int) -> None:
        super().__init__(f"conversation_order_not_monotonic_at_row:{source_row}")
        self.source_row = source_row


class ConversationRowLike(Protocol):
    """Salt-okunur XLSX satırı sözleşmesi.

    ``ConversationRow`` frozen dataclass olduğu için Protocol alanları da
    property olmalı; aksi hâlde mypy bunları yazılabilir değişken sayar ve
    gerçek satır tipini yapısal olarak reddeder.
    """

    @property
    def source_row(self) -> int: ...

    @property
    def included(self) -> bool: ...

    @property
    def session_id(self) -> str | None: ...

    @property
    def message_order(self) -> str | None: ...

    @property
    def role(self) -> str | None: ...

    @property
    def message_type(self) -> str | None: ...

    @property
    def text(self) -> str | None: ...


def _order_key(value: str) -> tuple[int, Decimal | str]:
    """Sayıları sayısal, diğer sıra/zaman değerlerini deterministik karşılaştır."""
    try:
        numeric = Decimal(value)
    except InvalidOperation:
        return (1, value.casefold())
    if not numeric.is_finite():
        raise ValueError("conversation_order_not_finite")
    return (0, numeric)


def _order_is_before(
    current: tuple[int, Decimal | str], previous: tuple[int, Decimal | str]
) -> bool:
    if current[0] != previous[0]:
        # Aynı session'da sıra formatının sayısaldan metne değişmesi güvenli
        # biçimde karşılaştırılamaz; geriye gidiş gibi reddedilir.
        return True
    if current[0] == 0:
        assert isinstance(current[1], Decimal)
        assert isinstance(previous[1], Decimal)
        return current[1] < previous[1]
    assert isinstance(current[1], str)
    assert isinstance(previous[1], str)
    return current[1] < previous[1]


def _prepare_context_text(raw: str, settings: Settings) -> tuple[str, str] | None:
    """Bağlamı PII'dan arındır; nezaket/takip mesajlarını bağlamda koru."""
    text = _WHITESPACE.sub(" ", raw).strip()
    if not text or len(text) < settings.preprocess_min_message_length or is_system_message(text):
        return None
    redacted = redact_pii(text)
    normalized = normalize(redacted)
    if not normalized or is_only_masks(normalized):
        return None
    return redacted, normalized


def _bounded_context(
    turns: list[ContextTurn],
    *,
    max_turns: int,
    max_tokens: int,
) -> tuple[ContextTurn, ...]:
    """En yeni bağlamı turn ve yaklaşık token tavanlarına sığdır."""
    selected: list[ContextTurn] = []
    remaining_chars = max_tokens * 3
    for turn in reversed(turns[-max_turns:]):
        if remaining_chars <= 0:
            break
        text = turn.redacted_text
        if len(text) > remaining_chars:
            text = text[:remaining_chars].rstrip()
        if text:
            selected.append(ContextTurn(role=turn.role, redacted_text=text))
            remaining_chars -= len(text)
    selected.reverse()
    return tuple(selected)


def _contextual_record_id(normalized_target: str, context: tuple[ContextTurn, ...]) -> str:
    """Target + sıralı canonical context kimliği; session kimliği bilinçli yoktur."""
    parts = [f"target\x00{normalized_target}"]
    parts.extend(f"{turn.role}\x00{normalize(turn.redacted_text)}" for turn in context)
    return _record_id("\x1f".join(parts))


class ContextualPreprocessor:
    """Kullanıcı mesajlarını session geçmişiyle, akışlı ve deterministik işler.

    Girdi satırları kaynak dosyadaki sırada gelmelidir. Session'lar birbirine
    karışabilir; geçmiş ve son sıra her session için ayrı tutulur. Aynı
    session içinde sıra geriye giderse yanlış bağlam üretmek yerine iş durur.
    """

    def __init__(self, settings: Settings, config: ConversationConfig) -> None:
        self._settings = settings
        self._config = config
        self._result = PreprocessResult()
        self._index: dict[str, RecordGroup] = {}
        self._variants: dict[str, Counter[str]] = {}
        self._history: dict[str, list[ContextTurn]] = {}
        self._last_order: dict[str, tuple[int, Decimal | str]] = {}

    @property
    def rows_seen(self) -> int:
        return self._result.total_rows

    def consume(self, rows: Iterable[ConversationRowLike]) -> None:
        result = self._result
        config = self._config
        user_values = set(config.user_role_values)
        assistant_values = set(config.assistant_role_values)
        target_types = set(config.target_message_types)
        context_types = set(config.context_message_types)

        for row in rows:
            result.total_rows += 1
            if not row.included:
                result.discarded_count += 1
                continue

            session_id = row.session_id
            message_order = row.message_order
            role_value = row.role
            message_type = row.message_type
            raw_text = row.text
            source_row = row.source_row
            if role_value is None or message_type is None or raw_text is None:
                result.discarded_count += 1
                continue

            if role_value in user_values:
                canonical_role: Literal["user", "assistant"] | None = "user"
            elif role_value in assistant_values:
                canonical_role = "assistant"
            else:
                canonical_role = None

            is_target = canonical_role == "user" and message_type in target_types
            if canonical_role is None or message_type not in context_types:
                result.discarded_count += 1
                continue
            if session_id is None or message_order is None:
                result.discarded_count += 1
                continue

            try:
                key = _order_key(message_order)
            except ValueError as exc:
                raise ConversationOrderError(source_row) from exc
            previous_key = self._last_order.get(session_id)
            if previous_key is not None and _order_is_before(key, previous_key):
                raise ConversationOrderError(source_row)
            self._last_order[session_id] = key

            history = self._history.setdefault(session_id, [])
            prepared_context = _prepare_context_text(raw_text, self._settings)

            if is_target:
                text = _WHITESPACE.sub(" ", raw_text).strip()
                if (
                    not text
                    or len(text) < self._settings.preprocess_min_message_length
                    or is_system_message(text)
                    or normalize(text) in COURTESY_ONLY
                ):
                    result.discarded_count += 1
                else:
                    redacted = redact_pii(text)
                    was_redacted = redacted != text
                    normalized = normalize(redacted)
                    if not normalized or is_only_masks(normalized):
                        result.discarded_count += 1
                    else:
                        context = _bounded_context(
                            history,
                            max_turns=config.max_context_turns,
                            max_tokens=config.max_context_tokens,
                        )
                        record_id = _contextual_record_id(normalized, context)
                        group = self._index.get(record_id)
                        if group is None:
                            group = RecordGroup(
                                record_id=record_id,
                                normalized=normalized,
                                redacted_text=redacted,
                                context_turns=context,
                                contextual=True,
                            )
                            self._index[record_id] = group
                            self._variants[record_id] = Counter()
                        group.count += 1
                        if was_redacted:
                            group.redacted_count += 1
                            result.redacted_count += 1
                        self._variants[record_id][redacted] += 1
                        result.analyzed_count += 1
            elif prepared_context is not None:
                # Bot veya hedef dışı kullanıcı mesajı bir bağlam adayıdır.
                result.context_only_count += 1
            else:
                result.discarded_count += 1

            if prepared_context is not None and canonical_role is not None:
                history.append(ContextTurn(role=canonical_role, redacted_text=prepared_context[0]))
                # Her session için bellek de kullanıcı ayarıyla sınırlı.
                if len(history) > config.max_context_turns:
                    del history[: len(history) - config.max_context_turns]

    def finish(self) -> PreprocessResult:
        for record_id, group in self._index.items():
            variants = self._variants[record_id].most_common()
            group.redacted_text = variants[0][0]
            group.examples = [
                variant for variant, _ in variants[: self._settings.report_examples_per_question]
            ]
        self._result.groups = sorted(
            self._index.values(), key=lambda group: (-group.count, group.record_id)
        )
        return self._result
