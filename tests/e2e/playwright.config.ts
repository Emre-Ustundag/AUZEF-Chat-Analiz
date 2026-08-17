import * as path from "node:path";

import { defineConfig, devices } from "@playwright/test";

/**
 * Uçtan uca test yapılandırması — ADR §2 test yığını, §12 QA/DevOps kapsamı.
 *
 * ## Neden kökte, `apps/web/` altında değil
 *
 * Backend testleri bilerek `apps/backend/tests/` altında duruyor: uygulamanın
 * kendi fixture'larını, `conftest.py`'sini ve `uv` ortamını kullanıyorlar.
 * Uçtan uca testler ise tek bir uygulamaya ait DEĞİL — tarayıcıdan başlayıp
 * Caddy, Next, FastAPI, Celery, Postgres ve MinIO'nun tamamından geçiyorlar.
 * Kökteki `tests/` dizini zaten "iki tarafın ortak" artefaktlarının yeri.
 *
 * ## İki proje, iki farklı soru
 *
 * `mock` — arayüz akışının tamamı (yükleme → kolon seçimi → ilerleme → rapor
 * → dışa aktarma). Repodaki mock backend'e karşı koşar: Docker, OpenRouter
 * anahtarı ve PARA gerektirmez, bu yüzden CI'da her PR'da koşabilir. Analiz
 * adımı gerçek bir LLM çağrısı yaptığı için tam akışı başka türlü otomatik
 * doğrulamanın yolu yok.
 *
 * `stack` — çalışan `docker compose` yığınına karşı upload → profilleme →
 * kolon ekranı. Burada gerçek olan şey mock'un TAKLİT EDEMEDİĞİ kısım:
 * Caddy'nin gövdeyi tamponlamadan geçirmesi, FastAPI'nin dosyayı MinIO'ya
 * stream etmesi ve Celery worker'ın `openpyxl` ile gerçekten profillemesi.
 * Analiz başlatılmıyor — orası kullanıcının parasını harcar.
 *
 * Varsayılan olarak İKİSİ de tanımlı; `--project=mock` ya da
 * `--project=stack` ile ayrılırlar. `stack` projesi yığın ayakta değilse
 * kendi içinde atlanır (bkz. `stack.spec.ts`).
 */

const MOCK_PORT = 3100;

/**
 * `webServer` Playwright'ta PROJE BAŞINA tanımlanamıyor, yalnızca global.
 * `--project=stack` ile koşarken mock sunucusunu ayağa kaldırmak, zaten
 * çalışan bir yığına karşı koşan testten önce gereksiz bir `next build`
 * beklemek demekti.
 */
const stackOnly = process.argv.includes("--project=stack");

export default defineConfig({
  testDir: ".",
  // Uçtan uca akış yükleme + polling içeriyor; birim testlerin varsayılanı
  // (5 sn) burada anlamsız.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // Tek worker: her iki proje de PAYLAŞILAN durum üzerinde çalışıyor (mock
  // store'u modül seviyesinde, `stack` ise gerçek veritabanı). Paralel
  // koşmak testleri birbirinin verisine bağımlı yapardı.
  workers: 1,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],

  use: {
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "mock",
      testMatch: /mock\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        // `localhost`, `127.0.0.1` DEĞİL: `next dev` bilinmeyen bir Host
        // başlığından gelen `/_next/static/*` isteklerini güvenlik
        // gerekçesiyle ENGELLİYOR ve sayfa JavaScript'siz yükleniyordu.
        // `localhost` varsayılan allowlist'te.
        baseURL: `http://localhost:${MOCK_PORT}`,
      },
    },
    {
      name: "stack",
      testMatch: /stack\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        // `docker compose up` sonrası :3000 Caddy'ye ait. Testin gerçek
        // istek yolundan geçmesi bilinçli: Next'e doğrudan gitmek proxy
        // katmanını ölçmeden atlardı.
        baseURL: process.env.E2E_STACK_URL ?? "http://127.0.0.1:3000",
      },
    },
  ],

  // Yalnızca `mock` projesi için sunucu ayağa kaldırılıyor.
  //
  // `next dev`, `next start` DEĞİL: `next.config.ts` `output: "standalone"`
  // kullanıyor ve Next bu modda `next start`'ın DESTEKLENMEDİĞİNİ açıkça
  // uyarıyor ("use node .next/standalone/server.js instead"). Standalone
  // sunucusunu elle kurmak statik dosyaları ve `public/`'i kopyalamak demek
  // — bir test koşucusunun taklit etmesi gereken şey bu değil, imajın işi
  // (`infra/docker/web.Dockerfile`).
  //
  // Üretim derlemesi kapsamsız KALMIYOR: CI'ın `web` job'ı `npm run build`
  // çalıştırıyor ve `stack` projesi zaten imajdan koşan gerçek `web`
  // servisine bağlanıyor. Burada ölçülen şey arayüz davranışı.
  webServer: stackOnly
    ? undefined
    : {
        command: `npm run dev --workspace apps/web -- --port ${MOCK_PORT}`,
        // Komut varsayılan olarak config dizininde (`tests/e2e/`) koşuyor ve
        // orada npm workspace'leri görünmüyor.
        cwd: path.join(__dirname, "../.."),
        url: `http://localhost:${MOCK_PORT}`,
        reuseExistingServer: !process.env.CI,
        timeout: 300_000,
        env: {
          NEXT_PUBLIC_API_BASE_URL: "/api/mock/v1",
          NEXT_TELEMETRY_DISABLED: "1",
        },
      },
});
