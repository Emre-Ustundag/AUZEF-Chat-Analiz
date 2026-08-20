"""Ön işleme, sınıflandırma ve TOPLAMA testleri — plan §4 ölçüt 4.

Bu dosya Faz 2'nin asıl değeridir. ADR §4'ün kararı ("sayıları backend
deterministik hesaplar") ancak burada kanıtlanabilir: altyapı yok, LLM yok,
yalnızca girdi → sayı ilişkisi.

Doğrulanan değişmezler:

* Oranlar adetlerden türetilir — hiçbir yüzde bağımsız bir kaynaktan gelmez.
* Tema toplamı analiz edilen kaydı AŞAMAZ.
* `top_n` kırpması tema `count`'unu DEĞİŞTİRMEZ, yalnızca
  `related_question_ids`'i filtreler (plan §1.2).
* Frekanslar tekilleştirmede korunur.
* PII sınıflandırıcıya gitmeden önce maskelenir.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from app.core.config import Settings
from app.pipeline.aggregate import AggregationError, aggregate
from app.pipeline.classifier import (
    Classification,
    DeterministicClassifier,
    QuestionAssignment,
    ThemeAssignment,
    _signature_tokens,
)
from app.pipeline.cost import (
    CostDecision,
    cost_for_tokens,
    estimate_cost,
    estimate_profile_cost,
    estimate_profile_cost_range,
    record_tokens,
)
from app.pipeline.preprocess import (
    ContextTurn,
    ContextualPreprocessor,
    ConversationOrderError,
    PreprocessResult,
    RecordGroup,
    normalize,
    preprocess,
)
from app.prompts.faq_analysis import V1, V2
from app.schemas.analysis import ConversationConfig
from app.schemas.report import AnalysisReport, percentage_half_up
from app.services.xlsx import ConversationRow


@pytest.fixture
def settings() -> Settings:
    return Settings()


# ------------------------------------------------------------- normalizasyon


def test_normalize_turkce_buyuk_i_yi_dogru_kucultur() -> None:
    """`str.lower()` "I" → "i" yapar; Türkçe'de doğrusu "ı"dır.

    Yanlış olsaydı "SINAV" ile "sınav" farklı kovalara düşer ve
    tekilleştirme sessizce eksik çalışırdı.
    """
    assert normalize("SINAV NE ZAMAN") == normalize("sınav ne zaman")
    assert normalize("İSTANBUL") == "istanbul"


def test_normalize_noktalama_ve_bosluk_sadelestirir() -> None:
    assert normalize("Sınav   ne zaman???") == "sınav ne zaman"


# ---------------------------------------------------------------- ön işleme


def test_frekanslar_tekillestirmede_korunur(settings: Settings) -> None:
    """ADR §5 B madde 4: gerçek frekans korunur."""
    values = ["sınav ne zaman"] * 5 + ["harç nasıl ödenir"] * 2
    result = preprocess(values, settings)

    assert result.total_rows == 7
    assert result.analyzed_count == 7
    assert result.unique_count == 2
    assert result.duplicate_count == 5
    assert sum(group.count for group in result.groups) == result.analyzed_count
    assert result.groups[0].count == 5


def test_bos_ve_sistem_kayitlari_elenir(settings: Settings) -> None:
    values = [
        "sınav ne zaman",
        None,
        "   ",
        "[sistem] oturum açıldı",
        "teşekkürler",
        "ok",
    ]
    result = preprocess(values, settings)

    assert result.total_rows == 6
    assert result.analyzed_count == 1
    assert result.discarded_count == 5
    # Değişmez: hiçbir kayıt kaybolmaz.
    assert result.analyzed_count + result.discarded_count == result.total_rows


def test_pii_siniflandiriciya_gitmeden_maskelenir(settings: Settings) -> None:
    """ADR §9 / plan §5.3: PII, sınıflandırıcıdan ÖNCE maskelenir."""
    values = [
        "numaram 05551234567 sınav bilgisi lazım",
        "ali@example.com adresine sınav bilgisi gönderin",
    ]
    result = preprocess(values, settings)

    joined = " ".join(group.redacted_text for group in result.groups)
    normalized = " ".join(group.normalized for group in result.groups)

    assert "05551234567" not in joined
    assert "ali@example.com" not in joined
    # Sınıflandırıcı YALNIZCA `normalized` görüyor; orada da PII olmamalı.
    assert "05551234567" not in normalized
    assert "example" not in normalized
    assert result.redacted_count == 2


def test_yalniz_pii_iceren_kayit_elenir(settings: Settings) -> None:
    """Maskelendikten sonra geriye soru kalmıyorsa kayıt analize girmez."""
    result = preprocess(["05551234567"], settings)
    assert result.analyzed_count == 0
    assert result.discarded_count == 1


def test_ayni_girdi_ayni_ciktiyi_uretir(settings: Settings) -> None:
    """Determinizm: aynı girdi her çalıştırmada aynı gruplamayı vermeli."""
    values = ["sınav ne zaman", "harç ödeme", "sınav ne zaman", "kayıt yenileme"]
    first = preprocess(values, settings)
    second = preprocess(values, settings)

    assert [g.record_id for g in first.groups] == [g.record_id for g in second.groups]
    assert [g.count for g in first.groups] == [g.count for g in second.groups]


def _conversation_config(**overrides: object) -> ConversationConfig:
    return ConversationConfig.model_validate(
        {
            "session_id_column": "session_id",
            "message_order_column": "message_order",
            "role_column": "direction",
            "message_type_column": "message_type",
            **overrides,
        }
    )


def _conversation_row(
    row: int,
    session: str,
    order: int,
    role: str,
    text: str,
    message_type: str = "text",
) -> ConversationRow:
    return ConversationRow(
        source_row=row,
        included=True,
        session_id=session,
        message_order=str(order),
        role=role,
        message_type=message_type,
        text=text,
    )


def test_contextual_ayni_hedef_farkli_baglamda_ayri_kalir(settings: Settings) -> None:
    processor = ContextualPreprocessor(settings, _conversation_config())
    processor.consume(
        [
            _conversation_row(2, "s1", 1, "Bot", "Sınavlar 10 Ağustos tarihinde."),
            _conversation_row(3, "s1", 2, "Kullanıcı", "Ne zaman?"),
            _conversation_row(4, "s2", 1, "Bot", "Kayıtlar 20 Eylül tarihinde."),
            _conversation_row(5, "s2", 2, "Kullanıcı", "Ne zaman?"),
        ]
    )
    result = processor.finish()

    assert result.total_rows == 4
    assert result.analyzed_count == 2
    assert result.context_only_count == 2
    assert result.discarded_count == 0
    assert result.unique_count == 2
    assert {group.redacted_text for group in result.groups} == {"Ne zaman?"}
    assert {group.context_turns[0].redacted_text for group in result.groups} == {
        "Sınavlar 10 Ağustos tarihinde.",
        "Kayıtlar 20 Eylül tarihinde.",
    }


def test_contextual_session_id_dedupe_anahtarina_girmez(settings: Settings) -> None:
    processor = ContextualPreprocessor(settings, _conversation_config())
    processor.consume(
        [
            _conversation_row(2, "s1", 1, "Bot", "Başvurular yarın açılıyor."),
            _conversation_row(3, "s1", 2, "Kullanıcı", "Nasıl başvururum?"),
            _conversation_row(4, "s2", 1, "Bot", "Başvurular yarın açılıyor."),
            _conversation_row(5, "s2", 2, "Kullanıcı", "Nasıl başvururum?"),
        ]
    )
    result = processor.finish()

    assert result.unique_count == 1
    assert result.groups[0].count == 2
    assert result.duplicate_count == 1


def test_contextual_bot_baglamindaki_pii_modele_gitmez(settings: Settings) -> None:
    processor = ContextualPreprocessor(settings, _conversation_config())
    processor.consume(
        [
            _conversation_row(
                2,
                "s1",
                1,
                "Bot",
                "05551234567 numarasına gönderildi, ali@example.com adresini kontrol edin.",
            ),
            _conversation_row(3, "s1", 2, "Kullanıcı", "Ulaşmadı, ne yapmalıyım?"),
        ]
    )
    result = processor.finish()

    context_text = result.groups[0].context_turns[0].redacted_text
    assert "05551234567" not in context_text
    assert "ali@example.com" not in context_text
    assert "[TELEFON]" in context_text
    assert "[EPOSTA]" in context_text
    # Redaksiyon sayacı yalnız sınıflandırılan hedef kullanıcı turn'ünü sayar.
    assert result.redacted_count == 0


def test_contextual_session_sirasi_geriye_giderse_reddeder(settings: Settings) -> None:
    processor = ContextualPreprocessor(settings, _conversation_config())
    with pytest.raises(ConversationOrderError):
        processor.consume(
            [
                _conversation_row(2, "s1", 2, "Bot", "Önce görülen yanıt."),
                _conversation_row(3, "s1", 1, "Kullanıcı", "Sonra gelen soru."),
            ]
        )


def test_contextual_sira_nan_ise_reddeder(settings: Settings) -> None:
    processor = ContextualPreprocessor(settings, _conversation_config())
    row = _conversation_row(2, "s1", 1, "Kullanıcı", "Sınav ne zaman?")

    with pytest.raises(ConversationOrderError):
        processor.consume([replace(row, message_order="NaN")])


def test_contextual_sistem_mesaji_baglama_girmez(settings: Settings) -> None:
    processor = ContextualPreprocessor(settings, _conversation_config())
    processor.consume(
        [
            _conversation_row(2, "s1", 1, "Bot", "[Sistem] oturum başladı"),
            _conversation_row(3, "s1", 2, "Kullanıcı", "Sınav ne zaman?"),
        ]
    )
    result = processor.finish()

    assert result.context_only_count == 0
    assert result.discarded_count == 1
    assert result.groups[0].context_turns == ()


def test_context_boyutu_prompt_token_tahminine_dahil_edilir() -> None:
    flat = RecordGroup(record_id="flat", normalized="ne zaman", redacted_text="Ne zaman?")
    contextual = RecordGroup(
        record_id="ctx",
        normalized="ne zaman",
        redacted_text="Ne zaman?",
        contextual=True,
        context_turns=(
            ContextTurn(
                role="assistant",
                redacted_text="Başvuru takvimi gelecek hafta ilan edilecek.",
            ),
        ),
    )

    assert record_tokens(contextual) > record_tokens(flat)


# ------------------------------------------------------------ sınıflandırıcı


def test_siniflandirici_hicbir_adet_dondurmez(settings: Settings) -> None:
    """ADR §4'ün kod düzeyindeki karşılığı.

    Sınıflandırıcının döndürdüğü yapıların hiçbirinde sayısal bir alan
    OLMAMALI. Olsaydı Faz 3'te LLM'in uydurduğu bir sayı rapora sızabilirdi.
    """
    result = preprocess(["sınav tarihi ne zaman"] * 3, settings)
    classification = DeterministicClassifier().classify(result.groups)

    for question in classification.questions:
        assert set(vars(question)) == {
            "question_id",
            "canonical_question",
            "record_ids",
            "theme_id",
        }
    for theme in classification.themes:
        assert set(vars(theme)) == {"theme_id", "name", "question_ids"}


def test_imza_once_kirpar_sonra_siralar(settings: Settings) -> None:
    """Regresyon: imza ÖNCE kırpılır, SONRA sıralanır.

    Ters sırada (önce sırala, sonra kırp) alfabetik olarak ilk sözcükler
    kalıyor ve konu sözcüğü atılıyordu: "sınav tarihleri ne zaman
    açıklanacak" imzasında "sınav" yerine "açıklanacak" kalıyor, aynı
    konudaki sorular ayrı temalara dağılıyordu.
    """
    signature = _signature_tokens(normalize("sınav tarihleri ne zaman açıklanacak"))

    assert "sınav" in signature
    # Sıralama yine de kelime sırasından bağımsız olmalı.
    assert _signature_tokens(normalize("ne zaman sınav tarihleri açıklanacak")) == signature


def test_ayni_konudaki_sorular_tek_temada_toplanir(settings: Settings) -> None:
    """Tema, birden çok soruyu GERÇEKTEN gruplayabilmeli.

    Her temada tek soru olsaydı plan §1.2'nin kırpma kuralı hiç sınanmamış
    olurdu: kırpılan soru zaten temayı boşaltırdı.
    """
    values = (
        ["sınav tarihleri ne zaman açıklanacak"] * 40
        + ["sınav yerimi nereden öğrenebilirim"] * 30
        + ["sınav sonuçları nereden görülür"] * 20
        + ["harç ödemesini nasıl yaparım"] * 10
    )
    result = preprocess(values, settings)
    classification = DeterministicClassifier().classify(result.groups)

    sizes = sorted(len(theme.question_ids) for theme in classification.themes)
    assert sizes[-1] >= 3, f"tema başına soru sayıları: {sizes}"


def test_kirpilan_soru_temanin_countunda_kalir(settings: Settings) -> None:
    """Plan §1.2'nin ASIL kritik hâli: kırpılan soru temada SAYILMAYA devam eder.

    Tema `count`'u ile raporda görünen sorularının toplamı arasında
    KESİN bir fark olmalı; aksi hâlde "kırpma tema count'unu değiştirmiyor"
    iddiası boş bir eşitlik üzerinden doğrulanmış olur.
    """
    values = (
        ["sınav tarihleri ne zaman açıklanacak"] * 40
        + ["sınav yerimi nereden öğrenebilirim"] * 30
        + ["sınav sonuçları nereden görülür"] * 20
        + ["harç ödemesini nasıl yaparım"] * 10
    )
    result = preprocess(values, settings)
    classifier = DeterministicClassifier()
    classification = classifier.classify(result.groups)

    def build(top_n: int) -> AnalysisReport:
        return aggregate(
            analysis_id=uuid.uuid4(),
            preprocess_result=result,
            classification=classification,
            filename="veri.xlsx",
            sheet_name="Mesajlar",
            text_column="mesaj",
            model="anthropic/claude-sonnet-4.6",
            prompt_version="faq_analysis/v1",
            classifier_id=classifier.identifier,
            top_n=top_n,
            settings=settings,
        )

    full = build(20)
    trimmed = build(1)

    assert len(trimmed.top_questions) == 1
    assert len(full.top_questions) > 1

    # Tema adetleri kırpmadan ETKİLENMEZ.
    assert {t.id: t.count for t in full.themes} == {t.id: t.count for t in trimmed.themes}

    shown = {q.id: q.count for q in trimmed.top_questions}
    strict = [
        theme
        for theme in trimmed.themes
        if theme.count > sum(shown.get(qid, 0) for qid in theme.related_question_ids)
    ]
    # En az bir temada KESİN eşitsizlik olmalı: kırpılan sorular hâlâ
    # temanın büyüklüğüne katkı veriyor.
    assert strict, "hiçbir temada kırpılmış soru kalmamış; test tautolojik"

    for theme in trimmed.themes:
        assert set(theme.related_question_ids) <= set(shown)


def test_siniflandirici_her_kaydi_en_fazla_bir_soruya_esler(settings: Settings) -> None:
    values = [
        "sınav tarihi ne zaman açıklanacak",
        "sınav tarihleri açıklandı mı",
        "harç ödemesi nasıl yapılır",
        "ders materyali nerede",
    ] * 3
    result = preprocess(values, settings)
    classification = DeterministicClassifier().classify(result.groups)

    assigned = [rid for q in classification.questions for rid in q.record_ids]
    assert len(assigned) == len(set(assigned))
    # Hiçbir kayıt DIŞARIDA da kalmamalı: toplam adet korunmalı.
    assert set(assigned) == {group.record_id for group in result.groups}


# ------------------------------------------------------------------ toplama


def _build(counts: dict[str, int]) -> PreprocessResult:
    """Verilen frekanslarla yapay bir ön işleme sonucu üretir."""
    groups = [
        RecordGroup(
            record_id=f"r{index}",
            normalized=text,
            redacted_text=text,
            count=count,
            examples=[text],
        )
        for index, (text, count) in enumerate(counts.items(), start=1)
    ]
    analyzed = sum(counts.values())
    return PreprocessResult(
        total_rows=analyzed + 10,
        analyzed_count=analyzed,
        discarded_count=10,
        redacted_count=0,
        groups=groups,
    )


def _classification() -> Classification:
    """4 soru, 2 tema. Tema büyüklükleri kasıtlı olarak farklı."""
    return Classification(
        questions=[
            QuestionAssignment("q1", "Sınav ne zaman?", ("r1",), "t1"),
            QuestionAssignment("q2", "Sınav yeri neresi?", ("r2",), "t1"),
            QuestionAssignment("q3", "Harç nasıl ödenir?", ("r3",), "t2"),
            QuestionAssignment("q4", "Belge nasıl alınır?", ("r4",), "t2"),
        ],
        themes=[
            ThemeAssignment("t1", "Sınav", ("q1", "q2")),
            ThemeAssignment("t2", "İşlemler", ("q3", "q4")),
        ],
    )


def _aggregate(top_n: int, settings: Settings) -> AnalysisReport:
    counts = {"a": 100, "b": 50, "c": 30, "d": 20}
    return aggregate(
        analysis_id=uuid.uuid4(),
        preprocess_result=_build(counts),
        classification=_classification(),
        filename="veri.xlsx",
        sheet_name="Mesajlar",
        text_column="mesaj",
        model="anthropic/claude-sonnet-4.6",
        prompt_version="faq_analysis/v1",
        classifier_id="deterministic-proxy/v1",
        top_n=top_n,
        settings=settings,
    )


def test_oranlar_adetlerden_turetilir(settings: Settings) -> None:
    report = _aggregate(top_n=10, settings=settings)
    analyzed = report.preprocessing_summary.analyzed_count

    assert analyzed == 200
    for question in report.top_questions:
        assert question.percentage == percentage_half_up(question.count, analyzed)
    for theme in report.themes:
        assert theme.percentage == percentage_half_up(theme.count, analyzed)


def test_half_up_esitlik_yuzdesi_rapor_uretmesini_engellemez(settings: Settings) -> None:
    """2.400 satırdaki %2,25, şemanın beklediği şekilde %2,3 olmalı.

    Python ``round(2.25, 1)`` half-even ile 2,2 döndürür. Bu fark daha önce
    rapor şemasının doğrulamasını patlatıp LLM işi tamamlandıktan sonra analizi
    başarısız kılıyordu.
    """
    report = aggregate(
        analysis_id=uuid.uuid4(),
        preprocess_result=_build({"a": 54, "b": 846, "c": 800, "d": 700}),
        classification=_classification(),
        filename="veri.xlsx",
        sheet_name="Mesajlar",
        text_column="mesaj",
        model="anthropic/claude-sonnet-4.6",
        prompt_version="faq_analysis/v3",
        classifier_id="test/v1",
        top_n=10,
        settings=settings,
    )

    question = next(question for question in report.top_questions if question.id == "q1")
    assert question.count == 54
    assert question.percentage == 2.3


def test_tema_toplami_analiz_edilen_kaydi_asmaz(settings: Settings) -> None:
    report = _aggregate(top_n=10, settings=settings)
    assert sum(theme.count for theme in report.themes) <= (
        report.preprocessing_summary.analyzed_count
    )


def test_top_n_kirpmasi_tema_countunu_degistirmez(settings: Settings) -> None:
    """Plan §1.2: tema büyüklüğü kaç sorunun gösterildiğine BAĞLI DEĞİLDİR."""
    full = _aggregate(top_n=10, settings=settings)
    trimmed = _aggregate(top_n=1, settings=settings)

    assert len(trimmed.top_questions) == 1
    assert len(full.top_questions) == 4

    full_counts = {theme.id: theme.count for theme in full.themes}
    trimmed_counts = {theme.id: theme.count for theme in trimmed.themes}
    assert full_counts == trimmed_counts

    full_pct = {theme.id: theme.percentage for theme in full.themes}
    trimmed_pct = {theme.id: theme.percentage for theme in trimmed.themes}
    assert full_pct == trimmed_pct


def test_related_question_ids_yalnizca_rapordaki_sorulara_baglanir(
    settings: Settings,
) -> None:
    """Plan §1.2: arayüz çözemeyeceği bir kimliğe bağlantı vermemeli."""
    trimmed = _aggregate(top_n=2, settings=settings)
    included = {question.id for question in trimmed.top_questions}

    for theme in trimmed.themes:
        assert set(theme.related_question_ids) <= included


def test_ayni_kayit_iki_soruya_eslenirse_toplama_reddeder(settings: Settings) -> None:
    """Faz 3'te LLM'in yapması ÇOK OLASI hata: aynı kaydı iki kez saymak."""
    bad = Classification(
        questions=[
            QuestionAssignment("q1", "A", ("r1",), "t1"),
            QuestionAssignment("q2", "B", ("r1",), "t1"),
        ],
        themes=[ThemeAssignment("t1", "T", ("q1", "q2"))],
    )
    with pytest.raises(AggregationError):
        aggregate(
            analysis_id=uuid.uuid4(),
            preprocess_result=_build({"a": 10}),
            classification=bad,
            filename="veri.xlsx",
            sheet_name="S",
            text_column="m",
            model="anthropic/claude-sonnet-4.6",
            prompt_version="v1",
            classifier_id="test",
            top_n=5,
            settings=settings,
        )


def test_bilinmeyen_kayit_kimligi_reddedilir(settings: Settings) -> None:
    bad = Classification(
        questions=[QuestionAssignment("q1", "A", ("olmayan",), "t1")],
        themes=[ThemeAssignment("t1", "T", ("q1",))],
    )
    with pytest.raises(AggregationError):
        aggregate(
            analysis_id=uuid.uuid4(),
            preprocess_result=_build({"a": 10}),
            classification=bad,
            filename="veri.xlsx",
            sheet_name="S",
            text_column="m",
            model="anthropic/claude-sonnet-4.6",
            prompt_version="v1",
            classifier_id="test",
            top_n=5,
            settings=settings,
        )


def test_faz2_token_ve_maliyet_sifir_raporlanir(settings: Settings) -> None:
    """Plan §4: Faz 2'de gerçek token yok, 0 raporlanır."""
    report = _aggregate(top_n=10, settings=settings)

    assert report.token_usage.total_tokens == 0
    assert report.estimated_cost_usd == 0.0


