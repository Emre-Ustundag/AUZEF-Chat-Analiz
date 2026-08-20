"""Güvenli .xlsx doğrulama ve profilleme testleri (plan §3.3).

Bu testler altyapı GEREKTİRMEZ: `services/xlsx.py` bilinçli olarak saf
tutuldu. Faz 1'in en değerli testleri bunlar, çünkü tehdit modelinin
tamamını burada zorlayabiliyoruz.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.schemas.analysis import ConversationConfig
from app.services.xlsx import (
    SheetOrColumnNotFoundError,
    XlsxRejectedError,
    iter_column_values,
    iter_conversation_rows,
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


def test_satir_filtresi_eslesmeyenleri_none_olarak_korur() -> None:
    values = list(
        iter_column_values(
            FIXTURES / "valid_multi_sheet.xlsx",
            "Mesajlar",
            "mesaj",
            {"kanal": frozenset({"web"})},
        )
    )

    # Dosyanın 40 satırı da sayılır; 20 mobil satır filtre tarafından
    # elenir ve None olur. Atılsaydı rapor toplamı/değişmezi bozulurdu.
    assert len(values) == 40
    assert sum(value is not None for value in values) == 20
    assert sum(value is None for value in values) == 20


def test_birden_fazla_satir_filtresi_and_uygular() -> None:
    first_message = "sınav tarihleri ne zaman açıklanacak acaba bilgi alabilir miyim"
    values = list(
        iter_column_values(
            FIXTURES / "valid_multi_sheet.xlsx",
            "Mesajlar",
            "mesaj",
            {
                "kanal": frozenset({"web"}),
                "mesaj": frozenset({first_message}),
            },
        )
    )

    assert len(values) == 40
    assert [value for value in values if value is not None] == [first_message] * 4


def test_bilinmeyen_filtre_kolonu_reddedilir() -> None:
    with pytest.raises(SheetOrColumnNotFoundError) as exc_info:
        list(
            iter_column_values(
                FIXTURES / "valid_multi_sheet.xlsx",
                "Mesajlar",
                "mesaj",
                {"olmayan": frozenset({"x"})},
            )
        )

    assert exc_info.value.reason == "filter_column_not_found"


def test_konusma_kolonlari_birlikte_ve_satir_sirasiyla_okunur(tmp_path: Path) -> None:
    from openpyxl import Workbook

    path = tmp_path / "conversation.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Chat"
    sheet.append(["session_id", "message_order", "direction", "message_type", "message", "kanal"])
    sheet.append(["s1", 1, "Bot", "text", "Size nasıl yardımcı olabilirim?", "web"])
    sheet.append(["s1", 2, "Kullanıcı", "text", "Sınav ne zaman?", "web"])
    sheet.append(["s2", 1, "Kullanıcı", "text", "Harç nasıl ödenir?", "mobil"])
    workbook.save(path)

    config = ConversationConfig(
        session_id_column="session_id",
        message_order_column="message_order",
        role_column="direction",
        message_type_column="message_type",
    )
    rows = list(
        iter_conversation_rows(
            path,
            "Chat",
            "message",
            config,
            {"kanal": frozenset({"web"})},
        )
    )

    assert len(rows) == 3
    assert rows[0].source_row == 2
    assert rows[0].session_id == "s1"
    assert rows[0].message_order == "1"
    assert rows[1].role == "Kullanıcı"
    assert rows[1].text == "Sınav ne zaman?"
    # Filtre dışı satır toplam/ilerleme için korunur ama bağlama alınmaz.
    assert rows[2].included is False


def test_konusma_esleme_kolonu_yoksa_reddedilir(tmp_path: Path) -> None:
    from openpyxl import Workbook

    path = tmp_path / "missing-column.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Chat"
    sheet.append(["session_id", "direction", "message_type", "message"])
    sheet.append(["s1", "Kullanıcı", "text", "Sınav ne zaman?"])
    workbook.save(path)

    config = ConversationConfig(
        session_id_column="session_id",
        message_order_column="message_order",
        role_column="direction",
        message_type_column="message_type",
    )
    with pytest.raises(SheetOrColumnNotFoundError) as exc_info:
        list(iter_conversation_rows(path, "Chat", "message", config))

    assert exc_info.value.reason == "conversation_column_not_found"


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
