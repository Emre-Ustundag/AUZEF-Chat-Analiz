import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { analysisJobSchema, analysisReportSchema, uploadSchema } from "@/lib/api/schemas";
import type { AnalysisRequest } from "@/lib/api/schemas";

import {
  cancelAnalysisRecord,
  createAnalysisRecord,
  createUploadRecord,
  getAnalysisJobRecord,
  getAnalysisReportRecord,
  getUploadRecord,
} from "./store";

/**
 * Mock backend'in ürettiği her gövde, arayüzün doğrulama için kullandığı
 * şemadan geçmelidir.
 *
 * Bu testin asıl işi mock'u değil SÖZLEŞMEYİ korumak: şema ile mock zamanla
 * birbirinden ayrışırsa, arayüz mock'a karşı sorunsuz geliştirilip gerçek
 * backend'e bağlanınca patlar. Ayrışma burada yakalanır.
 */

const START = new Date("2026-08-11T10:00:00Z");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(START);
});

afterEach(() => {
  vi.useRealTimers();
});

function advance(seconds: number) {
  vi.setSystemTime(new Date(START.getTime() + seconds * 1000));
}

function analysisRequestFor(uploadId: string): AnalysisRequest {
  return {
    upload_id: uploadId,
    sheet_name: "Mesajlar",
    text_column: "mesaj",
    model: "anthropic/claude-sonnet-4",
    prompt_version: "faq_analysis/v1",
    top_n: 8,
    max_cost_usd: 10,
  };
}

describe("upload mock'u", () => {
  it("her aşamada uploadSchema'ya uyar", () => {
    const { uploadId } = createUploadRecord("veri.xlsx", 1024);

    for (const seconds of [0, 2, 5, 60]) {
      advance(seconds);
      const result = uploadSchema.safeParse(getUploadRecord(uploadId));
      expect(result.success, `t+${seconds}sn`).toBe(true);
    }
  });

  it("queued -> validating -> ready sırasını izler", () => {
    const { uploadId } = createUploadRecord("veri.xlsx", 1024);

    advance(0);
    expect(getUploadRecord(uploadId)?.status).toBe("queued");
    advance(2);
    expect(getUploadRecord(uploadId)?.status).toBe("validating");
    advance(5);
    expect(getUploadRecord(uploadId)?.status).toBe("ready");
  });

  it("yalnızca ready durumunda profil döner", () => {
    const { uploadId } = createUploadRecord("veri.xlsx", 1024);

    advance(2);
    expect(getUploadRecord(uploadId)?.profile).toBeNull();
    advance(5);
    expect(getUploadRecord(uploadId)?.profile).not.toBeNull();
  });

  it("dosya adındaki 'bozuk' anahtar kelimesi hata yolunu tetikler", () => {
    const { uploadId } = createUploadRecord("bozuk_veri.xlsx", 1024);
    advance(5);

    const upload = getUploadRecord(uploadId);
    expect(upload?.status).toBe("failed");
    expect(upload?.error?.code).toBe("UPLOAD_CORRUPT_OR_ENCRYPTED");
  });

  it("bilinmeyen upload için null döner", () => {
    expect(getUploadRecord("yok-boyle-bir-kayit")).toBeNull();
  });
});

describe("analiz mock'u", () => {
  it("ADR §6'daki aşama sırasını izler", () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    const { analysisId } = createAnalysisRecord(analysisRequestFor(upload.uploadId));

    const observed: string[] = [];
    for (let seconds = 0; seconds <= 40; seconds += 2) {
      advance(seconds);
      const status = getAnalysisJobRecord(analysisId)?.status;
      if (status && observed.at(-1) !== status) observed.push(status);
    }

    expect(observed).toEqual([
      "queued",
      "validating",
      "preprocessing",
      "analyzing",
      "aggregating",
      "completed",
    ]);
  });

  it("her aşamada analysisJobSchema'ya uyar", () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    const { analysisId } = createAnalysisRecord(analysisRequestFor(upload.uploadId));

    for (const seconds of [0, 5, 10, 20, 35, 40]) {
      advance(seconds);
      const result = analysisJobSchema.safeParse(getAnalysisJobRecord(analysisId));
      expect(result.success, `t+${seconds}sn`).toBe(true);
    }
  });

  it("ilerleme geri gitmez ve 100'ü aşmaz", () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    const { analysisId } = createAnalysisRecord(analysisRequestFor(upload.uploadId));

    let previous = -1;
    for (let seconds = 0; seconds <= 45; seconds += 1) {
      advance(seconds);
      const progress = getAnalysisJobRecord(analysisId)!.progress;
      expect(progress).toBeGreaterThanOrEqual(previous);
      expect(progress).toBeLessThanOrEqual(100);
      previous = progress;
    }
  });

  it("iptal terminal durumdur ve ilerlemeyi durdurur", () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    const { analysisId } = createAnalysisRecord(analysisRequestFor(upload.uploadId));

    advance(10);
    cancelAnalysisRecord(analysisId);
    expect(getAnalysisJobRecord(analysisId)?.status).toBe("cancelled");

    // İptalden sonra zaman ilerlese de durum değişmemeli.
    advance(60);
    expect(getAnalysisJobRecord(analysisId)?.status).toBe("cancelled");
  });

  it("terminal durumda kalan süre tahmini vermez", () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    const { analysisId } = createAnalysisRecord(analysisRequestFor(upload.uploadId));

    advance(10);
    expect(getAnalysisJobRecord(analysisId)?.estimated_seconds_remaining).toBeGreaterThan(0);
    advance(40);
    expect(getAnalysisJobRecord(analysisId)?.estimated_seconds_remaining).toBeNull();
  });

  it("'hata' senaryosu failed ve sağlayıcı hatasıyla biter", () => {
    const upload = createUploadRecord("hatali_veri.xlsx", 1024);
    const { analysisId } = createAnalysisRecord(analysisRequestFor(upload.uploadId));

    advance(40);
    const job = getAnalysisJobRecord(analysisId);
    expect(job?.status).toBe("failed");
    expect(job?.error?.code).toBe("PROVIDER_BAD_RESPONSE");
  });

  it("'limit' senaryosu retry_after ile 429 döner", () => {
    const upload = createUploadRecord("limit_veri.xlsx", 1024);
    const { analysisId } = createAnalysisRecord(analysisRequestFor(upload.uploadId));

    advance(40);
    const job = getAnalysisJobRecord(analysisId);
    expect(job?.error?.code).toBe("PROVIDER_RATE_LIMITED");
    expect(job?.error?.retry_after).toBe(60);
  });
});