def test_ucucu_ol_ucu_uctan_uca_gercek_veriyle(settings: Settings) -> None:
    """Ön işleme → sınıflandırma → toplama zincirinin bütünü."""
    values = (
        ["sınav tarihleri ne zaman açıklanacak"] * 40
        + ["sınav tarihi açıklandı mı acaba"] * 20
        + ["harç ödemesini nasıl yaparım"] * 15
        + ["ders materyallerine nereden ulaşırım"] * 10
        + [None] * 5
    )
    result = preprocess(values, settings)
    classifier = DeterministicClassifier()
    classification = classifier.classify(result.groups)

    report = aggregate(
        analysis_id=uuid.uuid4(),
        preprocess_result=result,
        classification=classification,
        filename="veri.xlsx",
        sheet_name="Mesajlar",
        text_column="mesaj",
        model="anthropic/claude-sonnet-4.6",
        prompt_version="faq_analysis/v1",
        classifier_id=classifier.identifier,
        top_n=20,
        settings=settings,
    )

    assert report.preprocessing_summary.analyzed_count == 85
    assert report.preprocessing_summary.discarded_count == 5
    assert report.source_summary.total_rows == 90
    # Soru adetlerinin toplamı analiz edilen kayda EŞİT olmalı: hiçbir mesaj
    # kaybolmamalı, hiçbiri iki kez sayılmamalı.
    assert sum(question.count for question in report.top_questions) == 85
    assert report.prompt_hash.startswith("sha256:")


