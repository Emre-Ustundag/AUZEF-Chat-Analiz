## Başlamadan önce

- Büyük bir özellik veya mimari değişiklik için önce GitHub Issue açın ya da mevcut issue üzerinden kapsamı netleştirin.
- Mimari kararları etkileyen değişikliklerde `docs/mimari.md` dosyasını inceleyin ve gerekiyorsa aynı pull request içinde güncelleyin.
- Gerçek AUZEF mesajlarını, kişisel verileri, API anahtarlarını veya diğer secret'ları repoya eklemeyin.

## Yerel kurulum

```bash
git clone https://github.com/Emre-Ustundag/AUZEF-Chat-Analiz.git
cd AUZEF-Chat-Analiz
npm ci
npm run dev
```

Uygulama varsayılan olarak `http://localhost:3000` adresinde çalışır.

## Branch akışı

Doğrudan `main` branch'ine geliştirme yapılmaz. Güncel `main` üzerinden kısa ömürlü ve tek amaca odaklanan bir branch oluşturun.

Önerilen branch adları:

- `feature/kisa-aciklama`
- `fix/kisa-aciklama`
- `docs/kisa-aciklama`
- `chore/kisa-aciklama`
- `refactor/kisa-aciklama`

Örnek:

```bash
git switch main
git pull --ff-only
git switch -c feature/excel-upload
```

## Geliştirme ilkeleri

- Değişikliği PR'ın amacıyla sınırlı tutun; ilişkisiz düzenlemeleri ayrı branch ve PR'a ayırın.
- TypeScript kodunda strict tip güvenliğini koruyun.
- LLM'e adet/oran hesabı yaptırmayın; sayısal sonuçları backend'de deterministik hesaplayın.
- OpenRouter anahtarını, ham mesajları ve PII içeren verileri loglamayın.
- Test verilerinde yalnızca anonimleştirilmiş veya sentetik içerik kullanın.
- Davranış, kurulum veya mimari değiştiğinde ilgili dokümantasyonu aynı PR içinde güncelleyin.

## Kalite kontrolleri

PR açmadan önce mevcut kontrolleri çalıştırın:

```bash
npm run format:check
npm run lint
npm run build
```

Test altyapısı henüz eklenmediği için şu anda zorunlu bir `npm run test` komutu yoktur. İlk test edilebilir özellik veya hata düzeltmesiyle birlikte uygun test altyapısı ve test komutu eklenmelidir. Test komutları eklendikten sonra ilgili tüm testler PR öncesinde çalıştırılmalıdır.

Her push ve pull request'te GitHub Actions üzerinden format kontrolü, lint, production build ve Gitleaks secret taraması çalışır. Bu kontroller başarılı olmadan PR merge edilmemelidir.

## Pull request süreci

1. Branch'inizi güncel `main` ile uyumlu hâle getirin.
2. PR şablonundaki özet, değişiklik, doğrulama ve güvenlik alanlarını doldurun.
3. UI değişikliklerinde mümkünse ekran görüntüsü veya kısa video ekleyin.
4. İlgili issue varsa `Closes #123` biçiminde bağlayın.
5. PR sahibinin dışında en az bir reviewer onayı alın.
6. Review yorumlarını çözmeden ve zorunlu kontroller başarılı olmadan merge etmeyin.
7. PR'ı mümkün olduğunca küçük, tek amaca odaklı ve incelenebilir tutun.

Draft durumundaki PR'lar erken geri bildirim için kullanılabilir; merge öncesinde hazır duruma getirilmelidir.

## Secret ve hassas veri kontrolü

- Secret'ları yalnızca yerel `.env` dosyalarında veya onaylı secret manager içinde saklayın.
- `.env.example` dosyasında yalnızca değişken adları ve güvenli örnek değerler bulunmalıdır.
- Commit öncesinde staged diff'i secret, token, gerçek kullanıcı verisi ve büyük çıktı dosyaları açısından kontrol edin.
- Bir secret yanlışlıkla commit edilirse yalnızca dosyadan silmekle yetinmeyin; anahtarı derhâl iptal edip yenileyin ve repository geçmişinin temizlenmesi için proje sorumlusuna bildirin.

## Hata ve güvenlik bildirimi

Normal hata ve özellik talepleri için GitHub Issues kullanılabilir. API anahtarı, kişisel veri sızıntısı veya erişim kontrolü açığı gibi hassas güvenlik konularını herkese açık issue içinde ayrıntılandırmayın; doğrudan proje sorumlusuna bildirin.
