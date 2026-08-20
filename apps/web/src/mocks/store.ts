import { randomUUID } from "node:crypto";

import type {
  AnalysisJob,
  AnalysisReport,
  AnalysisRequest,
  AnalysisStatus,
  ProblemDetails,
  Upload,
  UploadStatus,
} from "@/lib/api/schemas";
import {
  conversationConfigSchema,
  ERROR_STATUS_BY_CODE,
  percentageHalfUp,
} from "@/lib/api/schemas";
import { estimateCostUsd } from "@/mocks/catalog";

/**
 * Backend hazır olana kadar kullanılan bellek içi sahte durum deposu.
 *
 * Amaç sadece "veri göstermek" değil, ADR §6'daki job durum makinesini
 * ZAMANA BAĞLI ilerletmek: polling, ilerleme çubuğu, iptal ve hata ekranları
 * ancak gerçekten süren bir iş varsa test edilebilir.
 *
 * Durum kasıtlı olarak türetilmiştir (kaydedilmez): her okumada geçen süreye
 * bakılıp o anki aşama hesaplanır. Böylece zamanlayıcı/kuyruk taklidi
 * gerekmez ve sunucu yeniden başlasa bile tutarsız ara durum kalmaz.
 *
 * BU KLASÖR GEÇİCİDİR. FastAPI devreye girince src/mocks/ ve
 * src/app/api/mock/ silinir, .env'de NEXT_PUBLIC_API_BASE_URL=/api/v1
 * yapılır ve next.config'e reverse proxy rewrite'ı eklenir.
 */

/** Dosya adına gömülen anahtar kelimelerle hata yolları tetiklenir. */
export type Scenario =
  "success" | "upload-failed" | "analysis-failed" | "rate-limited" | "slow" | "row-limit";

export function scenarioFromFilename(filename: string): Scenario {
  const name = filename.toLocaleLowerCase("tr");
  if (name.includes("bozuk")) return "upload-failed";
  if (name.includes("hata")) return "analysis-failed";
  if (name.includes("limit")) return "rate-limited";
  if (name.includes("yavas")) return "slow";
  // ADR-0002 #2'yi tarayıcıda çalıştırılabilir yapan senaryo: satır sınırı
  // aşılır ama iş reddedilmez, kırpılır ve uyarılır.
  if (name.includes("buyuk")) return "row-limit";
  return "success";
}

/** ADR-0001 §9 / ADR-0002 #2: aşılırsa reddedilmez, kırpılır.
 *
 * Sözleşmedeki tek kaynaktan geliyor — kendi kopyasını tutsaydı, mock'un
 * ürettiği gövdeler Zod invariant'larından sessizce ayrışabilirdi.
 */
const ROW_LIMIT_TOTAL_ROWS = 250_000;

interface UploadRecord {
  uploadId: string;
  filename: string;
  sizeBytes: number;
  createdAt: number;
  scenario: Scenario;
}

interface AnalysisRecord {
  analysisId: string;
  request: AnalysisRequest;
  createdAt: number;
  scenario: Scenario;
  cancelledAt: number | null;
}

interface IdempotencyRecord {
  fingerprint: string;
  responseBody: unknown;
  traceId: string;
  expiresAt: number;
}

/**
 * Modül seviyesindeki Map'ler dev sunucusunun ömrü boyunca yaşar. globalThis
 * üzerinde tutuluyorlar çünkü Next dev'de modüller hot reload sırasında
 * yeniden değerlendirilir ve normal bir modül değişkeni sıfırlanırdı.
 */
const globalStore = globalThis as unknown as {
  __auzefMockUploads?: Map<string, UploadRecord>;
  __auzefMockAnalyses?: Map<string, AnalysisRecord>;
  __auzefMockIdempotency?: Map<string, IdempotencyRecord>;
};

const uploads = (globalStore.__auzefMockUploads ??= new Map());
const analyses = (globalStore.__auzefMockAnalyses ??= new Map());
const idempotency = (globalStore.__auzefMockIdempotency ??= new Map());

export const IDEMPOTENCY_TTL_MS = 24 * 60 * 60 * 1000;

export type IdempotencyLookup =
  | { kind: "miss" }
  | { kind: "conflict" }
  | { kind: "replay"; responseBody: unknown; traceId: string };

