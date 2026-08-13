import { MOCK_MODEL_LIST } from "@/mocks/catalog";
import { jsonResponse } from "@/mocks/responses";

/**
 * GET /api/mock/v1/models
 *
 * ADR-0002 #1 ile sözleşmeye giren uç. Liste `@/mocks/catalog` içinde tutulur;
 * `analyses/route.ts` de INVALID_MODEL doğrulaması için aynı kaynağı okur.
 */
export function GET() {
  return jsonResponse(MOCK_MODEL_LIST);
}
