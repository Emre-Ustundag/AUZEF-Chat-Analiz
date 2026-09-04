# Ürünleştirme Uygulama Planı

Hedef: teknik olmayan bir kullanıcının dosyayı bırakıp tek tuşla analiz
başlatabilmesi; model, prompt, kolon eşlemesi gibi hiçbir uzman ayarının
ekranda görünmemesi.

## Mevcut durumun özeti (doğrulandı)

```
tarayıcı → Caddy(:3000) → /api/* → backend(:8000)
                         → diğer → Next.js(:3000)

veritabanı : analyses, uploads, alembic_version   (users YOK)
kimlik     : yok — giriş yok, rol yok, middleware yok
maliyet    : yalnızca koşu başına max_cost_usd; toplam/aylık tavan yok
analiz     : mesaj birimi + map-reduce (ölçüldü: session birimi + sabit
             taksonomi belirgin daha iyi)
rapor      : tek şema (themes + top_questions); ürettiğimiz 6 rapor yok
saklama    : rapor ve yükleme 24 saat
```

---

## FAZ A — Kimlik ve bütçe

**Neden önce bu:** anahtarın `.env`'e taşınması buna bağlı. Anahtar sunucuya
alınınca uygulamaya erişen herkes AUZEF parasıyla koşabilir hâle gelir;
karma kullanıcı düzeninde bu savunulamaz.

| # | İş | Bağımlılık |
|---|---|---|
| A1 | `users` tablosu + alembic migration | — |
| A2 | Giriş ucu + oturum (httpOnly cookie) | A1 |
| A3 | Mevcut uçları koruma altına al | A2 |
| A4 | `analyses.user_id` — kim ne koştu | A1 |
| A5 | Roller: `koşabilen` / `görebilen` | A1 |
| A6 | Aylık bütçe sayacı + koşu öncesi kontrol | A4 |
| A7 | Frontend: giriş sayfası + middleware | A2 |
| A8 | Kullanıcı oluşturma (CLI betiği) | A1 |

**Kararlar:**
- Oturum mekanizması: **cookie tabanlı** öneriliyor (JWT değil). Tek origin,
  Caddy arkasında, yenileme derdi yok. Chatbot'ta da benzer desen var.
- Kullanıcı oluşturma: arayüzden değil, CLI betiğiyle (chatbot'taki
  `create_admin` deseni). Karma ekipte kullanıcı sayısı az olacak.
- A6 tavanı aşınca ne olur? Öneri: koşu başlatılamaz, ekranda kalan bütçe
  ve tavan görünür. Sessizce kesilmemeli.

**Çıktı:** giriş yapılmadan hiçbir analiz başlatılamaz; her analizin sahibi
ve maliyeti kayıtlı; aylık tavan aşılamıyor.

---

## FAZ B — Anahtarı sunucuya taşı

| # | İş | Bağımlılık |
|---|---|---|
| B1 | `X-OpenRouter-Key` opsiyonel, yoksa sunucu config'inden | FAZ A |
| B2 | Formdan `openrouter_api_key` alanını kaldır | B1 |
| B3 | Koşu öncesi bütçe kontrolü devreye | A6, B1 |

**Not:** ADR §6/§9 anahtarın yalnızca header'da taşınmasını şart koşuyordu.
Bu bilinçli bir kararın geri alınmasıdır; gerekçesi kurum içi tek
organizasyonlu kullanım ve anahtarı kullanıcıya vermenin daha büyük zafiyet
oluşturması. ADR'ye not düşülmeli.

---

## FAZ C — Doğru analizi ürüne al

Bugün arayüzden koşan analiz, ölçtüğümüz eski yöntem (mesaj birimi +
map-reduce). Kimlik eklenip kullanıcı serbest bırakılırsa **yanlış analizi**
koşmuş olur. Bu yüzden C, arayüz işinden önce gelmeli.

