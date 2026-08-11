import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // npm workspaces hoists node_modules to the repo root. Without this, the
  // standalone trace starts at apps/web and misses the hoisted dependencies.
  outputFileTracingRoot: path.join(__dirname, "../.."),

  // FastAPI devreye girince aynı origin altında reverse proxy rewrite'ı
  // buraya eklenecek (ADR §2):
  //
  //   async rewrites() {
  //     return [{ source: "/api/v1/:path*",
  //               destination: `${process.env.API_ORIGIN}/api/v1/:path*` }];
  //   }
  //
  // ⚠️ proxy.ts EKLEMEDEN ÖNCE OKU (Next 16'da middleware'in yeni adı):
  // Projede bir proxy.ts varsa Next, istek gövdesini birden fazla kez
  // okunabilsin diye BELLEĞE KOPYALAR. Sınır `experimental.proxyClientMaxBodySize`
  // ile belirlenir ve VARSAYILANI 10 MB'dir. Sınır aşıldığında istek
  // BAŞARISIZ OLMAZ: gövde sessizce 10 MB'de kırpılır ve yalnızca bir uyarı
  // loglanır. Yani ~130 MB'lık gerçek upload'lar fark edilmeden bozulur.
  //
  // proxy.ts eklenecekse `matcher` upload yolunu MUTLAKA dışarıda bırakmalı
  // (veya sınır 150 MB'ın üstüne çekilmeli — bellek maliyeti kabul edilirse).
};

export default nextConfig;
