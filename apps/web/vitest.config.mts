import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// .mts uzantısı bilinçli: vitest.config.ts, en yakın package.json'da
// "type": "module" olmadığı için CommonJS olarak yükleniyor ve Vite uyarı
// veriyordu. .mts her zaman ESM olarak çözülür.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  test: {
    // Varsayılan node: şema, biçimlendirme ve mock store testleri DOM
    // istemiyor ve node ortamı belirgin şekilde hızlı. Bileşen testleri
    // dosya başında `// @vitest-environment jsdom` satırıyla jsdom'a geçer.
    environment: "node",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
