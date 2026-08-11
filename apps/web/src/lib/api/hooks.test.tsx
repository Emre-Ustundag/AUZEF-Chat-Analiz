// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./client";
import { useAnalysisJob, useUploadStatus } from "./hooks";
import { createQueryClient } from "./query-client";
import { LIMITS } from "./schemas";
import type { AnalysisJob, ProblemDetails, Upload } from "./schemas";

const getAnalysisJob = vi.fn();
const getUpload = vi.fn();
vi.mock("./endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./endpoints")>()),
  getAnalysisJob: (...a: unknown[]) => getAnalysisJob(...a),
  getUpload: (...a: unknown[]) => getUpload(...a),
}));

const ID = "8c2a1b40-1111-4222-8333-044455556666";

function notFound(): ApiError {
  const problem: ProblemDetails = {
    type: "/errors/job-not-found",
    title: "İşlem bulunamadı",
    status: 404,
    code: "JOB_NOT_FOUND",
    detail: "unknown id",
    trace_id: "x",
    errors: [],
  };
  return new ApiError(problem);
}

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={createQueryClient()}>{children}</QueryClientProvider>;
}

/**
 * Poll aralığının katları kadar sahte zaman ilerletir.
 *
 * Testing Library'nin `waitFor`'u bilinçli olarak kullanılmıyor: gerçek
 * zamanlayıcıya dayandığı için sahte zamanla birlikte kilitleniyor. Sorgunun
 * yerleşmesi için de zamanı ilerletmek yeterli.
 */
async function advanceIntervals(count: number) {
  for (let i = 0; i < count; i += 1) {
    await vi.advanceTimersByTimeAsync(LIMITS.POLL_INTERVAL_MS);
  }
}

/** İlk isteğin çözülmesini bekler ve o ana kadarki çağrı sayısını verir. */
async function settleFirstRequest(spy: { mock: { calls: unknown[] } }) {
  await vi.advanceTimersByTimeAsync(10);
  expect(spy.mock.calls.length).toBeGreaterThan(0);
  return spy.mock.calls.length;
}

