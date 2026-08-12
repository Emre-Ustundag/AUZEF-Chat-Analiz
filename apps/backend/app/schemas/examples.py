"""Sözleşme örneklerinin tek kaynağı.

İki tüketicisi var:
  * `scripts/export_fixtures.py` → `tests/fixtures/contract/*.json`
  * `core/openapi.py` → `openapi.json` içindeki `examples`

`scripts/` altındaydı; OpenAPI'nin de aynı örnekleri kullanabilmesi için
`app/` içine alındı (uygulama katmanı script'ten import etmemeli).

Her örnek **Pydantic instance** olarak kurulur, elle dict yazılmaz: fixture
tanım gereği geçerli olur ve gerçek serializer'lardan geçer (Z datetime,
    `retry_after` düşürme, UUID casing). Elle yazılmış bir fixture bu
kartın var olma sebebi olan tuzakların yanından geçerdi.

SAYISAL INVARIANT'LAR (ADR-0002 §3 #2/#5, `test_fixture_invariants.py`):
  * `UploadProfile.total_row_count` == sheet satırlarının toplamı
  * `exceeds_row_limit` == en az bir sheet MAX_ROWS'u aşıyor
  * her kolonda `non_empty_count + empty_count == sheet.row_count`
  * `source_summary.total_rows` == analiz edilen sheet'in satır sayısı
  * `analyzed_count + discarded_count` == min(total_rows, MAX_ROWS)
  * `unique_count + duplicate_count == analyzed_count`
  * `total_tokens == prompt_tokens + completion_tokens`
  * `estimated_cost_usd` katalogdan hesaplanır
"""

from datetime import UTC, datetime
from typing import Any, NamedTuple

from app.core.catalog import (
    DEFAULT_MODEL,
    DEFAULT_PROMPT_VERSION,
    MODEL_LIST,
    estimate_cost_usd,
)
from app.core.config import settings
from app.core.errors import ERROR_STATUS, ERROR_TITLES, ErrorCode, error_type_uri
from app.schemas.analysis import (
    AnalysisCreated,
    AnalysisJob,
    AnalysisRequest,
    AnalysisStatus,
)
from app.schemas.common import ErrorItem, ProblemDetails, WarningCode
from app.schemas.report import (
    AnalysisReport,
    AnalysisWarning,
    PreprocessingSummary,
    SourceSummary,
    Theme,
    TokenUsage,
    TopQuestion,
)
from app.schemas.upload import (
    ColumnProfile,
    SheetProfile,
    Upload,
    UploadCreated,
    UploadProfile,
    UploadStatus,
)

UPLOAD_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
ANALYSIS_ID = "6b1cf3d2-0a44-4f1b-9d64-1c2a7e5f8b90"
TRACE_ID = "9d8c7b6a-5e4f-4321-8abc-0123456789ab"

CREATED_AT = datetime(2026, 8, 11, 10, 0, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 8, 11, 10, 4, 30, tzinfo=UTC)
GENERATED_AT = datetime(2026, 8, 11, 10, 12, 15, tzinfo=UTC)

#: Normal senaryoda analiz edilen sheet'in satır sayısı.
SHEET_ROWS = 48_213
#: Satır sınırını aşan senaryo.
SHEET_ROWS_OVER_LIMIT = 250_000

#: `mesaj` kolonundaki boş hücreler; analizde elenen kayıtlar bunlar.
EMPTY_MESSAGE_ROWS = 1_107
EMPTY_ANSWER_ROWS = 3_233

PROMPT_TOKENS = 1_284_000
COMPLETION_TOKENS = 96_400

FILENAME = "auzef-chatbot-mesajlari.xlsx"
FILENAME_OVER_LIMIT = "auzef-chatbot-mesajlari-buyuk.xlsx"


class Case(NamedTuple):
    """Bir fixture dosyası + manifest kaydı."""

    id: str
    method: str
    path: str
    status: int
    model: str | None
    payload: Any


# ------------------------------------------------------------------ uploads


