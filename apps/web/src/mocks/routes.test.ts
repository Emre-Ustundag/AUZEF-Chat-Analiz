import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DELETE as deleteAnalysis,
  GET as getAnalysis,
} from "@/app/api/mock/v1/analyses/[analysisId]/route";
import { GET as exportAnalysis } from "@/app/api/mock/v1/analyses/[analysisId]/export/route";
import { GET as getAnalysisResult } from "@/app/api/mock/v1/analyses/[analysisId]/result/route";
import { POST as postAnalysis } from "@/app/api/mock/v1/analyses/route";
import {
  DELETE as deleteUpload,
  GET as getUpload,
} from "@/app/api/mock/v1/uploads/[uploadId]/route";
import { POST as postUpload } from "@/app/api/mock/v1/uploads/route";
import {
  analysisCreatedSchema,
  problemDetailsSchema,
  uploadCreatedSchema,
} from "@/lib/api/schemas";
import type { AnalysisRequest } from "@/lib/api/schemas";
import { MOCK_MODEL_LIST } from "@/mocks/catalog";
import { TRACE_ID_HEADER } from "@/mocks/responses";
import { createAnalysisRecord, createUploadRecord, IDEMPOTENCY_TTL_MS } from "@/mocks/store";
import { IDEMPOTENCY_KEY_MAX_LENGTH } from "@/mocks/validation";

const START = new Date("2026-08-12T09:00:00.000Z");
const XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(START);
});

afterEach(() => {
  vi.useRealTimers();
});

function advance(milliseconds: number) {
  vi.setSystemTime(new Date(START.getTime() + milliseconds));
}

function uploadContext(uploadId: string) {
  return { params: Promise.resolve({ uploadId }) };
}

function analysisContext(analysisId: string) {
  return { params: Promise.resolve({ analysisId }) };
}

function xlsxFile(content = "xlsx-content", name = "veri.xlsx") {
  return new File([content], name, { type: XLSX_MEDIA_TYPE });
}

function uploadRequest(file: File, idempotencyKey?: string) {
  const form = new FormData();
  form.set("file", file);
  const headers = idempotencyKey === undefined ? undefined : { "Idempotency-Key": idempotencyKey };
  return new NextRequest("http://localhost/api/mock/v1/uploads", {
    method: "POST",
    headers,
    body: form,
  });
}

function analysisRequestFor(uploadId: string): AnalysisRequest {
  return {
    upload_id: uploadId,
    sheet_name: "Mesajlar",
    text_column: "mesaj",
    row_filters: [],
    model: MOCK_MODEL_LIST.default_model,
    prompt_version: MOCK_MODEL_LIST.default_prompt_version,
    top_n: 8,
    max_cost_usd: 10,
  };
}

