import { describe, expect, it } from "vitest";

import {
  analysisReportSchema,
  analysisStatusSchema,
  ERROR_MESSAGES_TR,
  errorCodeSchema,
  exportFormatSchema,
  modelIdSchema,
  problemDetailsSchema,
  promptVersionSchema,
  RETRYABLE_ERROR_CODES,
  uploadStatusSchema,
} from "./index";
import { OPENAPI_PATH, readJson } from "./contract-paths";

/**
 * DRIFT KONTROLÜ — Katman 2 (enum parity) ve Katman 4 (yol envanteri).
 *
 * `docs/api/openapi.json` Pydantic modellerinden üretilir ve sözleşmenin
 * kayıt artefaktıdır (ADR-0002 #8). Buradaki iddialar, backend'e eklenen bir
 * enum üyesinin veya yeniden adlandırılan bir ucun frontend'e yansıtılmadan
 * merge edilmesini engeller.
 */

interface OpenApiDocument {
  info: { version: string };
  paths: Record<
    string,
    Record<
      string,
      {
        security?: unknown[];
        parameters?: { name: string; example?: unknown }[];
        requestBody?: { content: Record<string, { examples?: Record<string, unknown> }> };
        responses: Record<
          string,
          {
            "x-error-codes"?: string[];
            content?: Record<
              string,
              { schema?: { $ref?: string }; examples?: Record<string, { value: unknown }> }
            >;
            headers?: Record<string, { example?: unknown }>;
          }
        >;
      }
    >
  >;
  components: {
    schemas: Record<
      string,
      {
        enum?: string[];
        properties?: Record<
          string,
          { type?: string; format?: string; $ref?: string; anyOf?: unknown }
        >;
        required?: string[];
      }
    >;
    securitySchemes?: Record<string, { name?: string }>;
  };
}

const openapi = readJson<OpenApiDocument>(OPENAPI_PATH);

const sorted = (values: readonly string[]) => [...values].sort();

describe("enum parity — OpenAPI ↔ Zod", () => {
  // Küme olarak karşılaştırılıyor: üye sırası bir sözleşme farkı değil.
  it.each([
    ["ErrorCode", errorCodeSchema.options],
    ["UploadStatus", uploadStatusSchema.options],
    ["AnalysisStatus", analysisStatusSchema.options],
    ["ExportFormat", exportFormatSchema.options],
    ["ModelId", modelIdSchema.options],
    ["PromptVersion", promptVersionSchema.options],
  ])("%s aynı üyelere sahip", (schemaName, zodOptions) => {
    const openapiEnum = openapi.components.schemas[schemaName]?.enum;
    expect(openapiEnum, `${schemaName} openapi.json'da bulunamadı`).toBeDefined();
    expect(sorted(openapiEnum!)).toEqual(sorted(zodOptions));
  });
});

describe("hata kodu tablosu", () => {
  it("her kodun Türkçe kullanıcı mesajı var", () => {
    for (const code of errorCodeSchema.options) {
      expect(ERROR_MESSAGES_TR[code], `${code} için mesaj yok`).toBeTruthy();
    }
  });

  it("ERROR_MESSAGES_TR fazladan anahtar taşımıyor", () => {
    expect(sorted(Object.keys(ERROR_MESSAGES_TR))).toEqual(sorted(errorCodeSchema.options));
  });

  it("retryable kodlar enum'un alt kümesi", () => {
    for (const code of RETRYABLE_ERROR_CODES) {
      expect(errorCodeSchema.options).toContain(code);
    }
  });

  // ADR-0002 #1: dördü de kullanıcı girdisi hatası; aynı isteği tekrarlamak
  // aynı hatayı üretir, dolayısıyla "Tekrar dene" düğmesi gösterilmemeli.
  it.each(["REQUEST_VALIDATION", "INVALID_MODEL", "INVALID_PROMPT", "COST_LIMIT_EXCEEDED"])(
    "%s retryable DEĞİL",
    (code) => {
      expect(RETRYABLE_ERROR_CODES).not.toContain(code);
    },
  );
});

describe("yol envanteri", () => {
  const EXPECTED_ENDPOINTS = [
    "GET /api/v1/health/live",
    "GET /api/v1/health/ready",
    "POST /api/v1/uploads",
    "GET /api/v1/uploads/{upload_id}",
    "DELETE /api/v1/uploads/{upload_id}",
    "GET /api/v1/models",
    "POST /api/v1/analyses",
    "GET /api/v1/analyses/{analysis_id}",
    "DELETE /api/v1/analyses/{analysis_id}",
    "GET /api/v1/analyses/{analysis_id}/result",
    "GET /api/v1/analyses/{analysis_id}/export",
  ];

  const actual = Object.entries(openapi.paths).flatMap(([p, ops]) =>
    Object.keys(ops).map((method) => `${method.toUpperCase()} ${p}`),
  );

  it("OpenAPI tam olarak beklenen uçları belgeliyor", () => {
    expect(sorted(actual)).toEqual(sorted(EXPECTED_ENDPOINTS));
  });
});

