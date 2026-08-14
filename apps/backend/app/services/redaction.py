"""PII redaksiyonu ve örnek değer kırpma.

ADR §9: "PII redaksiyonu bilinen T.C./öğrenci no, telefon, e-posta ve benzeri
desenleri kapsar; serbest metindeki kişi adlarının eksiksiz maskeleneceği
garanti edilmez."

Faz 1'de bu modülün tek tüketicisi kolon profilindeki `sample_values`. Faz 3'te
LLM'e gitmeden önceki redaksiyon adımı da buraya bağlanacak; bu yüzden modül
dosya okuma veya veritabanından bağımsız, saf fonksiyonlardan oluşuyor.

Desenlerin sırası önemlidir: e-posta önce maskelenmezse içindeki rakam dizileri
öğrenci numarası sanılıp e-postanın yalnızca bir parçası bozulur.
"""

from __future__ import annotations

import re

EMAIL_MASK = "[EPOSTA]"
PHONE_MASK = "[TELEFON]"
ID_MASK = "[KIMLIK]"
NUMBER_MASK = "[NUMARA]"
URL_MASK = "[BAGLANTI]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")

# T.C. kimlik numarası: tam 11 hane, 0 ile başlamaz.
_TCKN = re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)")

# Türkiye telefon biçimleri: +90..., 0(5xx) ..., 05xx xxx xx xx, boşluk/tire/
# parantez ayraçlarıyla. En az 10 hane arar.
_PHONE = re.compile(
    r"(?<!\d)(?:\+90[\s.\-]?|0)?[\s.\-]?\(?5\d{2}\)?[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}(?!\d)"
)

# Öğrenci numarası ve benzeri: 7-12 haneli serbest rakam dizisi. TCKN ve
# telefondan SONRA uygulanır, yoksa onların parçalarını yer.
_LONG_DIGITS = re.compile(r"(?<!\d)\d{7,12}(?!\d)")

_WHITESPACE = re.compile(r"\s+")


def redact_pii(text: str) -> str:
    """Bilinen PII desenlerini maskeler.

    Sıra kritiktir: e-posta ve URL bütün olarak maskelenmezse içlerindeki
    rakam dizileri diğer desenlerce parçalanır ve geriye kalan kırıntı hâlâ
    tanımlayıcı olabilir.
    """
    text = _EMAIL.sub(EMAIL_MASK, text)
    text = _URL.sub(URL_MASK, text)
    text = _PHONE.sub(PHONE_MASK, text)
    text = _TCKN.sub(ID_MASK, text)
    text = _LONG_DIGITS.sub(NUMBER_MASK, text)
    return text


def truncate(text: str, max_length: int) -> str:
    """Metni sınıra kırpar ve kırpıldığını görünür kılar."""
    if max_length <= 0:
        return ""
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def sanitize_sample(value: str, max_length: int) -> str:
    """Profil örneği için tek bir hücre değerini güvenli hâle getirir.

    Üç iş birden yapar (ADR §9):
      1. Satır sonu/sekme gibi boşlukları teke indirir — kolon seçim tablosunda
         çok satırlı bir hücre düzeni bozar.
      2. PII desenlerini maskeler.
      3. Sınıra kırpar. Kırpma redaksiyondan SONRA yapılır; önce kırpılsaydı
         yarım kalan bir telefon numarası desene uymayıp maskelenmeden kalırdı.
    """
    collapsed = _WHITESPACE.sub(" ", value).strip()
    return truncate(redact_pii(collapsed), max_length)
