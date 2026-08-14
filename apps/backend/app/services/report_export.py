"""Rapor dışa aktarma — plan §4 "Faz 4", ADR §6.

`GET /api/v1/analyses/{id}/export?format=xlsx|json` gövdesini burası üretir.

## Sayılar METİN OLARAK YAZILMAZ

Bu modülün en önemli kuralı. `f"{value:.1f}".replace(".", ",")` gibi bir
Türkçe biçimleme cazip görünür ama hücreyi sayı olmaktan çıkarır: Excel'de
toplama, sıralama, grafik ve pivot çalışmaz, hücrenin köşesinde yeşil
"sayı olarak saklanmış metin" uyarısı çıkar. Adet/oran/güven alanları ham
`int` ve `float` olarak yazılır; biçimlendirmeyi Excel kendi yerelinde
yapar. Oranlar rapordaki değerin AYNISIDIR (0-100 aralığı), yeniden
hesaplanmaz — ADR §4: sayıları backend deterministik üretir, hiçbir
tüketici onları türetmez.

## Örnekler redakte edilmiş gelir

`redacted_examples` alanı `pipeline/aggregate.py`'de zaten maskelenmiş ve
kırpılmış olarak üretiliyor (ADR §9). Bu modül ham mesaja HİÇ erişmez;
elindeki tek kaynak rapor gövdesidir.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.schemas.common import to_iso_z
from app.schemas.report import AnalysisReport

#: xlsx MIME türü. Yanlış tür verilirse tarayıcı dosyayı zip sanıp açmaya
#: çalışır veya Excel "bozuk dosya" uyarısı gösterir.
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

JSON_MEDIA_TYPE = "application/json"

#: Sayfa adları. Excel sayfa adı 31 karakterle sınırlı ve `[]:*?/\` kabul
#: etmiyor; kısa ve sabit tutuluyor.
SHEET_SUMMARY = "Özet"
SHEET_QUESTIONS = "Sorular"
SHEET_THEMES = "Temalar"

_HEADER_FONT = Font(bold=True)

#: Örnek mesajlar tek hücrede alt alta durur. Ayrı kolonlara yaymak, örnek
#: sayısı config'e bağlı olduğu için kolon şemasını değişken yapardı.
_EXAMPLE_SEPARATOR = "\n"


def content_disposition(analysis_id: str, extension: str) -> str:
    """`Content-Disposition` başlığı üretir.

    Dosya adı KULLANICININ yüklediği addan türetilmez; analiz kimliğinden
    üretilir ve ASCII kalır. İki sebebi var: (1) frontend'in
    `filenameFromContentDisposition` fonksiyonu yalnızca düz
    `filename="..."` biçimini ayrıştırıyor, RFC 5987'nin `filename*`
    kodlamasını değil — Türkçe karakterli bir dosya adı orada bozulurdu;
    (2) kullanıcı dosya adı denetimli değil, başlığa tırnak veya satır sonu
    enjekte edilebilirdi.
    """
    return f'attachment; filename="analiz-{analysis_id}.{extension}"'


def _write_header(sheet: Worksheet, headers: list[str]) -> None:
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = _HEADER_FONT
    sheet.freeze_panes = "A2"


def _autosize(sheet: Worksheet, widths: list[int]) -> None:
    """Kolon genişliklerini sabitler.

    Otomatik ölçüm yapılmıyor: tüm satırları gezmek 100.000 kayıtlık bir
    raporda pahalı ve kazancı kozmetik.
    """
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _build_summary_sheet(sheet: Worksheet, report: AnalysisReport) -> None:
    """Analizin künyesi: kaynak, ön işleme sayaçları, model ve maliyet.

    Sayaçlar ham sayı olarak yazılır; "Analiz edilen" ile "Elenen"in
    toplamı Excel'de doğrudan `SUM` ile doğrulanabilsin.
    """
    _write_header(sheet, ["Alan", "Değer"])

    source = report.source_summary
    pre = report.preprocessing_summary

    rows: list[tuple[str, str | int | float]] = [
        ("Analiz kimliği", str(report.analysis_id)),
        ("Oluşturulma", to_iso_z(report.generated_at)),
        ("Dosya", source.filename),
        ("Sayfa", source.sheet_name),
        ("Kolon", source.text_column),
        ("Toplam satır", source.total_rows),
        ("Analiz edilen kayıt", pre.analyzed_count),
        ("Elenen kayıt", pre.discarded_count),
        ("Tekrar eden kayıt", pre.duplicate_count),
        ("Benzersiz kayıt", pre.unique_count),
        ("PII maskelenen kayıt", pre.redacted_count),
        ("Model", report.model),
        ("Prompt sürümü", report.prompt_version),
        ("Prompt özeti", report.prompt_hash),
        ("Prompt token", report.token_usage.prompt_tokens),
        ("Yanıt token", report.token_usage.completion_tokens),
        ("Toplam token", report.token_usage.total_tokens),
        ("Tahmini maliyet (USD)", report.estimated_cost_usd),
    ]
    for label, value in rows:
        sheet.append([label, value])

    sheet.append([])
    sheet.append(["Yönetici özeti", report.executive_summary])
    sheet.cell(row=sheet.max_row, column=2).alignment = Alignment(wrap_text=True, vertical="top")

    if report.warnings:
        sheet.append([])
        sheet.append(["Uyarı kodu", "Açıklama"])
        for warning in report.warnings:
            sheet.append([warning.code, warning.message])

    _autosize(sheet, [26, 80])


def _build_questions_sheet(sheet: Worksheet, report: AnalysisReport) -> None:
    _write_header(
        sheet,
        ["Kimlik", "Soru", "Adet", "Oran (%)", "Güven", "Örnek mesajlar (redakte)"],
    )
    for question in report.top_questions:
        sheet.append(
            [
                question.id,
                question.canonical_question,
                # ---- ham sayılar: `int` ve `float`, dize DEĞİL ----
                question.count,
                question.percentage,
                question.confidence,
                _EXAMPLE_SEPARATOR.join(question.redacted_examples),
            ]
        )
        sheet.cell(row=sheet.max_row, column=6).alignment = Alignment(
            wrap_text=True, vertical="top"
        )

    _autosize(sheet, [16, 60, 10, 12, 10, 70])


def _build_themes_sheet(sheet: Worksheet, report: AnalysisReport) -> None:
    _write_header(sheet, ["Kimlik", "Tema", "Adet", "Oran (%)", "İlgili soru kimlikleri"])
    for theme in report.themes:
        sheet.append(
            [
                theme.id,
                theme.name,
                theme.count,
                theme.percentage,
                # Plan §1.2: bu liste top_n kırpmasından SONRA filtrelenmiş
                # hâliyle gelir; tema `count`'u ise kırpmadan etkilenmez.
                ", ".join(theme.related_question_ids),
            ]
        )

    _autosize(sheet, [16, 40, 10, 12, 50])


def build_xlsx(report: AnalysisReport) -> bytes:
    """Raporu üç sayfalı bir `.xlsx` gövdesine çevirir.

    SENKRON ve CPU yoğun: çağıran taraf bunu bir thread'e taşımalı, aksi
    hâlde büyük bir raporun yazımı event loop'u bloklar.
    """
    workbook = Workbook()
    # `Workbook()` "Sheet" adlı boş bir sayfayla geliyor; onu yeniden
    # adlandırıp kullanıyoruz, yoksa dosyada bir boş sayfa kalırdı.
    summary = workbook.active
    assert summary is not None
    summary.title = SHEET_SUMMARY

    _build_summary_sheet(summary, report)
    _build_questions_sheet(workbook.create_sheet(SHEET_QUESTIONS), report)
    _build_themes_sheet(workbook.create_sheet(SHEET_THEMES), report)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