describe("sözleşme detayları", () => {
  const expectedStatuses: Record<string, number[]> = {
    "GET /api/v1/health/live": [200],
    "GET /api/v1/health/ready": [200, 503],
    "POST /api/v1/uploads": [202, 409, 413, 415, 422, 500, 501],
    "GET /api/v1/uploads/{upload_id}": [200, 404, 422, 500, 501],
    "DELETE /api/v1/uploads/{upload_id}": [204, 404, 422, 500, 501],
    "GET /api/v1/models": [200, 500, 501],
    "POST /api/v1/analyses": [202, 404, 409, 422, 500, 501],
    "GET /api/v1/analyses/{analysis_id}": [200, 404, 422, 500, 501],
    "DELETE /api/v1/analyses/{analysis_id}": [204, 404, 409, 422, 500, 501],
    "GET /api/v1/analyses/{analysis_id}/result": [200, 404, 409, 422, 500, 501],
    "GET /api/v1/analyses/{analysis_id}/export": [200, 404, 409, 422, 500, 501],
  };

  it("her uç yalnızca gerçekten üretebildiği status'ları belgeler", () => {
    for (const [path, operations] of Object.entries(openapi.paths)) {
      for (const [method, operation] of Object.entries(operations)) {
        const key = `${method.toUpperCase()} ${path}`;
        expect(
          Object.keys(operation.responses)
            .map(Number)
            .sort((a, b) => a - b),
          key,
        ).toEqual(expectedStatuses[key]);
      }
    }
  });

  it("ProblemDetails alanları, required listesi ve null olmayan retry_after belgeli", () => {
    const schema = openapi.components.schemas.ProblemDetails;
    expect(Object.keys(schema.properties ?? {}).sort()).toEqual(
      ["type", "title", "status", "code", "detail", "trace_id", "errors", "retry_after"].sort(),
    );
    expect(schema.required?.sort()).toEqual(
      ["type", "title", "status", "code", "detail", "trace_id", "errors"].sort(),
    );
    expect(schema.properties?.trace_id?.format).toBe("uuid");
    expect(schema.properties?.retry_after?.type).toBe("number");
    expect(schema.properties?.retry_after).not.toHaveProperty("anyOf");
  });

  /**
   * Sunucunun HER cevapta yazdığı alanlar `required` olarak belgelenmelidir.
   *
   * Pydantic'in serialization şeması default'u olan alanları varsayılan olarak
   * required dışında bırakır; `ApiModel` bunu
   * `json_schema_serialization_defaults_required` ile geri çeviriyor. Bayrak
   * düşerse bu artefakttan üretilecek client'ta `status?: "completed"` ve
   * `error?: ProblemDetails | null` çıkar — arayüzün dayandığı iki
   * discriminator tip düzeyinde buharlaşır. Fixture'lar alanların gerçekten
   * her zaman yazıldığının kanıtı (ör. uploads.get.200.queued.json →
   * profile: null, error: null).
   */
  it.each([
    ["AnalysisCreated", ["analysis_id", "status"]],
    ["UploadCreated", ["upload_id", "status"]],
    [
      "AnalysisJob",
      [
        "analysis_id",
        "status",
        "progress",
        "created_at",
        "updated_at",
        "estimated_seconds_remaining",
        "error",
      ],
    ],
    ["Upload", ["upload_id", "status", "filename", "size_bytes", "created_at", "profile", "error"]],
    [
      "AnalysisReport",
      [
        "schema_version",
        "analysis_id",
        "status",
        "generated_at",
        "source_summary",
        "preprocessing_summary",
        "top_questions",
        "themes",
        "executive_summary",
        "warnings",
        "model",
        "prompt_version",
        "prompt_hash",
        "token_usage",
        "estimated_cost_usd",
        "cost_source",
        "pricing_snapshot",
      ],
    ],
  ])("%s cevap şemasında her alan required", (name, expected) => {
    const schema = openapi.components.schemas[name];
    expect(Object.keys(schema.properties ?? {}).sort()).toEqual([...expected].sort());
    expect(schema.required?.sort()).toEqual([...expected].sort());
  });

  it("hata cevapları yalnızca problem+json ve geçerli ProblemDetails örnekleri taşır", () => {
    for (const [path, operations] of Object.entries(openapi.paths)) {
      for (const [method, operation] of Object.entries(operations)) {
        if (!path.startsWith("/api/v1/health/")) {
          expect(
            operation.responses["501"]?.["x-error-codes"],
            `${method} ${path} public 501`,
          ).toEqual(["NOT_IMPLEMENTED"]);
        }
        for (const [status, response] of Object.entries(operation.responses)) {
          if (Number(status) < 400) continue;
          expect(Object.keys(response.content ?? {}), `${method} ${path} ${status}`).toEqual([
            "application/problem+json",
          ]);
          const media = response.content?.["application/problem+json"];
          expect(media?.schema?.$ref).toBe("#/components/schemas/ProblemDetails");
          expect(Object.keys(media?.examples ?? {}).length).toBeGreaterThan(0);
          for (const example of Object.values(media?.examples ?? {})) {
            expect(problemDetailsSchema.safeParse(example.value).success).toBe(true);
          }
        }
      }
    }
  });

  it("request, response ve header örnekleri belgeli", () => {
    const upload = openapi.paths["/api/v1/uploads"].post;
    const analysis = openapi.paths["/api/v1/analyses"].post;
    expect(
      Object.keys(Object.values(upload.requestBody!.content)[0].examples ?? {}),
    ).not.toHaveLength(0);
    expect(
      Object.keys(Object.values(analysis.requestBody!.content)[0].examples ?? {}),
    ).not.toHaveLength(0);
    expect(
      upload.parameters?.find((parameter) => parameter.name === "Idempotency-Key")?.example,
    ).toBeTruthy();

    for (const operations of Object.values(openapi.paths)) {
      for (const operation of Object.values(operations)) {
        for (const response of Object.values(operation.responses)) {
          expect(response.headers?.["X-Trace-Id"]?.example).toBeTruthy();
        }
      }
    }
  });

  it("JSON export gerçek AnalysisReport şeması ve geçerli rapor örneği taşır", () => {
    const response = openapi.paths["/api/v1/analyses/{analysis_id}/export"].get.responses["200"];
    const jsonMedia = response.content?.["application/json"];

    expect(jsonMedia?.schema?.$ref).toBe("#/components/schemas/AnalysisReport");
    expect(Object.keys(jsonMedia?.examples ?? {})).not.toHaveLength(0);
    for (const example of Object.values(jsonMedia?.examples ?? {})) {
      const result = analysisReportSchema.safeParse(example.value);
      expect(result.success ? null : result.error.issues).toBeNull();
    }
  });

  it("Idempotency-Key yalnızca iki POST ucunda belgeli", () => {
    const withHeader = Object.entries(openapi.paths).flatMap(([p, ops]) =>
      Object.entries(ops)
        .filter(([, op]) => op.parameters?.some((param) => param.name === "Idempotency-Key"))
        .map(([method]) => `${method.toUpperCase()} ${p}`),
    );
    expect(sorted(withHeader)).toEqual(["POST /api/v1/analyses", "POST /api/v1/uploads"]);
  });

  it("X-OpenRouter-Key security scheme'i yalnızca POST /analyses'e bağlı", () => {
    const secured = Object.entries(openapi.paths).flatMap(([p, ops]) =>
      Object.entries(ops)
        .filter(([, op]) => op.security !== undefined)
        .map(([method]) => `${method.toUpperCase()} ${p}`),
    );
    expect(secured).toEqual(["POST /api/v1/analyses"]);
    expect(openapi.components.securitySchemes?.OpenRouterKey?.name).toBe("X-OpenRouter-Key");
  });

  // Anahtar YALNIZCA security scheme olarak belgelenmeli. Ayrıca opsiyonel bir
  // header parametresi olarak da görünürse şema "isteğe bağlı" derken sunucu
  // header'sız isteğe 422 verir; `endpoints.ts` header'ı her zaman gönderiyor
  // ama üretilmiş bir client bu belgeye bakıp göndermeyebilirdi.
  it("X-OpenRouter-Key ayrıca düz parametre olarak belgelenmemiş", () => {
    const asParameter = Object.values(openapi.paths).flatMap((ops) =>
      Object.values(ops).filter((op) =>
        op.parameters?.some((param) => param.name === "X-OpenRouter-Key"),
      ),
    );
    expect(asParameter).toEqual([]);
  });

  // Bunlar FastAPI'nin otomatik 422 modelleri. Sunucu onları asla üretmiyor
  // (RequestValidationError handler'ı hepsini ProblemDetails'e çeviriyor);
  // şemada kalsalardı kayıt artefaktı daha ilk gün yalan söylerdi.
  it.each(["HTTPValidationError", "ValidationError"])("%s şemadan çıkarılmış", (name) => {
    expect(openapi.components.schemas).not.toHaveProperty(name);
  });

  it("sözleşme sürümü sabitlenmiş", () => {
    expect(openapi.info.version).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
