"""Güvenli .xlsx doğrulama ve profilleme testleri (plan §3.3).

Bu testler altyapı GEREKTİRMEZ: `services/xlsx.py` bilinçli olarak saf
tutuldu. Faz 1'in en değerli testleri bunlar, çünkü tehdit modelinin
tamamını burada zorlayabiliyoruz.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.xlsx import (
    XlsxRejectedError,
    profile_xlsx,
    validate_and_profile,
    validate_xlsx,
)
from tests import factories

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings() -> Settings:
    return Settings()


# ------------------------------------------------------------ reddedilenler


def test_bos_dosya_reddedilir(settings: Settings) -> None:
    with pytest.raises(XlsxRejectedError) as exc:
        validate_xlsx(FIXTURES / "empty.xlsx", settings)
    assert exc.value.reason == "empty_file"


def test_sifreli_dosya_reddedilir(settings: Settings) -> None:
    """OLE2 kabı — parola korumalı xlsx ve eski .xls bu imzayı taşır."""
    with pytest.raises(XlsxRejectedError) as exc:
        validate_xlsx(FIXTURES / "encrypted.xlsx", settings)
    assert exc.value.reason == "encrypted_or_legacy_ole2_container"


def test_bozuk_zip_reddedilir(settings: Settings) -> None:
    """Magic bytes'ı GEÇEN ama zip dizini okunamayan dosya."""
    with pytest.raises(XlsxRejectedError):
        validate_xlsx(FIXTURES / "corrupt.xlsx", settings)


def test_makrolu_dosya_reddedilir(settings: Settings) -> None:
    """Uzantı .xlsx olsa bile xl/vbaProject.bin varsa reddedilir (ADR §9)."""
    with pytest.raises(XlsxRejectedError) as exc:
        validate_xlsx(FIXTURES / "macro_enabled.xlsx", settings)
    assert exc.value.reason == "macro_enabled_workbook"


def test_ooxml_olmayan_zip_reddedilir(tmp_path: Path, settings: Settings) -> None:
    """Geçerli bir zip ama Excel dosyası değil."""
    import zipfile

    path = tmp_path / "notxlsx.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hello.txt", "merhaba")

    with pytest.raises(XlsxRejectedError) as exc:
        validate_xlsx(path, settings)
    assert exc.value.reason == "missing_ooxml_content_types"


# ---------------------------------------------------------------- ZIP bomba


def test_zip_bombasi_reddedilir(tmp_path: Path) -> None:
    """Açılmış boyut sınırını aşan arşiv reddedilir.

    Sınır test için küçültülüyor; 1 GB'lık gerçek bir bomba üretmek testi
    dakikalarca sürdürürdü. Kontrol edilen mantık aynı.
    """
    settings = Settings(max_uncompressed_bytes=8 * 1024 * 1024)
    bomb = factories.build_zip_bomb(tmp_path / "bomb.xlsx", uncompressed_bytes=64 * 1024 * 1024)

    with pytest.raises(XlsxRejectedError) as exc:
        validate_xlsx(bomb, settings)
    assert "uncompressed_size_exceeds_limit" in exc.value.reason


def test_yalan_boyut_beyan_eden_zip_reddedilir(tmp_path: Path) -> None:
    """Beyan edilen boyut küçük, gerçek boyut büyük.

    BU TESTİN ANLAMI: yalnızca `ZipInfo.file_size` toplamına bakan bir bomba
    kontrolü bu dosyayı GEÇİRİR. Reddin gelmesi, açılan baytları sayan ikinci
    katmanın gerçekten çalıştığını kanıtlar.
    """
    settings = Settings(max_uncompressed_bytes=8 * 1024 * 1024)
    lying = factories.build_lying_zip(tmp_path / "lying.xlsx")

    with pytest.raises(XlsxRejectedError):
        validate_xlsx(lying, settings)


def test_sikistirma_orani_sinirlanir(tmp_path: Path) -> None:
    """Tek bir üyenin sıkıştırma oranı sınırı aşarsa reddedilir."""
    settings = Settings(max_uncompressed_bytes=1024 * 1024 * 1024, max_compression_ratio=5.0)
    bomb = factories.build_zip_bomb(tmp_path / "ratio.xlsx", uncompressed_bytes=16 * 1024 * 1024)

    with pytest.raises(XlsxRejectedError) as exc:
        validate_xlsx(bomb, settings)
    assert exc.value.reason == "compression_ratio_exceeds_limit"


# ------------------------------------------------------------------ profil


def test_gecerli_dosya_dogrulanir(settings: Settings) -> None:
    uncompressed = validate_xlsx(FIXTURES / "valid_multi_sheet.xlsx", settings)
    assert uncompressed > 0


def test_profil_sayfalari_ve_kolonlari_cikarir(settings: Settings) -> None:
    profile = profile_xlsx(FIXTURES / "valid_multi_sheet.xlsx", settings)

    sheet_names = [sheet["name"] for sheet in profile["sheets"]]
    assert sheet_names == ["Mesajlar", "Ham Veri", "Iletisim"]

    messages = profile["sheets"][0]
    assert messages["row_count"] == 40
    assert messages["column_count"] == 4
    assert [column["name"] for column in messages["columns"]] == [
        "tarih",
        "kullanici_id",
        "mesaj",
        "kanal",
    ]

    # index 0 TABANLI olmalı — frontend `z.int().nonnegative()` bekliyor ve
    # kolon seçim ekranı sırayı buna göre gösteriyor.
    assert [column["index"] for column in messages["columns"]] == [0, 1, 2, 3]


