// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { createQueryClient } from "@/lib/api/query-client";
import type { AnalysisReport, Theme } from "@/lib/api/schemas";

import { ReportScreen } from "./report-screen";
import { foldThemes } from "./theme-distribution";

const getAnalysisReport = vi.fn();
const downloadAnalysisExport = vi.fn();
vi.mock("@/lib/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/endpoints")>()),
  getAnalysisReport: (...a: unknown[]) => getAnalysisReport(...a),
  downloadAnalysisExport: (...a: unknown[]) => downloadAnalysisExport(...a),
}));

// jsdom object URL'i uygulamıyor; indirme yolunun kendisi burada test
// edilmiyor, önemli olan doğru formatın istenmesi ve hatanın gösterilmesi.
vi.mock("@/lib/download", () => ({ saveBlob: vi.fn() }));

const ID = "8c2a1b40-1111-4222-8333-044455556666";

const report: AnalysisReport = {
  schema_version: "1.0",
  analysis_id: ID,
  status: "completed",
  generated_at: "2026-08-11T10:00:00Z",
  source_summary: {
    filename: "mesajlar.xlsx",
    sheet_name: "Mesajlar",
    text_column: "mesaj",
    row_filters: [],
    total_rows: 48_213,
  },
  preprocessing_summary: {
    analyzed_count: 47_106,
    discarded_count: 1_107,
    duplicate_count: 15_566,
    redacted_count: 2_841,
    unique_count: 31_540,
  },
  top_questions: [
    {
      id: "q1",
      canonical_question: "Sınav tarihleri ne zaman açıklanacak?",
      count: 11_680,
      percentage: 24.8,
      redacted_examples: ["sınav ne zaman"],
    },
    {
      id: "q2",
      canonical_question: "Ders materyallerine nereden ulaşabilirim?",
      count: 8_102,
      percentage: 17.2,
      redacted_examples: ["pdf bulamıyorum"],
    },
  ],
  themes: [
    {
      id: "t1",
      name: "Sınav takvimi",
      count: 11_680,
      percentage: 24.8,
      related_question_ids: ["q1"],
    },
    { id: "t2", name: "Materyaller", count: 8_102, percentage: 17.2, related_question_ids: ["q2"] },
  ],
  executive_summary: "Mesajların dörtte biri sınav takvimiyle ilgili.",
  warnings: [],
  model: "anthropic/claude-sonnet-4.6",
  prompt_version: "faq_analysis/v1",
  prompt_hash: "sha256:2f8a1c9e4b7d",
  token_usage: {
    prompt_tokens: 1_284_000,
    completion_tokens: 96_400,
    total_tokens: 1_380_400,
    cached_tokens: 0,
    cache_write_tokens: 0,
  },
  estimated_cost_usd: 4.1412,
  cost_source: "provider",
  pricing_snapshot: {
    input_cost_per_million: 3,
    output_cost_per_million: 15,
    cache_read_cost_per_million: 0.3,
    cache_write_cost_per_million: 3.75,
    source: "openrouter",
    fetched_at: "2026-08-11T09:55:00Z",
  },
};

function renderScreen() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ReportScreen analysisId={ID} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getAnalysisReport.mockReset();
  getAnalysisReport.mockResolvedValue(report);
  downloadAnalysisExport.mockReset();
});

