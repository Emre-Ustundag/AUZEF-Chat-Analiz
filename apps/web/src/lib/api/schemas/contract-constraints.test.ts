import { describe, expect, it } from "vitest";
import type * as z from "zod";

import { analysisJobSchema, analysisRequestSchema, uploadSchema } from "./index";
import { readConstraints, readFixture } from "./contract-paths";

/**
 * DRIFT KONTROLÜ — kısıt tablosu.
 *
 * Fixture doğrulaması (Katman 1) ve enum parity (Katman 2) SINIR
 * DEĞERLERİNİ göremez: backend `le=100` iken frontend `.max(50)` olsaydı her
 * ikisi de yeşil kalırdı, çünkü geçerli örnek her iki tarafta da geçerli.
 * Üretilmiş TS tiplerini diff'lemek de göremezdi — `z.int()` ve `z.uuid()`
 * tip düzeyinde `number`/`string`'e siliniyor.
 *
 * Bu yüzden sınırlar paylaşılan bir tabloda: `tests/fixtures/contract/
 * constraints.json`. Aynı tabloyu `apps/backend/tests/test_constraints.py`
 * Pydantic ile çalıştırır.
 */

const SCHEMAS: Record<string, z.ZodType> = {
  AnalysisRequest: analysisRequestSchema,
  AnalysisJob: analysisJobSchema,
  Upload: uploadSchema,
};

const constraints = readConstraints();

it("kısıt tablosu boş değil", () => {
  expect(constraints.length).toBeGreaterThan(10);
});

describe.each(constraints)(
  "$model.$field = $value -> valid: $valid",
  ({ model, base, field, value, valid }) => {
    it(valid ? "kabul edilir" : "reddedilir", () => {
      const schema = SCHEMAS[model];
      expect(schema, `${model} için şema eşlemesi yok`).toBeDefined();

      const payload = { ...readFixture<Record<string, unknown>>(`${base}.json`), [field]: value };
      expect(schema.safeParse(payload).success).toBe(valid);
    });
  },
);