beforeEach(() => {
  getAnalysisJob.mockReset();
  getUpload.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

/**
 * Polling'in DURMASI gereken yerler.
 *
 * Bu testlerin varlık sebebi somut bir regresyon: `refetchInterval` yalnızca
 * `data`ya bakarsa, hata veren bir sorguda veri hiç oluşmadığı için durum
 * "henüz başlamadı" sanılır ve poll sonsuza kadar sürer. Kullanıcı hata
 * ekranını görürken arkada 2,5 saniyede bir istek atılır.
 */
describe("polling durma koşulları", () => {
  it("analiz sorgusu kalıcı hatada poll etmeyi bırakır", async () => {
    getAnalysisJob.mockRejectedValue(notFound());

    renderHook(() => useAnalysisJob(ID), { wrapper });

    const afterFailure = await settleFirstRequest(getAnalysisJob);

    await advanceIntervals(4);

    expect(getAnalysisJob.mock.calls.length).toBe(afterFailure);
  });

  it("upload sorgusu kalıcı hatada poll etmeyi bırakır", async () => {
    getUpload.mockRejectedValue(notFound());

    renderHook(() => useUploadStatus(ID), { wrapper });

    const afterFailure = await settleFirstRequest(getUpload);

    await advanceIntervals(4);

    expect(getUpload.mock.calls.length).toBe(afterFailure);
  });

  it("iş tamamlanınca poll etmeyi bırakır", async () => {
    const completed: AnalysisJob = {
      analysis_id: ID,
      status: "completed",
      progress: 100,
      created_at: "2026-08-11T10:00:00Z",
      updated_at: "2026-08-11T10:30:00Z",
      estimated_seconds_remaining: 0,
      error: null,
    };
    getAnalysisJob.mockResolvedValue(completed);

    renderHook(() => useAnalysisJob(ID), { wrapper });

    const afterSettle = await settleFirstRequest(getAnalysisJob);

    await advanceIntervals(4);

    expect(getAnalysisJob.mock.calls.length).toBe(afterSettle);
  });

  it("iş sürerken poll etmeye devam eder", async () => {
    const running: AnalysisJob = {
      analysis_id: ID,
      status: "analyzing",
      progress: 40,
      created_at: "2026-08-11T10:00:00Z",
      updated_at: "2026-08-11T10:05:00Z",
      estimated_seconds_remaining: 600,
      error: null,
    };
    getAnalysisJob.mockResolvedValue(running);

    renderHook(() => useAnalysisJob(ID), { wrapper });

    const afterFirst = await settleFirstRequest(getAnalysisJob);

    await advanceIntervals(3);

    expect(getAnalysisJob.mock.calls.length).toBeGreaterThan(afterFirst);
  });

  it("upload hazır olunca poll etmeyi bırakır", async () => {
    const ready: Upload = {
      upload_id: ID,
      status: "ready",
      filename: "veri.xlsx",
      size_bytes: 2048,
      created_at: "2026-08-11T10:00:00Z",
      profile: {
        sheets: [],
        total_row_count: 0,
        exceeds_row_limit: false,
      },
      error: null,
    };
    getUpload.mockResolvedValue(ready);

    renderHook(() => useUploadStatus(ID), { wrapper });

    const afterSettle = await settleFirstRequest(getUpload);

    await advanceIntervals(4);

    expect(getUpload.mock.calls.length).toBe(afterSettle);
  });
});

/**
 * Polling'in DEVAM ETMESİ gereken yer: iş sürerken gelen GEÇİCİ hata.
 *
 * Bu testlerin varlık sebebi de somut bir regresyon. Durma koşulu yalnızca
 * `query.state.status === "error"` diye yazıldığında, TanStack arka plan
 * hatasında da status'ü "error" yaptığı için (query-core reducer: "flag
 * existing data as invalidated if we get a background error") çalışan bir
 * işin polling'i kalıcı olarak ölüyordu. Kullanıcı sayfayı yenilemeden
 * duruma bir daha ulaşamıyordu; oysa iş sunucuda devam ediyordu.
 */
describe("polling geçici hatadan sonra devam eder", () => {
  const running: AnalysisJob = {
    analysis_id: ID,
    status: "analyzing",
    progress: 40,
    created_at: "2026-08-11T10:00:00Z",
    updated_at: "2026-08-11T10:05:00Z",
    estimated_seconds_remaining: 600,
    error: null,
  };

  const readyUpload: Upload = {
    upload_id: ID,
    status: "validating",
    filename: "veri.xlsx",
    size_bytes: 2048,
    created_at: "2026-08-11T10:00:00Z",
    profile: null,
    error: null,
  };

  it("iş sürerken hata gelirse polling durmaz", async () => {
    // Önce başarılı bir cevap (veri oluşur), sonra hatalar.
    getAnalysisJob.mockResolvedValueOnce(running).mockRejectedValue(notFound());

    renderHook(() => useAnalysisJob(ID), { wrapper });

    await settleFirstRequest(getAnalysisJob);

    // Hatanın YERLEŞMESİNİ bekle. Ölçümü buradan sonra almak şart: hata
    // yerleşmeden sayarsak, hatalı isteğin kendisi sayacı artırır ve test
    // polling durmuş olsa bile geçer.
    await advanceIntervals(2);
    const afterErrorSettled = getAnalysisJob.mock.calls.length;

    await advanceIntervals(4);

    expect(getAnalysisJob.mock.calls.length).toBeGreaterThan(afterErrorSettled);
  });

  it("hata geçtikten sonra taze durum yeniden okunur", async () => {
    const aggregating: AnalysisJob = { ...running, status: "aggregating", progress: 90 };
    getAnalysisJob
      .mockResolvedValueOnce(running)
      .mockRejectedValueOnce(notFound())
      .mockResolvedValue(aggregating);

    const { result } = renderHook(() => useAnalysisJob(ID), { wrapper });

    await settleFirstRequest(getAnalysisJob);
    await advanceIntervals(5);

    // Polling kendini onardığı için arayüz ilerlemiş durumu görebilmeli.
    expect(result.current.data?.status).toBe("aggregating");
  });

  it("upload doğrulanırken hata gelirse polling durmaz", async () => {
    getUpload.mockResolvedValueOnce(readyUpload).mockRejectedValue(notFound());

    renderHook(() => useUploadStatus(ID), { wrapper });

    await settleFirstRequest(getUpload);

    await advanceIntervals(2);
    const afterErrorSettled = getUpload.mock.calls.length;

    await advanceIntervals(4);

    expect(getUpload.mock.calls.length).toBeGreaterThan(afterErrorSettled);
  });
});
