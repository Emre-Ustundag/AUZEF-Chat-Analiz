// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./client";
import { useAnalysisJob, useUploadStatus } from "./hooks";
import { createQueryClient } from "./query-client";
import { LIMITS } from "./schemas";
import type { AnalysisJob, ErrorCode, ProblemDetails, Upload } from "./schemas";

const getAnalysisJob = vi.fn();
const getUpload = vi.fn();
vi.mock("./endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./endpoints")>()),
  getAnalysisJob: (...args: unknown[]) => getAnalysisJob(...args),
  getUpload: (...args: unknown[]) => getUpload(...args),
}));

const ID = "8c2a1b40-1111-4222-8333-044455556666";

function apiError(code: ErrorCode, status: number): ApiError {
  const problem: ProblemDetails = {
    type: `/errors/${code.toLowerCase().replaceAll("_", "-")}`,
    title: code,
    status,
    code,
    detail: "test error",
    trace_id: "test-trace-id",
    errors: [],
  };
  return new ApiError(problem);
}

function notFound(): ApiError {
  return apiError("JOB_NOT_FOUND", 404);
}

function transientApiError(): ApiError {
  return apiError("PROVIDER_TIMEOUT", 504);
}

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={createQueryClient()}>{children}</QueryClientProvider>;
}

/** Poll aralığının katları kadar sahte zaman ilerletir. */
async function advanceIntervals(count: number) {
  for (let index = 0; index < count; index += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIMITS.POLL_INTERVAL_MS);
    });
  }
}

/** İlk isteğin çözülmesini bekler ve o ana kadarki çağrı sayısını verir. */
async function settleFirstRequest(spy: { mock: { calls: unknown[] } }) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10);
  });
  expect(spy.mock.calls.length).toBeGreaterThan(0);
  return spy.mock.calls.length;
}

const runningAnalysis: AnalysisJob = {
  analysis_id: ID,
  status: "analyzing",
  progress: 40,
  created_at: "2026-08-11T10:00:00Z",
  updated_at: "2026-08-11T10:05:00Z",
  estimated_seconds_remaining: 600,
  error: null,
};

const recoveredAnalysis: AnalysisJob = {
  ...runningAnalysis,
  status: "aggregating",
  progress: 90,
};

const completedAnalysis: AnalysisJob = {
  ...runningAnalysis,
  status: "completed",
  progress: 100,
  updated_at: "2026-08-11T10:30:00Z",
  estimated_seconds_remaining: 0,
};

const validatingUpload: Upload = {
  upload_id: ID,
  status: "validating",
  filename: "veri.xlsx",
  size_bytes: 2048,
  created_at: "2026-08-11T10:00:00Z",
  profile: null,
  error: null,
};

const queuedUpload: Upload = {
  ...validatingUpload,
  status: "queued",
};

const readyUpload: Upload = {
  ...validatingUpload,
  status: "ready",
  profile: {
    sheets: [],
    total_row_count: 0,
    exceeds_row_limit: false,
  },
};

interface PollingHookState {
  status: string | undefined;
  refetch: () => Promise<void>;
}

function useAnalysisPollingState(): PollingHookState {
  const query = useAnalysisJob(ID);
  return {
    status: query.data?.status,
    refetch: async () => {
      await query.refetch();
    },
  };
}

function useUploadPollingState(): PollingHookState {
  const query = useUploadStatus(ID);
  return {
    status: query.data?.status,
    refetch: async () => {
      await query.refetch();
    },
  };
}

function pollingResources() {
  return [
    {
      name: "analiz",
      request: getAnalysisJob,
      useStatus: useAnalysisPollingState,
      running: runningAnalysis,
      recovered: recoveredAnalysis,
      terminal: completedAnalysis,
    },
    {
      name: "upload",
      request: getUpload,
      useStatus: useUploadPollingState,
      running: validatingUpload,
      recovered: queuedUpload,
      terminal: readyUpload,
    },
  ] as const;
}

/**
 * Cache oluştuktan sonraki bir refetch'in TanStack retry'larını tüketir;
 * takip eden interval ise verilen iyileşmiş cevabı okur.
 */
function failCachedRequestThenRecover(
  request: ReturnType<typeof vi.fn>,
  running: AnalysisJob | Upload,
  error: unknown,
  recovered: AnalysisJob | Upload,
) {
  request
    .mockResolvedValueOnce(running)
    .mockRejectedValueOnce(error)
    .mockRejectedValueOnce(error)
    .mockRejectedValueOnce(error)
    .mockResolvedValue(recovered);
}