describe("analiz raporu mock'u", () => {
  function completedReport(topN = 8) {
    // advance() zamanı START'a göre mutlak ayarlıyor; aynı testte ikinci kez
    // çağrılırsa kayıt ilerlemiş saatte oluşup hiç yaşlanmaz. Her çağrı kendi
    // zaman çizgisinde başlasın.
    vi.setSystemTime(START);
    const upload = createUploadRecord("veri.xlsx", 1024);
    const { analysisId } = createAnalysisRecord({
      ...analysisRequestFor(upload.uploadId),
      top_n: topN,
    });
    advance(40);
    return getAnalysisReportRecord(analysisId);
  }

  it("tamamlanmadan rapor vermez", () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    const { analysisId } = createAnalysisRecord(analysisRequestFor(upload.uploadId));

    advance(10);
    expect(getAnalysisReportRecord(analysisId)).toBeNull();
  });

  it("analysisReportSchema'ya uyar", () => {
    const result = analysisReportSchema.safeParse(completedReport());
    expect(result.success).toBe(true);
  });

  it("top_n kadar soru döner", () => {
    expect(completedReport(3)?.top_questions).toHaveLength(3);
  });

  it("oranlar adetlerden türetilir, sabit yazılmaz", () => {
    const report = completedReport()!;
    const analyzed = report.preprocessing_summary.analyzed_count;

    for (const question of report.top_questions) {
      const expected = Number(((question.count / analyzed) * 100).toFixed(1));
      expect(question.percentage).toBe(expected);
    }
  });

  it("README'deki örnek tablo oranlarını üretir", () => {
    // README'nin örnek çıktısı sözleşmenin parçası: %24,8 / %17,2 / %12,2 / %9,6
    const percentages = completedReport()!
      .top_questions.slice(0, 4)
      .map((q) => q.percentage);

    expect(percentages).toEqual([24.8, 17.2, 12.2, 9.6]);
  });

  it("tema toplamı analiz edilen kayıt sayısını aşmaz", () => {
    const report = completedReport()!;
    const themeTotal = report.themes.reduce((sum, t) => sum + t.count, 0);

    expect(themeTotal).toBeLessThanOrEqual(report.preprocessing_summary.analyzed_count);
  });

  it.each([3, 5, 8])("top_n=%i olduğunda tema bağlantıları çözülebilir kalır", (topN) => {
    // Dashboard tema -> soru bağlantısını kuracak. top_n kırpması sonrası
    // raporda olmayan bir kimliğe bağlanmak arayüzde kırık bağlantı demek.
    const report = completedReport(topN)!;
    const questionIds = new Set(report.top_questions.map((q) => q.id));

    for (const theme of report.themes) {
      for (const id of theme.related_question_ids) {
        expect(questionIds.has(id), `${theme.name} -> ${id}`).toBe(true);
      }
    }
  });

  it("tema adedi top_n kırpmasından etkilenmez", () => {
    // Tema büyüklüğü gerçek mesaj sayısıdır; kaç soru gösterildiğine bağlı
    // olarak değişirse dashboard'daki oranlar yanlış olur.
    const withThree = completedReport(3)!;
    const withEight = completedReport(8)!;

    expect(withThree.themes.map((t) => t.count)).toEqual(withEight.themes.map((t) => t.count));
  });
});