def _message_columns(rows: int) -> list[ColumnProfile]:
    """`non_empty + empty == rows` invariant'ını koruyarak kolon üretir."""
    return [
        ColumnProfile(
            name="tarih",
            index=0,
            non_empty_count=rows,
            empty_count=0,
            unique_count=min(41_002, rows),
            avg_length=19,
            is_likely_text=False,
            sample_values=["2026-03-01 09:12", "2026-03-01 09:14"],
        ),
        ColumnProfile(
            name="kullanici_id",
            index=1,
            non_empty_count=rows,
            empty_count=0,
            unique_count=min(12_884, rows),
            avg_length=8,
            is_likely_text=False,
            sample_values=["[ID]", "[ID]"],
        ),
        ColumnProfile(
            name="mesaj",
            index=2,
            non_empty_count=rows - EMPTY_MESSAGE_ROWS,
            empty_count=EMPTY_MESSAGE_ROWS,
            unique_count=min(31_540, rows - EMPTY_MESSAGE_ROWS),
            avg_length=64,
            is_likely_text=True,
            sample_values=[
                "sınav tarihleri ne zaman açıklanacak",
                "ders materyallerine nereden ulaşabilirim",
                "harç ödemesini nasıl yaparım",
            ],
        ),
        ColumnProfile(
            name="kanal",
            index=3,
            non_empty_count=rows,
            empty_count=0,
            unique_count=3,
            avg_length=7,
            is_likely_text=False,
            sample_values=["web", "mobil"],
        ),
        ColumnProfile(
            name="yanit",
            index=4,
            non_empty_count=rows - EMPTY_ANSWER_ROWS,
            empty_count=EMPTY_ANSWER_ROWS,
            unique_count=min(8_712, rows - EMPTY_ANSWER_ROWS),
            avg_length=128,
            is_likely_text=True,
            sample_values=["Sınav takvimi için...", "Materyallere..."],
        ),
    ]


def _raw_columns(rows: int) -> list[ColumnProfile]:
    return [
        ColumnProfile(
            name="id",
            index=0,
            non_empty_count=rows,
            empty_count=0,
            unique_count=rows,
            avg_length=6,
            is_likely_text=False,
            sample_values=["1", "2"],
        ),
        ColumnProfile(
            name="icerik",
            index=1,
            non_empty_count=rows - EMPTY_MESSAGE_ROWS,
            empty_count=EMPTY_MESSAGE_ROWS,
            unique_count=min(31_540, rows - EMPTY_MESSAGE_ROWS),
            avg_length=64,
            is_likely_text=True,
            sample_values=["sınav ne zaman", "kayıt yenileme"],
        ),
    ]


def build_profile(rows: int = SHEET_ROWS) -> UploadProfile:
    sheets = [
        SheetProfile(
            name="Mesajlar", row_count=rows, column_count=5, columns=_message_columns(rows)
        ),
        SheetProfile(name="Ham Veri", row_count=rows, column_count=2, columns=_raw_columns(rows)),
    ]
    return UploadProfile(
        sheets=sheets,
        # Dosyanın tamamı: tüm sheet'lerin toplamı.
        total_row_count=sum(sheet.row_count for sheet in sheets),
        # Analiz sınırı SEÇİLEN sheet'e uygulanır, dolayısıyla bayrak da
        # sheet bazında değerlendirilir.
        exceeds_row_limit=any(sheet.row_count > settings.max_rows for sheet in sheets),
    )


def build_problem(
    code: ErrorCode,
    detail: str,
    *,
    errors: list[ErrorItem] | None = None,
) -> ProblemDetails:
    return ProblemDetails(
        type=error_type_uri(code),
        title=ERROR_TITLES[code],
        status=ERROR_STATUS[code],
        code=code,
        detail=detail,
        trace_id=TRACE_ID,
        errors=errors or [],
        # ADR-0002 #6: yalnızca 429'da; diğerlerinde alan hiç yayılmaz.
        retry_after=60 if code is ErrorCode.PROVIDER_RATE_LIMITED else None,
    )


