import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",

  experimental: {
    // ⚠️ ÖLÇÜLMÜŞ DAVRANIŞ, dokümantasyondan SAPIYOR.
    //
    // proxyClientMaxBodySize dokümanı "yalnızca proxy kullanıldığında
    // geçerlidir" diyor. Projede proxy.ts YOK, yine de Next 16.3.0 harici
    // rewrite'larda da gövdeyi belleğe klonluyor ve 10 MB'ta kesiyor.
    // 30 MB'lık bir upload denendiğinde web logunda görülen:
    //   "Request body exceeded 10MB for /api/v1/uploads"
    //   "Failed to proxy http://api:8000/api/v1/uploads Error: socket hang up"
    // ve istemciye HTTP 500 döndü.
    //
    // Bu yüzden sınır backend'in kendi upload sınırının (150 MB) ÜSTÜNE
    // çekildi; aksi hâlde büyük dosyalar arayüzden hiç geçmezdi.
    //
    // BEDELİ: Next eşzamanlı her upload için gövdeyi BELLEĞE alır. Tek
    // kullanıcılı geliştirme ortamı için kabul edilebilir, üretim için
    // DEĞİL — orada /api/v1'i FastAPI'ye ileten gerçek bir reverse proxy
    // (nginx/caddy) Next'in önüne konmalı; ADR §2 zaten "aynı origin
    // altındaki /api reverse proxy" diyor, Next'in rewrite'ını şart
    // koşmuyor. Faz 2'ye devredilen açık iş.
    proxyClientMaxBodySize: "160mb",
  },
  // npm workspaces hoists node_modules to the repo root. Without this, the
  // standalone trace starts at apps/web and misses the hoisted dependencies.
  outputFileTracingRoot: path.join(__dirname, "../.."),

  // ADR §2: frontend ve API aynı origin altında çalışır; tarayıcı /api/v1'e
  // gider, Next isteği FastAPI'ye geçirir. Böylece CORS gerekmez ve
  // NEXT_PUBLIC_API_BASE_URL göreli (/api/v1) kalabilir.
  //
  // ⚠️ API_ORIGIN BUILD ZAMANINDA okunur. rewrites() `next build` sırasında
  // değerlendirilip routes-manifest.json'a yazılır; runtime'da tekrar
  // okunmaz. Bu yüzden değer imaja build arg olarak geçilmeli
  // (bkz. infra/docker/web.Dockerfile) ve burada literal bir yedeği olmalı —
  // yoksa hedef "undefined/api/v1/..." olarak gömülür ve her istek çöker.
  //
  // ⚠️ MOCK'LAR HÂLÂ DURUYOR ve bu rewrite onları GÖLGELEMEZ: mock route
  // handler'ları /api/mock/v1 altında. Next'te route handler'lar rewrite'lardan
  // önce eşleştiği için aynı yolu paylaşsalardı mock'lar gerçek backend'i
  // sessizce ele geçirirdi (bkz. src/lib/api/client.ts). Mock'a dönmek için
  // NEXT_PUBLIC_API_BASE_URL=/api/mock/v1 yeterli.
  async rewrites() {
    const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiOrigin}/api/v1/:path*`,
      },
    ];
  },

  // ⚠️ proxy.ts EKLEMEYİN (Next 16'da middleware'in yeni adı):
  // Projede bir proxy.ts varsa Next, istek gövdesini birden fazla kez
  // okunabilsin diye BELLEĞE KOPYALAR. Sınır `experimental.proxyClientMaxBodySize`
  // ile belirlenir ve VARSAYILANI 10 MB'dir. Sınır aşıldığında istek
  // BAŞARISIZ OLMAZ: gövde sessizce 10 MB'de kırpılır ve yalnızca bir uyarı
  // loglanır. Yani ~130 MB'lık gerçek upload'lar fark edilmeden bozulur.
  //
  // Bu proje upload yolunu bilinçli olarak SADECE rewrite ile geçiriyor;
  // rewrite gövdeyi stream eder, belleğe almaz. proxy.ts eklenecekse
  // `matcher` upload yolunu MUTLAKA dışarıda bırakmalı.
};

export default nextConfig;