describe("ReportScreen", () => {
  it("manşet sayıları Türkçe biçimde gösterir", async () => {
    renderScreen();

    expect(await screen.findByText("48.213")).toBeInTheDocument();
    expect(screen.getByText("47.106")).toBeInTheDocument();
    expect(screen.getByText("31.540")).toBeInTheDocument();
    expect(screen.getByText("$4,1412")).toBeInTheDocument();
    expect(screen.getByText("Gerçek maliyet")).toBeInTheDocument();
    expect(screen.getByText("OpenRouter canlı katalog")).toBeInTheDocument();
  });

  it("soruları oranlarıyla listeler", async () => {
    renderScreen();

    // Grafik ve tabloda ikişer kez geçiyor.
    expect(await screen.findAllByText("Sınav tarihleri ne zaman açıklanacak?")).toHaveLength(2);
    expect(screen.getAllByText(/%24,8/).length).toBeGreaterThan(0);
  });

  it("izlenebilirlik künyesini gösterir", async () => {
    // ADR §8: sonuç tartışmaya açıldığında ilk sorulacak şey hangi model ve
    // hangi prompt sürümüyle üretildiği.
    renderScreen();

    expect(await screen.findByText("anthropic/claude-sonnet-4.6")).toBeInTheDocument();
    expect(screen.getByText("faq_analysis/v1")).toBeInTheDocument();
    expect(screen.getByText("sha256:2f8a1c9e4b7d")).toBeInTheDocument();
  });

  it("uygulanan satır filtrelerini kaynak künyesinde gösterir", async () => {
    getAnalysisReport.mockResolvedValue({
      ...report,
      source_summary: {
        ...report.source_summary,
        row_filters: [
          { column: "direction", allowed_values: ["Kullanıcı"] },
          { column: "message_type", allowed_values: ["text"] },
        ],
      },
    });

    renderScreen();

    expect(
      await screen.findByText("Filtreler: direction = Kullanıcı; message_type = text"),
    ).toBeInTheDocument();
  });

  it("dışa aktarmayı istenen formatla çalıştırır", async () => {
    downloadAnalysisExport.mockResolvedValue({
      blob: new Blob(["{}"], { type: "application/json" }),
      filename: "analiz.json",
    });
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: /JSON/ }));

    await waitFor(() => expect(downloadAnalysisExport).toHaveBeenCalledWith(ID, "json"));
  });

  it("dışa aktarma hatasını ham JSON yerine Türkçe mesajla gösterir", async () => {
    // Düz bir <a href> ile indirilseydi tarayıcı problem-details gövdesini
    // ham haliyle ekrana basardı.
    downloadAnalysisExport.mockRejectedValue(
      new ApiError({
        type: "/errors/job-conflict",
        title: "Rapor hazır değil",
        status: 409,
        code: "JOB_CONFLICT",
        detail: "Export only available for completed analyses.",
        trace_id: "t-1",
        errors: [],
      }),
    );
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: /Excel/ }));

    expect(await screen.findByText("Dosya indirilemedi")).toBeInTheDocument();
    expect(screen.getByText(/zaten devam eden bir analiz var/)).toBeInTheDocument();
    expect(screen.queryByText(/completed analyses/)).not.toBeInTheDocument();
  });

  it("tema dağılımını metin olarak da erişilebilir kılar", async () => {
    renderScreen();

    const chart = await screen.findByRole("img", { name: /Tema dağılımı/ });
    expect(chart).toHaveAccessibleName(/Sınav takvimi %24,8/);
  });
});

describe("foldThemes — renk slotu ataması", () => {
  const theme = (i: number): Theme => ({
    id: `t${i}`,
    name: `Tema ${i}`,
    count: 100 - i,
    percentage: 10,
    related_question_ids: [],
  });

  it("altı ve altındaki temaya kendi slotunu verir", () => {
    const result = foldThemes([1, 2, 3].map(theme));
    expect(result.map((s) => s.className)).toEqual(["bg-chart-1", "bg-chart-2", "bg-chart-3"]);
  });

  it("altıdan fazlasını 'Diğer'e katlar, yeni renk ÜRETMEZ", () => {
    // 7. bir ton renk körlüğü altında mevcut bir slottan ayırt edilemez.
    const result = foldThemes([1, 2, 3, 4, 5, 6, 7, 8].map(theme));

    expect(result).toHaveLength(6);
    expect(result.at(-1)!.name).toContain("Diğer");
    expect(new Set(result.map((s) => s.className)).size).toBe(6);
  });

  it("katlanan temaların adetlerini toplar", () => {
    const themes = [1, 2, 3, 4, 5, 6, 7, 8].map(theme);
    const result = foldThemes(themes);
    const expected = themes.slice(5).reduce((sum, t) => sum + t.count, 0);

    expect(result.at(-1)!.count).toBe(expected);
  });

  it("renk temanın kimliğine bağlı, sırasına göre yeniden atanmaz", () => {
    // Aynı tema listenin başındayken hangi slottaysa, liste kısalınca da
    // aynı slotta kalmalı; okuyucunun öğrendiği renk bozulmamalı.
    const full = foldThemes([1, 2, 3].map(theme));
    const shorter = foldThemes([1, 2].map(theme));

    expect(shorter[0].className).toBe(full[0].className);
    expect(shorter[1].className).toBe(full[1].className);
  });
});
