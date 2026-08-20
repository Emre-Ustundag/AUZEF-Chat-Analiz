// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "@/lib/api/query-client";
import type { ModelList, Upload } from "@/lib/api/schemas";

import { ConfigureForm } from "./configure-form";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const createAnalysis = vi.fn();
vi.mock("@/lib/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/endpoints")>()),
  createAnalysis: (...args: unknown[]) => createAnalysis(...args),
}));

const UPLOAD_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";
const API_KEY = "sk-or-cok-gizli-anahtar";

const upload: Upload = {
  upload_id: UPLOAD_ID,
  status: "ready",
  filename: "mesajlar.xlsx",
  size_bytes: 2048,
  created_at: "2026-08-11T10:00:00Z",
  error: null,
  profile: {
    total_row_count: 48_213,
    exceeds_row_limit: false,
    sheets: [
      {
        name: "Mesajlar",
        row_count: 48_213,
        column_count: 2,
        columns: [
          {
            name: "kullanici_id",
            index: 0,
            non_empty_count: 48_213,
            empty_count: 0,
            unique_count: 12_884,
            avg_length: 8,
            is_likely_text: false,
            sample_values: ["[ID]"],
          },
          {
            name: "mesaj",
            index: 1,
            non_empty_count: 47_106,
            empty_count: 1_107,
            unique_count: 31_540,
            avg_length: 64,
            is_likely_text: true,
            sample_values: ["sınav ne zaman"],
          },
        ],
      },
    ],
  },
};

const models: ModelList = {
  models: [
    {
      id: "anthropic/claude-sonnet-4.6",
      label: "Claude Sonnet 4.6",
      input_cost_per_million: 3,
      output_cost_per_million: 15,
      cache_read_cost_per_million: 0.3,
      cache_write_cost_per_million: 3.75,
      context_window: 1_000_000,
      pricing_source: "fallback",
      pricing_updated_at: null,
    },
    {
      id: "google/gemini-2.5-flash",
      label: "Gemini 2.5 Flash",
      input_cost_per_million: 0.3,
      output_cost_per_million: 2.5,
      cache_read_cost_per_million: 0.03,
      cache_write_cost_per_million: 0.0833333333333,
      context_window: 1_048_576,
      pricing_source: "fallback",
      pricing_updated_at: null,
    },
  ],
  default_model: "google/gemini-2.5-flash",
  default_prompt_version: "faq_analysis/v3",
};

function renderForm() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ConfigureForm upload={upload} models={models} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  push.mockReset();
  createAnalysis.mockReset();
  createAnalysis.mockResolvedValue({
    analysis_id: "8c2a1b40-1111-4222-8333-044455556666",
    status: "queued",
  });
});

describe("ConfigureForm", () => {
  it("kolonları profil istatistikleriyle listeler", () => {
    renderForm();

    expect(screen.getByText("mesaj")).toBeInTheDocument();
    expect(screen.getByText("kullanici_id")).toBeInTheDocument();
    // Sayılar Türkçe biçimde.
    expect(screen.getByText("47.106")).toBeInTheDocument();
    expect(screen.getByText("31.540")).toBeInTheDocument();
  });

  it("metin olduğu tahmin edilen kolonu işaretler", () => {
    renderForm();
    expect(screen.getByText("metin")).toBeInTheDocument();
  });

  it("varsayılan model ve metin kolonunu seçer", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByLabelText(/OpenRouter API anahtarı/), API_KEY);
    await user.click(screen.getByRole("button", { name: /Analizi başlat/ }));

    await waitFor(() => expect(createAnalysis).toHaveBeenCalled());

    const [request] = createAnalysis.mock.calls[0];
    expect(request.model).toBe("google/gemini-2.5-flash");
    expect(request.text_column).toBe("mesaj");
    expect(request.sheet_name).toBe("Mesajlar");
    expect(request.row_filters).toEqual([]);
  });

  it("isteğe bağlı satır filtresini isteğe ekler", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.click(screen.getByRole("button", { name: "Filtre ekle" }));
    await user.click(screen.getByLabelText("Kolon"));
    await user.click(await screen.findByRole("option", { name: "kullanici_id" }));
    await user.type(screen.getByLabelText("Kabul edilen değerler"), "1001, 1002");
    await user.type(screen.getByLabelText(/OpenRouter API anahtarı/), API_KEY);
    await user.click(screen.getByRole("button", { name: /Analizi başlat/ }));

    await waitFor(() => expect(createAnalysis).toHaveBeenCalled());
    const [request] = createAnalysis.mock.calls[0];
    expect(request.row_filters).toEqual([
      { column: "kullanici_id", allowed_values: ["1001", "1002"] },
    ]);
  });

  it("API anahtarını gövdede DEĞİL, ayrı argümanda gönderir", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByLabelText(/OpenRouter API anahtarı/), API_KEY);
    await user.click(screen.getByRole("button", { name: /Analizi başlat/ }));

    await waitFor(() => expect(createAnalysis).toHaveBeenCalled());

    const [request, apiKey] = createAnalysis.mock.calls[0];

    // ADR §6/§9: anahtar yalnızca X-OpenRouter-Key header'ında taşınır.
    expect(apiKey).toBe(API_KEY);
    expect(JSON.stringify(request)).not.toContain(API_KEY);
    expect(Object.keys(request)).not.toContain("openrouter_api_key");
  });

  it("anahtar girilmeden analiz başlatmaz", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.click(screen.getByRole("button", { name: /Analizi başlat/ }));

    expect(await screen.findByText("OpenRouter API anahtarı gereklidir.")).toBeInTheDocument();
    expect(createAnalysis).not.toHaveBeenCalled();
  });

  it("anahtar alanı parola tipinde ve otomatik doldurmaya kapalı", () => {
    renderForm();

    const input = screen.getByLabelText(/OpenRouter API anahtarı/);
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "off");
  });

  it("geçersiz top_n ile analiz başlatmaz", async () => {
    const user = userEvent.setup();
    renderForm();

    const topN = screen.getByLabelText("Gösterilecek soru sayısı");
    await user.clear(topN);
    await user.type(topN, "0");
    await user.type(screen.getByLabelText(/OpenRouter API anahtarı/), API_KEY);
    await user.click(screen.getByRole("button", { name: /Analizi başlat/ }));

    await waitFor(() => expect(screen.getByText(/En az 1 sonuç istenmelidir/)).toBeInTheDocument());
    expect(createAnalysis).not.toHaveBeenCalled();
  });

  it("başarılı başlatmada analiz sayfasına yönlendirir", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByLabelText(/OpenRouter API anahtarı/), API_KEY);
    await user.click(screen.getByRole("button", { name: /Analizi başlat/ }));

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/analizler/8c2a1b40-1111-4222-8333-044455556666"),
    );
  });
});
