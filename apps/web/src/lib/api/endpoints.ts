import * as z from "zod";

import {
  API_BASE_URL,
  apiRequest,
  openRouterKeyHeader,
  toApiError,
  unknownApiError,
} from "./client";
import { ApiError } from "./client";
import {
  analysisCreatedSchema,
  analysisJobSchema,
  analysisReportSchema,
  modelListSchema,
  problemDetailsSchema,
  uploadCreatedSchema,
  uploadSchema,
} from "./schemas";
import type {
  AnalysisCreated,
  AnalysisJob,
  AnalysisReport,
  AnalysisRequest,
  ExportFormat,
  ModelList,
  Upload,
  UploadCreated,
} from "./schemas";

/** ADR §6'daki endpoint'lerin tiplendirilmiş karşılıkları. */

export function getUpload(
  uploadId: string,
  signal?: AbortSignal,
): Promise<Upload> {
  return apiRequest(`/uploads/${uploadId}`, uploadSchema, { signal });
}

export function deleteUpload(uploadId: string): Promise<void> {
  return apiRequest(`/uploads/${uploadId}`, z.void(), { method: "DELETE" });
}

export function getModels(signal?: AbortSignal): Promise<ModelList> {
  return apiRequest("/models", modelListSchema, { signal });
}

export function createAnalysis(
  request: AnalysisRequest,
  openRouterApiKey: string,
): Promise<AnalysisCreated> {
  return apiRequest("/analyses", analysisCreatedSchema, {
    method: "POST",
    body: JSON.stringify(request),
    headers: {
      "Content-Type": "application/json",
      ...openRouterKeyHeader(openRouterApiKey),
    },
  });
}

export function getAnalysisJob(
  analysisId: string,
  signal?: AbortSignal,
): Promise<AnalysisJob> {
  return apiRequest(`/analyses/${analysisId}`, analysisJobSchema, { signal });
}

export function getAnalysisReport(
  analysisId: string,
  signal?: AbortSignal,
): Promise<AnalysisReport> {
  return apiRequest(`/analyses/${analysisId}/result`, analysisReportSchema, {
    signal,
  });
}

export function cancelAnalysis(analysisId: string): Promise<void> {
  return apiRequest(`/analyses/${analysisId}`, z.void(), { method: "DELETE" });
}

export function analysisExportUrl(
  analysisId: string,
  format: ExportFormat,
): string {
  return `${API_BASE_URL}/analyses/${analysisId}/export?format=${format}`;
}

export interface ExportedFile {
  blob: Blob;
  filename: string;
}

/**
 * Rapor dosyasını indirir.
 *
 * Gövde şemadan geçmiyor — xlsx ikili, JSON'ın içeriği de zaten rapor
 * ekranında doğrulanmış durumda. Buradaki asıl iş HATA yolunu düzgün
 * kurmak: doğrudan bir <a href> ile indirilseydi 409/500 cevabında tarayıcı
 * problem JSON'unu ham haliyle gösterirdi.
 */
export async function downloadAnalysisExport(
  analysisId: string,
  format: ExportFormat,
): Promise<ExportedFile> {
  let response: Response;
  try {
    response = await fetch(analysisExportUrl(analysisId, format));
  } catch {
    throw unknownApiError(0, "Sunucuya ulaşılamadı.");
  }

  if (!response.ok) {
    throw await toApiError(response);
  }

  return {
    blob: await response.blob(),
    filename:
      filenameFromContentDisposition(
        response.headers.get("Content-Disposition"),
      ) ?? `analiz-${analysisId}.${format}`,
  };
}

/**
 * Content-Disposition başlığındaki dosya adını okur.
 *
 * Yalnızca düz `filename="..."` biçimi destekleniyor; backend dosya adını
 * kendisi üretiyor ve ASCII tutuyor (ADR §6), RFC 5987'nin `filename*`
 * kodlamasına ihtiyaç duyulmuyor. Okunamazsa çağıran taraf kendi adını üretir.
 */
function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const match = /filename="([^"]+)"/.exec(header);
  return match ? match[1] : null;
}

export interface UploadProgress {
  loadedBytes: number;
  totalBytes: number;
  /** 0-100 arası. */
  percentage: number;
}

/**
 * POST /api/v1/uploads — multipart dosya yükleme.
 *
 * Bilerek fetch yerine XMLHttpRequest kullanılıyor: fetch yükleme (upload)
 * ilerlemesi için bir olay yayınlamıyor. Gerçek dosyalar ~130 MB olduğu için
 * kullanıcıya yükleme sırasında ilerleme göstermek zorunlu; ilerlemesiz bir
 * yükleme dakikalarca donmuş bir arayüz demek.
 */
export function createUpload(
  file: File,
  options: {
    onProgress?: (progress: UploadProgress) => void;
    signal?: AbortSignal;
  } = {},
): Promise<UploadCreated> {
  const { onProgress, signal } = options;

  return new Promise<UploadCreated>((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE_URL}/uploads`);
    xhr.responseType = "text";

    if (onProgress) {
      xhr.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        onProgress({
          loadedBytes: event.loaded,
          totalBytes: event.total,
          percentage: (event.loaded / event.total) * 100,
        });
      });
    }

    const abort = () => xhr.abort();
    signal?.addEventListener("abort", abort);

    const cleanup = () => signal?.removeEventListener("abort", abort);

    xhr.addEventListener("error", () => {
      cleanup();
      reject(unknownApiError(0, "Dosya yüklenirken bağlantı koptu."));
    });

    xhr.addEventListener("abort", () => {
      cleanup();
      reject(new DOMException("Yükleme iptal edildi.", "AbortError"));
    });

    xhr.addEventListener("load", () => {
      cleanup();

      let payload: unknown;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch {
        reject(unknownApiError(xhr.status, "Sunucu yanıtı çözümlenemedi."));
        return;
      }

      if (xhr.status < 200 || xhr.status >= 300) {
        const problem = problemDetailsSchema.safeParse(payload);
        reject(
          problem.success
            ? new ApiError(problem.data)
            : unknownApiError(
                xhr.status,
                `Yükleme başarısız (HTTP ${xhr.status}).`,
              ),
        );
        return;
      }

      const parsed = uploadCreatedSchema.safeParse(payload);
      if (!parsed.success) {
        reject(
          unknownApiError(
            xhr.status,
            `Sunucu yanıtı beklenen sözleşmeye uymuyor: ${z.prettifyError(parsed.error)}`,
          ),
        );
        return;
      }

      resolve(parsed.data);
    });

    xhr.send(formData);
  });
}
