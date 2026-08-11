"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";

import { saveBlob } from "@/lib/download";

import {
  cancelAnalysis,
  createAnalysis,
  createUpload,
  deleteUpload,
  downloadAnalysisExport,
  getAnalysisJob,
  getAnalysisReport,
  getModels,
  getUpload,
} from "./endpoints";
import type { UploadProgress } from "./endpoints";
import { queryKeys } from "./query-keys";
import { LIMITS, isAnalysisSettled, isUploadSettled } from "./schemas";
import type { AnalysisRequest, ExportFormat } from "./schemas";

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
      // Hata durumunda polling DURUR. Yalnızca `data`ya bakılırsa, istek hata
      // verdiğinde veri hiç oluşmadığı için durum "henüz başlamadı" sanılır ve
      // poll sonsuza kadar sürer: ekran hata gösterirken arkada 2,5 saniyede
      // bir istek atılır. Geçici hataları `retry` politikası zaten ayrı ele
      // alıyor; buraya düşen hata kalıcıdır.
      if (query.state.status === "error") return false;
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
      // useUploadStatus'taki ile aynı kural: hata kalıcıdır, poll durur.
      if (query.state.status === "error") return false;
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
 * Dosya yükleme mutation'ı, yükleme ilerlemesi ve iptal ile birlikte.
 *
 * İlerleme TanStack Query'nin dışında ayrı bir state'te tutulur; mutation
 * state'i yalnızca başlangıç/bitiş biliyor, ara ilerlemeyi taşıyamaz.
 *
 * `cancel` zorunlu: gerçek dosyalar ~130 MB ve yükleme dakikalar sürüyor.
 * İptal edilemeyen bir yüklemede yanlış dosya seçen kullanıcının tek çıkışı
 * sekmeyi kapatmak olurdu.
 */
export function useCreateUpload() {
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const mutation = useMutation({
    mutationFn: (file: File) => {
      const controller = new AbortController();
      abortRef.current = controller;
      return createUpload(file, {
        onProgress: setProgress,
        signal: controller.signal,
      });
    },
    onMutate: () => setProgress(null),
    onSettled: () => {
      abortRef.current = null;
    },
  });

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setProgress(null);
  }, []);

  const reset = useCallback(() => {
    setProgress(null);
    mutation.reset();
  }, [mutation]);

  return { ...mutation, progress, cancel, reset };
}

/**
 * Yüklemeyi iptal eder (ADR §6: DELETE /uploads/{id} — iptal ve cleanup).
 *
 * Profilleme sürerken vazgeçen kullanıcı için: kayıt silinmezse yüklenen
 * dosya sunucuda lifecycle süresi dolana kadar durur.
 */
export function useDeleteUpload() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (uploadId: string) => deleteUpload(uploadId),
    onSuccess: (_data, uploadId) => {
      queryClient.removeQueries({ queryKey: queryKeys.upload(uploadId) });
    },
  });
}

/**
 * Rapor dışa aktarma.
 *
 * Dosya bilerek `<a href>` ile değil fetch ile indiriliyor: doğrudan bağlantıda
 * export hata dönerse tarayıcı RFC 9457 problem JSON'unu ham haliyle ekrana
 * basar. Gövdeyi kendimiz alınca hata, arayüzün geri kalanıyla aynı Türkçe
 * mesaj tablosundan gösterilebiliyor.
 */
export function useExportAnalysis() {
  return useMutation({
    mutationFn: ({ analysisId, format }: { analysisId: string; format: ExportFormat }) =>
      downloadAnalysisExport(analysisId, format),
    onSuccess: (file) => saveBlob(file.blob, file.filename),
  });
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