function analysisRequest(
  body: unknown,
  options: { apiKey?: string; idempotencyKey?: string } = {},
) {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (options.apiKey !== undefined) headers.set("X-OpenRouter-Key", options.apiKey);
  if (options.idempotencyKey !== undefined) {
    headers.set("Idempotency-Key", options.idempotencyKey);
  }

  return new NextRequest("http://localhost/api/mock/v1/analyses", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

async function problemCode(response: Response) {
  return problemDetailsSchema.parse(await response.json()).code;
}

describe("dynamic path UUID parity", () => {
  it("lookup'tan önce tüm dinamik kimlikleri 422 ile doğrular", async () => {
    const badId = "uuid-degil";
    const calls = [
      () => getUpload(new Request("http://localhost"), uploadContext(badId)),
      () => deleteUpload(new Request("http://localhost"), uploadContext(badId)),
      () => getAnalysis(new Request("http://localhost"), analysisContext(badId)),
      () => deleteAnalysis(new Request("http://localhost"), analysisContext(badId)),
      () => getAnalysisResult(new Request("http://localhost"), analysisContext(badId)),
      () =>
        exportAnalysis(
          new NextRequest("http://localhost/api/mock/v1/analyses/uuid-degil/export?format=json"),
          analysisContext(badId),
        ),
    ];

    for (const call of calls) {
      const response = await call();
      expect(response.status).toBe(422);
      expect(await problemCode(response)).toBe("REQUEST_VALIDATION");
    }
  });

  it("upload ve analysis GET uçları bilinen kaydı 200, bilinmeyeni 404 döner", async () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    const knownUpload = await getUpload(
      new Request("http://localhost"),
      uploadContext(upload.uploadId),
    );
    const unknownUploadId = crypto.randomUUID();
    const unknownUpload = await getUpload(
      new Request("http://localhost"),
      uploadContext(unknownUploadId),
    );

    expect(knownUpload.status).toBe(200);
    expect(unknownUpload.status).toBe(404);
    expect(await problemCode(unknownUpload)).toBe("JOB_NOT_FOUND");

    const analysis = createAnalysisRecord(analysisRequestFor(upload.uploadId));
    const knownAnalysis = await getAnalysis(
      new Request("http://localhost"),
      analysisContext(analysis.analysisId),
    );
    const unknownAnalysisId = crypto.randomUUID();
    const unknownAnalysis = await getAnalysis(
      new Request("http://localhost"),
      analysisContext(unknownAnalysisId),
    );

    expect(knownAnalysis.status).toBe(200);
    expect(unknownAnalysis.status).toBe(404);
    expect(await problemCode(unknownAnalysis)).toBe("JOB_NOT_FOUND");
  });

  it("result ucu unknown için 404, pending için 409 ve completed için 200 döner", async () => {
    const unknownId = crypto.randomUUID();
    const unknown = await getAnalysisResult(
      new Request("http://localhost"),
      analysisContext(unknownId),
    );
    expect(unknown.status).toBe(404);
    expect(await problemCode(unknown)).toBe("JOB_NOT_FOUND");

    const upload = createUploadRecord("veri.xlsx", 1024);
    const analysis = createAnalysisRecord(analysisRequestFor(upload.uploadId));
    const pending = await getAnalysisResult(
      new Request("http://localhost"),
      analysisContext(analysis.analysisId),
    );
    expect(pending.status).toBe(409);
    expect(await problemCode(pending)).toBe("JOB_CONFLICT");

    advance(40_000);
    const completed = await getAnalysisResult(
      new Request("http://localhost"),
      analysisContext(analysis.analysisId),
    );
    expect(completed.status).toBe(200);
    expect(await completed.json()).toMatchObject({
      analysis_id: analysis.analysisId,
      status: "completed",
    });
  });
});

describe("POST /analyses doğrulama parity'si", () => {
  it("boşluklardan oluşan BYOK header'ını PROVIDER_AUTH_FAILED ile reddeder", async () => {
    const response = await postAnalysis(
      analysisRequest(analysisRequestFor(crypto.randomUUID()), { apiKey: "   " }),
    );

    expect(response.status).toBe(422);
    expect(await problemCode(response)).toBe("PROVIDER_AUTH_FAILED");
  });

  it("bilinmeyen upload'ı 404 ile reddeder", async () => {
    const response = await postAnalysis(
      analysisRequest(analysisRequestFor(crypto.randomUUID()), { apiKey: "sk-test" }),
    );

    expect(response.status).toBe(404);
    expect(await problemCode(response)).toBe("JOB_NOT_FOUND");
  });

  it("ready olmayan upload'ı 409 ile reddeder", async () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    const response = await postAnalysis(
      analysisRequest(analysisRequestFor(upload.uploadId), { apiKey: "sk-test" }),
    );

    expect(response.status).toBe(409);
    expect(await problemCode(response)).toBe("JOB_CONFLICT");
  });

  it.each([
    ["sheet_name", "Olmayan Sayfa"],
    ["text_column", "olmayan_kolon"],
  ] as const)("profilde olmayan %s için özel 422 kodunu döner", async (field, value) => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    advance(5_000);
    const body = { ...analysisRequestFor(upload.uploadId), [field]: value };
    const response = await postAnalysis(analysisRequest(body, { apiKey: "sk-test" }));

    expect(response.status).toBe(422);
    expect(await problemCode(response)).toBe("SHEET_OR_COLUMN_NOT_FOUND");
  });

  it.each([
    ["model", "   "],
    ["prompt_version", "   "],
  ] as const)(
    "yalnızca boşluk içeren %s alanını genel validation hatası sayar",
    async (field, value) => {
      const body = { ...analysisRequestFor(crypto.randomUUID()), [field]: value };
      const response = await postAnalysis(analysisRequest(body, { apiKey: "sk-test" }));

      expect(response.status).toBe(422);
      expect(await problemCode(response)).toBe("REQUEST_VALIDATION");
    },
  );
});