function normalizedPath(path: string): string {
  const collapsed = path.replace(/\/{2,}/g, "/");
  return collapsed.length > 1 ? collapsed.replace(/\/$/, "") : collapsed;
}

function idempotencyStorageKey(method: string, path: string, key: string): string {
  return JSON.stringify([method.toUpperCase(), normalizedPath(path), key]);
}

/** Aynı tuple/fingerprint replay, aynı tuple/farklı fingerprint conflict'tir. */
export function lookupIdempotency(
  method: string,
  path: string,
  key: string,
  fingerprint: string,
): IdempotencyLookup {
  const storageKey = idempotencyStorageKey(method, path, key);
  const stored = idempotency.get(storageKey);

  if (!stored) return { kind: "miss" };
  if (stored.expiresAt <= Date.now()) {
    idempotency.delete(storageKey);
    return { kind: "miss" };
  }
  if (stored.fingerprint !== fingerprint) return { kind: "conflict" };

  return {
    kind: "replay",
    responseBody: stored.responseBody,
    traceId: stored.traceId,
  };
}

/** İlk 202'nin body ve trace metadata'sını 24 saat saklar. */
export function rememberIdempotency(
  method: string,
  path: string,
  key: string,
  fingerprint: string,
  responseBody: unknown,
): { responseBody: unknown; traceId: string } {
  const record: IdempotencyRecord = {
    fingerprint,
    responseBody,
    traceId: randomUUID(),
    expiresAt: Date.now() + IDEMPOTENCY_TTL_MS,
  };
  idempotency.set(idempotencyStorageKey(method, path, key), record);
  return { responseBody: record.responseBody, traceId: record.traceId };
}

const UPLOAD_VALIDATING_AFTER_MS = 1_500;
const UPLOAD_SETTLED_AFTER_MS = 4_000;

/** Aşama eşikleri, işin başlangıcından itibaren milisaniye. */
const ANALYSIS_STAGES: readonly { status: AnalysisStatus; until: number }[] = [
  { status: "queued", until: 3_000 },
  { status: "validating", until: 7_000 },
  { status: "preprocessing", until: 14_000 },
  { status: "analyzing", until: 32_000 },
  { status: "aggregating", until: 38_000 },
];

const ANALYSIS_TOTAL_MS = 38_000;
const SLOW_MULTIPLIER = 8;

export function problem(
  code: ProblemDetails["code"],
  status: number,
  title: string,
  detail: string,
  extra: Partial<ProblemDetails> = {},
): ProblemDetails {
  if (status !== ERROR_STATUS_BY_CODE[code]) {
    throw new Error(`${code} status=${ERROR_STATUS_BY_CODE[code]} taşımalı.`);
  }
  return {
    type: `/errors/${code.toLowerCase().replaceAll("_", "-")}`,
    title,
    status,
    code,
    detail,
    trace_id: randomUUID(),
    errors: [],
    ...extra,
  };
}

// ---------------------------------------------------------------- uploads

export function createUploadRecord(filename: string, sizeBytes: number): UploadRecord {
  const record: UploadRecord = {
    uploadId: randomUUID(),
    filename,
    sizeBytes,
    createdAt: Date.now(),
    scenario: scenarioFromFilename(filename),
  };
  uploads.set(record.uploadId, record);
  return record;
}

function uploadStatusOf(record: UploadRecord): UploadStatus {
  const elapsed = Date.now() - record.createdAt;
  if (elapsed < UPLOAD_VALIDATING_AFTER_MS) return "queued";
  if (elapsed < UPLOAD_SETTLED_AFTER_MS) return "validating";
  return record.scenario === "upload-failed" ? "failed" : "ready";
}

export function getUploadRecord(uploadId: string): Upload | null {
  const record = uploads.get(uploadId);
  if (!record) return null;

  const status = uploadStatusOf(record);

  return {
    upload_id: record.uploadId,
    status,
    filename: record.filename,
    size_bytes: record.sizeBytes,
    created_at: new Date(record.createdAt).toISOString(),
    profile: status === "ready" ? buildProfile(record.scenario === "row-limit") : null,
    error:
      status === "failed"
        ? problem(
            "UPLOAD_CORRUPT_OR_ENCRYPTED",
            422,
            "Dosya okunamadı",
            "OOXML yapısı doğrulanamadı.",
          )
        : null,
  };
}

