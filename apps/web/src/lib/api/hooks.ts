"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import {
  cancelAnalysis,
  createAnalysis,
  createUpload,
  getAnalysisJob,
  getAnalysisReport,
  getModels,
  getUpload,
} from "./endpoints";
import type { UploadProgress } from "./endpoints";
import { queryKeys } from "./query-keys";
import {
  LIMITS,
  isAnalysisSettled,
  isUploadSettled,
} from "./schemas";
import type { AnalysisRequest } from "./schemas";

/**
 * Sunucu durumu hook'ları.
 *
 * ADR §2: ilk sürümde WebSocket/SSE yok, durum 2-3 saniyede bir poll edilir.
 * Polling'in terminal durumda durması kritik — durmazsa tamamlanmış bir job
 * için sonsuza kadar istek atılır.
 */

export function useModels() {
  return useQuery({
    queryKey: queryKeys.models(),
    queryFn: ({ signal }) => getModels(signal),
    staleTime: 5 * 60_000,
  });
}

export function useUploadStatus(uploadId: string | null) {
  return useQuery({
    queryKey: queryKeys.upload(uploadId ?? ""),
    queryFn: ({ signal }) => getUpload(uploadId!, signal),
    enabled: uploadId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (!status) return LIMITS.POLL_INTERVAL_MS;
      return isUploadSettled(status) ? false : LIMITS.POLL_INTERVAL_MS;
    },
    // Poll edilen kaynak her zaman taze kabul edilmeli, yoksa refetchInterval
    // ile staleTime birbiriyle yarışır.
    staleTime: 0,
  });
}

export function useAnalysisJob(analysisId: string | null) {
  return useQuery({
    queryKey: queryKeys.analysisJob(analysisId ?? ""),
    queryFn: ({ signal }) => getAnalysisJob(analysisId!, signal),
    enabled: analysisId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (!status) return LIMITS.POLL_INTERVAL_MS;
      return isAnalysisSettled(status) ? false : LIMITS.POLL_INTERVAL_MS;
    },
    // Bilinçli karar: refetchIntervalInBackground açılmadı. İş 45 dakika
    // sürebildiği için kullanıcı sekmeyi arka plana alacak; varsayılan
    // davranışta polling orada duruyor ve sekmeye dönünce kaldığı yerden
    // devam ediyor. Bakılmayan bir sekme için 45 dakika boyunca 2,5 saniyede
    // bir istek atmanın karşılığı yok; kullanıcı geri döndüğünde en fazla
    // bir aralık kadar bayat veri görür.
    staleTime: 0,
  });
}

/**
 * Tamamlanmış analizin raporu.
 *
 * `enabled` çağıran tarafından job durumuna göre verilir; job "completed"
 * olmadan çağrılırsa backend hata döner.
 */
export function useAnalysisReport(analysisId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.analysisReport(analysisId ?? ""),
    queryFn: ({ signal }) => getAnalysisReport(analysisId!, signal),
    enabled: analysisId !== null && enabled,
    // Rapor değişmez; bir kez alındıktan sonra yeniden çekmeye gerek yok.
    staleTime: Infinity,
  });
}

/**
 * Dosya yükleme mutation'ı, yükleme ilerlemesiyle birlikte.
 *
 * İlerleme TanStack Query'nin dışında ayrı bir state'te tutulur; mutation
 * state'i yalnızca başlangıç/bitiş biliyor, ara ilerlemeyi taşıyamaz.
 */
export function useCreateUpload() {
  const [progress, setProgress] = useState<UploadProgress | null>(null);

  const mutation = useMutation({
    mutationFn: (file: File) =>
      createUpload(file, { onProgress: setProgress }),
    onMutate: () => setProgress(null),
  });

  const reset = useCallback(() => {
    setProgress(null);
    mutation.reset();
  }, [mutation]);

  return { ...mutation, progress, reset };
}

export function useCreateAnalysis() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      request,
      openRouterApiKey,
    }: {
      request: AnalysisRequest;
      openRouterApiKey: string;
    }) => createAnalysis(request, openRouterApiKey),
    onSuccess: (created) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.analysisJob(created.analysis_id),
      });
    },
  });
}

export function useCancelAnalysis() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (analysisId: string) => cancelAnalysis(analysisId),
    onSuccess: (_data, analysisId) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.analysisJob(analysisId),
      });
    },
  });
}
