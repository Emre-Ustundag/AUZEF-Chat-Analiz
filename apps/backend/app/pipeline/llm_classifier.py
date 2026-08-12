"""OpenRouter sınıflandırıcısı — Faz 3'ün `RecordClassifier` uygulaması.

`DeterministicClassifier` ile AYNI protokolü sağlar (`pipeline/classifier.py`)
ve aynı `Classification`'ı döndürür: yalnızca kimlik eşlemesi, tek bir sayı
bile yok (ADR §4). Bu yüzden `pipeline/aggregate.py` tek satır değişmeden
çalışmaya devam eder — Faz 2'de ampirik olarak doğrulanan toplama matematiği
LLM devreye girdiğinde de aynıdır.

AKIŞ (ADR §5 Aşama B, madde 5-7):

    kayıtlar → token bütçesine göre chunk → her chunk için MAP çağrısı
             → chunk kategorileri → REDUCE çağrısı → birleşik kategoriler

MAP çağrısı bir chunk'taki kayıtları o chunk'a özel kategorilere ayırır.
Farklı chunk'lar aynı soruyu farklı adlarla etiketleyeceği için REDUCE
aşaması bunları birleştirir. Tek chunk varsa REDUCE ATLANIR: birleştirilecek
bir şey yoktur ve boşuna bir çağrının parası ödenmez.

======================================================================
EN KRİTİK KISIM: KAYIT KAYBI OLMAZ
======================================================================

`aggregate.py`'nin `_validate_assignment`'ı iki şeyi yakalar — aynı kaydın
iki kez eşlenmesi ve bilinmeyen kayıt kimliği. YAKALAMADIĞI bir üçüncü durum
var: hiçbir soruya eşlenmemiş kayıt. O durumda toplama patlamaz, sessizce
eksik sayar ve rapordaki yüzdeler tutmaz.

Bir LLM'in üç hatası da ÇOK OLASI:

* kayıt uydurur (girdide olmayan bir kimlik döndürür),
* aynı kaydı iki kategoriye koyar,
* kayıt atlar (en sinsi olanı — hiçbir kontrole takılmaz).

Bu yüzden üçü de BURADA, toplama görmeden önce ele alınıyor:

* uydurulan kimlik → atılır (girdi kümesinde yoksa yok sayılır),
* tekrar eden kimlik → ilk eşleme tutulur, sonrakiler atılır,
* atlanan kayıt → "Sınıflandırılamayan kayıtlar" kovasına düşer.

Böylece `classify()` çıkışında şu değişmez HER ZAMAN doğrudur:

    eşlenen kayıt kimlikleri kümesi == girdideki kayıt kimlikleri kümesi

Her üç durum da rapora bir `warnings[]` satırı yazar: sessizce düzeltmek,
modelin kalitesizliğini kullanıcıdan saklamak olurdu.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.logging import get_logger
from app.pipeline.classifier import (
    Classification,
    QuestionAssignment,
    ThemeAssignment,
)
from app.pipeline.cost import CHARS_PER_TOKEN, OVERHEAD_TOKENS_PER_RECORD
from app.pipeline.preprocess import RecordGroup
from app.prompts.faq_analysis import PromptBundle
from app.prompts.faq_analysis.v1 import escape_record_text
from app.services.openrouter import OpenRouterClient, Usage

logger = get_logger(__name__)

#: `on_progress(tamamlanan_chunk, toplam_chunk) -> devam_et_mi`.
#: `False` dönerse iş iptal edilmiş demektir ve sınıflandırma durur.
ProgressCallback = Callable[[int, int], bool]

#: Sınıflandırılamayan kayıtların düştüğü kova. `DeterministicClassifier`
#: ile aynı kalıp: kayıp OLMAMALI, toplam adet analiz edilen kayda eşit
#: kalmalı.
FALLBACK_QUESTION = "Sınıflandırılamayan kayıtlar"
FALLBACK_THEME = "Diğer"


class ClassificationCancelledError(Exception):
    """İş, chunk sınırında iptal edilmiş olarak bulundu."""


# --------------------------------------------------------- model yanıt şemaları
#
# DİKKAT: bu modellerin hiçbirinde sayısal alan YOK (ADR §4). Modelin
# "bu kategoride 42 kayıt var" diyebileceği bir alan olsaydı, o sayı er ya
# da geç rapora sızardı. Adetler yalnızca `RecordGroup.count`'tan gelir.


class MapCategory(BaseModel):
    category_id: str = Field(min_length=1)
    canonical_question: str = Field(min_length=1)
    theme: str = Field(min_length=1)


class MapAssignment(BaseModel):
    record_id: str = Field(min_length=1)
    category_id: str = Field(min_length=1)


class MapResponse(BaseModel):
    categories: list[MapCategory]
    assignments: list[MapAssignment]


class ReduceGroup(BaseModel):
    canonical_question: str = Field(min_length=1)
    theme: str = Field(min_length=1)
    member_category_ids: list[str]


class ReduceResponse(BaseModel):
    groups: list[ReduceGroup]


# ------------------------------------------------------------------- chunk'lama


def build_chunks(groups: Sequence[RecordGroup], settings: Settings) -> list[list[RecordGroup]]:
    """Kayıtları token bütçesine ve kayıt sayısına göre partilere böler.

    İKİ SINIR BİRDEN uygulanır, hangisi önce dolarsa parti orada kapanır:

    * `llm_chunk_max_records` — tek bir yanıtta çok fazla kayıt istemek,
      modelin kayıt atlama olasılığını artırır ve tek bir bozuk yanıt çok
      kaydı riske atar.
    * `llm_chunk_max_prompt_tokens` — bağlam penceresi bir ÜST sınırdır ama
      ona kadar doldurmak kaliteyi düşürür. Uzun bir mesaj tek başına
      bütçeyi aşsa bile kendi partisinde gönderilir; kayıt ATLANMAZ.
    """
    max_records = max(1, settings.llm_chunk_max_records)
    max_tokens = max(1, settings.llm_chunk_max_prompt_tokens)

    chunks: list[list[RecordGroup]] = []
    current: list[RecordGroup] = []
    current_tokens = 0

    for group in groups:
        cost = _record_tokens(group)
        would_overflow = current and (
            len(current) >= max_records or current_tokens + cost > max_tokens
        )
        if would_overflow:
            chunks.append(current)
            current = []
            current_tokens = 0

        current.append(group)
        current_tokens += cost

    if current:
        chunks.append(current)
    return chunks


def _record_tokens(group: RecordGroup) -> int:
    """Bir kaydın prompt'ta kaplayacağı tahmini token.

    `pipeline/cost.py`'deki sabitler YENİDEN KULLANILIYOR: maliyet tahmini
    ile chunk'lama aynı varsayıma dayanmalı, yoksa tavan kontrolü ile gerçek
    çağrı boyutu birbirinden ayrışır.
    """
    return int(len(group.redacted_text) / CHARS_PER_TOKEN) + OVERHEAD_TOKENS_PER_RECORD


# ----------------------------------------------------------------- sınıflandırıcı


@dataclass
class _Bucket:
    """Birleştirme öncesi tek bir (chunk, kategori) kovası."""

    key: str
    canonical_question: str
    theme: str
    record_ids: list[str] = field(default_factory=list)


class OpenRouterClassifier:
    """`RecordClassifier` protokolünün LLM uygulaması.

    Senkron: `workers/tasks.py` bunu `asyncio.to_thread` içinde çağırıyor.
    """

    def __init__(
        self,
        *,
        client: OpenRouterClient,
        prompt: PromptBundle,
        model: str,
        settings: Settings,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._client = client
        self._prompt = prompt
        self._model = model
        self._settings = settings
        self._on_progress = on_progress
        self._usage = Usage()
        self._repairs = 0

    @property
    def identifier(self) -> str:
        """Rapordaki `prompt_hash`'in girdisi.

        Model kimliği VE prompt metninin hash'i birlikte: aynı prompt'un
        farklı modellerle koşturulması farklı sonuç üretir, rapora bakan
        biri ikisini ayırt edebilmeli.
        """
        return f"openrouter/{self._model}/{self._prompt.text_hash[:12]}"

    @property
    def usage(self) -> Usage:
        """Sınıflandırmanın token tüketimi.

        `Classification` içine KONMUYOR (ADR §4: protokolün dönüş tiplerinde
        sayısal alan olmamalı). Sağlayıcının faturalama ölçümü ayrı bir
        kanaldan, `classify()` sonrası buradan okunur.
        """
        return self._usage

    def close(self) -> None:
        """Alttaki HTTP bağlantı havuzunu kapatır.

        `workers/tasks.py` bunu her analizden sonra çağırıyor. Olmadan
        UZUN ÖMÜRLÜ Celery worker'ında her analiz bir httpx bağlantı havuzu
        sızdırırdı — tek bir koşuda görünmez, günler içinde worker'ı yer.
        """
        self._client.close()

    @property
    def repair_attempts(self) -> int:
        """Toplam onarım denemesi — sürekli onarım gerektiren prompt pahalıdır."""
        return self._repairs

    # ------------------------------------------------------------------ classify

    def classify(self, groups: Sequence[RecordGroup]) -> Classification:
        if not groups:
            return Classification()

        chunks = build_chunks(groups, self._settings)
        known_ids = {group.record_id for group in groups}

        buckets: dict[str, _Bucket] = {}
        assigned: set[str] = set()
        hallucinated = 0
        duplicated = 0

        for index, chunk in enumerate(chunks):
            chunk_buckets, stats = self._map_chunk(index, chunk, known_ids, assigned)
            buckets.update(chunk_buckets)
            hallucinated += stats[0]
            duplicated += stats[1]

            if self._on_progress is not None and not self._on_progress(index + 1, len(chunks)):
                raise ClassificationCancelledError

        # Chunk'lar ARASI birleştirme. Ölçüt CHUNK sayısıdır, kova sayısı
        # değil: tek bir chunk içindeki kategorileri model zaten kendisi
        # ayırdı, onları tekrar birleştirmeye göndermek hem para harcar hem
        # de modelin kendi kararını ikinci kez sorgulayıp bozmasına yol açar.
        # Birleştirme yalnızca farklı chunk'ların aynı soruyu farklı adlarla
        # etiketlemesi sorununu çözmek için var.
        needs_reduce = len(chunks) > 1 and len(buckets) > 1
        merged = self._reduce(list(buckets.values())) if needs_reduce else _identity(buckets)

        missing = sorted(known_ids - assigned)
        warnings = self._warnings(hallucinated, duplicated, len(missing))

        return self._build_classification(merged, missing, warnings)

    # ---------------------------------------------------------------- map aşaması

    def _map_chunk(
        self,
        index: int,
        chunk: Sequence[RecordGroup],
        known_ids: set[str],
        assigned: set[str],
    ) -> tuple[dict[str, _Bucket], tuple[int, int]]:
        """Bir chunk'ı sınıflandırır; uydurma/tekrar eden kimlikleri eler."""
        rendered = "\n".join(
            f'<kayit id="{group.record_id}">{escape_record_text(group.redacted_text)}</kayit>'
            for group in chunk
        )
        completion = self._client.complete_structured(
            system=self._prompt.map_system,
            user=self._prompt.map_user_template.format(records=rendered),
            schema=self._prompt.map_schema,
            schema_name="faq_map",
            model_type=MapResponse,
        )
        self._usage = self._usage + completion.usage
        self._repairs += completion.repair_attempts

        chunk_ids = {group.record_id for group in chunk}
        catalog = {category.category_id: category for category in completion.data.categories}

        buckets: dict[str, _Bucket] = {}
        hallucinated = 0
        duplicated = 0

        for assignment in completion.data.assignments:
            record_id = assignment.record_id

            # Model, girdide OLMAYAN bir kimlik uydurmuş olabilir. Bu kimlik
            # aggregate.py'de AggregationError'a yol açar; burada elemek işi
            # tek bir halüsinasyon yüzünden başarısız saymaktan iyidir.
            if record_id not in chunk_ids or record_id not in known_ids:
                hallucinated += 1
                continue

            # Aynı kayıt iki kategoriye konmuş olabilir → mesaj iki kez
            # sayılırdı. İlk eşleme tutulur.
            if record_id in assigned:
                duplicated += 1
                continue

            category = catalog.get(assignment.category_id)
            if category is None:
                # Tanımlanmamış kategoriye atıf: kaydı kaybetmiyoruz, sonda
                # "sınıflandırılamayan" kovasına düşecek.
                hallucinated += 1
                continue

            key = f"{index}:{assignment.category_id}"
            bucket = buckets.get(key)
            if bucket is None:
                bucket = _Bucket(
                    key=key,
                    canonical_question=_trim(category.canonical_question),
                    theme=_trim(category.theme),
                )
                buckets[key] = bucket
            bucket.record_ids.append(record_id)
            assigned.add(record_id)

        return buckets, (hallucinated, duplicated)

    # ------------------------------------------------------------- reduce aşaması

    def _reduce(self, buckets: list[_Bucket]) -> list[_Bucket]:
        """Chunk kategorilerini birleştirir.

        Birleştirmede DE kayıt kaybı olmamalı: modelin gruplamada atladığı
        kova, kendi başına bir grup olarak korunur.
        """
        rendered = "\n".join(
            f'<kategori id="{bucket.key}" tema="{escape_record_text(bucket.theme)}">'
            f"{escape_record_text(bucket.canonical_question)}</kategori>"
            for bucket in buckets
        )
        completion = self._client.complete_structured(
            system=self._prompt.reduce_system,
            user=self._prompt.reduce_user_template.format(categories=rendered),
            schema=self._prompt.reduce_schema,
            schema_name="faq_reduce",
            model_type=ReduceResponse,
        )
        self._usage = self._usage + completion.usage
        self._repairs += completion.repair_attempts

        by_key = {bucket.key: bucket for bucket in buckets}
        used: set[str] = set()
        merged: list[_Bucket] = []

        for position, group in enumerate(completion.data.groups):
            members = [
                by_key[key]
                for key in group.member_category_ids
                if key in by_key and key not in used
            ]
            if not members:
                continue
            used.update(member.key for member in members)

            record_ids: list[str] = []
            for member in members:
                record_ids.extend(member.record_ids)
            merged.append(
                _Bucket(
                    key=f"m{position}",
                    canonical_question=_trim(group.canonical_question),
                    theme=_trim(group.theme),
                    record_ids=record_ids,
                )
            )

        # Modelin hiç bahsetmediği kovalar. ATLANAMAZ: içlerindeki kayıtlar
        # yoksa rapordaki yüzdeler tutmaz.
        leftovers = [bucket for bucket in buckets if bucket.key not in used]
        if leftovers:
            logger.info(
                "llm_reduce_leftover_categories",
                extra={"model": self._model, "count": len(leftovers)},
            )
        merged.extend(leftovers)
        return merged

    # ----------------------------------------------------------------- birleştirme

    def _build_classification(
        self,
        buckets: list[_Bucket],
        missing: list[str],
        warnings: list[tuple[str, str]],
    ) -> Classification:
        questions: list[QuestionAssignment] = []
        theme_members: dict[str, list[str]] = {}
        theme_names: dict[str, str] = {}

        ordered = [bucket for bucket in buckets if bucket.record_ids]
        for position, bucket in enumerate(ordered, start=1):
            question_id = f"q{position}"
            theme_id = _theme_id(bucket.theme)
            questions.append(
                QuestionAssignment(
                    question_id=question_id,
                    canonical_question=bucket.canonical_question or FALLBACK_QUESTION,
                    record_ids=tuple(bucket.record_ids),
                    theme_id=theme_id,
                )
            )
            theme_members.setdefault(theme_id, []).append(question_id)
            theme_names.setdefault(theme_id, bucket.theme or FALLBACK_THEME)

        if missing:
            # Modelin atladığı kayıtlar. Rapordan DÜŞMELERİNE izin verilmez:
            # adetler analiz edilen kayıt sayısını tutturmak zorunda.
            question_id = f"q{len(questions) + 1}"
            theme_id = _theme_id(FALLBACK_THEME)
            questions.append(
                QuestionAssignment(
                    question_id=question_id,
                    canonical_question=FALLBACK_QUESTION,
                    record_ids=tuple(missing),
                    theme_id=theme_id,
                )
            )
            theme_members.setdefault(theme_id, []).append(question_id)
            theme_names.setdefault(theme_id, FALLBACK_THEME)

        themes = [
            ThemeAssignment(
                theme_id=theme_id,
                name=theme_names[theme_id],
                question_ids=tuple(question_ids),
            )
            for theme_id, question_ids in theme_members.items()
        ]
        return Classification(questions=questions, themes=themes, warnings=warnings)

    def _warnings(self, hallucinated: int, duplicated: int, missing: int) -> list[tuple[str, str]]:
        """Model hatalarını kullanıcıya görünür kılar.

        Sessizce düzeltmek, modelin kalitesizliğini kullanıcıdan saklamak
        olurdu — raporun ne kadar güvenilir olduğuna o karar vermeli.
        """
        warnings: list[tuple[str, str]] = []
        if hallucinated:
            warnings.append(
                (
                    "LLM_UNKNOWN_RECORD_ID",
                    f"Model {hallucinated} adet geçersiz kayıt kimliği döndürdü; "
                    "bu eşlemeler yok sayıldı.",
                )
            )
        if duplicated:
            warnings.append(
                (
                    "LLM_DUPLICATE_ASSIGNMENT",
                    f"Model {duplicated} kaydı birden fazla kategoriye eşledi; "
                    "yalnızca ilk eşleme kullanıldı.",
                )
            )
        if missing:
            warnings.append(
                (
                    "LLM_UNASSIGNED_RECORDS",
                    f"Model {missing} kaydı hiçbir kategoriye eşlemedi; bu kayıtlar "
                    f"«{FALLBACK_QUESTION}» başlığında toplandı.",
                )
            )
        if warnings:
            logger.warning(
                "llm_classification_anomalies",
                extra={
                    "model": self._model,
                    "hallucinated": hallucinated,
                    "duplicated": duplicated,
                    "missing": missing,
                },
            )
        return warnings


