import { describe, expect, it } from "vitest";

import faz3Report from "./__fixtures__/faz3-llm-report.json";
import { analysisReportSchema } from "./index";

/**
 * Sözleşme testi — GERÇEK backend çıktısına karşı.
 *
 * `__fixtures__/faz3-llm-report.json` elle yazılmadı: Faz 3 backend'inin
 * uçtan uca koşusundan (upload → analiz → LLM map/reduce → rapor) çıkan
 * gövdenin aynısıdır. Üretim biçimi fixture dosyasının yanındaki
 * README'de yazılı.
 *
 * NEDEN AYRI BİR TEST: `schemas.test.ts` elle kurulmuş nesneleri
 * doğruluyor, yani şemanın kendi kendisiyle tutarlı olduğunu gösteriyor.
 * Bu dosya farklı bir şey kanıtlıyor — backend'in GERÇEKTEN ürettiği
 * gövdenin arayüzün beklediği biçimde olduğunu. Pydantic'in kendi
 * çıktısını kabul etmesi bu konuda hiçbir şey söylemez; alan adları
 * ayrışırsa hata tam olarak burada yakalanır.
 */
describe("backend Faz 3 raporu", () => {
  it("analysisReportSchema'dan geçer", () => {
    const result = analysisReportSchema.safeParse(faz3Report);

    if (!result.success) {
      throw new Error(
        `Rapor sözleşmeye uymuyor:\n${JSON.stringify(result.error.issues, null, 2)}`,
      );
    }
    expect(result.success).toBe(true);
  });

  it("oranlar adetlerden türetilmiş (ADR §4 — LLM sayı üretmez)", () => {
    const report = analysisReportSchema.parse(faz3Report);
    const analyzed = report.preprocessing_summary.analyzed_count;

    for (const question of report.top_questions) {
      expect(question.percentage).toBeCloseTo(
        Math.round((question.count / analyzed) * 1000) / 10,
        5,
      );
    }
    for (const theme of report.themes) {
      expect(theme.percentage).toBeCloseTo(
        Math.round((theme.count / analyzed) * 1000) / 10,
        5,
      );
    }
  });

  it("hiçbir mesaj kaybolmamış: soru adetleri analiz edilen kayda eşit", () => {
    const report = analysisReportSchema.parse(faz3Report);
    const total = report.top_questions.reduce((sum, q) => sum + q.count, 0);

    expect(total).toBe(report.preprocessing_summary.analyzed_count);
  });

  it("themes[].related_question_ids yalnızca raporda yer alan sorulara bağlanır", () => {
    // Plan §1.2 kararı: arayüz çözemeyeceği bir kimliğe bağlantı vermemeli.
    const report = analysisReportSchema.parse(faz3Report);
    const shown = new Set(report.top_questions.map((q) => q.id));

    for (const theme of report.themes) {
      for (const id of theme.related_question_ids) {
        expect(shown.has(id)).toBe(true);
      }
    }
  });

  it("gerçek token tüketimi ve maliyet raporlanmış", () => {
    const report = analysisReportSchema.parse(faz3Report);

    expect(report.token_usage.total_tokens).toBeGreaterThan(0);
    expect(report.token_usage.total_tokens).toBe(
      report.token_usage.prompt_tokens + report.token_usage.completion_tokens,
    );
    expect(report.estimated_cost_usd).toBeGreaterThan(0);
    expect(report.prompt_hash).toMatch(/^sha256:/);
  });

  it("model uyarıları kullanıcıya taşınmış", () => {
    // Fixture, modelin kayıt atladığı bir koşudan üretildi: bu bilgi
    // kullanıcıdan saklanmamalı.
    const report = analysisReportSchema.parse(faz3Report);
    const codes = report.warnings.map((w) => w.code);

    expect(codes).toContain("LLM_UNASSIGNED_RECORDS");
  });
});