# ------------------------------------------------------------------- report

_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "q1",
        "canonical_question": "Sınav tarihleri ne zaman açıklanacak?",
        "count": 11_680,
        "confidence": 0.94,
        "examples": ["sınav tarihleri belli mi", "vize ne zaman"],
    },
    {
        "id": "q2",
        "canonical_question": "Ders materyallerine nereden ulaşabilirim?",
        "count": 8_102,
        "confidence": 0.91,
        "examples": ["ders kitabı nerede", "pdf'leri bulamıyorum"],
    },
    {
        "id": "q3",
        "canonical_question": "Harç ödemesini nasıl yaparım?",
        "count": 5_748,
        "confidence": 0.89,
        "examples": ["harç yatırma", "ödeme yapamıyorum"],
    },
    {
        "id": "q4",
        "canonical_question": "Kayıt yenileme işlemi nasıl yapılır?",
        "count": 4_523,
        "confidence": 0.87,
        "examples": ["kayıt yenilemedim ne olur"],
    },
    {
        "id": "q5",
        "canonical_question": "Sınav yerimi nereden öğrenebilirim?",
        "count": 3_311,
        "confidence": 0.85,
        "examples": ["sınav yeri", "hangi binada"],
    },
    {
        "id": "q6",
        "canonical_question": "Mazeret sınavına nasıl başvurulur?",
        "count": 2_204,
        "confidence": 0.82,
        "examples": ["mazeret sınavı başvuru"],
    },
    {
        "id": "q7",
        "canonical_question": "Not itirazı nasıl yapılır?",
        "count": 1_640,
        "confidence": 0.78,
        "examples": ["notuma itiraz etmek istiyorum"],
    },
    {
        "id": "q8",
        "canonical_question": "Öğrenci belgesi nasıl alınır?",
        "count": 1_129,
        "confidence": 0.76,
        "examples": ["öğrenci belgesi lazım"],
    },
]

_THEMES: list[dict[str, Any]] = [
    {"id": "t1", "name": "Sınav ve takvim", "question_ids": ["q1", "q5", "q6"]},
    {"id": "t2", "name": "Ders materyalleri", "question_ids": ["q2"]},
    {"id": "t3", "name": "Ödeme ve kayıt", "question_ids": ["q3", "q4"]},
    {"id": "t4", "name": "Belge ve itiraz", "question_ids": ["q7", "q8"]},
]


