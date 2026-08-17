import { beforeAll, describe, expect, it } from "vitest";
import type * as z from "zod";

import {
  analysisCreatedSchema,
  analysisJobSchema,
  analysisReportSchema,
  analysisRequestSchema,
  livenessResponseSchema,
  LIMITS,
  modelListSchema,
  percentageHalfUp,
  problemDetailsSchema,
  readinessResponseSchema,
  uploadCreatedSchema,
  uploadSchema,
} from "./index";
import { readFixture, readManifest, type ManifestCase } from "./contract-paths";

/**
 * DRIFT KONTROLÜ — Katman 1.
 *
 * `tests/fixtures/contract/` Python tarafında Pydantic INSTANCE'larından
 * üretiliyor (apps/backend/scripts/export_fixtures.py), yani gerçek
 * serializer'lardan geçiyor. Burada aynı dosyaları Zod ile doğruluyoruz:
 * iki dilin sözleşmeyi aynı okuduğunun kanıtı bu.
 *
 * CI ayrıca `export_fixtures.py --check` çalıştırır; o adım olmadan biri
 * Pydantic modelini değiştirip yeniden üretmeyi unutabilir ve iki suite de
 * bayat dosyalara karşı yeşil kalırdı.
 */

/** Manifest'teki model adı -> Zod şeması. Tek bağlama noktası. */
const SCHEMAS: Record<string, z.ZodType> = {
  Upload: uploadSchema,
  UploadCreated: uploadCreatedSchema,
  ModelList: modelListSchema,
  AnalysisRequest: analysisRequestSchema,
  AnalysisCreated: analysisCreatedSchema,
  AnalysisJob: analysisJobSchema,
  AnalysisReport: analysisReportSchema,
  LivenessResponse: livenessResponseSchema,
  ReadinessResponse: readinessResponseSchema,
  ProblemDetails: problemDetailsSchema,
};

const manifest = readManifest();
const withPayload = manifest.cases.filter(
  (c): c is ManifestCase & { file: string; model: string } => c.file !== null && c.model !== null,
);

describe("fixture envanteri", () => {
  it("manifest boş değil", () => {
    expect(withPayload.length).toBeGreaterThan(20);
  });

  // Çift yönlü: iki taraftan birindeki yeniden adlandırma düşsün.
  it("her manifest modeli için bir Zod şeması var", () => {
    const missing = [...new Set(withPayload.map((c) => c.model))].filter((m) => !(m in SCHEMAS));
    expect(missing).toEqual([]);
  });

  it("her Zod şeması en az bir manifest kaydından referanslanıyor", () => {
    const referenced = new Set(withPayload.map((c) => c.model));
    const orphans = Object.keys(SCHEMAS).filter((name) => !referenced.has(name));
    expect(orphans).toEqual([]);
  });

  /**
   * MAX_ROWS iki dilde ayrı ayrı yazılı bir sabit ve ikisi de onu cevap
   * invariant'ında kullanıyor (`analyzed_count + discarded_count ==
   * min(total_rows, MAX_ROWS)`, `exceeds_row_limit`, `ROW_LIMIT_TRUNCATED`).
   * Bu iddia olmadan taraflardan biri değiştiğinde hiçbir kontrol düşmez;
   * backend'in DOĞRU ürettiği cevap Zod'da patlar ve kullanıcı sentetik bir
   * INTERNAL_ERROR görür. Sınır sözleşmede donmuştur (apps/backend/app/core/
   * config.py → MAX_ROWS); değiştirmek contract_version bump'ı gerektirir.
   */
  it("LIMITS sabitleri backend'in donmuş sınırlarıyla aynı", () => {
    expect(LIMITS.MAX_UPLOAD_BYTES).toBe(manifest.limits.max_upload_bytes);
    expect(LIMITS.MAX_ROWS).toBe(manifest.limits.max_rows);
  });

  it("204 cevapları gövdesiz kayıtlıdır", () => {
    const noContent = manifest.cases.filter((c) => c.status === 204);
    expect(noContent.length).toBeGreaterThan(0);
    for (const c of noContent) {
      expect(c.file).toBeNull();
      expect(c.model).toBeNull();
    }
  });
});

describe.each(withPayload)("fixture $id ($model)", (testCase) => {
  const raw = readFixture<Record<string, unknown>>(testCase.file);

  it("Zod şemasından geçer", () => {
    const result = SCHEMAS[testCase.model].safeParse(raw);
    expect(result.success ? null : result.error.issues).toBeNull();
  });

  /**
   * EN DEĞERLİ İDDİA. Zod bilinmeyen anahtarları SESSİZCE strip eder, yani
   * düz bir `.parse()` backend gövdeye yeni bir alan eklediğinde de yeşil
   * kalır — tam olarak yakalamak istediğimiz drift. `toEqual` ayrıştırılmış
   * sonucu ham gövdeyle karşılaştırdığı için strip edilen alan düşer.
   *
   * `toStrictEqual` değil: Vitest `undefined` değerli property ile hiç
   * olmayan property'yi eşit sayar, böylece `retry_after` bulunmayan vakalar
   * doğru şekilde geçer.
   */
  it("hiçbir alan strip edilmez (toEqual)", () => {
    expect(SCHEMAS[testCase.model].parse(raw)).toEqual(raw);
  });
});

