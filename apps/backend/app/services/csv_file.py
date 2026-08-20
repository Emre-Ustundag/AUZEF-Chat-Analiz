"""Güvenli `.csv` doğrulama, profilleme ve akışlı okuma.

`services/xlsx.py`'nin CSV karşılığıdır ve aynı sözleşmeyi üretir: profil
çıktısı `UploadProfile` şemasıyla birebir uyumludur ve tek bir sanal sayfa
(`CSV_SHEET_NAME`) içerir. Böylece upload/analiz akışının geri kalanı dosya
biçimini bilmek zorunda kalmaz — sayfa/kolon seçimi, satır sınırı ve
`SHEET_OR_COLUMN_NOT_FOUND` davranışı iki biçimde de aynıdır.

Tehdit modeli — buraya gelen dosya GÜVENİLMEYEN kullanıcı girdisidir:

* Uzantısı `.csv` yapılmış binary dosya: zip/OLE2 imzaları ve NUL baytları
  reddedilir; kalan içerik desteklenen kodlamalardan biriyle STRICT olarak
  çözülemiyorsa dosya reddedilir.
* Bellek: dosya asla topluca belleğe alınmaz. Kodlama/ayraç tespiti sınırlı
  bir örnek üzerinde yapılır, satırlar `csv.reader` ile akışlı okunur ve
  aşırı büyük tek bir alan `csv` modülünün alan sınırıyla kesilir.

Kodlama gerçek dünyaya göre seçilir (plan B1): Excel/BI export'ları çoğunlukla
UTF-8 BOM (`utf-8-sig`) yazar; eski kurumsal sistemler `cp1254`/ISO-8859-9
üretebilir. Sıra: BOM varsa `utf-8-sig`, yoksa strict `utf-8`, o da düşerse
`cp1254`. Ayraç, başlık satırında tırnak dışı sayımla `,`/`;`/tab
arasından seçilir.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, TextIO

from app.core.config import MAX_ROWS, Settings
from app.core.logging import get_logger
from app.services.xlsx import (
    OLE2_MAGIC,
    ZIP_EMPTY_MAGIC,
    ZIP_MAGIC,
    ColumnStats,
    SheetOrColumnNotFoundError,
    _column_name,
)

logger = get_logger(__name__)

#: CSV'nin sayfası yoktur; profil bu sabit adla TEK sanal sayfa üretir ve
#: analiz isteğindeki `sheet_name` bu ada eşit olmak zorundadır.
CSV_SHEET_NAME = "CSV"

#: UTF-8 BOM — `utf-8-sig` ile çözülür (plan B1: gerçek dataset böyle geliyor).
UTF8_BOM = b"\xef\xbb\xbf"

#: Kodlama/ayraç tespiti için okunan örnek. Tespit İÇİN yeterli, 130 MB'lık
#: dosyayı belleğe almayacak kadar küçük.
_SNIFF_BYTES = 256 * 1024

#: BOM'suz dosyalarda sırayla denenen kodlamalar.
_ENCODING_FALLBACKS = ("utf-8", "cp1254")

#: Desteklenen ayraçlar.
_DELIMITERS = (",", ";", "\t")

#: Tek bir hücre için üst sınır. `csv` modülünün varsayılanı 128 KB ve aşımı
#: `csv.Error` üretiyor; sınırı açıkça sabitliyoruz ki davranış Python
#: sürümüne göre değişmesin.
_FIELD_SIZE_LIMIT = 1 * 1024 * 1024


class CsvRejectedError(Exception):
    """Dosya güvenlik veya biçim kontrolünden geçemedi.

    Çağıran bunu `UPLOAD_CORRUPT_OR_ENCRYPTED` (422) hatasına çevirir —
    `XlsxRejectedError` ile aynı sözleşme. `reason` yalnızca log ve test için.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _read_sample(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(_SNIFF_BYTES)
    except OSError as exc:
        raise CsvRejectedError("unreadable_file") from exc


def detect_encoding(sample: bytes) -> str:
    """Örnek baytlardan kodlamayı seçer; metin değilse reddeder."""
    if sample.startswith((ZIP_MAGIC, ZIP_EMPTY_MAGIC)):
        # Uzantısı değiştirilmiş bir OOXML/zip; CSV olarak okunamaz.
        raise CsvRejectedError("zip_container_named_csv")
    if sample.startswith(OLE2_MAGIC):
        raise CsvRejectedError("ole2_container_named_csv")
    if b"\x00" in sample:
        # Hiçbir desteklenen metin kodlaması NUL üretmez (UTF-16 desteklenmiyor).
        raise CsvRejectedError("binary_content")

    if sample.startswith(UTF8_BOM):
        return "utf-8-sig"

    for encoding in _ENCODING_FALLBACKS:
        # Örnek çok baytlı bir UTF-8 dizisinin ortasında bitmiş olabilir;
        # sondaki 3 bayta kadar kırparak yanlış negatif engellenir.
        for trim in range(4):
            view = sample[: len(sample) - trim] if trim else sample
            try:
                view.decode(encoding)
            except UnicodeDecodeError:
                continue
            return encoding

    raise CsvRejectedError("undecodable_text")


def detect_delimiter(header_line: str) -> str:
    """Başlık satırındaki tırnak DIŞI sayıma göre ayracı seçer.

    Hiçbir aday bulunamazsa virgül varsayılır: tek kolonlu bir CSV geçerli
    bir dosyadır ve ayraç içermez.
    """
    counts = dict.fromkeys(_DELIMITERS, 0)
    in_quotes = False
    for char in header_line:
        if char == '"':
            in_quotes = not in_quotes
        elif not in_quotes and char in counts:
            counts[char] += 1

    best = max(_DELIMITERS, key=lambda candidate: counts[candidate])
    return best if counts[best] > 0 else ","


def sniff_csv(path: Path) -> tuple[str, str]:
    """(encoding, delimiter) döndürür; dosya CSV değilse reddeder."""
    if not path.exists() or path.stat().st_size == 0:
        raise CsvRejectedError("empty_file")

    sample = _read_sample(path)
    encoding = detect_encoding(sample)

    text = sample.decode(encoding, errors="ignore")
    header_line = text.splitlines()[0] if text.splitlines() else ""
    if not header_line.strip():
        raise CsvRejectedError("missing_header")

    return encoding, detect_delimiter(header_line)


def _open_reader(path: Path, encoding: str, delimiter: str) -> tuple[TextIO, Any]:
    csv.field_size_limit(_FIELD_SIZE_LIMIT)
    # newline="": satır sonu çevirisini csv modülü yapar (alan içi \n korunur).
    handle = path.open("r", encoding=encoding, newline="")
    return handle, csv.reader(handle, delimiter=delimiter)


def _is_blank(row: Sequence[str]) -> bool:
    return all(not cell.strip() for cell in row)


def validate_csv(path: Path, settings: Settings) -> tuple[str, str]:
    """Dosyayı doğrular ve (encoding, delimiter) döndürür.

    XLSX'in aksine sıkıştırma yoktur; boyut savunması upload sınırında zaten
    yapıldı. Burada doğrulanan şey dosyanın gerçekten çözümlenebilir bir CSV
    olduğudur: kodlama, başlık satırı ve ilk satırların parse edilebilirliği.
    """
    encoding, delimiter = sniff_csv(path)

    # İlk birkaç satırı gerçekten parse ederek erken ve ucuz bir kontrol:
    # bozuk tırnaklama veya devasa tek alan burada `csv.Error` üretir.
    handle, reader = _open_reader(path, encoding, delimiter)
    try:
        for _ in range(50):
            if next(reader, None) is None:
                break
    except (csv.Error, UnicodeDecodeError) as exc:
        raise CsvRejectedError("csv_parse_failed") from exc
    finally:
        handle.close()

    return encoding, delimiter


def profile_csv(path: Path, settings: Settings) -> dict[str, Any]:
    """Doğrulanmış bir CSV'den `UploadProfile` uyumlu profil çıkarır.

    Kurallar `services/xlsx.py::_profile_sheet` ile AYNIDIR: ilk satır
    başlıktır, tamamen boş satırlar sayılmaz, tarama tavanı `break` ile
    uygulanır ve boş başlıklar `Kolon N` olur. İki biçimin farklı sayması,
    kullanıcının arayüzde gördüğü profil ile analizin okuduğu verinin
    ayrışması demekti.
    """
    encoding, delimiter = sniff_csv(path)

    columns: list[ColumnStats] = []
    row_count = 0
    header_seen = False

    handle, reader = _open_reader(path, encoding, delimiter)
    try:
        for row in reader:
            if not header_seen:
                for index, value in enumerate(row):
                    columns.append(ColumnStats(name=_column_name(value, index), index=index))
                header_seen = True
                continue

            if row_count >= settings.profile_max_scan_rows:
                # xlsx ile aynı gerekçe: `break`, `continue` DEĞİL — profil
                # yalnızca gerçekten taranmış satırları anlatmalı.
                break

            if _is_blank(row):
                continue

            row_count += 1
            for index, value in enumerate(row):
                if index >= len(columns):
                    columns.append(ColumnStats(name=f"Kolon {index + 1}", index=index))
                columns[index].observe(value if value.strip() else None, settings=settings)
    except (csv.Error, UnicodeDecodeError) as exc:
        raise CsvRejectedError("csv_parse_failed") from exc
    finally:
        handle.close()

    if not header_seen:
        raise CsvRejectedError("missing_header")

    return {
        "sheets": [
            {
                "name": CSV_SHEET_NAME,
                "row_count": row_count,
                "column_count": len(columns),
                "columns": [
                    {
                        "name": column.name,
                        "index": column.index,
                        "non_empty_count": column.non_empty_count,
                        "empty_count": column.empty_count,
                        "unique_count": column.unique_count,
                        "avg_length": column.avg_length,
                        "is_likely_text": column.is_likely_text,
                        "sample_values": column.samples,
                    }
                    for column in columns
                ],
            }
        ],
        "total_row_count": row_count,
        "exceeds_row_limit": row_count > MAX_ROWS,
    }


def iter_row_values(
    path: Path,
    sheet_name: str,
    columns: Sequence[str],
) -> Iterator[tuple[str | None, ...]]:
    """Seçilen kolonların hücrelerini SATIR SATIR, istenen sırada üretir.

    `services/xlsx.py::iter_row_values` ile aynı sözleşme: ilk satır başlıktır,
    tamamen boş satırlar atlanır, boş hücreler `None` olarak üretilir ve
    bilinmeyen sayfa/kolon `SheetOrColumnNotFoundError` fırlatır.
    """
    if sheet_name != CSV_SHEET_NAME:
        raise SheetOrColumnNotFoundError("sheet_not_found")

    encoding, delimiter = sniff_csv(path)
    handle, reader = _open_reader(path, encoding, delimiter)
    try:
        header = next(reader, None)
        if header is None:
            raise SheetOrColumnNotFoundError("column_not_found")

        names = [_column_name(value, index) for index, value in enumerate(header)]
        try:
            indexes = [names.index(column) for column in columns]
        except ValueError:
            raise SheetOrColumnNotFoundError("column_not_found") from None

        for row in reader:
            if _is_blank(row):
                continue
            # Boş hücre CSV'de "" olarak gelir; xlsx'in `None` hücresine denk
            # sayılır ki `discarded_count` iki biçimde aynı çalışsın.
            yield tuple(
                row[index] if index < len(row) and row[index] != "" else None for index in indexes
            )
    except (csv.Error, UnicodeDecodeError) as exc:
        raise CsvRejectedError("csv_parse_failed") from exc
    finally:
        handle.close()


def iter_column_values(
    path: Path,
    sheet_name: str,
    text_column: str,
) -> Iterator[str | None]:
    """Tek kolonluk kısayol; `iter_row_values` üzerinden aynı kuralları uygular."""
    for row in iter_row_values(path, sheet_name, [text_column]):
        yield row[0]


def validate_and_profile(path: Path, settings: Settings) -> dict[str, Any]:
    """Doğrulama + profilleme. Worker'ın çağırdığı tek giriş noktası."""
    encoding, delimiter = validate_csv(path, settings)
    logger.info(
        "csv_validated",
        extra={"encoding": encoding, "delimiter": repr(delimiter)},
    )
    return profile_csv(path, settings)
