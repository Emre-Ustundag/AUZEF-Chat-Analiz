"""Sınıflandırıcı arayüzü ve Faz 2'nin deterministik vekili.

BU MODÜLÜN VARLIK SEBEBİ — ADR §4, planın en kritik kararı:

    "LLM doğrudan toplam sayı üretmez. Her temizlenmiş mesaj veya benzersiz
    mesaj kimliğini bir kanonik SSS ya da tema kimliğine eşler. Nihai adet ve
    oranları backend, mesajların gerçek frekanslarından deterministik olarak
    hesaplar."

Bu ayrımın kodda GÖRÜNÜR olması gerekiyor. `RecordClassifier` protokolü
`classify()` ile YALNIZCA eşleme döndürür: hangi kayıt kimliği hangi soruya,
hangi soru hangi temaya. Protokolün döndürdüğü hiçbir yapıda `count`,
`percentage` veya benzeri bir sayı YOKTUR — olsaydı Faz 3'te LLM'in uydurduğu
bir sayı sessizce rapora sızabilirdi. Sayıları `pipeline/aggregate.py` üretir
ve girdisi yalnızca `RecordGroup.count` (gerçek frekans) + bu eşlemedir.

Faz 2 uygulaması `DeterministicClassifier`: normalize metinden anahtar
sözcük imzası çıkarıp aynı imzalı kayıtları tek bir kanonik soruda toplar.
Bir LLM'in yaptığı anlamsal gruplamayı YAPMAZ, taklit eder — ama arayüzü
Faz 3'ün OpenRouter uygulamasıyla birebir aynıdır, dolayısıyla toplama
matematiği LLM devreye girdiğinde tek satır değişmeden çalışmaya devam eder.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.pipeline.preprocess import RecordGroup

#: İmzadan atılan Türkçe dolgu sözcükleri. Amaç, aynı soruyu farklı
#: kelimelerle soran kayıtları aynı kovaya düşürmek: "sınav ne zaman" ile
#: "acaba sınav tarihi ne zaman olacak" aynı imzayı üretmeli.
STOPWORDS = frozenset(
    [
        "acaba",
        "ama",
        "ancak",
        "bana",
        "bazı",
        "belki",
        "ben",
        "beni",
        "benim",
        "bir",
        "biraz",
        "birşey",
        "biz",
        "bu",
        "bunu",
        "bunun",
        "çok",
        "çünkü",
        "da",
        "daha",
        "de",
        "defa",
        "değil",
        "diye",
        "diğer",
        "eğer",
        "en",
        "gibi",
        "hem",
        "hep",
        "hepsi",
        "her",
        "hiç",
        "için",
        "ile",
        "ise",
        "kez",
        "ki",
        "kim",
        "mi",
        "mı",
        "mu",
        "mü",
        "nasıl",
        "ne",
        "neden",
        "nerde",
        "nerede",
        "nereden",
        "niye",
        "o",
        "olarak",
        "olduğu",
        "olur",
        "olsun",
        "onu",
        "şey",
        "siz",
        "sonra",
        "şu",
        "tüm",
        "ve",
        "veya",
        "ya",
        "yani",
        "zaten",
        "mısınız",
        "misiniz",
        "musunuz",
        "lütfen",
        "merhaba",
        "selam",
        "teşekkür",
        "ederim",
        "rica",
        "acil",
        "bilgi",
        "alabilir",
        "miyim",
        "var",
        "yok",
        "mıyım",
        "miyim",
        "edeyim",
        "mısın",
        "misin",
        "bi",
    ]
)

#: İmzada tutulacak azami sözcük sayısı. Uzun bir mesajın tamamı imzaya
#: girerse hiçbir iki kayıt eşleşmez ve gruplama tekilleştirmeye dönüşür.
SIGNATURE_TOKEN_LIMIT = 3

#: İmzaya alınacak asgari sözcük uzunluğu.
_MIN_TOKEN_LENGTH = 3

_TOKEN = re.compile(r"\w+", re.UNICODE)

#: Faz 2 vekilinin kimliği. `prompt_hash` bundan türetilir: rapora bakan biri
#: sonucun bir LLM'den mi vekilden mi geldiğini ayırt edebilmeli.
CLASSIFIER_ID = "deterministic-proxy/v1"


@dataclass(frozen=True)
class QuestionAssignment:
    """Bir kanonik soru ve ona eşlenen kayıt kimlikleri.

    DİKKAT: burada adet YOK. Adet, `record_ids`'e karşılık gelen
    `RecordGroup.count` değerlerinin toplamıdır ve `aggregate.py` hesaplar.
    """

    question_id: str
    canonical_question: str
    #: Bu soruya eşlenen benzersiz kayıt kimlikleri.
    record_ids: tuple[str, ...]
    #: Bu soruyu içeren tema kimliği.
    theme_id: str


@dataclass(frozen=True)
class ThemeAssignment:
    """Bir tema ve ona bağlı soru kimlikleri. Yine adet YOK."""

    theme_id: str
    name: str
    question_ids: tuple[str, ...]


@dataclass
class Classification:
    """Sınıflandırıcının tüm çıktısı — yalnızca eşlemeler."""

    questions: list[QuestionAssignment] = field(default_factory=list)
    themes: list[ThemeAssignment] = field(default_factory=list)
    #: İşi durdurmayan uyarılar (rapordaki `warnings[]`).
    warnings: list[tuple[str, str]] = field(default_factory=list)


class RecordClassifier(Protocol):
    """Faz 3'te OpenRouter uygulaması bu protokolü sağlayacak.

    Sözleşme:

    * Girdi: ön işlenmiş, PII'ı MASKELENMİŞ benzersiz kayıt grupları.
    * Çıktı: yalnızca kimlik eşlemesi. Hiçbir adet/oran döndürülmez.
    * Her kayıt kimliği en fazla BİR soruya eşlenmeli; aksi hâlde toplama
      aynı mesajı iki kez sayardı. `aggregate.py` bunu doğrular.
    """

    @property
    def identifier(self) -> str:
        """İzlenebilirlik için sınıflandırıcı kimliği (`prompt_hash` girdisi)."""
        ...

    def classify(self, groups: Sequence[RecordGroup]) -> Classification:
        """Kayıt gruplarını kanonik soru ve temalara eşler."""
        ...


def _signature_tokens(normalized: str) -> tuple[str, ...]:
    """Normalize metinden anahtar sözcük imzası üretir.

    Sözcükler ALFABETİK sıraya sokulur: "sınav ne zaman" ile "ne zaman sınav"
    aynı imzayı üretsin. Sıra korunsaydı aynı soru iki kategori olurdu.
    """
    tokens = [
        token
        for token in _TOKEN.findall(normalized)
        if len(token) >= _MIN_TOKEN_LENGTH and token not in STOPWORDS
    ]
    if not tokens:
        return ()
    # Frekans değil, ilk görülme sırası önemli: mesajın baştaki sözcükleri
    # konuyu daha iyi taşıyor. İlk N tanesi alınıp sıralanır.
    return tuple(sorted(dict.fromkeys(tokens))[:SIGNATURE_TOKEN_LIMIT])


class DeterministicClassifier:
    """Faz 2'nin vekil sınıflandırıcısı — LLM YOK, ağ çağrısı YOK.

    Algoritma:

    1. Her kayıt grubunun normalize metninden anahtar sözcük imzası çıkarılır.
    2. Aynı imzalı gruplar tek bir kanonik soruda birleşir.
    3. Kanonik soru metni, grup içindeki EN SIK kaydın redakte edilmiş
       hâlidir — uydurulmuş bir cümle değil, gerçekten görülmüş bir mesaj.
    4. Tema, imzanın ilk sözcüğüdür: aynı ana sözcüğü paylaşan sorular
       ("sınav tarih", "sınav yer") tek temada toplanır.

    Bunun bir LLM kadar iyi olmadığı AÇIKTIR ve olması da gerekmiyor. İşlevi,
    toplama matematiğini LLM belirsizliği olmadan test edilebilir kılmak.
    """

    @property
    def identifier(self) -> str:
        return CLASSIFIER_ID

    def classify(self, groups: Sequence[RecordGroup]) -> Classification:
        buckets: dict[tuple[str, ...], list[RecordGroup]] = defaultdict(list)
        unmatched: list[RecordGroup] = []

        for group in groups:
            signature = _signature_tokens(group.normalized)
            if signature:
                buckets[signature].append(group)
            else:
                # İmza çıkmayan kayıt (yalnızca dolgu sözcükler). Kayıp
                # OLMAMALI: toplam adet analiz edilen kayda eşit kalmalı.
                unmatched.append(group)

        questions: list[QuestionAssignment] = []
        theme_members: dict[str, list[str]] = defaultdict(list)
        theme_names: dict[str, str] = {}

        # Sıralama deterministik: önce toplam frekans, eşitlikte imza.
        ordered = sorted(
            buckets.items(),
            key=lambda item: (-sum(g.count for g in item[1]), item[0]),
        )

        for position, (signature, members) in enumerate(ordered, start=1):
            question_id = f"q{position}"
            dominant = max(members, key=lambda g: (g.count, g.record_id))
            theme_id = _theme_id(signature[0])

            questions.append(
                QuestionAssignment(
                    question_id=question_id,
                    canonical_question=_as_question(dominant.redacted_text),
                    record_ids=tuple(g.record_id for g in members),
                    theme_id=theme_id,
                )
            )
            theme_members[theme_id].append(question_id)
            theme_names.setdefault(theme_id, signature[0].capitalize())

        if unmatched:
            question_id = f"q{len(questions) + 1}"
            theme_id = _theme_id("diger")
            questions.append(
                QuestionAssignment(
                    question_id=question_id,
                    canonical_question="Sınıflandırılamayan kısa mesajlar",
                    record_ids=tuple(g.record_id for g in unmatched),
                    theme_id=theme_id,
                )
            )
            theme_members[theme_id].append(question_id)
            theme_names.setdefault(theme_id, "Diğer")

        themes = [
            ThemeAssignment(
                theme_id=theme_id,
                name=theme_names[theme_id],
                question_ids=tuple(question_ids),
            )
            for theme_id, question_ids in theme_members.items()
        ]

        return Classification(questions=questions, themes=themes)


def _theme_id(token: str) -> str:
    return f"t-{token}"


def _as_question(text: str) -> str:
    """Temsilci metni okunabilir bir başlık hâline getirir.

    Metin UYDURULMAZ: yalnızca kırpılır ve baş harfi büyütülür. Uydurma bir
    cümle, ADR §4'ün "model sayı/metin üretmez" ilkesinin ruhuna aykırı olurdu
    — rapordaki her şey gerçekten görülmüş veriden gelmelidir.
    """
    cleaned = text.strip()
    if not cleaned:
        return "Boş mesaj"
    if len(cleaned) > 120:
        cleaned = cleaned[:119].rstrip() + "…"
    return cleaned[0].upper() + cleaned[1:]