def test_metin_kolonu_sezgiseli(settings: Settings) -> None:
    """Serbest metin kolonu işaretlenmeli; tarih/sayı/kategori kolonları değil."""
    profile = profile_xlsx(FIXTURES / "valid_multi_sheet.xlsx", settings)
    columns = {c["name"]: c for c in profile["sheets"][0]["columns"]}

    assert columns["mesaj"]["is_likely_text"] is True
    assert columns["tarih"]["is_likely_text"] is False
    assert columns["kullanici_id"]["is_likely_text"] is False
    # "web"/"mobil" — metin ama serbest metin değil; ortalama uzunluk eler.
    assert columns["kanal"]["is_likely_text"] is False


def test_kolon_sayaclari_tutarli(settings: Settings) -> None:
    profile = profile_xlsx(FIXTURES / "valid_multi_sheet.xlsx", settings)
    sheet = profile["sheets"][0]

    for column in sheet["columns"]:
        assert column["non_empty_count"] + column["empty_count"] == sheet["row_count"]
        assert column["unique_count"] <= column["non_empty_count"]
        assert column["avg_length"] >= 0


def test_ornek_degerler_redakte_edilir(settings: Settings) -> None:
    """ADR §9: kolon seçim ekranında ham öğrenci verisi gösterilmez."""
    profile = profile_xlsx(FIXTURES / "valid_multi_sheet.xlsx", settings)
    iletisim = next(s for s in profile["sheets"] if s["name"] == "Iletisim")
    samples = iletisim["columns"][0]["sample_values"]

    joined = " ".join(samples)
    assert "ali@example.com" not in joined
    assert "05551234567" not in joined
    assert "12345678901" not in joined
    assert "[EPOSTA]" in joined
    assert "[TELEFON]" in joined
    assert "[KIMLIK]" in joined


def test_ornek_degerler_kirpilir(settings: Settings) -> None:
    tight = Settings(sample_value_max_length=20)
    profile = profile_xlsx(FIXTURES / "valid_multi_sheet.xlsx", tight)

    for sheet in profile["sheets"]:
        for column in sheet["columns"]:
            for sample in column["sample_values"]:
                assert len(sample) <= 20


def test_ornek_deger_sayisi_sinirli(settings: Settings) -> None:
    profile = profile_xlsx(FIXTURES / "valid_multi_sheet.xlsx", settings)
    for sheet in profile["sheets"]:
        for column in sheet["columns"]:
            assert len(column["sample_values"]) <= settings.sample_values_per_column


def test_satir_siniri_isaretlenir_ama_basarisiz_olmaz(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan §3.2 (g): sınır aşılırsa iş BAŞARISIZ OLMAZ, yalnızca işaretlenir."""
    monkeypatch.setattr("app.services.xlsx.MAX_ROWS", 5)
    profile = validate_and_profile(FIXTURES / "valid_multi_sheet.xlsx", settings)

    assert profile["exceeds_row_limit"] is True
    assert profile["total_row_count"] > 5
    assert len(profile["sheets"]) == 3


def test_satir_siniri_asilmadiginda_isaretlenmez(settings: Settings) -> None:
    profile = validate_and_profile(FIXTURES / "valid_multi_sheet.xlsx", settings)
    assert profile["exceeds_row_limit"] is False


def test_profil_sozlesme_semasina_uyar(settings: Settings) -> None:
    """Profil çıktısı doğrudan Pydantic şemasına oturmalı."""
    from app.schemas.upload import UploadProfile

    profile = validate_and_profile(FIXTURES / "valid_multi_sheet.xlsx", settings)
    parsed = UploadProfile.model_validate(profile)

    assert parsed.total_row_count == profile["total_row_count"]
    assert len(parsed.sheets) == 3


def test_tarama_tavani_asildiginda_profil_hala_semaya_uyar(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tavan aşıldığında profil KENDİ İÇİNDE tutarlı kalmalı.

    REGRESYON: tavan `continue` ile uygulanıyordu, yani kolon istatistikleri
    ilk N satırı sayarken `row_count` dosyanın tamamını sayıyordu. Sonuç
    `UploadProfile`'ın değişmezini (`non_empty_count + empty_count ==
    row_count`) ihlal ediyor, Pydantic patlıyor ve kullanıcı tavanı aşan HER
    dosyada `INTERNAL_ERROR` alıyordu — oysa ADR-0002 #13 dosyanın
    profillenmesini ve yalnızca işaretlenmesini şart koşuyor.

    Bu 2,8 M satırlık gerçek bir dosyada bulundu (ADR §10 risk 1 yük testi);
    burada tavan küçültülerek aynı kod yolu saniyeler içinde zorlanıyor.
    """
    from app.schemas.upload import UploadProfile

    scan_cap = 4
    monkeypatch.setattr(settings, "profile_max_scan_rows", scan_cap)

    profile = validate_and_profile(FIXTURES / "valid_multi_sheet.xlsx", settings)
    parsed = UploadProfile.model_validate(profile)

    for sheet in parsed.sheets:
        assert sheet.row_count <= scan_cap
        for column in sheet.columns:
            assert column.non_empty_count + column.empty_count == sheet.row_count


def test_tarama_tavani_satir_sinirinin_altina_indirilemez() -> None:
    """Tavan `MAX_ROWS`'un altına düşerse `exceeds_row_limit` yalan söylerdi.

    Profil tavanda kesildiği için `row_count` tavana eşit kalır; tavan satır
    sınırının altındaysa sınır aşımı HİÇ işaretlenmez ve kullanıcı kırpılmış
    bir analizi tam sanır. Config bu yüzden fail-fast doğruluyor.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, profile_max_scan_rows=1000)
