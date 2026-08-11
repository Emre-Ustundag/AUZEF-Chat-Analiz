/**
 * Türkçe (tr-TR) sayı, oran, tarih ve boyut biçimlendirmesi.
 *
 * Rapordaki her sayı bu modülden geçmelidir. Doğrudan `toFixed`, `toString`
 * veya string birleştirme kullanılırsa binlik ayıracı nokta yerine virgül
 * olur ve README'de örneklenen "1.240" / "%24,8" biçimi bozulur.
 */

const LOCALE = "tr-TR";

// Formatter'lar modül seviyesinde bir kez kurulur; Intl.NumberFormat kurulumu
// pahalıdır ve bu fonksiyonlar tablo/grafik render'ında satır başına çağrılır.
const countFormatter = new Intl.NumberFormat(LOCALE, {
  maximumFractionDigits: 0,
});

const percentFormatter = new Intl.NumberFormat(LOCALE, {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const decimalFormatter = new Intl.NumberFormat(LOCALE, {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const usdFormatter = new Intl.NumberFormat(LOCALE, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

const dateTimeFormatter = new Intl.DateTimeFormat(LOCALE, {
  dateStyle: "medium",
  timeStyle: "short",
});

/** 1240 -> "1.240" */
export function formatCount(value: number): string {
  return countFormatter.format(value);
}

/**
 * Yüzde biçimlendirir. Girdi 0-100 aralığında bir yüzde değeridir
 * (backend `percentage` alanı bu ölçekte döner), 0-1 aralığında oran değil.
 *
 * 24.8 -> "%24,8"
 */
export function formatPercentage(percentage: number): string {
  return percentFormatter.format(percentage / 100);
}

/** 12.34 -> "12,3" */
export function formatDecimal(value: number): string {
  return decimalFormatter.format(value);
}

/** 1.2345 -> "$1,2345" */
export function formatUsd(value: number): string {
  return usdFormatter.format(value);
}

/** ISO 8601 string veya Date -> "11 Ağu 2026 13:05" */
export function formatDateTime(value: string | Date): string {
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return dateTimeFormatter.format(date);
}

const SIZE_UNITS = ["B", "KB", "MB", "GB"] as const;

/**
 * Bayt cinsinden dosya boyutunu okunabilir biçime çevirir.
 * Upload sınırı 150 MB olduğu için 1024 tabanı kullanılır; ölçek
 * backend'in MB sınırıyla aynı olmalı ki kullanıcıya gösterilen boyut
 * reddedilme eşiğiyle tutarlı olsun.
 *
 * 133_169_152 -> "127,0 MB"
 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${countFormatter.format(bytes)} B`;

  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < SIZE_UNITS.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return `${decimalFormatter.format(value)} ${SIZE_UNITS[unitIndex]}`;
}

/**
 * Saniye cinsinden süreyi Türkçe olarak yazar. Analiz job'ı 45 dakikaya kadar
 * sürebildiği için ilerleme ekranında kalan/geçen süre gösterimi gerekir.
 *
 * 3725 -> "1 sa 2 dk"
 */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  if (hours > 0) return `${hours} sa ${minutes} dk`;
  if (minutes > 0) return `${minutes} dk`;
  return `${seconds} sn`;
}