# ------------------------------------------------------------- maliyet tavanı


def test_profil_maliyet_tahmini_kayit_sayisiyla_artar(settings: Settings) -> None:
    one = estimate_profile_cost(
        1,
        120.0,
        "anthropic/claude-sonnet-4.6",
        settings=settings,
        prompt=V1,
    )
    hundred = estimate_profile_cost(
        100,
        120.0,
        "anthropic/claude-sonnet-4.6",
        settings=settings,
        prompt=V1,
    )

    assert one > 0
    assert hundred > one
    # Çağrı başına sabit JSON/prompt yükü var; bu yüzden maliyet tam doğrusal
    # değildir ve tek kaydın maliyetini 100'le çarpmak fazla tahmin verir.
    assert hundred < one * 100


def test_profil_maliyet_tahmini_bos_kolonda_sifirdir(settings: Settings) -> None:
    assert (
        estimate_profile_cost(
            0,
            120.0,
            "anthropic/claude-sonnet-4.6",
            settings=settings,
            prompt=V1,
        )
        == 0.0
    )


def test_hiyerarsik_reduce_maliyet_tahmini_aralik_dondurur(settings: Settings) -> None:
    narrow = settings.model_copy(
        update={"llm_chunk_max_records": 3, "llm_reduce_max_prompt_tokens": 45}
    )
    forecast = estimate_profile_cost_range(
        30,
        60.0,
        "google/gemini-2.5-flash",
        settings=narrow,
        prompt=V2,
        max_cost_usd=0.0001,
    )

    assert forecast.estimated_cost_usd > 0
    assert forecast.upper_cost_usd > forecast.estimated_cost_usd
    assert forecast.upper_prompt_tokens > forecast.estimated_prompt_tokens
    assert forecast.cost_range_usd == (forecast.estimated_cost_usd, forecast.upper_cost_usd)
    assert forecast.exceeds is True