describe("ADR-0002 #6 — retry_after yalnızca 429'da", () => {
  const problems = withPayload.filter((c) => c.model === "ProblemDetails");

  it("429 fixture'ı retry_after taşır", () => {
    const rateLimited = problems.find((c) => c.status === 429);
    expect(rateLimited).toBeDefined();
    const body = readFixture<Record<string, unknown>>(rateLimited!.file);
    expect(body.retry_after).toBeTypeOf("number");
  });

  // Kritik: `null` DEĞİL, alanın hiç bulunmaması gerekiyor. Aksi hâlde
  // problemDetailsSchema düşer ve toApiError her hatayı INTERNAL_ERROR'a
  // çevirir; tüm Türkçe hata tablosu sessizce ölür.
  it.each(problems.filter((c) => c.status !== 429))(
    "$id gövdesinde retry_after anahtarı HİÇ yok",
    (testCase) => {
      const body = readFixture<Record<string, unknown>>(testCase.file);
      expect(Object.keys(body)).not.toContain("retry_after");
    },
  );
});

describe("ADR-0002 #5 — top_n kırpması ve related_question_ids", () => {
  const full = readFixture<Record<string, never>>("analyses.result.200.json");
  const truncated = readFixture<Record<string, never>>("analyses.result.200.over-row-limit.json");

  // `parse` describe gövdesinde DEĞİL burada: modül seviyesinde çağrılırsa
  // bir sözleşme ayrışması suite'i yüklenirken çökertir ve vitest o dosyadaki
  // diğer TÜM iddiaları — ayrışmanın sebebini açıklayan `LIMITS` kontrolü
  // dahil — hiç çalıştırmadan atlar. Teşhis mesajı yanlış yeri gösterirdi.
  let parsedFull: ReturnType<typeof analysisReportSchema.parse>;
  let parsedTruncated: ReturnType<typeof analysisReportSchema.parse>;
  beforeAll(() => {
    parsedFull = analysisReportSchema.parse(full);
    parsedTruncated = analysisReportSchema.parse(truncated);
  });

  it("kırpılmış raporda daha az soru var", () => {
    expect(parsedTruncated.top_questions.length).toBeLessThan(parsedFull.top_questions.length);
  });

  it("related_question_ids yalnızca raporda bulunan sorulara işaret eder", () => {
    const presentIds = new Set(parsedTruncated.top_questions.map((q) => q.id));
    for (const theme of parsedTruncated.themes) {
      for (const id of theme.related_question_ids) {
        expect(presentIds).toContain(id);
      }
    }
  });

  it("tema count top_n'den etkilenmez; percentage kendi analiz paydasından türer", () => {
    const fullById = new Map(parsedFull.themes.map((t) => [t.id, t]));
    for (const theme of parsedTruncated.themes) {
      expect(theme.count).toBe(fullById.get(theme.id)!.count);
      expect(theme.percentage).toBe(
        percentageHalfUp(theme.count, parsedTruncated.preprocessing_summary.analyzed_count),
      );
    }
  });

  it("satır sınırı üstündeki rapor kırpılmaz ve uyarı taşımaz", () => {
    // Kesme yok: tüm satırlar sayılır, dolayısıyla ROW_LIMIT_TRUNCATED de
    // üretilmez. Bu iddia bizi kıran senaryoyu sözleşmede kilitliyor.
    const prep = parsedTruncated.preprocessing_summary;
    expect(parsedTruncated.source_summary.total_rows).toBeGreaterThan(LIMITS.MAX_ROWS);
    expect(prep.analyzed_count + prep.discarded_count).toBe(
      parsedTruncated.source_summary.total_rows,
    );
    expect(parsedTruncated.warnings.some((w) => w.code === "ROW_LIMIT_TRUNCATED")).toBe(false);
  });
});

describe("ADR-0002 #2 — satır sınırı reddetmez, işaretler", () => {
  it("sınırı aşan upload yine de READY ve tam profilli döner", () => {
    const upload = uploadSchema.parse(readFixture("uploads.get.200.row-limit.json"));
    expect(upload.status).toBe("ready");
    expect(upload.profile).not.toBeNull();
    expect(upload.profile!.exceeds_row_limit).toBe(true);
    expect(upload.error).toBeNull();
  });
});
