import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

/**
 * Çalışan `docker compose` yığınına karşı uçtan uca — mock'un TAKLİT
 * EDEMEDİĞİ kısım.
 *
 * Ölçülen şey mock'ta hiç yok:
 *
 * * Caddy'nin multipart gövdeyi tamponlamadan FastAPI'ye geçirmesi (:3000
 *   proxy'ye ait, `web`'in portu yayınlanmıyor — istek gerçek üretim yolundan
 *   gidiyor)
 * * FastAPI'nin dosyayı MinIO'ya stream etmesi
 * * Celery worker'ın `openpyxl` ile GERÇEKTEN profillemesi ve arayüzün o
 *   profille kolon ekranını kurması
 *
 * ANALİZ BAŞLATILMIYOR: orası gerçek bir OpenRouter çağrısı ve kullanıcının
 * parası. Arayüz tarafındaki analiz akışı `mock.spec.ts` içinde kapsanıyor.
 *
 * Yığın ayakta değilse test ATLANIR (`test.skip`), düşmez: `stack` projesi
 * `docker compose up -d` gerektirir ve bunu bir ön koşul olarak bildirmek,
 * yığını olmayan geliştiricide kırmızı bir suite bırakmaktan iyidir.
 */

// `__dirname` kullanılıyor, `import.meta.dirname` değil: Playwright bu
// dosyayı CommonJS olarak yüklüyor (kökte `"type": "module"` yok) ve
// `import.meta` orada sözdizimi hatası veriyor.
const FIXTURE = path.join(__dirname, "../../apps/backend/tests/fixtures/valid_multi_sheet.xlsx");

async function stackIsUp(baseURL: string): Promise<boolean> {
  try {
    const response = await fetch(`${baseURL}/api/v1/health/ready`, {
      signal: AbortSignal.timeout(3_000),
    });
    return response.status === 200;
  } catch {
    return false;
  }
}

test.beforeEach(async ({ baseURL }) => {
  test.skip(
    !(await stackIsUp(baseURL!)),
    "Yığın ayakta değil: `docker compose up -d` ile başlatın.",
  );
});

test("proxy → FastAPI → MinIO → Celery: gerçek dosya gerçekten profilleniyor", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Chatbot mesajlarını analiz edin" }),
  ).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles({
    name: "valid_multi_sheet.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: readFileSync(FIXTURE),
  });
  await page.getByRole("button", { name: "Yükle ve devam et" }).click();

  await expect(page).toHaveURL(/\/yuklemeler\//);

  // Worker gerçekten profillediyse kolon ekranı fixture'ın GERÇEK içeriğiyle
  // dolar. Sabit bir metin değil, dosyadan okunan kolon adı bekleniyor —
  // mock'un uydurma profili bu iddiayı geçemezdi.
  await expect(page.getByRole("heading", { name: "Analizi yapılandırın" })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText("mesaj", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("valid_multi_sheet.xlsx")).toBeVisible();
});

test("readiness ucu üç bağımlılığı da raporluyor", async ({ request }) => {
  // Container healthcheck'i bu uca bağlı; cevabın ŞEKLİ de sözleşmenin
  // parçası (`tests/fixtures/contract/health.ready.200.json`).
  const response = await request.get("/api/v1/health/ready");

  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual({
    status: "ready",
    checks: [
      { name: "postgres", status: "ok" },
      { name: "redis", status: "ok" },
      { name: "object-storage", status: "ok" },
    ],
  });
});

test("Idempotency-Key replay proxy üzerinden de aynı 202'yi döndürür", async ({ request }) => {
  const key = `e2e-${randomUUID()}`;
  const file = {
    name: "valid_multi_sheet.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: readFileSync(FIXTURE),
  };

  const first = await request.post("/api/v1/uploads", {
    headers: { "Idempotency-Key": key },
    multipart: { file },
  });
  const second = await request.post("/api/v1/uploads", {
    headers: { "Idempotency-Key": key },
    multipart: { file },
  });

  expect(first.status()).toBe(202);
  expect(second.status()).toBe(202);
  expect(await second.json()).toEqual(await first.json());
});
