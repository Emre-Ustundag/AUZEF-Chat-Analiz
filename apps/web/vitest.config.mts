import path from "node:path";
import { defineConfig } from "vitest/config";

// .mts uzantısı bilinçli: vitest.config.ts, en yakın package.json'da
// "type": "module" olmadığı için CommonJS olarak yükleniyor ve Vite uyarı
// veriyordu. .mts her zaman ESM olarak çözülür.
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