# ---------------------------------------------------------------------- yardımcı


def _identity(buckets: dict[str, _Bucket]) -> list[_Bucket]:
    return list(buckets.values())


def _trim(text: str, limit: int = 120) -> str:
    """Model uzun bir başlık döndürürse kırpar (rapor şeması sınırsız değil)."""
    cleaned = " ".join(text.split())
    if len(cleaned) > limit:
        return cleaned[: limit - 1].rstrip() + "…"
    return cleaned


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_TR_LOWER = str.maketrans({"I": "ı", "İ": "i", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç"})

#: Türkçe harflerin ASCII karşılıkları. AÇIKÇA yazılmak ZORUNDA: `ı` harfi
#: Unicode'da `i`'nin aksanlı bir biçimi DEĞİL, bağımsız bir harftir ve
#: NFKD ayrıştırması onu çözemez. Yalnızca NFKD'ye güvenmek "sınav"ı
#: "snav"a, "kayıt"ı "kayt"a çeviriyordu — kimlikler okunaksızdı.
_TR_ASCII = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c"})


def _theme_id(name: str) -> str:
    """Tema adından kararlı bir kimlik üretir.

    Kimlik frontend'de bağlantı hedefi olarak kullanılıyor; Türkçe harfler
    ASCII'ye indirgeniyor. Aynı tema adı HER ZAMAN aynı kimliği üretmeli,
    yoksa aynı tema iki kez görünürdü.
    """
    lowered = name.translate(_TR_LOWER).lower().translate(_TR_ASCII)
    ascii_form = unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_form).strip("-")
    return f"t-{slug or 'diger'}"