def build_report(
    *,
    top_n: int,
    sheet_rows: int = SHEET_ROWS,
    warnings: list[AnalysisWarning] | None = None,
) -> AnalysisReport:
    """ADR-0002 #5'in çalıştırılabilir spesifikasyonu.

    Tema `count`/`percentage` TÜM mesajları yansıtır; yalnızca
    `related_question_ids` raporda gerçekten yer alan sorulara filtrelenir.
    """
    # ADR-0002 #2: sınır aşılırsa iş reddedilmez, ilk MAX_ROWS satır işlenir.
    considered = min(sheet_rows, settings.max_rows)
    discarded = EMPTY_MESSAGE_ROWS
    analyzed = considered - discarded
    # Normal fixture'daki 31.540 / 47.106 oranı büyük korpusta korunur.
    unique = round(analyzed * (31_540 / 47_106))
    duplicate = analyzed - unique

    included = _QUESTIONS[:top_n]
    included_ids = {question["id"] for question in included}

    def percentage(count: int) -> float:
        return round(count / analyzed * 100, 1)

    themes: list[Theme] = []
    for theme in _THEMES:
        ids = set(theme["question_ids"])
        count = sum(q["count"] for q in _QUESTIONS if q["id"] in ids)
        themes.append(
            Theme(
                id=theme["id"],
                name=theme["name"],
                count=count,
                percentage=percentage(count),
                related_question_ids=[qid for qid in theme["question_ids"] if qid in included_ids],
            )
        )

    return AnalysisReport(
        schema_version="1.0",
        analysis_id=ANALYSIS_ID,
        generated_at=GENERATED_AT,
        source_summary=SourceSummary(
            filename=FILENAME if sheet_rows == SHEET_ROWS else FILENAME_OVER_LIMIT,
            sheet_name="Mesajlar",
            text_column="mesaj",
            # Analiz edilen sheet'in satır sayısı (dosyanın tamamı değil).
            total_rows=sheet_rows,
        ),
        preprocessing_summary=PreprocessingSummary(
            analyzed_count=analyzed,
            discarded_count=discarded,
            duplicate_count=duplicate,
            redacted_count=round(analyzed * 0.0603),
            unique_count=unique,
        ),
        top_questions=[
            TopQuestion(
                id=question["id"],
                canonical_question=question["canonical_question"],
                count=question["count"],
                percentage=percentage(question["count"]),
                confidence=question["confidence"],
                redacted_examples=question["examples"],
            )
            for question in included
        ],
        themes=themes,
        executive_summary=(
            "Mesajların dörtte birinden fazlası sınav takvimiyle ilgili. Ders "
            "materyallerine erişim ve harç ödemesi ikinci ve üçüncü sırada "
            "geliyor. Bu üç başlık toplam mesajların yaklaşık yarısını "
            "oluşturuyor; chatbot bilgi tabanında öncelikli iyileştirme "
            "alanları bunlar."
        ),
        warnings=warnings or [],
        model=DEFAULT_MODEL,
        prompt_version=DEFAULT_PROMPT_VERSION,
        prompt_hash="sha256:2f8a1c9e4b7d",
        token_usage=TokenUsage(
            prompt_tokens=PROMPT_TOKENS,
            completion_tokens=COMPLETION_TOKENS,
            total_tokens=PROMPT_TOKENS + COMPLETION_TOKENS,
        ),
        # Sabit yazılmaz: katalog fiyatı değişince sessizce yanlış olurdu.
        estimated_cost_usd=estimate_cost_usd(DEFAULT_MODEL, PROMPT_TOKENS, COMPLETION_TOKENS),
    )


def row_limit_warning(sheet_rows: int) -> AnalysisWarning:
    return AnalysisWarning(
        code=WarningCode.ROW_LIMIT_TRUNCATED,
        # ADR-0002 #2: mesaj kullanıcıya hazır Türkçedir.
        message=(
            f"Sayfada {sheet_rows:,} satır bulundu; analiz ilk "
            f"{settings.max_rows:,} satırla sınırlandırıldı. Sonuçlar bu alt "
            "küme üzerinden hesaplandı."
        ).replace(",", "."),
    )


# --------------------------------------------------------------------- API

ANALYSIS_REQUEST = AnalysisRequest(
    upload_id=UPLOAD_ID,
    sheet_name="Mesajlar",
    text_column="mesaj",
    model=DEFAULT_MODEL,
    prompt_version=DEFAULT_PROMPT_VERSION,
    top_n=8,
    max_cost_usd=10,
)


