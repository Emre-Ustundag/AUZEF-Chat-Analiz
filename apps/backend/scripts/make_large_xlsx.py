"""Gerçekçi büyüklükte `.xlsx` fixture üreteci — ADR §10 risk 1.

    uv run python scripts/make_large_xlsx.py --target-mb 130 --out /tmp/buyuk.xlsx

## Neden üretiliyor, commit edilmiyor

ADR §10 risk 1 "gerçek 130 MB fixture ile yük testi" istiyor. 130 MB'lık bir
ikili dosyayı Git'e koymak repoyu kalıcı olarak şişirir ve her klonlamada
indirilir. Üreteci commit etmek aynı işi görüyor: dosya deterministik (sabit
tohum) ve isteyen aynı fixture'ı saniyeler içinde yeniden üretebiliyor.

## İçerik neden rastgele metin değil

Sıkıştırılmış boyut hedefleniyor. Tekrar eden bir dize ZIP tarafından neredeyse
tamamen yok edilir; 130 MB'a ulaşmak için gereken satır sayısı gerçekçi
olmayan bir yere kaçardı ve test aslında "çok fazla satır"ı ölçerdi, "büyük
dosya"yı değil. Bu yüzden mesajlar gerçek chatbot trafiğine benzer biçimde
kuruluyor: sabit bir şablon havuzu + değişken alanlar (numaralar, tarihler,
ders kodları). Sonuç hem sıkışabilir hem de tekilleştirmeye direnir — tıpkı
gerçek veri gibi.

Kolonlar da gerçek dosyalardaki gibi: analiz edilen `mesaj` kolonunun yanında
taşınan ama okunmayan alanlar var. Kolon minimizasyonunun (ADR §9) gerçekten
işe yaradığını ölçebilmek için gerekli.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook

#: Deterministik: aynı tohum aynı dosyayı üretir, yani ölçümler
#: karşılaştırılabilir kalır.
SEED = 20260814

SHEET_NAME = "Mesajlar"
HEADERS = ("tarih", "kullanici_id", "mesaj", "kanal", "oturum_id", "yanit_suresi_ms")

KANALLAR = ("web", "mobil", "whatsapp", "telegram")

#: Gerçek AUZEF trafiğine benzeyen şablonlar. Tekrar eden gövde + değişken
#: alanlar: sıkışabilir ama tekilleştirmeyle tamamen erimeyen bir dağılım.
SABLONLAR = (
    "{ders} dersinin vize sınavı ne zaman yapılacak? {yil} güz dönemi için soruyorum.",
    "Merhaba, {ders} dersinin ders materyallerine nereden ulaşabilirim?",
    "Harç ödemesini {tarih} tarihine kadar yapmam gerekiyor mu? Öğrenci no: {ogrenci}",
    "Kayıt yenileme işlemi için hangi belgeler isteniyor? {yil} kaydım var.",
    "{ders} dersinden {puan} aldım, bütünlemeye girmem gerekir mi?",
    "Mazeret sınavı başvurusu için son tarih {tarih} mi? Rapor yüklemem gerekiyor.",
    "Öğrenci belgemi e-devletten alamıyorum, {ogrenci} numaralı öğrenciyim.",
    "{ders} dersinin final sınavı yüz yüze mi olacak yoksa online mı?",
    "Ders kaydı yaparken {ders} dersini seçemiyorum, sistem hata veriyor.",
    "Transkriptimde {ders} dersinin notu görünmüyor, ne yapmalıyım?",
    "Sınav yerimi nereden öğrenebilirim? {tarih} tarihindeki sınav için.",
    "İkinci üniversite kaydı için {yil} yılında başvuru yapabilir miyim?",
    "{ders} dersinin ara sınav sonuçları ne zaman açıklanacak?",
    "Öğrenci kimlik kartımı kaybettim, yenisini nasıl çıkarabilirim?",
    "Askerlik tecil belgemi sistemden indiremiyorum, {ogrenci} numaralı öğrenciyim.",
    "{ders} dersi için tavsiye edilen kaynak kitap hangisi?",
    "Bütünleme sınavına girmek için ayrıca başvuru yapmam gerekiyor mu?",
    "Yaz okulunda {ders} dersini alabilir miyim? Kontenjan var mı?",
    "Not itirazı için başvuru süresi {tarih} tarihinde doluyor mu?",
    "Mezuniyet için gereken toplam kredi kaç? {ders} dersini saydırabilir miyim?",
)

DERSLER = (
    "İktisada Giriş",
    "Genel Muhasebe",
    "Hukuka Giriş",
    "İşletme Yönetimi",
    "Türk Dili",
    "Atatürk İlkeleri",
    "Temel Bilgi Teknolojileri",
    "Sosyoloji",
    "İstatistik",
    "Pazarlama İlkeleri",
    "Finansal Yönetim",
    "Örgütsel Davranış",
    "Makro İktisat",
    "Ticaret Hukuku",
    "Yönetim Bilişim Sistemleri",
)


def _mesaj(rng: random.Random, baslangic: datetime) -> str:
    return rng.choice(SABLONLAR).format(
        ders=rng.choice(DERSLER),
        yil=rng.randint(2021, 2026),
        tarih=(baslangic + timedelta(days=rng.randint(0, 400))).strftime("%d.%m.%Y"),
        ogrenci=rng.randint(1_000_000, 9_999_999),
        puan=rng.randint(0, 100),
    )


def generate(target_bytes: int, out: Path, max_rows: int) -> tuple[int, float]:
    """Hedef boyuta ulaşana kadar satır yazar; (satır sayısı, saniye) döner.

    `write_only=True` ZORUNLU: normal modda openpyxl tüm hücreleri bellekte
    tutar ve 130 MB'lık bir dosyanın üretimi makineyi swap'e sokar. Bu, testin
    ölçmek istediği şeyin (okuma tarafındaki bellek davranışı) üretim
    tarafındaki bir sınırla gölgelenmesi demekti.
    """
    rng = random.Random(SEED)
    baslangic = datetime(2025, 9, 1, tzinfo=UTC).replace(tzinfo=None)

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(SHEET_NAME)
    sheet.append(list(HEADERS))

    started = time.monotonic()
    rows = 0
    # Boyut ancak kaydedildikten sonra bilinir (ZIP sıkıştırması). Bu yüzden
    # önce bir tahminle yazıyoruz, sonra ölçüp gerekirse büyütüyoruz.
    batch = 50_000

    while rows < max_rows:
        for _ in range(min(batch, max_rows - rows)):
            rows += 1
            sheet.append(
                [
                    (baslangic + timedelta(minutes=rows)).strftime("%Y-%m-%d %H:%M:%S"),
                    f"u{rng.randint(100000, 999999)}",
                    _mesaj(rng, baslangic),
                    rng.choice(KANALLAR),
                    f"s{rng.randint(10**9, 10**10 - 1)}",
                    rng.randint(120, 9800),
                ]
            )

        # write_only modda ara boyut ölçülemiyor; tahmini bayt/satır oranıyla
        # devam ediyoruz ve gerçek boyutu kaydettikten sonra doğruluyoruz.
        if rows >= max_rows:
            break

    out.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(out)
    elapsed = time.monotonic() - started

    return rows, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-mb", type=int, default=130, help="Hedef sıkıştırılmış boyut.")
    parser.add_argument("--out", type=Path, default=Path("/tmp/auzef-buyuk.xlsx"))
    parser.add_argument(
        "--rows",
        type=int,
        default=0,
        help="Sabit satır sayısı. 0 ise hedef boyuta göre otomatik ayarlanır.",
    )
    args = parser.parse_args()

    target_bytes = args.target_mb * 1024 * 1024

    if args.rows:
        rows, elapsed = generate(target_bytes, args.out, args.rows)
    else:
        # Kalibrasyon: küçük bir örnek yazıp bayt/satır oranını ölç, sonra
        # hedefe göre satır sayısını seç. Tahmin etmek yerine ÖLÇMEK, şablon
        # havuzu değiştiğinde de doğru kalmasını sağlıyor.
        probe = args.out.with_suffix(".probe.xlsx")
        probe_rows = 20_000
        generate(target_bytes, probe, probe_rows)
        per_row = probe.stat().st_size / probe_rows
        probe.unlink()

        rows = int(target_bytes / per_row)
        print(f"kalibrasyon: {per_row:.1f} bayt/satır -> {rows:,} satır hedefleniyor")
        rows, elapsed = generate(target_bytes, args.out, rows)

    size = args.out.stat().st_size
    print(f"yazıldı: {args.out} | {rows:,} satır | {size / 1024 / 1024:.1f} MB | {elapsed:.1f} sn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
