"""`services/csv_file.py` — CSV doğrulama, profilleme ve akışlı okuma (B1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.schemas.upload import UploadProfile
from app.services.csv_file import (
    CSV_SHEET_NAME,
    CsvRejectedError,
    detect_delimiter,
    iter_column_values,
    iter_row_values,
    profile_csv,
    sniff_csv,
    validate_and_profile,
    validate_csv,
)
from app.services.xlsx import SheetOrColumnNotFoundError


@pytest.fixture
def settings() -> Settings:
    return Settings()


def _write(
    tmp_path: Path, content: str, *, encoding: str = "utf-8", name: str = "veri.csv"
) -> Path:
    path = tmp_path / name
    path.write_bytes(content.encode(encoding))
    return path


BASIC = "mesaj,kanal\nsınav ne zaman,web\nharç ödemesi,mobil\n"


# ------------------------------------------------------------------ kodlama


def test_utf8_bom_dosyasi_kabul_edilir(tmp_path: Path, settings: Settings) -> None:
    """Gerçek dökümler `utf-8-sig` geliyor; BOM başlık adına SIZMAMALI."""
    path = _write(tmp_path, "﻿" + BASIC)

    encoding, delimiter = validate_csv(path, settings)
    assert encoding == "utf-8-sig"
    assert delimiter == ","

    profile = profile_csv(path, settings)
    assert profile["sheets"][0]["columns"][0]["name"] == "mesaj"


def test_cp1254_fallback(tmp_path: Path, settings: Settings) -> None:
    """UTF-8 çözülemeyen Türkçe içerik cp1254 olarak okunur."""
    path = _write(tmp_path, "mesaj\nharç ödemesi ğüşiöç\n", encoding="cp1254")

    encoding, _ = validate_csv(path, settings)
    assert encoding == "cp1254"

    values = list(iter_column_values(path, CSV_SHEET_NAME, "mesaj"))
    assert values == ["harç ödemesi ğüşiöç"]


def test_binary_icerik_reddedilir(tmp_path: Path, settings: Settings) -> None:
    path = tmp_path / "veri.csv"
    path.write_bytes(b"mesaj\n\x00\x01\x02binary\n")

    with pytest.raises(CsvRejectedError) as excinfo:
        validate_csv(path, settings)
    assert excinfo.value.reason == "binary_content"


def test_csv_adiyla_zip_reddedilir(tmp_path: Path, settings: Settings) -> None:
    """Uzantısı `.csv` yapılmış bir xlsx/zip CSV olarak açılmamalı."""
    path = tmp_path / "veri.csv"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 32)

    with pytest.raises(CsvRejectedError) as excinfo:
        sniff_csv(path)
    assert excinfo.value.reason == "zip_container_named_csv"


def test_bos_dosya_reddedilir(tmp_path: Path, settings: Settings) -> None:
    path = tmp_path / "veri.csv"
    path.write_bytes(b"")

    with pytest.raises(CsvRejectedError) as excinfo:
        validate_csv(path, settings)
    assert excinfo.value.reason == "empty_file"


# -------------------------------------------------------------------- ayraç


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("a,b,c", ","),
        ("a;b;c", ";"),
        ("a\tb\tc", "\t"),
        # Tırnak İÇİNDEKİ ayraç sayılmaz: gerçek ayraç noktalı virgül.
        ('"a,b";c', ";"),
        # Ayraçsız tek kolon: virgül varsayılır.
        ("mesaj", ","),
    ],
)
def test_ayrac_tespiti(header: str, expected: str) -> None:
    assert detect_delimiter(header) == expected


def test_noktali_virgullu_dosya_profillenir(tmp_path: Path, settings: Settings) -> None:
    path = _write(tmp_path, "mesaj;kanal\nsınav ne zaman;web\n")

    profile = profile_csv(path, settings)
    sheet = profile["sheets"][0]
    assert [column["name"] for column in sheet["columns"]] == ["mesaj", "kanal"]
    assert sheet["row_count"] == 1


# -------------------------------------------------------------------- profil


def test_profil_upload_profile_semasina_uyar(tmp_path: Path, settings: Settings) -> None:
    """Profil, xlsx ile aynı sözleşmeyi üretmeli: `UploadProfile` doğrulanır."""
    content = 'mesaj,kanal,puan\nsınav ne zaman,web,5\n"çok, uzun bir soru",mobil,\n,,3\n'
    path = _write(tmp_path, content)

    profile = UploadProfile.model_validate(validate_and_profile(path, settings))

    assert profile.total_row_count == 3
    sheet = profile.sheets[0]
    assert sheet.name == CSV_SHEET_NAME
    assert sheet.row_count == 3
    mesaj = sheet.columns[0]
    assert mesaj.non_empty_count == 2
    assert mesaj.empty_count == 1
    # Tırnaklı alan tek hücre olarak okunmalı.
    assert any("çok, uzun" in sample for sample in mesaj.sample_values)


def test_tamamen_bos_satirlar_sayilmaz(tmp_path: Path, settings: Settings) -> None:
    path = _write(tmp_path, "mesaj,kanal\n,\n\nsoru,web\n")

    profile = profile_csv(path, settings)
    assert profile["sheets"][0]["row_count"] == 1


def test_basliksiz_dosya_reddedilir(tmp_path: Path, settings: Settings) -> None:
    path = _write(tmp_path, "\n\n")

    with pytest.raises(CsvRejectedError):
        validate_csv(path, settings)


# ------------------------------------------------------------------- okuma


def test_iter_row_values_coklu_kolon(tmp_path: Path) -> None:
    content = (
        "message_text_clean,direction,message_type\n"
        "sınav ne zaman,Kullanıcı,text\n"
        "Menüden seçin,Bot,single-choice\n"
        ",Kullanıcı,text\n"
    )
    path = _write(tmp_path, content)

    rows = list(
        iter_row_values(path, CSV_SHEET_NAME, ["message_text_clean", "direction", "message_type"])
    )
    assert rows == [
        ("sınav ne zaman", "Kullanıcı", "text"),
        ("Menüden seçin", "Bot", "single-choice"),
        (None, "Kullanıcı", "text"),
    ]


def test_bilinmeyen_sayfa_ve_kolon(tmp_path: Path) -> None:
    path = _write(tmp_path, BASIC)

    with pytest.raises(SheetOrColumnNotFoundError):
        list(iter_row_values(path, "Mesajlar", ["mesaj"]))
    with pytest.raises(SheetOrColumnNotFoundError):
        list(iter_row_values(path, CSV_SHEET_NAME, ["yok"]))
