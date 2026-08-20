"""`CHATBOT_LOG` ön ayarı: satır filtresi, oturum ve zaman serisi (B2)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.config import Settings
from app.pipeline.aggregate import aggregate
from app.pipeline.classifier import DeterministicClassifier
from app.pipeline.preprocess import (
    Preprocessor,
    PreprocessResult,
    SourceRecord,
    chatbot_records,
    utc_date_of,
)
from app.schemas.analysis import ChatbotLogConfig, DatasetType


@pytest.fixture
def settings() -> Settings:
    return Settings()


FULL_CONFIG = ChatbotLogConfig(
    role_column="direction",
    role_user_values=["Kullanıcı", "user"],
    session_id_column="session_id",
    timestamp_column="message_time_tr",
    message_type_column="message_type",
    allowed_message_types=["text"],
)


# ------------------------------------------------------------- zaman damgası


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-05-31 21:04:14", "2026-05-31"),
        ("2026-05-31T21:04:14", "2026-05-31"),
        ("2026-05-31", "2026-05-31"),
        ("31.05.2026 21:04:14", "2026-05-31"),
        # Timezone taşıyan değer UTC'ye çevrilir: +03:00'daki gece 01:30,
        # UTC'de bir ÖNCEKİ gündür.
        ("2026-06-01T01:30:00+03:00", "2026-05-31"),
        ("2026-06-01T01:30:00Z", "2026-06-01"),
        ("bozuk", None),
        ("", None),
        (None, None),
    ],
)
def test_utc_date_of(raw: str | None, expected: str | None) -> None:
    assert utc_date_of(raw) == expected


# --------------------------------------------------------------- satır filtresi


def test_chatbot_filtresi_bot_ve_sistem_satirlarini_eler() -> None:
    """Plan B2'nin çekirdeği: yalnızca kullanıcı + izinli tip analize girer."""
    rows = [
        # (text, direction, message_type, session_id, timestamp)
        ("sınav ne zaman", "Kullanıcı", "text", "s1", "2026-05-31 09:00:00"),
        ("Menüden bir seçenek seçin", "Bot", "single-choice", "s1", "2026-05-31 09:00:05"),
        ("Yardımcı olabilir miyim?", "Bot", "text", "s1", "2026-05-31 09:00:06"),
        ("page_changed", "Sistem", "event", "s1", "2026-05-31 09:00:07"),
        # Rol eşleşiyor ama tip izinli değil: elenir.
        ("konum", "Kullanıcı", "location", "s2", "2026-05-31 09:10:00"),
        # Rol karşılaştırması kırpma + Türkçe küçük harfle: "KULLANICI" geçer.
        ("harç ödemesi nasıl yapılır", " KULLANICI ", "text", "s2", "2026-06-01 10:00:00"),
        (None, "Kullanıcı", "text", "s3", "2026-06-01 11:00:00"),
    ]

    records = list(chatbot_records(iter(rows), FULL_CONFIG))

    assert len(records) == len(rows)  # toplam satır sayısı korunur
    kept = [record for record in records if record.text is not None]
    assert [record.text for record in kept] == [
        "sınav ne zaman",
        "harç ödemesi nasıl yapılır",
    ]
    assert kept[0].session_id == "s1"
    assert kept[0].date == "2026-05-31"
    assert kept[1].date == "2026-06-01"


def test_chatbot_filtresi_opsiyonel_kolonlar_olmadan() -> None:
    """Yalnızca rol kolonu: tuple iki elemanlıdır, tip/oturum/zaman yoktur."""
    config = ChatbotLogConfig(role_column="direction", role_user_values=["user"])
    rows = [("soru bir", "user"), ("cevap", "bot")]

    records = list(chatbot_records(iter(rows), config))

    assert records[0] == SourceRecord(text="soru bir", session_id=None, date=None)
    assert records[1].text is None


# ------------------------------------------------- preprocess sayaçları


def test_preprocessor_oturum_ve_tarih_takibi(settings: Settings) -> None:
    preprocessor = Preprocessor(settings, track_sessions=True, track_dates=True)
    preprocessor.consume_records(
        [
            SourceRecord(text="sınav tarihleri ne zaman", session_id="s1", date="2026-05-30"),
            SourceRecord(text="sınav tarihleri ne zaman", session_id="s2", date="2026-05-31"),
            SourceRecord(text="harç ödemesi nasıl yapılır", session_id="s1", date="2026-05-31"),
            # Filtreye takılmış satır: yalnızca discarded'a sayılır.
            SourceRecord(text=None),
            # Tarihi çözümlenememiş kayıt: analize girer, seriye girmez.
            SourceRecord(text="kayıt yenileme", session_id=None, date=None),
        ]
    )
    result = preprocessor.finish()

    assert result.total_rows == 5
    assert result.analyzed_count == 4
    assert result.discarded_count == 1
    assert result.session_count == 2
    assert result.dates_tracked is True

    sinav = next(g for g in result.groups if "sınav" in g.normalized)
    assert sinav.count == 2
    assert sinav.session_ids == {"s1", "s2"}
    assert dict(sinav.daily_counts) == {"2026-05-30": 1, "2026-05-31": 1}