describe("mock idempotency", () => {
  it("upload replay'inde ilk 202 gövdesini ve trace header'ını aynen döner", async () => {
    const key = `upload-replay-${crypto.randomUUID()}`;
    const first = await postUpload(uploadRequest(xlsxFile(), key));
    const firstTrace = first.headers.get(TRACE_ID_HEADER);
    const firstBody = uploadCreatedSchema.parse(await first.json());
    const replay = await postUpload(uploadRequest(xlsxFile(), key));
    const replayBody = uploadCreatedSchema.parse(await replay.json());

    expect(first.status).toBe(202);
    expect(replay.status).toBe(202);
    expect(replayBody).toEqual(firstBody);
    expect(replay.headers.get(TRACE_ID_HEADER)).toBe(firstTrace);
  });

  it("aynı upload key'i altında byte içeriği değişirse 409 döner", async () => {
    const key = `upload-conflict-${crypto.randomUUID()}`;
    await postUpload(uploadRequest(xlsxFile("abc"), key));
    const conflict = await postUpload(uploadRequest(xlsxFile("xyz"), key));

    expect(conflict.status).toBe(409);
    expect(await problemCode(conflict)).toBe("JOB_CONFLICT");
  });

  it("upload fingerprint'ine orijinal MIME type'ı da katar", async () => {
    const key = `upload-mime-${crypto.randomUUID()}`;
    const first = new File(["same"], "veri.xlsx", { type: XLSX_MEDIA_TYPE });
    const changedMime = new File(["same"], "veri.xlsx", { type: "application/octet-stream" });

    await postUpload(uploadRequest(first, key));
    const conflict = await postUpload(uploadRequest(changedMime, key));

    expect(conflict.status).toBe(409);
    expect(await problemCode(conflict)).toBe("JOB_CONFLICT");
  });

  it("analysis replay'inde yeni BYOK değerini fingerprint'e katmaz", async () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    advance(5_000);
    const body = analysisRequestFor(upload.uploadId);
    const key = `analysis-replay-${crypto.randomUUID()}`;

    const first = await postAnalysis(
      analysisRequest(body, { apiKey: "sk-original", idempotencyKey: key }),
    );
    const firstTrace = first.headers.get(TRACE_ID_HEADER);
    const firstBody = analysisCreatedSchema.parse(await first.json());
    const replay = await postAnalysis(
      analysisRequest(body, { apiKey: "sk-rotated", idempotencyKey: key }),
    );
    const replayBody = analysisCreatedSchema.parse(await replay.json());

    expect(replay.status).toBe(202);
    expect(replayBody).toEqual(firstBody);
    expect(replay.headers.get(TRACE_ID_HEADER)).toBe(firstTrace);
  });

  it("aynı analysis key'i farklı canonical gövdeyle kullanılırsa 409 döner", async () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    advance(5_000);
    const body = analysisRequestFor(upload.uploadId);
    const key = `analysis-conflict-${crypto.randomUUID()}`;

    await postAnalysis(analysisRequest(body, { apiKey: "sk-test", idempotencyKey: key }));
    const conflict = await postAnalysis(
      analysisRequest({ ...body, top_n: 7 }, { apiKey: "sk-test", idempotencyKey: key }),
    );

    expect(conflict.status).toBe(409);
    expect(await problemCode(conflict)).toBe("JOB_CONFLICT");
  });

  it("24 saat dolunca aynı upload key'i yeni istek sayar", async () => {
    const key = `upload-expiry-${crypto.randomUUID()}`;
    const first = uploadCreatedSchema.parse(
      await (await postUpload(uploadRequest(xlsxFile(), key))).json(),
    );

    advance(IDEMPOTENCY_TTL_MS + 1);
    const afterExpiry = uploadCreatedSchema.parse(
      await (await postUpload(uploadRequest(xlsxFile(), key))).json(),
    );

    expect(afterExpiry.upload_id).not.toBe(first.upload_id);
  });

  it("iki POST ucunda da 255 karakterlik header sınırını uygular", async () => {
    const tooLong = "x".repeat(IDEMPOTENCY_KEY_MAX_LENGTH + 1);
    const uploadResponse = await postUpload(uploadRequest(xlsxFile(), tooLong));
    const analysisResponse = await postAnalysis(
      analysisRequest(analysisRequestFor(crypto.randomUUID()), {
        apiKey: "sk-test",
        idempotencyKey: tooLong,
      }),
    );

    for (const response of [uploadResponse, analysisResponse]) {
      expect(response.status).toBe(422);
      expect(await problemCode(response)).toBe("REQUEST_VALIDATION");
    }
  });
});

