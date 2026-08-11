import { ERROR_MESSAGES_TR, LIMITS } from "@/lib/api/schemas";
import type { ErrorCode } from "@/lib/api/schemas";

/**
 * Dosya seçildiğinde, yükleme BAŞLAMADAN önce yapılan istemci doğrulaması.
 *
 * Bu bir güvenlik kontrolü değildir — gerçek kapı backend'de. Amacı tamamen
 * kullanıcıyı korumak: 130 MB'lık bir dosyayı yavaş bir bağlantıdan tamamen
 * yükleyip sonunda 413 almak, dakikalar süren ve hiçbir şey kazandırmayan
 * bir hata deneyimi olurdu.
 *
 * Hata kodları backend'in kodlarıyla aynı kümeden seçilir ki kullanıcı
 * istemci ve sunucu reddi arasında farklı metinler görmesin.
 */
export type FileValidationResult = { ok: true } | { ok: false; code: ErrorCode; message: string };

const EMPTY_FILE_MESSAGE = "Seçilen dosya boş. Lütfen geçerli bir .xlsx dosyası seçin.";

export function validateFile(file: File): FileValidationResult {
  const name = file.name.toLocaleLowerCase("tr");

  // Uzantı kontrolü bilinçli olarak MIME tipinden önce ve tek başına
  // belirleyici: tarayıcılar .xlsx için işletim sistemine göre farklı
  // (bazen boş) MIME tipi bildiriyor, uzantı ise kullanıcının gördüğü şey.
  if (!name.endsWith(LIMITS.ACCEPTED_EXTENSION)) {
    return {
      ok: false,
      code: "UPLOAD_INVALID_TYPE",
      message: ERROR_MESSAGES_TR.UPLOAD_INVALID_TYPE,
    };
  }

  if (file.size === 0) {
    return {
      ok: false,
      code: "UPLOAD_CORRUPT_OR_ENCRYPTED",
      message: EMPTY_FILE_MESSAGE,
    };
  }

  if (file.size > LIMITS.MAX_UPLOAD_BYTES) {
    return {
      ok: false,
      code: "UPLOAD_TOO_LARGE",
      message: ERROR_MESSAGES_TR.UPLOAD_TOO_LARGE,
    };
  }

  return { ok: true };
}