beforeEach(() => {
  getAnalysisJob.mockReset();
  getUpload.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

for (const resource of pollingResources()) {
  describe(`${resource.name} polling karar matrisi`, () => {
    it("ilk cevap kalıcı ApiError ise retry ve polling yapmaz", async () => {
      resource.request.mockRejectedValue(notFound());

      renderHook(resource.useStatus, { wrapper });

      await settleFirstRequest(resource.request);
      await advanceIntervals(4);

      expect(resource.request).toHaveBeenCalledTimes(1);
    });

    it("ilk cevap geçici ApiError ise kısa retry'lardan sonra polling yapmaz", async () => {
      resource.request.mockRejectedValue(transientApiError());

      renderHook(resource.useStatus, { wrapper });

      await advanceIntervals(5);
      const afterRetries = resource.request.mock.calls.length;
      expect(afterRetries).toBeGreaterThan(1);

      await advanceIntervals(4);

      expect(resource.request.mock.calls.length).toBe(afterRetries);
    });

    it("ilk cevap bilinmeyen hata ise kısa retry'lardan sonra polling yapmaz", async () => {
      resource.request.mockRejectedValue(new Error("network reset"));

      renderHook(resource.useStatus, { wrapper });

      await advanceIntervals(5);
      const afterRetries = resource.request.mock.calls.length;
      expect(afterRetries).toBeGreaterThan(1);

      await advanceIntervals(4);

      expect(resource.request.mock.calls.length).toBe(afterRetries);
    });

    it("aktif durumda poll etmeye devam eder", async () => {
      resource.request.mockResolvedValue(resource.running);

      renderHook(resource.useStatus, { wrapper });

      const afterFirst = await settleFirstRequest(resource.request);
      await advanceIntervals(3);

      expect(resource.request.mock.calls.length).toBeGreaterThan(afterFirst);
    });

    it("terminal cache poll etmeyi her zaman durdurur", async () => {
      resource.request.mockResolvedValue(resource.terminal);

      renderHook(resource.useStatus, { wrapper });

      const afterTerminal = await settleFirstRequest(resource.request);
      await advanceIntervals(4);

      expect(resource.request.mock.calls.length).toBe(afterTerminal);
    });

    it("terminal cache manuel refetch hatasından sonra polling'i yeniden başlatmaz", async () => {
      resource.request
        .mockResolvedValueOnce(resource.terminal)
        .mockRejectedValue(transientApiError());

      const { result } = renderHook(resource.useStatus, { wrapper });
      await settleFirstRequest(resource.request);

      act(() => {
        void result.current.refetch();
      });
      await advanceIntervals(5);
      const afterRefetchRetries = resource.request.mock.calls.length;

      await advanceIntervals(4);

      expect(resource.request.mock.calls.length).toBe(afterRefetchRetries);
      expect(result.current.status).toBe(resource.terminal.status);
    });

    it("aktif cache + 404 kalıcı hatasında polling'i durdurur", async () => {
      resource.request.mockResolvedValueOnce(resource.running).mockRejectedValue(notFound());

      renderHook(resource.useStatus, { wrapper });

      await settleFirstRequest(resource.request);
      await advanceIntervals(2);
      const afterNotFound = resource.request.mock.calls.length;

      await advanceIntervals(4);

      expect(afterNotFound).toBe(2);
      expect(resource.request.mock.calls.length).toBe(afterNotFound);
    });

    it("aktif cache + geçici ApiError sonrası taze aktif duruma ulaşır", async () => {
      failCachedRequestThenRecover(
        resource.request,
        resource.running,
        transientApiError(),
        resource.recovered,
      );

      const { result } = renderHook(resource.useStatus, { wrapper });

      await settleFirstRequest(resource.request);
      await advanceIntervals(8);

      expect(result.current.status).toBe(resource.recovered.status);
      const afterRecovery = resource.request.mock.calls.length;

      await advanceIntervals(2);

      expect(resource.request.mock.calls.length).toBeGreaterThan(afterRecovery);
    });

    it("aktif cache + bilinmeyen hata sonrası taze aktif duruma ulaşır", async () => {
      failCachedRequestThenRecover(
        resource.request,
        resource.running,
        new Error("connection reset"),
        resource.recovered,
      );

      const { result } = renderHook(resource.useStatus, { wrapper });

      await settleFirstRequest(resource.request);
      await advanceIntervals(8);

      expect(result.current.status).toBe(resource.recovered.status);
    });

    it("geçici hatadan terminal duruma iyileşir ve orada polling'i durdurur", async () => {
      failCachedRequestThenRecover(
        resource.request,
        resource.running,
        transientApiError(),
        resource.terminal,
      );

      const { result } = renderHook(resource.useStatus, { wrapper });

      await settleFirstRequest(resource.request);
      await advanceIntervals(8);

      expect(result.current.status).toBe(resource.terminal.status);
      const afterTerminalRecovery = resource.request.mock.calls.length;

      await advanceIntervals(4);

      expect(resource.request.mock.calls.length).toBe(afterTerminalRecovery);
    });
  });
}