describe("export route parity", () => {
  it("bilinmeyen analiz için 404, tamamlanmayan analiz için 409 döner", async () => {
    const unknown = crypto.randomUUID();
    const unknownResponse = await exportAnalysis(
      new NextRequest(`http://localhost/api/mock/v1/analyses/${unknown}/export?format=json`),
      analysisContext(unknown),
    );
    expect(unknownResponse.status).toBe(404);
    expect(await problemCode(unknownResponse)).toBe("JOB_NOT_FOUND");

    const upload = createUploadRecord("veri.xlsx", 1024);
    const analysis = createAnalysisRecord(analysisRequestFor(upload.uploadId));
    const pendingResponse = await exportAnalysis(
      new NextRequest(
        `http://localhost/api/mock/v1/analyses/${analysis.analysisId}/export?format=json`,
      ),
      analysisContext(analysis.analysisId),
    );
    expect(pendingResponse.status).toBe(409);
    expect(await problemCode(pendingResponse)).toBe("JOB_CONFLICT");
  });

  it("varsayılan XLSX export'u 200 ve geçerli ZIP imzasıyla döner", async () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    const analysis = createAnalysisRecord(analysisRequestFor(upload.uploadId));
    advance(40_000);

    const response = await exportAnalysis(
      new NextRequest(`http://localhost/api/mock/v1/analyses/${analysis.analysisId}/export`),
      analysisContext(analysis.analysisId),
    );
    const bytes = new Uint8Array(await response.arrayBuffer());

    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toBe(XLSX_MEDIA_TYPE);
    expect(response.headers.get("Content-Disposition")).toBe(
      `attachment; filename="analiz-${analysis.analysisId}.xlsx"`,
    );
    expect([...bytes.slice(0, 4)]).toEqual([0x50, 0x4b, 0x03, 0x04]);
    expect(bytes.length).toBeGreaterThan(500);
  });

  it("tamamlanan analizin JSON attachment export'unu 200 döner", async () => {
    const upload = createUploadRecord("veri.xlsx", 1024);
    const analysis = createAnalysisRecord(analysisRequestFor(upload.uploadId));
    advance(40_000);

    const response = await exportAnalysis(
      new NextRequest(
        `http://localhost/api/mock/v1/analyses/${analysis.analysisId}/export?format=json`,
      ),
      analysisContext(analysis.analysisId),
    );
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toBe("application/json");
    expect(response.headers.get("Content-Disposition")).toBe(
      `attachment; filename="analiz-${analysis.analysisId}.json"`,
    );
    expect(body).toMatchObject({ analysis_id: analysis.analysisId, status: "completed" });
  });
});
