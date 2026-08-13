import * as z from "zod";
import { describe, expect, it } from "vitest";

import { uploadSchema } from "./index";
import { readFixture, readManifest } from "./contract-paths";

/**
 * DRIFT KONTROLÜ — tarih biçimi (ADR-0002 #4).
 *
 * Bu kısıt bilerek `constraints.json` dışında: Pydantic GİRİŞTE "+03:00"
 * kabul edip normalize ederken Zod reddediyor, yani ortak bir
 * `"valid": false` satırı Python tarafında düşerdi. Asimetri doğrudur
 * (girişte Postel, çıkışta katı) ve bir ÇIKTI kuralı olarak test edilir.
 */

const UTC_MILLIS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

/** ISO-8601 gibi görünen her string'i toplar. */
function collectDateLike(value: unknown, seen: string[] = []): string[] {
  if (typeof value === "string") {
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(value)) seen.push(value);
  } else if (Array.isArray(value)) {
    for (const item of value) collectDateLike(item, seen);
  } else if (value !== null && typeof value === "object") {
    for (const item of Object.values(value)) collectDateLike(item, seen);
  }
  return seen;
}

describe("üretilmiş fixture'lardaki tüm tarihler UTC-Z", () => {
  const files = readManifest()
    .cases.map((c) => c.file)
    .filter((f): f is string => f !== null);

  const samples = files.flatMap((file) =>
    collectDateLike(readFixture(file)).map((value) => ({ file, value })),
  );

  it("en az bir tarih örneklendi", () => {
    expect(samples.length).toBeGreaterThan(0);
  });

  it.each(samples)("$file: $value", ({ value }) => {
    expect(value).toMatch(UTC_MILLIS);
  });
});

/**
 * Regresyon kilidi: biri bir hatayı susturmak için şemayı gevşetmeye
 * kalkarsa burada patlasın. Zod'un varsayılan `z.iso.datetime()`'ı offset
 * kabul etmez; Pydantic'in VARSAYILAN datetime çıktısı ise "+00:00" üretir.
 * Backend'in `UtcDateTime` tipi tam olarak bu yüzden var.
 */
describe("z.iso.datetime() katılığı korunuyor", () => {
  const schema = z.iso.datetime();

  it.each(["2026-08-11T10:00:00Z", "2026-08-11T10:00:00.000Z"])("%s kabul edilir", (value) => {
    expect(schema.safeParse(value).success).toBe(true);
  });

  it.each([
    ["2026-08-11T10:00:00+00:00", "Pydantic'in varsayılan çıktısı"],
    ["2026-08-11T10:00:00+03:00", "yerel offset"],
    ["2026-08-11T10:00:00", "naive datetime"],
  ])("%s reddedilir (%s)", (value) => {
    expect(schema.safeParse(value).success).toBe(false);
  });
});

it("uploadSchema offset'li created_at'i reddeder", () => {
  const upload = readFixture<Record<string, unknown>>("uploads.get.200.ready.json");
  const result = uploadSchema.safeParse({ ...upload, created_at: "2026-08-11T10:00:00+00:00" });
  expect(result.success).toBe(false);
});
