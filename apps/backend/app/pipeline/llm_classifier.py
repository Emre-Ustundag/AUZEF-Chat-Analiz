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
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings
from app.core.logging import get_logger
from app.pipeline.classifier import (
    Classification,
    QuestionAssignment,
    ThemeAssignment,
)
from app.pipeline.cost import build_chunks, cost_for_usage
from app.pipeline.preprocess import RecordGroup
from app.pipeline.record_rendering import render_record
from app.prompts.faq_analysis import PromptBundle
from app.prompts.faq_analysis.v1 import escape_record_text
from app.schemas.analysis import PricingSnapshot
from app.services.map_cache import MapCache, build_key
from app.services.openrouter import Completion, OpenRouterClient, Usage

logger = get_logger(__name__)

#: `on_progress(tamamlanan_chunk, toplam_chunk) -> devam_et_mi`.
#: `False` dönerse iş iptal edilmiş demektir ve sınıflandırma durur.
ProgressCallback = Callable[[int, int], bool]


class CostLimitExceededError(Exception):
    """Koşu SIRASINDA maliyet tavanı aşıldı.

    `estimate_cost` uçuş öncesi bir TAHMİN üretiyor ve tahmin gerçekten
    sapabiliyor: onarım gerektiren bir chunk biriken mesaj geçmişini yeniden
    gönderiyor (ölçüldü: tek onarımda 1.23x). Tavan yalnızca uçuş öncesi
    bakıldığı sürece bu fark doğrudan kullanıcının KENDİ anahtarından çıkar.

    `spent_usd` o ana kadar sağlayıcının `usage` bloğuna göre GERÇEKTEN
    harcanan tutardır — tahmin değil.
    """

    def __init__(self, spent_usd: float, max_cost_usd: float) -> None:
        super().__init__(f"{spent_usd} > {max_cost_usd}")
        self.spent_usd = spent_usd
        self.max_cost_usd = max_cost_usd


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
        max_cost_usd: float | None = None,
        pricing_snapshot: PricingSnapshot | None = None,
        map_cache: MapCache | None = None,
    ) -> None:
        self._client = client
        self._prompt = prompt
        self._model = model
        self._settings = settings
        self._on_progress = on_progress
        #: Koşu sırasında uygulanan tavan. `None` ise yalnızca uçuş öncesi
        #: tahmin kontrolü geçerlidir (Faz 2 vekil sınıflandırıcı bunu
        #: kullanmıyor).
        self._max_cost_usd = max_cost_usd
        self._pricing_snapshot = pricing_snapshot
        #: Tamamlanmış map çağrılarının önbelleği (bulgu A3). VARSAYILAN
        #: `None` — önbellek yalnızca ENJEKTE EDİLDİĞİNDE devreye girer.
        #: Sebebi test izolasyonu: sınıflandırıcıyı kuran her test, sahte
        #: sağlayıcıya kaç çağrı gittiğini sayıyor; kendiliğinden açılan
        #: paylaşımlı bir önbellek o sayıları testler arasında sızdırırdı.
        #: Üretimde `workers/tasks.py::_build_classifier` enjekte ediyor.
        self._map_cache = map_cache
        self._usage = Usage()
        self._repairs = 0
        self._reduce_bucket_serial = 0
        self._reduce_partial_coverage = False

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

        def consume_chunk(
            index: int,
            chunk: Sequence[RecordGroup],
            completion: Completion[MapResponse],
        ) -> None:
            nonlocal hallucinated, duplicated
            chunk_buckets, stats = self._merge_map_chunk(
                index, chunk, completion, known_ids, assigned
            )
            buckets.update(chunk_buckets)
            hallucinated += stats[0]
            duplicated += stats[1]

            # ADR §9 tavanı, harcandıkça. Uçuş öncesi kontrol bir TAHMİNE
            # dayanıyor; burası sağlayıcının kendi `usage` bloğuna bakıyor.
            #
            # Kontrol chunk'tan SONRA: harcanmış parayı geri alamayız,
            # yapabileceğimiz tek şey KALAN chunk'ları hiç göndermemek.
            self._guard_cost()

            if self._on_progress is not None and not self._on_progress(index + 1, len(chunks)):
                raise ClassificationCancelledError

        self._run_in_order(chunks, self._map_chunk_call, consume_chunk)

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

    def _run_in_order[TItem, TResult](
        self,
        items: Sequence[TItem],
        call: Callable[[TItem], TResult],
        consume: Callable[[int, TItem, TResult], None],
    ) -> None:
        """`call`'ı sınırlı eşzamanlılıkla koşturur, `consume`'u SIRAYLA çağırır.

        NEDEN VAR: `classify` chunk'ları düz bir döngüde sırayla gönderiyordu.
        Ölçülen chunk süresi ~26 sn ve gerçek AUZEF dökümü bağlamsal modda 492
        chunk üretiyor — ~3,5 saat, 45 dakikalık hard timeout'un beş katı. Chunk
        büyütmek çözmez: toplam completion token sabit kaldığı için duvar saati
        de sabit kalır.

        DETERMİNİZM: `call` yalnızca ağ çağrısıdır ve `self`'e yazmaz; sıraya
        duyarlı her şey (`_usage`, `assigned` kümesi, kova anahtarları) tek tek
        `consume` içinde, ANA iş parçacığında ve indeks sırasına göre işlenir.
        Bu yüzden çıktı, eşzamanlılık kaç olursa olsun sıralı koşuyla aynıdır.

        `consume` istisna atarsa (maliyet tavanı ya da iptal) YENİ İŞ
        GÖNDERİLMEZ; kuyrukta bekleyenler iptal edilir, uçuşta olanlar havuz
        kapanırken tamamlanır. Uçuştaki çağrıların parası harcanmış olur —
        eşzamanlılığın bilinçli bedeli (bkz. `Settings.llm_max_concurrency`).
        """
        if not items:
            return

        concurrency = max(1, min(self._settings.llm_max_concurrency, len(items)))
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="auzef-llm") as pool:
            pending: dict[int, Future[TResult]] = {}
            next_to_submit = 0
            try:
                for index in range(len(items)):
                    while next_to_submit < len(items) and len(pending) < concurrency:
                        pending[next_to_submit] = pool.submit(call, items[next_to_submit])
                        next_to_submit += 1
                    consume(index, items[index], pending.pop(index).result())
            except BaseException:
                for future in pending.values():
                    future.cancel()
                raise

    def _map_chunk_call(self, chunk: Sequence[RecordGroup]) -> Completion[MapResponse]:
        """Önbellek/sağlayıcı çağrısı — havuzdaki iş parçacığından çalışır.

        Burada sıralamaya duyarlı sınıflandırıcı durumu üzerinde HİÇBİR yazma
        yok. `_usage`, `_repairs` ve kova bookkeeping'i `_merge_map_chunk`'ta,
        ana iş parçacığında ve chunk SIRASINA göre işlenir. Önbellek isabeti
        sıfır kullanımlı bir `Completion` döndürür; böylece rapor yalnızca bu
        koşuda sağlayıcıya gerçekten ödenen tüketimi içerir.
        """
        rendered = "\n".join(render_record(group) for group in chunk)
        cache = self._map_cache
        key: str | None = None

        if cache is not None:
            key = build_key(
                model=self._model,
                prompt_text_hash=self._prompt.text_hash,
                map_schema=self._prompt.map_schema,
                # Anahtar sağlayıcıya giden metinden türer; kayıt sırası veya
                # kaçış biçimi değişirse eski yanıt yanlışlıkla kullanılmaz.
                rendered_records=rendered,
            )
            raw = cache.load(key)
            if raw is not None:
                try:
                    cached = MapResponse.model_validate_json(raw)
                except ValidationError:
                    # Eski/bozuk kayıt ıska sayılır ve aşağıdaki normal çağrı
                    # yolunda geçerli yanıtla üzerine yazılır.
                    logger.warning("map_cache_invalid_payload")
                else:
                    logger.info("map_cache_hit")
                    return Completion(data=cached, usage=Usage())

        completion = self._client.complete_structured(
            system=self._prompt.map_system,
            user=self._prompt.map_user_template.format(records=rendered),
            schema=self._prompt.map_schema,
            schema_name="faq_map",
            model_type=MapResponse,
        )

        if cache is not None and key is not None:
            cache.store(key, completion.data.model_dump_json())

        return completion

    def _merge_map_chunk(
        self,
        index: int,
        chunk: Sequence[RecordGroup],
        completion: Completion[MapResponse],
        known_ids: set[str],
        assigned: set[str],
    ) -> tuple[dict[str, _Bucket], tuple[int, int]]:
        """Bir chunk yanıtını işler; uydurma/tekrar eden kimlikleri eler."""
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

    def _guard_cost(self) -> None:
        """Gerçek tüketim tavanı aştıysa koşuyu keser.

        OpenRouter `usage.cost` döndürüyorsa gerçek borçlandırma tutarı
        esastır. Eski/eksik bir sağlayıcı yanıtında tokenlar, job
        oluşturulurken sabitlenen fiyat snapshot'ıyla hesaplanır.
        """
        if self._max_cost_usd is None:
            return
        spent = self._usage.cost_usd
        if spent is None:
            spent = cost_for_usage(
                prompt_tokens=self._usage.prompt_tokens,
                completion_tokens=self._usage.completion_tokens,
                cached_tokens=self._usage.cached_tokens,
                cache_write_tokens=self._usage.cache_write_tokens,
                model_id=self._model,
                pricing_snapshot=self._pricing_snapshot,
            )
        if spent > self._max_cost_usd:
            logger.warning(
                "llm_cost_limit_reached",
                extra={
                    "model": self._model,
                    "spent_usd": spent,
                    "max_cost_usd": self._max_cost_usd,
                },
            )
            raise CostLimitExceededError(spent, self._max_cost_usd)

    def _reduce(self, buckets: list[_Bucket]) -> list[_Bucket]:
        """Kategorileri token-bütçeli hiyerarşik turlarda birleştirir.

        Tek dev reduce çağrısı, modelin prompt'ta binlerce kategoriyi
        kapsayamamasına ve bahsedilmeyen kovaların nihai sonuca doğrudan
        ``leftover`` olarak eklenmesine yol açıyordu. Burada her turun tüm
        çıktısı (leftover'lar dahil) bir sonraki tura taşınır. Böylece veri
        kaybolmaz ve bir turda görülemeyen kova, sonraki turda yeniden
        birleştirme şansı bulur.

        Bir tur hiç küçülme üretmezse tekrar denemek sonsuz döngü olur. O
        durumda eldeki kayıpsız sonuç döner ve gözlemlenebilir bir kalite
        uyarısı bırakır.
        """
        current = buckets
        level = 0

        while len(current) > 1:
            batches = self._reduce_batches(current)

            # Tüm eldeki kategoriler artık tek çağrıya sığıyorsa bu nihai
            # turdur. Çıktıyı yeniden aynı prompt'a vermek fayda sağlamaz;
            # modelin farklı adlar üretmesiyle gereksiz ek çağrı ve maliyet
            # yaratır.
            if len(batches) == 1:
                final = self._merge_reduce_batch(
                    batches[0], self._reduce_batch_call(batches[0]), level, 0
                )
                self._guard_cost()
                if self._on_progress is not None and not self._on_progress(1, 1):
                    raise ClassificationCancelledError
                logger.info(
                    "llm_reduce_level_completed",
                    extra={
                        "model": self._model,
                        "level": level,
                        "input_categories": len(current),
                        "batches": 1,
                        "output_categories": len(final),
                    },
                )
                return final

            next_level = self._reduce_level(batches, level)

            logger.info(
                "llm_reduce_level_completed",
                extra={
                    "model": self._model,
                    "level": level,
                    "input_categories": len(current),
                    "batches": len(batches),
                    "output_categories": len(next_level),
                },
            )

            if len(next_level) >= len(current):
                # Tek batch'e sığan ama gerçekten farklı kategoriler bu yola
                # doğal olarak düşebilir; sonuç doğru ve kayıpsızdır. Ancak
                # birden fazla batch varken artık üst turda ortak tema
                # arayamayacağımız için raporda görünür kılmak gerekir.
                if len(batches) > 1:
                    self._reduce_partial_coverage = True
                    logger.warning(
                        "llm_reduce_no_progress",
                        extra={
                            "model": self._model,
                            "level": level,
                            "categories": len(current),
                            "batches": len(batches),
                        },
                    )
                return next_level

            current = next_level
            level += 1

        return current

    def _reduce_level(self, batches: list[list[_Bucket]], level: int) -> list[_Bucket]:
        """Bir hiyerarşi turunun tüm batch'lerini koşturur, çıktıyı SIRAYLA toplar.

        Ayrı bir metot çünkü batch'ler sınırlı eşzamanlılıkla gönderiliyor ve
        kapanışların tur döngüsünün değişkenlerine bağlanması (ruff B023) hem
        lint hatası hem de gerçek bir tuzak olurdu.

        Bir turun batch'leri birbirinden bağımsız; TURLAR sıralı kalır, çünkü
        her tur bir öncekinin çıktısını girdi alır. Sıralı bırakılsaydı gerçek
        veride reduce tek başına ~18 dakika ekliyor ve map hızlansa bile toplam
        süre limite dayanıyordu.
        """
        next_level: list[_Bucket] = []

        def call_batch(batch: list[_Bucket]) -> Completion[ReduceResponse] | None:
            # Tek, olağandışı uzun bir kategori token bütçesini tek başına
            # aşabilir. Kayıt kaybetmemek için çağrı yapmadan taşınır; diğer
            # partiler yine birleştirilmeye devam eder.
            if len(batch) == 1:
                return None
            return self._reduce_batch_call(batch)

        def consume_batch(
            batch_index: int,
            batch: list[_Bucket],
            completion: Completion[ReduceResponse] | None,
        ) -> None:
            if completion is None:
                next_level.extend(batch)
                return
            next_level.extend(self._merge_reduce_batch(batch, completion, level, batch_index))
            self._guard_cost()
            if self._on_progress is not None and not self._on_progress(1, 1):
                raise ClassificationCancelledError

        self._run_in_order(batches, call_batch, consume_batch)
        return next_level

    def _reduce_batches(self, buckets: list[_Bucket]) -> list[list[_Bucket]]:
        """Kategori metinlerini tek reduce çağrısının token bütçesine böler."""
        max_tokens = max(1, self._settings.llm_reduce_max_prompt_tokens)
        batches: list[list[_Bucket]] = []
        current: list[_Bucket] = []
        current_tokens = 0

        for bucket in buckets:
            tokens = _reduce_bucket_tokens(bucket)
            if current and current_tokens + tokens > max_tokens:
                batches.append(current)
                current = []
                current_tokens = 0
            current.append(bucket)
            current_tokens += tokens

        if current:
            batches.append(current)
        return batches

    def _reduce_batch_call(self, buckets: list[_Bucket]) -> Completion[ReduceResponse]:
        """SAĞLAYICI ÇAĞRISI — havuzdaki iş parçacığından çalışır.

        `_map_chunk_call` ile aynı kural: `self`'e yazma yok. Kova anahtarı
        üreten sayaç (`_next_reduce_bucket_key`) ve `_usage`, birleştirmede
        batch sırasına göre ilerler; aksi hâlde aynı girdi her koşuda farklı
        anahtarlar üretir ve rapor deterministik olmaktan çıkardı.

        Prompt'a yalnızca konumsal kimlik basılır. Model üretimi olan
        ``bucket.key`` hiç prompt'a girmez; böylece delimiter kaçışı veya
        batch'ler arası anahtar çakışması eşleştirmeyi bozamaz.
        """
        by_position = {f"c{index}": bucket for index, bucket in enumerate(buckets)}
        rendered = "\n".join(
            f'<kategori id="{position_id}" tema="{escape_record_text(bucket.theme)}">'
            f"{escape_record_text(bucket.canonical_question)}</kategori>"
            for position_id, bucket in by_position.items()
        )
        return self._client.complete_structured(
            system=self._prompt.reduce_system,
            user=self._prompt.reduce_user_template.format(categories=rendered),
            schema=self._prompt.reduce_schema,
            schema_name="faq_reduce",
            model_type=ReduceResponse,
        )

    def _merge_reduce_batch(
        self,
        buckets: list[_Bucket],
        completion: Completion[ReduceResponse],
        level: int,
        batch_index: int,
    ) -> list[_Bucket]:
        """Bir reduce yanıtını kovalara çevirir; bahsedilmeyenleri taşır."""
        # Konum → kova. Sıra `buckets` listesinin sırası; model yalnızca bu
        # kimlikleri görüyor ve yalnızca bunları geri yansıtabiliyor.
        by_position = {f"c{index}": bucket for index, bucket in enumerate(buckets)}
        self._usage = self._usage + completion.usage
        self._repairs += completion.repair_attempts

        used_positions: set[str] = set()
        merged: list[_Bucket] = []

        for group in completion.data.groups:
            members = [
                by_position[position_id]
                for position_id in group.member_category_ids
                if position_id in by_position and position_id not in used_positions
            ]
            if not members:
                continue
            used_positions.update(group.member_category_ids)

            record_ids: list[str] = []
            for member in members:
                record_ids.extend(member.record_ids)
            merged.append(
                _Bucket(
                    key=self._next_reduce_bucket_key(),
                    canonical_question=_trim(group.canonical_question),
                    theme=_trim(group.theme),
                    record_ids=record_ids,
                )
            )

        # Modelin hiç bahsetmediği kovalar. ATLANAMAZ: içlerindeki kayıtlar
        # yoksa rapordaki yüzdeler tutmaz.
        leftovers = [
            bucket
            for position_id, bucket in by_position.items()
            if position_id not in used_positions
        ]
        if leftovers:
            logger.info(
                "llm_reduce_leftover_categories",
                extra={
                    "model": self._model,
                    "level": level,
                    "batch": batch_index,
                    "count": len(leftovers),
                },
            )
        merged.extend(leftovers)
        return merged

    def _next_reduce_bucket_key(self) -> str:
        key = f"reduce:{self._reduce_bucket_serial}"
        self._reduce_bucket_serial += 1
        return key

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
        if self._reduce_partial_coverage:
            warnings.append(
                (
                    "LLM_REDUCE_PARTIAL_COVERAGE",
                    "Kategori birleştirme ölçek sınırında tüm partileri ortak bir turda "
                    "birleştiremedi; kayıtlar korunarak ayrı kategoriler bırakıldı.",
                )
            )

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


def _reduce_bucket_tokens(bucket: _Bucket) -> int:
    """Bir reduce kategori bloğunun yaklaşık prompt token maliyeti.

    Modelin gördüğü metin tema + kanonik sorudur; konumsal kimlik ve XML
    ayraçları da sabit bir yük taşır. Aynı 3 karakter/token varsayımı map
    chunk hesabıyla tutarlıdır; yalnızca parti seçimi içindir, faturalama
    ölçümü değildir.
    """
    return max(1, int((len(bucket.theme) + len(bucket.canonical_question)) / 3.0) + 12)


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