export function deleteUploadRecord(uploadId: string): boolean {
  return uploads.delete(uploadId);
}

function buildProfile(exceedsRowLimit = false) {
  const totalRows = exceedsRowLimit ? ROW_LIMIT_TOTAL_ROWS : 48_213;

  return {
    sheets: [
      {
        name: "Mesajlar",
        row_count: totalRows,
        column_count: 5,
        columns: [
          {
            name: "tarih",
            index: 0,
            non_empty_count: totalRows,
            empty_count: 0,
            unique_count: 41_002,
            avg_length: 19,
            is_likely_text: false,
            sample_values: ["2026-03-01 09:12", "2026-03-01 09:14"],
          },
          {
            name: "kullanici_id",
            index: 1,
            non_empty_count: totalRows,
            empty_count: 0,
            unique_count: 12_884,
            avg_length: 8,
            is_likely_text: false,
            sample_values: ["[ID]", "[ID]"],
          },
          {
            name: "mesaj",
            index: 2,
            non_empty_count: totalRows - 1_107,
            empty_count: 1_107,
            unique_count: 31_540,
            avg_length: 64,
            is_likely_text: true,
            sample_values: [
              "sınav tarihleri ne zaman açıklanacak",
              "ders materyallerine nereden ulaşabilirim",
              "harç ödemesini nasıl yaparım",
            ],
          },
          {
            name: "kanal",
            index: 3,
            non_empty_count: totalRows,
            empty_count: 0,
            unique_count: 3,
            avg_length: 7,
            is_likely_text: false,
            sample_values: ["web", "mobil"],
          },
          {
            name: "yanit",
            index: 4,
            non_empty_count: totalRows - 3_233,
            empty_count: 3_233,
            unique_count: 8_712,
            avg_length: 128,
            is_likely_text: true,
            sample_values: ["Sınav takvimi için...", "Materyallere..."],
          },
        ],
      },
      {
        name: "Ham Veri",
        row_count: totalRows,
        column_count: 2,
        columns: [
          {
            name: "id",
            index: 0,
            non_empty_count: totalRows,
            empty_count: 0,
            unique_count: totalRows,
            avg_length: 6,
            is_likely_text: false,
            sample_values: ["1", "2"],
          },
          {
            name: "icerik",
            index: 1,
            non_empty_count: totalRows - 1_107,
            empty_count: 1_107,
            unique_count: 31_540,
            avg_length: 64,
            is_likely_text: true,
            sample_values: ["sınav ne zaman", "kayıt yenileme"],
          },
        ],
      },
    ],
    total_row_count: totalRows * 2,
    exceeds_row_limit: exceedsRowLimit,
  };
}

// --------------------------------------------------------------- analyses

export function createAnalysisRecord(request: AnalysisRequest): AnalysisRecord {
  const upload = uploads.get(request.upload_id);
  const record: AnalysisRecord = {
    analysisId: randomUUID(),
    request,
    createdAt: Date.now(),
    scenario: upload?.scenario ?? "success",
    cancelledAt: null,
  };
  analyses.set(record.analysisId, record);
  return record;
}

function totalDurationOf(record: AnalysisRecord): number {
  return record.scenario === "slow" ? ANALYSIS_TOTAL_MS * SLOW_MULTIPLIER : ANALYSIS_TOTAL_MS;
}

function stageOf(record: AnalysisRecord): {
  status: AnalysisStatus;
  progress: number;
} {
  if (record.cancelledAt !== null) {
    return { status: "cancelled", progress: 0 };
  }

  const scale = totalDurationOf(record) / ANALYSIS_TOTAL_MS;
  const elapsed = Date.now() - record.createdAt;
  const total = totalDurationOf(record);
  const progress = Math.min(100, (elapsed / total) * 100);

  for (const stage of ANALYSIS_STAGES) {
    if (elapsed < stage.until * scale) {
      return { status: stage.status, progress };
    }
  }

  // Hata senaryoları yalnızca iş gerçekten "analyzing" aşamasını geçtikten
  // sonra tetiklenir; böylece arayüz hatayı ilerleme ekranında karşılar,
  // henüz hiçbir şey göstermeden değil.
  if (record.scenario === "analysis-failed" || record.scenario === "rate-limited") {
    return { status: "failed", progress: 100 };
  }

  return { status: "completed", progress: 100 };
}

