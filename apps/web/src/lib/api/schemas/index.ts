/**
 * Backend API sözleşmesi — ADR (docs/mimari.md) §6, §7 ve §8'den türetilmiştir.
 *
 * Backend henüz yazılmadığı için bu şemalar elle yazıldı. İki işi birden
 * görürler: arayüzde runtime doğrulama ve backend'e teslim edilecek sözleşme.
 * ADR §6 OpenAPI'den TypeScript client üretilmesini öngörüyor; backend hazır
 * olduğunda bu klasörün üretilen client ile değiştirilmesi beklenir.
 */

export * from "./common";
export * from "./upload";
export * from "./analysis";
export * from "./report";
export * from "./health";
