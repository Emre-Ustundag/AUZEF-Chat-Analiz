from app.prompts.faq_analysis import DEFAULT_VERSION, V1, V2, get_prompt


def test_v1_metni_tarihsel_surum_olarak_degismedi() -> None:
    assert V1.text_hash == "bf4aa998d22eafd0253b21dab2271fa6a031cdc6109f0021d72e7a5aa79b25b0"
    assert get_prompt("faq_analysis/v1") is V1


def test_v2_varsayilan_ve_departmandan_bagimsizdir() -> None:
    combined = f"{V2.map_system}\n{V2.reduce_system}"

    assert DEFAULT_VERSION == "faq_analysis/v2"
    assert get_prompt(DEFAULT_VERSION) is V2
    assert "AUZEF" not in combined
    assert "üniversite" not in combined.casefold()


def test_v2_tema_birlestirme_politikasi_sabit_taksonomi_dayatmaz() -> None:
    combined = f"{V2.map_system}\n{V2.reduce_system}"

    assert "Sabit bir tema listesi ya da hedef tema sayısı YOKTUR" in combined
    assert "Aynı kullanıcı niyetini" in combined
    assert "Her soruya ayrı tema açma" in combined
    assert "mevcut bir üst temaya" in combined