def test_generic_yolda_takip_kapali(settings: Settings) -> None:
    """`consume` kısayolu eski davranışla birebir: oturum/tarih izi yok."""
    preprocessor = Preprocessor(settings)
    preprocessor.consume(["soru bir nedir", None, "soru bir nedir"])
    result = preprocessor.finish()

    assert result.session_count is None
    assert result.dates_tracked is False
    assert result.analyzed_count == 2
    assert result.groups[0].session_ids == set()


# ------------------------------------------------------------- aggregate


def _chatbot_result(settings: Settings) -> PreprocessResult:
    preprocessor = Preprocessor(settings, track_sessions=True, track_dates=True)
    preprocessor.consume_records(
        [
            SourceRecord(text="sınav tarihleri ne zaman", session_id="s1", date="2026-05-30"),
            SourceRecord(text="sınav tarihleri ne zaman", session_id="s2", date="2026-05-31"),
            SourceRecord(
                text="sınav tarihi ne zaman belli olur", session_id="s2", date="2026-05-31"
            ),
            SourceRecord(text="harç ödemesi nasıl yapılır", session_id="s3", date="2026-05-30"),
            SourceRecord(text=None),
        ]
    )
    return preprocessor.finish()


def test_aggregate_chatbot_metrikleri(settings: Settings) -> None:
    result = _chatbot_result(settings)
    classification = DeterministicClassifier().classify(result.groups)

    report = aggregate(
        analysis_id=uuid4(),
        preprocess_result=result,
        classification=classification,
        filename="dokum.csv",
        sheet_name="CSV",
        text_column="message_text_clean",
        model="google/gemini-2.5-flash",
        prompt_version="faq_analysis/v1",
        classifier_id="deterministic-proxy/v1",
        top_n=10,
        settings=settings,
        dataset_type=DatasetType.CHATBOT_LOG,
    )

    assert report.dataset_type is DatasetType.CHATBOT_LOG
    assert report.preprocessing_summary.session_count == 3

    # Sayılar gerçek frekanslardan: oturum sayısı mesaj sayısını aşamaz.
    for question in report.top_questions:
        assert question.session_count is not None
        assert question.session_count <= question.count
    for theme in report.themes:
        assert theme.session_count is not None

    assert report.time_series is not None
    totals = {point.date: point.count for point in report.time_series.daily_totals}
    assert totals == {"2026-05-30": 2, "2026-05-31": 2}
    # Tarihler artan sıralı.
    dates = [point.date for point in report.time_series.daily_totals]
    assert dates == sorted(dates)

    question_ids = {question.id for question in report.top_questions}
    assert {series.id for series in report.time_series.question_trends} <= question_ids
    theme_ids = {theme.id for theme in report.themes}
    assert {series.id for series in report.time_series.theme_trends} == theme_ids

    # Soru serilerinin toplamı sorunun adedini aşamaz.
    counts = {question.id: question.count for question in report.top_questions}
    for series in report.time_series.question_trends:
        assert sum(point.count for point in series.daily) <= counts[series.id]


def test_aggregate_generic_yolda_yeni_alanlar_bos(settings: Settings) -> None:
    """GENERIC rapor eski davranışla birebir: yeni alanlar null."""
    preprocessor = Preprocessor(settings)
    preprocessor.consume(["soru bir nedir", "soru iki nedir"])
    result = preprocessor.finish()
    classification = DeterministicClassifier().classify(result.groups)

    report = aggregate(
        analysis_id=uuid4(),
        preprocess_result=result,
        classification=classification,
        filename="veri.xlsx",
        sheet_name="Mesajlar",
        text_column="mesaj",
        model="google/gemini-2.5-flash",
        prompt_version="faq_analysis/v1",
        classifier_id="deterministic-proxy/v1",
        top_n=10,
        settings=settings,
    )

    assert report.dataset_type is DatasetType.GENERIC
    assert report.time_series is None
    assert report.preprocessing_summary.session_count is None
    assert all(question.session_count is None for question in report.top_questions)
    assert all(theme.session_count is None for theme in report.themes)