def build_cases() -> list[Case]:
    cases: list[Case] = [
        Case(
            "uploads.create.202",
            "POST",
            "/api/v1/uploads",
            202,
            "UploadCreated",
            UploadCreated(upload_id=UPLOAD_ID, status=UploadStatus.QUEUED),
        ),
        Case("uploads.delete.204", "DELETE", "/api/v1/uploads/{upload_id}", 204, None, None),
        Case(
            "uploads.get.200.queued",
            "GET",
            "/api/v1/uploads/{upload_id}",
            200,
            "Upload",
            Upload(
                upload_id=UPLOAD_ID,
                status=UploadStatus.QUEUED,
                filename=FILENAME,
                size_bytes=88_412_160,
                created_at=CREATED_AT,
            ),
        ),
        Case(
            "uploads.get.200.ready",
            "GET",
            "/api/v1/uploads/{upload_id}",
            200,
            "Upload",
            Upload(
                upload_id=UPLOAD_ID,
                status=UploadStatus.READY,
                filename=FILENAME,
                size_bytes=88_412_160,
                created_at=CREATED_AT,
                profile=build_profile(),
            ),
        ),
        Case(
            "uploads.get.200.row-limit",
            "GET",
            "/api/v1/uploads/{upload_id}",
            200,
            "Upload",
            Upload(
                upload_id=UPLOAD_ID,
                status=UploadStatus.READY,
                filename=FILENAME_OVER_LIMIT,
                size_bytes=142_606_336,
                created_at=CREATED_AT,
                # ADR-0002 #2: sınır aşımı upload'ı REDDETMEZ, işaretler.
                profile=build_profile(SHEET_ROWS_OVER_LIMIT),
            ),
        ),
        Case(
            "uploads.get.200.failed",
            "GET",
            "/api/v1/uploads/{upload_id}",
            200,
            "Upload",
            Upload(
                upload_id=UPLOAD_ID,
                status=UploadStatus.FAILED,
                filename="bozuk.xlsx",
                size_bytes=1_204,
                created_at=CREATED_AT,
                error=build_problem(
                    ErrorCode.UPLOAD_CORRUPT_OR_ENCRYPTED,
                    "OOXML yapısı doğrulanamadı.",
                ),
            ),
        ),
        Case("models.list.200", "GET", "/api/v1/models", 200, "ModelList", MODEL_LIST),
        # İSTEK yönü: frontend'in ürettiği tek gövde.
        Case(
            "analyses.request", "POST", "/api/v1/analyses", 202, "AnalysisRequest", ANALYSIS_REQUEST
        ),
        Case(
            "analyses.create.202",
            "POST",
            "/api/v1/analyses",
            202,
            "AnalysisCreated",
            AnalysisCreated(analysis_id=ANALYSIS_ID, status=AnalysisStatus.QUEUED),
        ),
        Case(
            "analyses.get.200.analyzing",
            "GET",
            "/api/v1/analyses/{analysis_id}",
            200,
            "AnalysisJob",
            AnalysisJob(
                analysis_id=ANALYSIS_ID,
                status=AnalysisStatus.ANALYZING,
                progress=62.5,
                created_at=CREATED_AT,
                updated_at=UPDATED_AT,
                estimated_seconds_remaining=430,
            ),
        ),
        Case(
            "analyses.get.200.failed",
            "GET",
            "/api/v1/analyses/{analysis_id}",
            200,
            "AnalysisJob",
            AnalysisJob(
                analysis_id=ANALYSIS_ID,
                status=AnalysisStatus.FAILED,
                progress=100,
                created_at=CREATED_AT,
                updated_at=UPDATED_AT,
                # Sağlayıcı hatası ASENKRON: HTTP 200 döner, hata gövdede.
                error=build_problem(
                    ErrorCode.PROVIDER_RATE_LIMITED,
                    "OpenRouter istek sınırı aşıldı.",
                ),
            ),
        ),
        Case(
            "analyses.get.200.cancelled",
            "GET",
            "/api/v1/analyses/{analysis_id}",
            200,
            "AnalysisJob",
            AnalysisJob(
                analysis_id=ANALYSIS_ID,
                status=AnalysisStatus.CANCELLED,
                progress=41,
                created_at=CREATED_AT,
                updated_at=UPDATED_AT,
            ),
        ),
        Case("analyses.delete.204", "DELETE", "/api/v1/analyses/{analysis_id}", 204, None, None),
        Case(
            "analyses.result.200",
            "GET",
            "/api/v1/analyses/{analysis_id}/result",
            200,
            "AnalysisReport",
            build_report(top_n=8),
        ),
        # Tek başına ADR-0002 #2 ve #5'in çalıştırılabilir spesifikasyonu.
        Case(
            "analyses.result.200.truncated",
            "GET",
            "/api/v1/analyses/{analysis_id}/result",
            200,
            "AnalysisReport",
            build_report(
                top_n=5,
                sheet_rows=SHEET_ROWS_OVER_LIMIT,
                warnings=[row_limit_warning(SHEET_ROWS_OVER_LIMIT)],
            ),
        ),
    ]

    # Hata fixture'ları kayıttan türetilir: yeni bir ErrorCode eklendiğinde
    # fixture'ı da kendiliğinden gelir.
    for code in ErrorCode:
        cases.append(
            Case(
                f"errors.{code.value.lower().replace('_', '-')}.{ERROR_STATUS[code]}",
                "ANY",
                "*",
                ERROR_STATUS[code],
                "ProblemDetails",
                build_problem(code, f"{ERROR_TITLES[code]} (örnek gövde)."),
            )
        )

    # Alan adı OLMAYAN doğrulama hatası: `field: null` iki tarafta da geçerli.
    # Gövdesiz bir POST, Pydantic'te tam olarak bu durumu üretir.
    cases.append(
        Case(
            "errors.request-validation.422.no-field",
            "POST",
            "/api/v1/analyses",
            422,
            "ProblemDetails",
            build_problem(
                ErrorCode.REQUEST_VALIDATION,
                "İstek gövdesi veya parametreleri doğrulanamadı.",
                errors=[
                    ErrorItem(message="Field required"),
                    ErrorItem(field="top_n", message="Input should be greater than or equal to 1"),
                ],
            ),
        )
    )

    return cases


