import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { GET as getModels } from "@/app/api/mock/v1/models/route";
import { POST as postUpload } from "@/app/api/mock/v1/uploads/route";
import { problemDetailsSchema } from "@/lib/api/schemas";

import {
  jsonResponse,
  noContentResponse,
  PROBLEM_MEDIA_TYPE,
  problemResponse,
  TRACE_ID_HEADER,
} from "./responses";
import { problem } from "./store";

describe("mock HTTP response parity", () => {
  it("problem+json döner ve gövde/header aynı UUID'yi taşır", async () => {
    const response = problemResponse(
      problem("JOB_NOT_FOUND", 404, "İşlem bulunamadı", "Kayıt yok."),
    );
    const body = problemDetailsSchema.parse(await response.json());

    expect(response.status).toBe(404);
    expect(response.headers.get("Content-Type")).toBe(PROBLEM_MEDIA_TYPE);
    expect(response.headers.get(TRACE_ID_HEADER)).toBe(body.trace_id);
  });

  it("başarılı JSON ve 204 cevapları da trace header'ı taşır", async () => {
    const json = jsonResponse({ ok: true });
    const noContent = noContentResponse();

    expect(await json.json()).toEqual({ ok: true });
    expect(json.headers.get(TRACE_ID_HEADER)).toMatch(/^[0-9a-f-]{36}$/);
    expect(noContent.status).toBe(204);
    expect(noContent.headers.get(TRACE_ID_HEADER)).toMatch(/^[0-9a-f-]{36}$/);
    expect(await noContent.text()).toBe("");
  });

  it("eksik multipart file alanını 422 REQUEST_VALIDATION ile reddeder", async () => {
    const request = new NextRequest("http://localhost/api/mock/v1/uploads", {
      method: "POST",
      body: new FormData(),
    });
    const response = await postUpload(request);
    const body = problemDetailsSchema.parse(await response.json());

    expect(response.status).toBe(422);
    expect(response.headers.get("Content-Type")).toBe(PROBLEM_MEDIA_TYPE);
    expect(body.code).toBe("REQUEST_VALIDATION");
    expect(body.errors[0]?.field).toBe("file");
  });

  it("gönderilmiş fakat desteklenmeyen dosyayı 415 ile reddeder", async () => {
    const form = new FormData();
    form.set("file", new File(["x"], "veri.txt", { type: "text/plain" }));
    const request = new NextRequest("http://localhost/api/mock/v1/uploads", {
      method: "POST",
      body: form,
    });
    const response = await postUpload(request);
    const body = problemDetailsSchema.parse(await response.json());

    expect(response.status).toBe(415);
    expect(body.code).toBe("UPLOAD_INVALID_TYPE");
  });

  it("CSV dosyasını kabul eder (B1)", async () => {
    const form = new FormData();
    form.set("file", new File(["mesaj\nsoru\n"], "veri.csv", { type: "text/csv" }));
    const request = new NextRequest("http://localhost/api/mock/v1/uploads", {
      method: "POST",
      body: form,
    });
    const response = await postUpload(request);

    expect(response.status).toBe(202);
  });

  it("models route başarılı cevapta trace header'ı yayar", async () => {
    const response = getModels();
    expect(response.status).toBe(200);
    expect(response.headers.get(TRACE_ID_HEADER)).toMatch(/^[0-9a-f-]{36}$/);
  });
});
