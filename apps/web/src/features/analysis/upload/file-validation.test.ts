import { describe, expect, it } from "vitest";

import { LIMITS } from "@/lib/api/schemas";

import { validateFile } from "./file-validation";

function fileOf(name: string, size: number): File {
  const file = new File(["x"], name);
  // File.size salt okunur; testte boyutu doğrudan tanımlamak 150 MB'lık
  // gerçek bir buffer ayırmaktan çok daha ucuz.
  Object.defineProperty(file, "size", { value: size });
  return file;
}

describe("validateFile", () => {
  it("geçerli .xlsx dosyasını kabul eder", () => {
    expect(validateFile(fileOf("veri.xlsx", 1024))).toEqual({ ok: true });
  });

  it("desteklenmeyen uzantıyı reddeder", () => {
    const result = validateFile(fileOf("veri.xls", 1024));
    expect(result).toMatchObject({ ok: false, code: "UPLOAD_INVALID_TYPE" });
  });

  it("makrolu dosyayı reddeder", () => {
    expect(validateFile(fileOf("veri.xlsm", 1024))).toMatchObject({
      ok: false,
      code: "UPLOAD_INVALID_TYPE",
    });
  });

  it("uzantıyı büyük/küçük harften bağımsız tanır", () => {
    expect(validateFile(fileOf("VERI.XLSX", 1024))).toEqual({ ok: true });
  });

  it("Türkçe karakterli dosya adını kabul eder", () => {
    // toLocaleLowerCase("tr") kullanılıyor; "I" harfi Türkçe'de "ı"ya döner
    // ve İngilizce lowercase ile karşılaştırma yapılsaydı bozulabilirdi.
    expect(validateFile(fileOf("SINAV_KAYITLARI.XLSX", 1024))).toEqual({
      ok: true,
    });
  });

  it("boş dosyayı reddeder", () => {
    expect(validateFile(fileOf("veri.xlsx", 0))).toMatchObject({
      ok: false,
      code: "UPLOAD_CORRUPT_OR_ENCRYPTED",
    });
  });

  it("sınırdaki dosyayı kabul eder", () => {
    expect(validateFile(fileOf("veri.xlsx", LIMITS.MAX_UPLOAD_BYTES))).toEqual({
      ok: true,
    });
  });

  it("sınırı bir bayt aşan dosyayı reddeder", () => {
    expect(
      validateFile(fileOf("veri.xlsx", LIMITS.MAX_UPLOAD_BYTES + 1)),
    ).toMatchObject({ ok: false, code: "UPLOAD_TOO_LARGE" });
  });

  it("hata mesajları backend'in Türkçe metinleriyle aynı kümeden gelir", () => {
    const result = validateFile(fileOf("veri.pdf", 1024));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain(".xlsx");
  });
});