export function getAnalysisJobRecord(analysisId: string): AnalysisJob | null {
  const record = analyses.get(analysisId);
  if (!record) return null;

  const { status, progress } = stageOf(record);
  const remainingMs = Math.max(0, totalDurationOf(record) - (Date.now() - record.createdAt));

  return {
    analysis_id: record.analysisId,
    status,
    progress,
    created_at: new Date(record.createdAt).toISOString(),
    updated_at: new Date().toISOString(),
    estimated_seconds_remaining:
      status === "completed" || status === "failed" || status === "cancelled"
        ? null
        : Math.round(remainingMs / 1000),
    error:
      status !== "failed"
        ? null
        : record.scenario === "rate-limited"
          ? problem(
              "PROVIDER_RATE_LIMITED",
              429,
              "İstek sınırına ulaşıldı",
              "OpenRouter istek sınırı aşıldı.",
              { retry_after: 60 },
            )
          : problem(
              "PROVIDER_BAD_RESPONSE",
              502,
              "Geçersiz model yanıtı",
              "Model çıktısı şemaya uymadı; iki onarım denemesi başarısız oldu.",
            ),
  };
}

/** DELETE /analyses/{id} sonucu — ADR-0002 #9. */
export type CancelResult = "cancelled" | "not-found" | "terminal";

/**
 * Aktif job iptal edilir (204). Terminal job 409 JOB_CONFLICT üretir:
 * tamamlanmış veya çoktan iptal edilmiş bir işi "iptal etmek" sessizce
 * başarılı sayılmamalı. Bilinmeyen id 404.
 */
export function cancelAnalysisRecord(analysisId: string): CancelResult {
  const record = analyses.get(analysisId);
  if (!record) return "not-found";

  const { status } = stageOf(record);
  if (status === "completed" || status === "failed" || status === "cancelled") {
    return "terminal";
  }

  record.cancelledAt = Date.now();
  return "cancelled";
}

export function analysisExists(analysisId: string): boolean {
  return analyses.has(analysisId);
}

