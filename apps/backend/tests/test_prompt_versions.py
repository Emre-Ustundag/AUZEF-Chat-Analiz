from app.prompts.faq_analysis import DEFAULT_VERSION, V1, V2, V3, V4, get_prompt


def test_v1_metni_tarihsel_surum_olarak_degismedi() -> None:
    assert V1.text_hash == "bf4aa998d22eafd0253b21dab2271fa6a031cdc6109f0021d72e7a5aa79b25b0"
    assert get_prompt("faq_analysis/v1") is V1


def test_legacy_prompt_hashleri_v4_eklenirken_degismedi() -> None:
    assert V2.text_hash == "f7c32631f087f551d25c65ae87b4c2ce409fcc2596067ba767d6f7e95d0955c1"
    assert V3.text_hash == "122145e32670d2468872f2b2cf2f5062a6fc45b4d234e232c592842beb1ff61e"


def test_v3_varsayilan_ve_departmandan_bagimsizdir() -> None:
    combined = f"{V3.map_system}\n{V3.reduce_system}"

    assert DEFAULT_VERSION == "faq_analysis/v3"
    assert get_prompt(DEFAULT_VERSION) is V3
    assert "AUZEF" not in combined
    assert "üniversite" not in combined.casefold()


def test_v2_tema_birlestirme_politikasi_sabit_taksonomi_dayatmaz() -> None:
    combined = f"{V2.map_system}\n{V2.reduce_system}"

    assert "Sabit bir tema listesi ya da hedef tema sayısı YOKTUR" in combined
    assert "Aynı kullanıcı niyetini" in combined
    assert "Her soruya ayrı tema açma" in combined
    assert "mevcut bir üst temaya" in combined


def test_v3_hiyerarsik_reduce_turunun_kismi_baglami_anlatilir() -> None:
    assert "çok aşamalı bir" in V3.reduce_system
    assert "TEK TURU" in V3.reduce_system
    assert get_prompt("faq_analysis/v3") is V3


def test_v4_baglami_kanit_hedefi_tek_sayim_birimi_olarak_tanimlar() -> None:
    combined = f"{V4.map_system}\n{V4.map_user_template}"

    assert get_prompt("faq_analysis/v4") is V4
    assert V4.text_hash not in {V1.text_hash, V2.text_hash, V3.text_hash}
    assert '<baglam><mesaj rol="user|assistant">' in combined
    assert "Yalnızca `<hedef>`" in combined
    assert "Bağlamı SAYMA" in combined
    assert "Yalnızca dış `<kayit" in combined


def test_v4_v3_reduce_ve_cikti_semalarini_aynen_korur() -> None:
    assert V4.map_schema is V3.map_schema
    assert V4.reduce_system is V3.reduce_system
    assert V4.reduce_user_template is V3.reduce_user_template
    assert V4.reduce_schema is V3.reduce_schema
    assert V3.version == DEFAULT_VERSION
