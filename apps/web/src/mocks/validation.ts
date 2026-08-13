import * as z from "zod";

import type { ProblemDetails } from "@/lib/api/schemas";

import { problem } from "./store";

const uuidSchema = z.uuid();

/** OpenAPI'deki `Idempotency-Key` üst sınırı. */
export const IDEMPOTENCY_KEY_MAX_LENGTH = 255;

/**
 * FastAPI'nin UUID path parametresi doğrulamasıyla aynı 422 gövdesini üretir.
 * Lookup'tan önce çağrılmalıdır; biçimi bozuk bir kimlik 404 değildir.
 */
export function invalidUuidProblem(value: string, field: string): ProblemDetails | null {
  if (uuidSchema.safeParse(value).success) return null;

  return problem(
    "REQUEST_VALIDATION",
    422,
    "Geçersiz işlem kimliği",
    "Path parametresi geçerli bir UUID olmalıdır.",
    { errors: [{ field, message: "Geçerli bir UUID girilmelidir." }] },
  );
}

export type IdempotencyKeyValidation =
  { key: string | null; error: null } | { key: null; error: ProblemDetails };

/** Opsiyonel header'ı normalize eder ve OpenAPI'deki 255 karakter sınırını uygular. */
export function validateIdempotencyKey(value: string | null): IdempotencyKeyValidation {
  if (value === null || value.trim() === "") return { key: null, error: null };

  if (value.length > IDEMPOTENCY_KEY_MAX_LENGTH) {
    return {
      key: null,
      error: problem(
        "REQUEST_VALIDATION",
        422,
        "Idempotency anahtarı geçersiz",
        `Idempotency-Key en fazla ${IDEMPOTENCY_KEY_MAX_LENGTH} karakter olabilir.`,
        {
          errors: [
            {
              field: "header.Idempotency-Key",
              message: `En fazla ${IDEMPOTENCY_KEY_MAX_LENGTH} karakter olabilir.`,
            },
          ],
        },
      ),
    };
  }

  return { key: value, error: null };
}