def test_gercek_smoke_completion_olcumune_yakin_tahmin(settings: Settings) -> None:
    """2026-08-19 gerçek Gemini çağrısı: 905 input, 469 output token."""
    values = [
        "sınav tarihleri ne zaman açıklanacak acaba bilgi alabilir miyim",
        "ders materyallerine nereden ulaşabilirim linkler çalışmıyor",
        "harç ödemesini nasıl yaparım banka şubesinden mi",
        "kayıt yenileme işlemi için son tarih ne zaman",
        "sınav yerimi nereden öğrenebilirim bilgi yok",
    ]
    result = preprocess(values, settings)
    decision = estimate_cost(
        result.groups,
        "google/gemini-2.5-flash",
        max_cost_usd=5.0,
        settings=settings,
        prompt=V2,
    )

    actual_cost = cost_for_tokens(905, 469, "google/gemini-2.5-flash")

    assert decision.estimated_completion_tokens == 500
    assert actual_cost <= decision.estimated_cost_usd <= round(actual_cost * 1.20, 6)


def test_coklu_chunk_completion_tahmini_reduce_yukunu_icerir(settings: Settings) -> None:
    dar = settings.model_copy(update={"llm_chunk_max_records": 3})
    result = preprocess([f"benzersiz soru numarası {index}" for index in range(7)], dar)
    decision = estimate_cost(
        result.groups,
        "google/gemini-2.5-flash",
        max_cost_usd=5.0,
        settings=dar,
        prompt=V2,
    )

    # 3 map çağrısı: 3×200 sabit + 7×60 kayıt; reduce: 200 + 7×50.
    assert decision.estimated_completion_tokens == 1_570