export function getAnalysisReportRecord(analysisId: string): AnalysisReport | null {
  const record = analyses.get(analysisId);
  if (!record) return null;
  if (stageOf(record).status !== "completed") return null;

  // ADR-0002 #2: satır sınırı aşılırsa iş reddedilmez. KIRPMA DA YOK —
  // analiz her zaman tüm satırları işler, dolayısıyla `considered` dosyanın
  // kendi satır sayısıdır ve uyarı üretilmez.
  const overRowLimit = record.scenario === "row-limit";
  const totalRows = overRowLimit ? ROW_LIMIT_TOTAL_ROWS : 48_213;
  const considered = totalRows;
  const analysisMode = record.request.analysis_mode ?? "message";
  const conversationConfig =
    analysisMode === "contextual_user_turns"
      ? conversationConfigSchema.parse(record.request.conversation_config)
      : null;
  // Bağlamsal mock'ta kullanıcı hedefi olmayan satırlar yalnız önceki
  // konuşma bağlamına girer. Mesaj modu eski adetleri aynen korur.
  const contextOnly = analysisMode === "contextual_user_turns" ? Math.round(considered * 0.1) : 0;
  const discarded = 1_107;
  const analyzed = considered - contextOnly - discarded;
  const unique = Math.round(analyzed * (31_540 / 47_106));
  const duplicate = analyzed - unique;

  // Adet ve oranlar tek kaynaktan türetiliyor: gerçek backend de oranı
  // sayımdan hesaplayacak (ADR §4), sabit yazılmış yüzdeler sözleşmeyi
  // yanlış temsil ederdi.
  const questions = [
    {
      id: "q1",
      canonical_question: "Sınav tarihleri ne zaman açıklanacak?",
      count: 11_680,
      examples: ["sınav tarihleri belli mi", "vize ne zaman"],
    },
    {
      id: "q2",
      canonical_question: "Ders materyallerine nereden ulaşabilirim?",
      count: 8_102,
      examples: ["ders kitabı nerede", "pdf'leri bulamıyorum"],
    },
    {
      id: "q3",
      canonical_question: "Harç ödemesini nasıl yaparım?",
      count: 5_748,
      examples: ["harç yatırma", "ödeme yapamıyorum"],
    },
    {
      id: "q4",
      canonical_question: "Kayıt yenileme işlemi nasıl yapılır?",
      count: 4_523,
      examples: ["kayıt yenilemedim ne olur"],
    },
    {
      id: "q5",
      canonical_question: "Sınav yerimi nereden öğrenebilirim?",
      count: 3_311,
      examples: ["sınav yeri", "hangi binada"],
    },
    {
      id: "q6",
      canonical_question: "Mazeret sınavına nasıl başvurulur?",
      count: 2_204,
      examples: ["mazeret sınavı başvuru"],
    },
    {
      id: "q7",
      canonical_question: "Not itirazı nasıl yapılır?",
      count: 1_640,
      examples: ["notuma itiraz etmek istiyorum"],
    },
    {
      id: "q8",
      canonical_question: "Öğrenci belgesi nasıl alınır?",
      count: 1_129,
      examples: ["öğrenci belgesi lazım"],
    },
  ];

  const themes = [
    { id: "t1", name: "Sınav ve takvim", questionIds: ["q1", "q5", "q6"] },
    { id: "t2", name: "Ders materyalleri", questionIds: ["q2"] },
    { id: "t3", name: "Ödeme ve kayıt", questionIds: ["q3", "q4"] },
    { id: "t4", name: "Belge ve itiraz", questionIds: ["q7", "q8"] },
  ];

  return {
    schema_version: "1.0",
    analysis_id: record.analysisId,
    status: "completed",
    generated_at: new Date().toISOString(),
    source_summary: {
      filename: uploads.get(record.request.upload_id)?.filename ?? "veri.xlsx",
      sheet_name: record.request.sheet_name,
      text_column: record.request.text_column,
      row_filters: record.request.row_filters,
      analysis_mode: analysisMode,
      conversation_config: conversationConfig,
      total_rows: totalRows,
    },
    preprocessing_summary: {
      analyzed_count: analyzed,
      context_only_count: contextOnly,
      discarded_count: discarded,
      duplicate_count: duplicate,
      redacted_count: Math.round(analyzed * (2_841 / 47_106)),
      unique_count: unique,
    },
    top_questions: questions.slice(0, record.request.top_n).map((q) => ({
      id: q.id,
      canonical_question: q.canonical_question,
      count: q.count,
      percentage: percentageHalfUp(q.count, analyzed),
      redacted_examples: q.examples,
    })),
    themes: themes.map((theme) => {
      // Tema adedi o temaya düşen TÜM mesajları kapsar; top_n kırpması
      // yalnızca soru listesini kısaltır, temanın gerçek büyüklüğünü değil.
      const count = theme.questionIds.reduce(
        (sum, id) => sum + (questions.find((q) => q.id === id)?.count ?? 0),
        0,
      );

      // ADR-0002 #5 ile donduruldu: yalnızca raporda gerçekten yer alan
      // sorulara bağlanır; aksi halde arayüz çözemeyeceği bir kimliğe
      // bağlantı vermiş olurdu. Backend aynı kuralı uygular ve
      // tests/fixtures/contract/analyses.result.200.truncated.json ile
      // iki taraftan da doğrulanır.
      const includedIds = new Set(questions.slice(0, record.request.top_n).map((q) => q.id));

      return {
        id: theme.id,
        name: theme.name,
        count,
        percentage: percentageHalfUp(count, analyzed),
        related_question_ids: theme.questionIds.filter((id) => includedIds.has(id)),
      };
    }),
    executive_summary:
      "Mesajların dörtte birinden fazlası sınav takvimiyle ilgili. Ders materyallerine erişim ve harç ödemesi ikinci ve üçüncü sırada geliyor. Bu üç başlık toplam mesajların yaklaşık yarısını oluşturuyor; chatbot bilgi tabanında öncelikli iyileştirme alanları bunlar.",
    warnings: [],
    model: record.request.model,
    prompt_version: record.request.prompt_version,
    prompt_hash: "sha256:2f8a1c9e4b7d",
    token_usage: {
      prompt_tokens: 1_284_000,
      completion_tokens: 96_400,
      total_tokens: 1_380_400,
      cached_tokens: 0,
      cache_write_tokens: 0,
    },
    estimated_cost_usd: estimateCostUsd(record.request.model),
    cost_source: "calculated",
    pricing_snapshot: null,
  };
}