#: Kısıt tablosu — fixture doğrulaması ve enum parity'nin göremediği tek şey
#: sınır değerlerinin ayrışmasıdır (backend `le=100` vs frontend `.max(100)`).
#:
#: Datetime formatı BİLEREK burada değil: Pydantic girişte "+03:00" kabul
#: ederken Zod reddediyor, yani ortak bir satır Python tarafında düşerdi.
CONSTRAINT_CASES: list[dict[str, Any]] = [
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "top_n",
        "value": 0,
        "valid": False,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "top_n",
        "value": 1,
        "valid": True,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "top_n",
        "value": 100,
        "valid": True,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "top_n",
        "value": 101,
        "valid": False,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "max_cost_usd",
        "value": 0,
        "valid": False,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "max_cost_usd",
        "value": 0.01,
        "valid": True,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "max_cost_usd",
        "value": 100,
        "valid": True,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "max_cost_usd",
        "value": 100.01,
        "valid": False,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "sheet_name",
        "value": "",
        "valid": False,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "text_column",
        "value": "",
        "valid": False,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "model",
        "value": "",
        "valid": False,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "prompt_version",
        "value": "",
        "valid": False,
    },
    {
        "model": "AnalysisRequest",
        "base": "analyses.request",
        "field": "upload_id",
        "value": "not-a-uuid",
        "valid": False,
    },
    {
        "model": "AnalysisJob",
        "base": "analyses.get.200.analyzing",
        "field": "progress",
        "value": 101,
        "valid": False,
    },
    {
        "model": "AnalysisJob",
        "base": "analyses.get.200.analyzing",
        "field": "progress",
        "value": 100,
        "valid": True,
    },
    {
        "model": "AnalysisJob",
        "base": "analyses.get.200.analyzing",
        "field": "progress",
        "value": -1,
        "valid": False,
    },
    {
        "model": "AnalysisJob",
        "base": "analyses.get.200.analyzing",
        "field": "status",
        "value": "bilinmeyen",
        "valid": False,
    },
    {
        "model": "AnalysisJob",
        "base": "analyses.get.200.analyzing",
        "field": "estimated_seconds_remaining",
        "value": -1,
        "valid": False,
    },
    {
        "model": "AnalysisJob",
        "base": "analyses.get.200.analyzing",
        "field": "estimated_seconds_remaining",
        "value": None,
        "valid": True,
    },
    {
        "model": "Upload",
        "base": "uploads.get.200.ready",
        "field": "size_bytes",
        "value": -1,
        "valid": False,
    },
    {
        "model": "Upload",
        "base": "uploads.get.200.ready",
        "field": "status",
        "value": "bilinmeyen",
        "valid": False,
    },
]
