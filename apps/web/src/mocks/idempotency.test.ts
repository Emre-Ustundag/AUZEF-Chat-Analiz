import { describe, expect, it } from "vitest";

import { analysisRequestSchema } from "@/lib/api/schemas";
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
      row_filters: [],
      model: "anthropic/claude-sonnet-4.6",
      prompt_version: "faq_analysis/v1",
      top_n: 8,
      max_cost_usd: 10,
    };
    const right = Object.fromEntries(Object.entries(left).reverse()) as typeof left;

    expect(analysisFingerprint(analysisRequestSchema.parse(left))).toBe(
      analysisFingerprint(analysisRequestSchema.parse(right)),
    );
  });

  it("message modu defaultlarını legacy fingerprint'ten çıkarır", () => {
    const legacy: AnalysisRequest = {
      upload_id: "00000000-0000-4000-8000-000000000000",
      sheet_name: "Mesajlar",
      text_column: "mesaj",
      row_filters: [],
      model: "anthropic/claude-sonnet-4.6",
      prompt_version: "faq_analysis/v1",
      top_n: 8,
      max_cost_usd: 10,
    };

    expect(
      analysisFingerprint(
        analysisRequestSchema.parse({
          ...legacy,
          analysis_mode: "message",
          conversation_config: null,
        }),
      ),
    ).toBe(analysisFingerprint(analysisRequestSchema.parse(legacy)));
  });

  it("bağlamsal konuşma ayarlarını fingerprint'e dahil eder", () => {
    const contextual = analysisRequestSchema.parse({
      upload_id: "00000000-0000-4000-8000-000000000000",
      sheet_name: "Mesajlar",
      text_column: "message_text_clean",
      row_filters: [],
      analysis_mode: "contextual_user_turns",
      conversation_config: {
        session_id_column: "session_id",
        message_order_column: "message_order",
        role_column: "direction",
        message_type_column: "message_type",
        user_role_values: ["Kullanıcı"],
        assistant_role_values: ["Bot"],
        include_assistant_context: false,
        target_message_types: ["text", "quick_reply"],
        context_message_types: ["text", "quick_reply", "single-choice"],
        max_context_turns: 4,
        max_context_tokens: 1000,
      },
      model: "anthropic/claude-sonnet-4.6",
      prompt_version: "faq_analysis/v4",
      top_n: 8,
      max_cost_usd: 10,
    });

    expect(analysisFingerprint(contextual)).not.toBe(
      analysisFingerprint({
        ...contextual,
        conversation_config: { ...contextual.conversation_config!, max_context_turns: 5 },
      }),
    );
    expect(analysisFingerprint(contextual)).not.toBe(
      analysisFingerprint({
        ...contextual,
        conversation_config: {
          ...contextual.conversation_config!,
          include_assistant_context: true,
        },
      }),
    );
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
