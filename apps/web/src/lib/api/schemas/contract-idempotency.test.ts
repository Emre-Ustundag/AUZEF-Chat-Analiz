import { describe, expect, it } from "vitest";

import { analysisFingerprint, canonicalJson, uploadMetadataFingerprint } from "@/mocks/idempotency";

import { analysisRequestSchema } from "./index";
import { readFixture } from "./contract-paths";

/**
 * DRIFT KONTROLÜ — `Idempotency-Key` fingerprint'i (ADR-0002 #3).
 *
 * Fingerprint kuralı iki dilde AYRI yazıldı: Python
 * `apps/backend/app/services/idempotency.py`, TypeScript
 * `apps/web/src/mocks/idempotency.ts`. Diğer sözleşme alanlarının aksine
 * fingerprint'ler TEL ÜSTÜNDE KARŞILAŞMAZ — mock ile gerçek backend aynı anda
 * kullanılmıyor. Yani ayrışmaları hiçbir çalışma zamanı hatası üretmez;
 * yalnızca mock'a karşı geliştirilen bir istemci gerçek backend'de sessizce
 * başka davranır. Bu dosya o sessiz farkı CI'da sese çevirir.
 *
 * Fixture Python tarafında üretiliyor (`scripts/export_fixtures.py`), yani
 * beklenen değerler gerçek backend kodundan geliyor.
 */

interface FingerprintCase {
  id: string;
  kind: "analysis" | "upload";
  input: Record<string, unknown>;
  canonical_json: string;
  fingerprint: string;
}

const cases = readFixture<{ cases: FingerprintCase[] }>("idempotency.fingerprints.json").cases;

describe("idempotency fingerprint parity", () => {
  it("fixture iki uç için de vaka taşır", () => {
    expect(cases.length).toBeGreaterThan(0);
    expect(new Set(cases.map((c) => c.kind))).toEqual(new Set(["analysis", "upload"]));
  });

  it.each(cases)("$id canonical JSON'u iki dilde aynı", (testCase) => {
    expect(canonicalJson(testCase.input)).toBe(testCase.canonical_json);
  });

  it.each(cases.filter((c) => c.kind === "analysis"))("$id fingerprint'i eşleşir", (testCase) => {
    // Girdi ÖNCE Zod'dan geçiyor: backend de doğrulanmış gövdeyi hash'liyor,
    // ham isteği değil.
    const request = analysisRequestSchema.parse(testCase.input);

    expect(analysisFingerprint(request)).toBe(testCase.fingerprint);
  });

  it.each(cases.filter((c) => c.kind === "upload"))("$id fingerprint'i eşleşir", (testCase) => {
    const metadata = testCase.input as {
      file_sha256: string;
      filename: string;
      mime_type: string;
      size: number;
    };

    expect(uploadMetadataFingerprint(metadata)).toBe(testCase.fingerprint);
  });

  it("tam sayı değerli max_cost_usd JS biçiminde yazılır", () => {
    // Python'un varsayılanı `5.0` olurdu; `JSON.stringify(5.0)` → `5`.
    // Sözleşmenin en tipik gövdesindeki bu fark, iki dilin fingerprint'ini
    // sessizce ayrıştıran tek somut tuzaktı.
    expect(canonicalJson({ max_cost_usd: 5 })).toBe('{"max_cost_usd":5}');
    expect(canonicalJson({ max_cost_usd: 2.5 })).toBe('{"max_cost_usd":2.5}');
  });
});
