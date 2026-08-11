import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";

/**
 * İstemcinin yeniden deneme politikası.
 *
 * Kullanıcı hataları (geçersiz dosya, bulunamayan job, kimlik doğrulama)
 * tekrar denemekle düzelmez; denemek yalnızca hata mesajını geciktirir.
 * Yalnızca sağlayıcı kaynaklı geçici hatalarda tekrar denenir.
 */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (error instanceof ApiError) return error.isRetryable;
  return true;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        // Polling'e dayanan bir arayüz; pencereye her dönüşte ekstra istek
        // atmak 45 dakikalık bir job sırasında gereksiz yük yaratır.
        refetchOnWindowFocus: false,
        staleTime: 30_000,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