| # | İş | Bağımlılık |
|---|---|---|
| C1 | Kaynak profili: kolon eşlemesi + birim + sinyal tanımı, config'de | — |
| C2 | Session birimi ön işleme (betikteki mantık backend'e) | C1 |
| C3 | Taksonomi deposu (repo içi config dosyası) | — |
| C4 | Sabit taksonomiye sınıflandırma — yeni iş tipi, reduce yok | C2, C3 |
| C5 | Taksonomi anlık görüntüsü analiz kaydına yazılsın | C3 |
| C6 | Rapor üretimi (6 rapor) iş çıktısına | C4 |

**Kritik tasarım kararı — C5:** `analyses` tablosu zaten `pricing_snapshot`
tutuyor; sebebi, fiyat kataloğu sonradan değişince eski raporun maliyet
hesabının bozulmaması. **Taksonomi de aynı sebeple anlık görüntü olarak
saklanmalı.** Aksi hâlde taksonomi güncellendiğinde eski raporlar okunamaz
hâle gelir — dönemsel karşılaştırma da anlamını yitirir.

**Not:** eski analiz modu silinmez, dokunulmaz. Uzman yolu olarak kalır.

---

## FAZ D — Sadeleştirilmiş arayüz

| # | İş | Bağımlılık |
|---|---|---|
| D1 | Yükleme ekranı: dosya + başlat, başka hiçbir şey | C4, B2 |
| D2 | Rapor ekranları (6 rapor) | C6 |
| D3 | İlerleme ekranı korunur (zaten var) | — |

**Karar gereken:** altı raporun hepsi ayrı ekran mı, sekmeli tek ekran mı?
Öneri: sekmeli tek ekran + xlsx indirme butonu. İndirme yine kalsın —
kullanıcı raporu kuruma dağıtmak isteyebilir.

---

## FAZ E — Saklama ve geçmiş

| # | İş | Bağımlılık |
|---|---|---|
| E1 | `report_retention_hours` uzun, `upload_retention_hours` kısa | — |
| E2 | Geçmiş analizler listesi ekranı | A4, D2 |
| E3 | Dönemsel karşılaştırma (bu ay / geçen ay) | E2, C5 |

**E3 asıl değeri üreten yer:** "KB'ye makale eklendikten sonra o sorunun
sorunlu oranı düştü mü" sorusunu ölçülebilir yapar. Chatbot başarı oranını
yükseltme hedefi bir varsayım olmaktan çıkar.

---

## FAZ F — Bayatlama uyarısı

`«hiçbiri»` oranı her koşuda zaten hesaplanıyor (şu an %14,2). Rapor
şemasına alan eklenir, eşiği aşınca ekranda uyarı çıkar ve bildirim gider.
Taksonomi yenileme kararı takvime değil ölçüme bağlanır.

---

## Sıralama gerekçesi

```
A → B → C → D → E → F
```

- **A önce**, çünkü B ona bağlı ve B olmadan hedef kullanıcı sisteme giremez.
- **C, D'den önce**, çünkü kullanıcıyı yanlış analize serbest bırakmamak
  gerekir. Arayüzü sadeleştirip altında eski yöntemi bırakmak, kötü sonucu
  daha kolay üretilebilir yapmak olurdu.
- **D en büyük kalem** ama en az riskli; altındaki her şey doğruysa
  görselleştirme mekanik iş.
- **E ve F** ürün çalışır hâle geldikten sonra değer katan katmanlar.

## Her fazda değişmeyecekler

Ölçümle gerekçelendirildi, ürünleşirken korunmalı:

1. **Sıcaklık 0** — sağlayıcı varsayılanında aynı prompt aynı veride 78 ve
   106 kategori üretti; sıcaklık 0'da 113 ve 113.
2. **Sabit taksonomi, reduce yok** — kategori sayısı modele bırakılınca
   kapsama ile başlık netliği arasında rastgele bir noktaya düşüyor.
3. **Adetler koddan, modelden değil** — model yalnızca eşleme yapar.
4. **Session birimi** — mesaj birimine dönmek kategori patlamasını geri getirir.