def test_maliyet_tahmini_benzersiz_kayitlardan_hesaplanir(settings: Settings) -> None:
    """ADR §10 risk 3 — dedupe maliyeti düşürür.

    Ölçüt MUTLAK BİR EŞİK DEĞİL: 100 tekrarın tahmini, tek kaydın tahminiyle
    AYNI olmalı. Eskiden burada `< 0.001` vardı ve o eşik farkında olmadan
    eksik sayımı kodluyordu — tahmin map system prompt'unu hiç saymıyordu,
    oysa tek bir kayıt bile tam bir system prompt bedeli ödüyor (B5a).
    """
    yuz_tekrar = preprocess(["sınav ne zaman acaba bilgi verir misiniz"] * 100, settings)
    tek = preprocess(["sınav ne zaman acaba bilgi verir misiniz"], settings)

    def tahmin(result: PreprocessResult) -> CostDecision:
        return estimate_cost(
            result.groups,
            "anthropic/claude-sonnet-4.6",
            max_cost_usd=5.0,
            settings=settings,
            prompt=V1,
        )

    coklu, tekli = tahmin(yuz_tekrar), tahmin(tek)

    assert len(yuz_tekrar.groups) == 1, "100 tekrar tek kayda inmeli"
    assert coklu.estimated_prompt_tokens == tekli.estimated_prompt_tokens
    assert coklu.estimated_cost_usd == tekli.estimated_cost_usd
    assert coklu.estimated_prompt_tokens > 0
    assert coklu.exceeds is False


def test_maliyet_tavani_asimi_tespit_edilir(settings: Settings) -> None:
    result = preprocess([f"soru numarası {i} hakkında bilgi" for i in range(5_000)], settings)
    decision = estimate_cost(
        result.groups,
        "anthropic/claude-sonnet-4.6",
        max_cost_usd=0.0001,
        settings=settings,
        prompt=V1,
    )

    assert decision.exceeds is True
