/**
 * Backend API sözleşmesi — ADR (docs/mimari.md) §6, §7 ve §8'den türetilmiştir.
 *
 * Bu şemalar ELLE yazılıyor ve öyle kalacak (ADR §6: "frontend client'ının
 * OpenAPI'den otomatik üretilmesi halef karardır"). İki işi birden görürler:
 * arayüzde runtime doğrulama ve sözleşmenin TypeScript tarafındaki tanımı.
 *
 * Sözleşmenin KAYNAĞI `apps/backend/` altındaki Pydantic modelleridir; buradaki
 * şemalar onlarla `contract-*.test.ts` dosyaları üzerinden karşılaştırılır.
 * Python'un ürettiği gerçek gövdeler (`tests/fixtures/contract/`) burada Zod ile
 * doğrulanır — iki dilin sözleşmeyi aynı okuduğunun kanıtı bu, ve CI'daki
 * `contract` job'ı artefaktların bayatlamasını ayrıca engeller.
 */

export * from "./common";
export * from "./upload";
export * from "./analysis";
export * from "./report";
export * from "./health";
