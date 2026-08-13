import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";

/**
 * Query hataları için tek yeniden deneme politikası.
 *
 * Tanınan API hatalarında backend'in hata sözlüğü belirleyicidir.
 * Ağ kopması veya beklenmeyen bir exception gibi tanınmayan hatalar geçici
 * kabul edilir. Hem TanStack'in kısa retry döngüsü hem de arka plan
 * polling'i aynı kararı kullanır; aksi halde biri durdurduğu isteği diğeri
 * yeniden başlatabilir.
 */
export function isRetryableQueryError(error: unknown): boolean {
  if (error instanceof ApiError) return error.isRetryable;
  return true;
}

/** Aynı geçici hata için ilk istekten sonra en fazla iki kısa retry. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  return isRetryableQueryError(error);
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
