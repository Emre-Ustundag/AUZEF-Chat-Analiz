import { expect, test } from "@playwright/test";

import { minimalXlsxBytes } from "../../apps/web/src/mocks/minimal-xlsx";

/**
 * Arayüzün TAM akışı, gerçek bir tarayıcıda — mock backend'e karşı.
 *
 * Neden mock: akışın son adımı gerçek bir LLM koşusu ve kullanıcının kendi
 * OpenRouter anahtarıyla PARA harcıyor. Bunu her PR'da otomatik koşturmanın
 * yolu yok. Mock aynı sözleşmeyi (aşama sırası, hata kodları, rapor şekli)
 * zamana bağlı bir durum makinesiyle taklit ediyor, yani ölçülen şey
 * arayüzün sözleşmeye verdiği tepki.
 *
 * Yığına özgü olan (Caddy, MinIO'ya stream, Celery profilleme) `stack.spec.ts`
 * tarafında ölçülüyor. İkisi birbirinin yerine geçmez.
 *
 * Mock, senaryoyu DOSYA ADINDAN seçiyor (`src/mocks/store.ts`):
 * "bozuk" → upload hatası, "hata" → analiz hatası, "buyuk" → satır sınırı.
 */

const xlsx = () => ({
  name: "auzef-mesajlar.xlsx",
  mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  buffer: Buffer.from(minimalXlsxBytes()),
});

test("yükleme → kolon seçimi → analiz → rapor → dışa aktarma", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Chatbot mesajlarını analiz edin" }),
  ).toBeVisible();

  // ---- yükleme ----
  await page.locator('input[type="file"]').setInputFiles(xlsx());
  await expect(page.getByText("auzef-mesajlar.xlsx")).toBeVisible();
  await page.getByRole("button", { name: "Yükle ve devam et" }).click();

  // ---- profilleme (mock ~4 sn'de "ready") ----
  await expect(page).toHaveURL(/\/yuklemeler\//);
  await expect(page.getByRole("heading", { name: "Analizi yapılandırın" })).toBeVisible({
    timeout: 30_000,
  });

  // ---- yapılandırma ----
  // Kolon seçimi profilden geliyor: `is_likely_text` olan kolon önceden
  // seçili olmalı, kullanıcı yalnızca anahtarı girip başlatabilmeli.
  await page.getByLabel("OpenRouter API anahtarı").fill("sk-or-v1-e2e-test-anahtari");
  await page.getByRole("button", { name: "Analizi başlat" }).click();

  // ---- ilerleme → rapor (mock toplam 38 sn) ----
  await expect(page).toHaveURL(/\/analizler\//);
  await expect(page.getByRole("heading", { name: "Analiz sonuçları" })).toBeVisible({
    timeout: 75_000,
  });
  await expect(page.getByText("En sık sorulan sorular")).toBeVisible();
  await expect(page.getByText("Tema dağılımı")).toBeVisible();

  // ---- dışa aktarma ----
  // Gerçek bir indirme olayı bekleniyor: `<a download>` yerine fetch + blob
  // kullanılıyor (hata yolunda ham problem JSON'u göstermemek için), bu
  // yüzden akışın çalıştığını yalnızca indirme olayı kanıtlar.
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Excel" }).click();
  const file = await download;

  expect(file.suggestedFilename()).toMatch(/\.xlsx$/);
});

test("bozuk dosya kullanıcıya sözleşmedeki hatayı gösterir", async ({ page }) => {
  await page.goto("/");

  await page.locator('input[type="file"]').setInputFiles({
    ...xlsx(),
    // Mock senaryosu dosya adından seçiliyor.
    name: "bozuk-dosya.xlsx",
  });
  await page.getByRole("button", { name: "Yükle ve devam et" }).click();

  // Kullanıcı ham backend metnini değil, koda bağlı Türkçe metni görmeli
  // (ADR §7: "ham backend metni kullanıcıya basılmaz").
  await expect(page.getByText(/dosya okunamadı|okunamadı/i).first()).toBeVisible({
    timeout: 30_000,
  });
});

test("desteklenmeyen dosya türü yüklemeden önce reddedilir", async ({ page }) => {
  await page.goto("/");

  await page.locator('input[type="file"]').setInputFiles({
    name: "notlar.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("bu bir excel dosyası değil"),
  });

  // Doğrulama SEÇİM anında yapılıyor, yükle düğmesine basıldığında değil:
  // kullanıcı yanlış dosyayı hemen öğrensin (`upload-screen.tsx`).
  await expect(page.getByText("Dosya kabul edilmedi")).toBeVisible();
  await expect(page.getByRole("button", { name: "Yükle ve devam et" })).toHaveCount(0);
});
