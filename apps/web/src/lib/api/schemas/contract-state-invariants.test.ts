import { describe, expect, it } from "vitest";

import {
  analysisCreatedSchema,
  analysisJobSchema,
  analysisReportSchema,
  modelListSchema,
  problemDetailsSchema,
  uploadCreatedSchema,
  uploadSchema,
} from "./index";
import { readFixture } from "./contract-paths";

describe("status bağımlı payload kuralları", () => {
  it("created cevapları yalnızca queued kabul eder", () => {
    expect(
      uploadCreatedSchema.safeParse({
        ...readFixture<Record<string, unknown>>("uploads.create.202.json"),
        status: "ready",
      }).success,
    ).toBe(false);
    expect(
      analysisCreatedSchema.safeParse({
        ...readFixture<Record<string, unknown>>("analyses.create.202.json"),
        status: "completed",
      }).success,
    ).toBe(false);
  });

  it("upload profile/error alanlarını status ile hizalar", () => {
    const ready = readFixture<Record<string, unknown>>("uploads.get.200.ready.json");
    const failed = readFixture<Record<string, unknown>>("uploads.get.200.failed.json");
    expect(uploadSchema.safeParse({ ...ready, profile: null }).success).toBe(false);
    expect(uploadSchema.safeParse({ ...ready, status: "queued" }).success).toBe(false);
    expect(uploadSchema.safeParse({ ...failed, error: null }).success).toBe(false);
  });

  it("analysis error/ETA alanlarını status ile hizalar", () => {
    const active = readFixture<Record<string, unknown>>("analyses.get.200.analyzing.json");
    const failed = readFixture<Record<string, unknown>>("analyses.get.200.failed.json");
    const cancelled = readFixture<Record<string, unknown>>("analyses.get.200.cancelled.json");
    expect(analysisJobSchema.safeParse({ ...failed, error: null }).success).toBe(false);
    expect(analysisJobSchema.safeParse({ ...active, error: failed.error }).success).toBe(false);
    expect(
      analysisJobSchema.safeParse({ ...cancelled, estimated_seconds_remaining: 5 }).success,
    ).toBe(false);
  });
});

describe("diğer çapraz alan kuralları", () => {
  it("ErrorItem.field için null ve eksik alanı kabul eder", () => {
    const problem = readFixture<Record<string, unknown>>(
      "errors.request-validation.422.no-field.json",
    );
    expect(problemDetailsSchema.safeParse(problem).success).toBe(true);
    const errors = problem.errors as Record<string, unknown>[];
    expect(
      problemDetailsSchema.safeParse({ ...problem, errors: [{ ...errors[0], field: null }] })
        .success,
    ).toBe(true);
  });

  it("model varsayılanı whitelist içinde olmalı", () => {
    const catalog = readFixture<Record<string, unknown>>("models.list.200.json");
    expect(modelListSchema.safeParse({ ...catalog, default_model: "unknown/model" }).success).toBe(
      false,
    );
  });

  it("rapor sayısal invariant'larını zorlar", () => {
    const report = readFixture<Record<string, unknown>>("analyses.result.200.truncated.json");
    const prep = report.preprocessing_summary as Record<string, number>;
    const usage = report.token_usage as Record<string, number>;
    expect(
      analysisReportSchema.safeParse({
        ...report,
        preprocessing_summary: { ...prep, analyzed_count: prep.analyzed_count + 1 },
      }).success,
    ).toBe(false);
    expect(
      analysisReportSchema.safeParse({ ...report, token_usage: { ...usage, total_tokens: 1 } })
        .success,
    ).toBe(false);
  });
});
