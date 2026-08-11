// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { createQueryClient } from "@/lib/api/query-client";
import type { AnalysisJob, ProblemDetails } from "@/lib/api/schemas";

import { ProgressScreen } from "./progress-screen";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const getAnalysisJob = vi.fn();
const cancelAnalysis = vi.fn();
const getAnalysisReport = vi.fn();
vi.mock("@/lib/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/endpoints")>()),
  getAnalysisJob: (...a: unknown[]) => getAnalysisJob(...a),
  cancelAnalysis: (...a: unknown[]) => cancelAnalysis(...a),
  getAnalysisReport: (...a: unknown[]) => getAnalysisReport(...a),
}));

const ID = "8c2a1b40-1111-4222-8333-044455556666";

function job(overrides: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    analysis_id: ID,
    status: "analyzing",
    progress: 42.5,
    created_at: "2026-08-11T10:00:00Z",
    updated_at: "2026-08-11T10:05:00Z",
    estimated_seconds_remaining: 600,
    error: null,
    ...overrides,
  };
}

function problem(overrides: Partial<ProblemDetails> = {}): ProblemDetails {
  return {
    type: "/errors/provider-bad-response",
    title: "Geçersiz model yanıtı",
    status: 502,
    code: "PROVIDER_BAD_RESPONSE",
    detail: "raw provider payload that must not reach the user",
    trace_id: "x",
    errors: [],
    ...overrides,
  };
}

function renderScreen() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ProgressScreen analysisId={ID} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getAnalysisJob.mockReset();
  cancelAnalysis.mockReset();
  getAnalysisReport.mockReset();
});

describe("ProgressScreen — devam eden iş", () => {
  it("aşamayı, yüzdeyi ve kalan süreyi Türkçe biçimde gösterir", async () => {
    getAnalysisJob.mockResolvedValue(job());
    renderScreen();

    // Aşama adı kasıtlı olarak iki yerde: başlıkta ve adım listesinde.
    expect(
      await screen.findAllByText("Mesajlar analiz ediliyor"),
    ).toHaveLength(2);
    expect(screen.getByText("%42,5")).toBeInTheDocument();
    expect(screen.getByText(/10 dk/)).toBeInTheDocument();
  });

  it("sekmenin kapatılabileceğini söyler", async () => {
    // 45 dakikalık bir işte kullanıcı beklemek zorunda olduğunu sanmamalı.
    getAnalysisJob.mockResolvedValue(job());
    renderScreen();

    expect(
      await screen.findByText(/Sekmeyi\s+kapatabilirsiniz/),
    ).toBeInTheDocument();
  });

  it("iptal onay ister ve onaylanınca isteği gönderir", async () => {
    getAnalysisJob.mockResolvedValue(job());
    cancelAnalysis.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderScreen();

    await user.click(
      await screen.findByRole("button", { name: /Analizi iptal et/ }),
    );

    // Tek tıkla iptal olmamalı; geri alınamaz bir işlem.
    expect(cancelAnalysis).not.toHaveBeenCalled();
    expect(screen.getByText(/geri alınamaz/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Evet, iptal et/ }));
    await waitFor(() => expect(cancelAnalysis).toHaveBeenCalledWith(ID));
  });

  it("iptal isteği başarısız olursa sessiz kalmaz", async () => {
    // İş bu sırada bittiyse backend 409 döner. Gösterilmezse düğme normale
    // döner ve kullanıcı iptalin geçtiğini sanır.
    getAnalysisJob.mockResolvedValue(job());
    cancelAnalysis.mockRejectedValue(
      new ApiError(
        problem({
          code: "JOB_CONFLICT",
          status: 409,
          title: "İş zaten bitti",
          detail: "job already completed",
        }),
      ),
    );
    const user = userEvent.setup();
    renderScreen();

    await user.click(
      await screen.findByRole("button", { name: /Analizi iptal et/ }),
    );
    await user.click(screen.getByRole("button", { name: /Evet, iptal et/ }));

    expect(
      await screen.findByText("Analiz iptal edilemedi"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/job already completed/)).not.toBeInTheDocument();
  });

  it("iptalden vazgeçilebilir", async () => {
    getAnalysisJob.mockResolvedValue(job());
    const user = userEvent.setup();
    renderScreen();

    await user.click(
      await screen.findByRole("button", { name: /Analizi iptal et/ }),
    );
    await user.click(screen.getByRole("button", { name: "Vazgeç" }));

    expect(cancelAnalysis).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: /Analizi iptal et/ }),
    ).toBeInTheDocument();
  });
});

describe("ProgressScreen — terminal durumlar", () => {
  it("iptal edilmiş işte iptal mesajı gösterir", async () => {
    getAnalysisJob.mockResolvedValue(job({ status: "cancelled", progress: 0 }));
    renderScreen();

    expect(await screen.findByText("Analiz iptal edildi")).toBeInTheDocument();
  });

  it("tamamlanmış işte rapor ekranına devreder", async () => {
    getAnalysisJob.mockResolvedValue(
      job({ status: "completed", progress: 100, estimated_seconds_remaining: null }),
    );
    // Rapor isteği askıda bırakılıyor; burada test edilen şey raporun içeriği
    // değil, ilerleme arayüzünün yerini rapora bırakması.
    getAnalysisReport.mockReturnValue(new Promise(() => {}));
    renderScreen();

    expect(await screen.findByText("Rapor hazırlanıyor…")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Analizi iptal et/ }),
    ).not.toBeInTheDocument();
  });

  it("hatada backend'in ham detayını DEĞİL Türkçe metni gösterir", async () => {
    getAnalysisJob.mockResolvedValue(
      job({ status: "failed", error: problem() }),
    );
    renderScreen();

    expect(
      await screen.findByText("Analiz tamamlanamadı"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/raw provider payload/),
    ).not.toBeInTheDocument();
  });

  it("geçici sağlayıcı hatasında tekrar denemeyi önerir", async () => {
    getAnalysisJob.mockResolvedValue(
      job({
        status: "failed",
        error: problem({ code: "PROVIDER_RATE_LIMITED", retry_after: 60 }),
      }),
    );
    renderScreen();

    // Base UI dokümanı link'i Button'a sarmayı yasakladığı için bu bir <a>;
    // rolü de link olmalı, button değil.
    expect(
      await screen.findByRole("link", { name: /Tekrar dene/ }),
    ).toBeInTheDocument();
    // retry_after kullanıcıya süre olarak aktarılmalı.
    expect(screen.getByText(/1 dk sonra/)).toBeInTheDocument();
  });

  it("kalıcı hatada tekrar denemeyi ÖNERMEZ", async () => {
    // Bozuk dosyada tekrar denemek anlamsız; kullanıcıyı boşa uğraştırır.
    getAnalysisJob.mockResolvedValue(
      job({
        status: "failed",
        error: problem({
          code: "UPLOAD_CORRUPT_OR_ENCRYPTED",
          status: 422,
          title: "Dosya okunamadı",
        }),
      }),
    );
    renderScreen();

    expect(
      await screen.findByText("Analiz tamamlanamadı"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /Tekrar dene/ }),
    ).not.toBeInTheDocument();
  });
});
