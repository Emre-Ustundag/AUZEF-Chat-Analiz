import { expect, it } from "vitest";

import { modelListSchema } from "@/lib/api/schemas";
import { readFixture } from "@/lib/api/schemas/contract-paths";

import { KNOWN_PROMPT_VERSIONS, MOCK_MODEL_LIST } from "./catalog";

it("mock model kataloğu backend'in ürettiği whitelist fixture'ıyla birebir aynı", () => {
  const generated = modelListSchema.parse(readFixture("models.list.200.json"));
  expect(MOCK_MODEL_LIST).toEqual(generated);
  expect(KNOWN_PROMPT_VERSIONS).toContain(generated.default_prompt_version);
});
