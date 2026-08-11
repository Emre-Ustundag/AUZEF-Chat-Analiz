/**
 * TanStack Query cache anahtarları.
 *
 * Tek yerde tutulur ki invalidate çağrıları anahtarı elle yazmasın; elle
 * yazılan anahtarlar sessizce eşleşmeyi kaçırır ve cache bayat kalır.
 */
export const queryKeys = {
  models: () => ["models"] as const,
  upload: (uploadId: string) => ["uploads", uploadId] as const,
  analysisJob: (analysisId: string) => ["analyses", analysisId] as const,
  analysisReport: (analysisId: string) =>
    ["analyses", analysisId, "result"] as const,
} as const;
