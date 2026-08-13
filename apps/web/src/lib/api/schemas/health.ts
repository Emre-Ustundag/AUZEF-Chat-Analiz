import * as z from "zod";

/** Process liveness cevabı; dış bağımlılık kontrol etmez. */
export const livenessResponseSchema = z.object({
  status: z.literal("ok"),
});

export type LivenessResponse = z.infer<typeof livenessResponseSchema>;

/** Readiness'e kayıtlı tek bir zorunlu bağımlılığın sonucu. */
export const readinessCheckResponseSchema = z.object({
  name: z.string(),
  status: z.literal("ok"),
});

export type ReadinessCheckResponse = z.infer<typeof readinessCheckResponseSchema>;

/** Trafik kabul etmeye hazır uygulama cevabı. */
export const readinessResponseSchema = z.object({
  status: z.literal("ready"),
  checks: z.array(readinessCheckResponseSchema).min(1),
});

export type ReadinessResponse = z.infer<typeof readinessResponseSchema>;
