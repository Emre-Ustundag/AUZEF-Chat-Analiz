import { describe, expect, it } from "vitest";

import type { AnalysisRequest } from "@/lib/api/schemas";

import { analysisFingerprint, canonicalJson, uploadFingerprint } from "./idempotency";

describe("idempotency fingerprint'leri", () => {
  it("ASCII object anahtarlarını code-unit sırasıyla ve boşluksuz yazar", () => {
    expect(canonicalJson({ top_n: 8, model: "m", nested: { z: 2, a: 1 } })).toBe(
      '{"model":"m","nested":{"a":1,"z":2},"top_n":8}',
    );
  });

  it("analysis fingerprint'ini property eklenme sırasından bağımsız üretir", () => {
    const left: AnalysisRequest = {
      upload_id: "00000000-0000-4000-8000-000000000000",
      sheet_name: "Mesajlar",
      text_column: "mesaj",
      model: "anthropic/claude-sonnet-4.6",
      prompt_version: "faq_analysis/v1",
      top_n: 8,
      max_cost_usd: 10,
    };
    const right = Object.fromEntries(Object.entries(left).reverse()) as typeof left;

    expect(analysisFingerprint(left)).toBe(analysisFingerprint(right));
  });

  it("upload fingerprint'inde byte, filename, MIME ve size metadata'sını kullanır", async () => {
    const original = new File(["same"], "veri.xlsx", { type: "application/x-one" });
    const same = new File(["same"], "veri.xlsx", { type: "application/x-one" });
    const changedMime = new File(["same"], "veri.xlsx", { type: "application/x-two" });
    const changedBytes = new File(["diff"], "veri.xlsx", { type: "application/x-one" });

    const fingerprint = await uploadFingerprint(original);
    expect(await uploadFingerprint(same)).toBe(fingerprint);
    expect(await uploadFingerprint(changedMime)).not.toBe(fingerprint);
    expect(await uploadFingerprint(changedBytes)).not.toBe(fingerprint);
  });
});
