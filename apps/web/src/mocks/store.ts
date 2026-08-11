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
  | "success"
  | "upload-failed"
  | "analysis-failed"
  | "rate-limited"
  | "slow";

export function scenarioFromFilename(filename: string): Scenario {
  const name = filename.toLocaleLowerCase("tr");
  if (name.includes("bozuk")) return "upload-failed";
  if (name.includes("hata")) return "analysis-failed";
  if (name.includes("limit")) return "rate-limited";
  if (name.includes("yavas")) return "slow";
  return "success";
}

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

/**
 * Modül seviyesindeki Map'ler dev sunucusunun ömrü boyunca yaşar. globalThis
 * üzerinde tutuluyorlar çünkü Next dev'de modüller hot reload sırasında
 * yeniden değerlendirilir ve normal bir modül değişkeni sıfırlanırdı.
 */
const globalStore = globalThis as unknown as {
  __auzefMockUploads?: Map<string, UploadRecord>;
  __auzefMockAnalyses?: Map<string, AnalysisRecord>;
};

const uploads = (globalStore.__auzefMockUploads ??= new Map());
const analyses = (globalStore.__auzefMockAnalyses ??= new Map());

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

export function createUploadRecord(
  filename: string,
  sizeBytes: number,
): UploadRecord {
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
    profile: status === "ready" ? buildProfile() : null,
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

function buildProfile() {
  return {
    sheets: [
      {
        name: "Mesajlar",
        row_count: 48_213,
        column_count: 5,
        columns: [
          {
            name: "tarih",
            index: 0,
            non_empty_count: 48_213,
            empty_count: 0,
            unique_count: 41_002,
            avg_length: 19,
            is_likely_text: false,
            sample_values: ["2026-03-01 09:12", "2026-03-01 09:14"],
          },
          {
            name: "kullanici_id",
            index: 1,
            non_empty_count: 48_213,
            empty_count: 0,
            unique_count: 12_884,
            avg_length: 8,
            is_likely_text: false,
            sample_values: ["[ID]", "[ID]"],
          },
          {
            name: "mesaj",
            index: 2,
            non_empty_count: 47_106,
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
            non_empty_count: 48_213,
            empty_count: 0,
            unique_count: 3,
            avg_length: 7,
            is_likely_text: false,
            sample_values: ["web", "mobil"],
          },
          {
            name: "yanit",
            index: 4,
            non_empty_count: 44_980,
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
        row_count: 48_213,
        column_count: 2,
        columns: [
          {
            name: "id",
            index: 0,
            non_empty_count: 48_213,
            empty_count: 0,
            unique_count: 48_213,
            avg_length: 6,
            is_likely_text: false,
            sample_values: ["1", "2"],
          },
          {
            name: "icerik",
            index: 1,
            non_empty_count: 47_106,
            empty_count: 1_107,
            unique_count: 31_540,
            avg_length: 64,
            is_likely_text: true,
            sample_values: ["sınav ne zaman", "kayıt yenileme"],
          },
        ],
      },
    ],
    total_row_count: 48_213,
    exceeds_row_limit: false,
  };
}

// --------------------------------------------------------------- analyses

export function createAnalysisRecord(
  request: AnalysisRequest,
): AnalysisRecord {
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
  return record.scenario === "slow"
    ? ANALYSIS_TOTAL_MS * SLOW_MULTIPLIER
    : ANALYSIS_TOTAL_MS;
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
  const remainingMs = Math.max(
    0,
    totalDurationOf(record) - (Date.now() - record.createdAt),
  );

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

export function cancelAnalysisRecord(analysisId: string): boolean {
  const record = analyses.get(analysisId);
  if (!record) return false;
  record.cancelledAt = Date.now();
  return true;
}

export function analysisExists(analysisId: string): boolean {
  return analyses.has(analysisId);
}

export function getAnalysisReportRecord(
  analysisId: string,
): AnalysisReport | null {
  const record = analyses.get(analysisId);
  if (!record) return null;
  if (stageOf(record).status !== "completed") return null;

  const analyzed = 47_106;

  // Adet ve oranlar tek kaynaktan türetiliyor: gerçek backend de oranı
  // sayımdan hesaplayacak (ADR §4), sabit yazılmış yüzdeler sözleşmeyi
  // yanlış temsil ederdi.
  const questions = [
    { id: "q1", canonical_question: "Sınav tarihleri ne zaman açıklanacak?", count: 11_680, confidence: 0.94, examples: ["sınav tarihleri belli mi", "vize ne zaman"] },
    { id: "q2", canonical_question: "Ders materyallerine nereden ulaşabilirim?", count: 8_102, confidence: 0.91, examples: ["ders kitabı nerede", "pdf'leri bulamıyorum"] },
    { id: "q3", canonical_question: "Harç ödemesini nasıl yaparım?", count: 5_748, confidence: 0.89, examples: ["harç yatırma", "ödeme yapamıyorum"] },
    { id: "q4", canonical_question: "Kayıt yenileme işlemi nasıl yapılır?", count: 4_523, confidence: 0.87, examples: ["kayıt yenilemedim ne olur"] },
    { id: "q5", canonical_question: "Sınav yerimi nereden öğrenebilirim?", count: 3_311, confidence: 0.85, examples: ["sınav yeri", "hangi binada"] },
    { id: "q6", canonical_question: "Mazeret sınavına nasıl başvurulur?", count: 2_204, confidence: 0.82, examples: ["mazeret sınavı başvuru"] },
    { id: "q7", canonical_question: "Not itirazı nasıl yapılır?", count: 1_640, confidence: 0.78, examples: ["notuma itiraz etmek istiyorum"] },
    { id: "q8", canonical_question: "Öğrenci belgesi nasıl alınır?", count: 1_129, confidence: 0.76, examples: ["öğrenci belgesi lazım"] },
  ];

  const themes = [
    { id: "t1", name: "Sınav ve takvim", questionIds: ["q1", "q5", "q6"] },
    { id: "t2", name: "Ders materyalleri", questionIds: ["q2"] },
    { id: "t3", name: "Ödeme ve kayıt", questionIds: ["q3", "q4"] },
    { id: "t4", name: "Belge ve itiraz", questionIds: ["q7", "q8"] },
  ];

  const pct = (count: number) => Number(((count / analyzed) * 100).toFixed(1));

  return {
    schema_version: "1.0",
    analysis_id: record.analysisId,
    status: "completed",
    generated_at: new Date().toISOString(),
    source_summary: {
      filename: uploads.get(record.request.upload_id)?.filename ?? "veri.xlsx",
      sheet_name: record.request.sheet_name,
      text_column: record.request.text_column,
      total_rows: 48_213,
    },
    preprocessing_summary: {
      analyzed_count: analyzed,
      discarded_count: 1_107,
      duplicate_count: 15_566,
      redacted_count: 2_841,
      unique_count: 31_540,
    },
    top_questions: questions.slice(0, record.request.top_n).map((q) => ({
      id: q.id,
      canonical_question: q.canonical_question,
      count: q.count,
      percentage: pct(q.count),
      confidence: q.confidence,
      redacted_examples: q.examples,
    })),
    themes: themes.map((theme) => {
      // Tema adedi o temaya düşen TÜM mesajları kapsar; top_n kırpması
      // yalnızca soru listesini kısaltır, temanın gerçek büyüklüğünü değil.
      const count = theme.questionIds.reduce(
        (sum, id) => sum + (questions.find((q) => q.id === id)?.count ?? 0),
        0,
      );

      // SÖZLEŞME SORUSU: ADR §8 related_question_ids'in top_n kırpmasından
      // sonra ne göstereceğini tanımlamıyor. Burada yalnızca raporda gerçekten
      // yer alan sorulara bağlanıyor; aksi halde arayüz çözemeyeceği bir
      // kimliğe bağlantı vermiş olurdu. Backend'in aynı davranışı seçmesi
      // gerekiyor, teyit edilmeli.
      const includedIds = new Set(
        questions.slice(0, record.request.top_n).map((q) => q.id),
      );

      return {
        id: theme.id,
        name: theme.name,
        count,
        percentage: pct(count),
        related_question_ids: theme.questionIds.filter((id) =>
          includedIds.has(id),
        ),
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
    },
    estimated_cost_usd: 4.1412,
  };
}
