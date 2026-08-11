import { describe, expect, it } from "vitest";

import {
  formatCount,
  formatDateTime,
  formatDecimal,
  formatDuration,
  formatFileSize,
  formatPercentage,
  formatUsd,
} from "./format";

/**
 * Bu testler README'deki örnek tabloyu (1.240 / %24,8) sözleşme olarak alır.
 * Türkçe biçimde binlik ayıracı nokta, ondalık ayıracı virgüldür ve yüzde
 * işareti sayının ÖNÜNDE gelir; İngilizce biçimin tam tersi.
 */

describe("formatCount", () => {
  it("binlik ayıracı olarak nokta kullanır", () => {
    expect(formatCount(1240)).toBe("1.240");
    expect(formatCount(1234567)).toBe("1.234.567");
  });

  it("küçük sayıları ayıraçsız yazar", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(999)).toBe("999");
  });
});

describe("formatPercentage", () => {
  it("0-100 ölçeğini alır ve yüzde işaretini başa koyar", () => {
    expect(formatPercentage(24.8)).toBe("%24,8");
    expect(formatPercentage(9.6)).toBe("%9,6");
  });

  it("tam sayı yüzdelerde de tek ondalık gösterir", () => {
    expect(formatPercentage(100)).toBe("%100,0");
    expect(formatPercentage(0)).toBe("%0,0");
  });
});

describe("formatDecimal", () => {
  it("ondalık ayıracı olarak virgül kullanır", () => {
    expect(formatDecimal(12.34)).toBe("12,3");
  });
});

describe("formatUsd", () => {
  it("dolar işaretiyle ve virgüllü ondalıkla yazar", () => {
    expect(formatUsd(1.23)).toBe("$1,23");
  });
});

describe("formatFileSize", () => {
  it("bayt altı eşikte birim değiştirmez", () => {
    expect(formatFileSize(512)).toBe("512 B");
  });

  it("1024 tabanıyla MB'ye çevirir", () => {
    // 150 MB upload sınırı, backend'in reddettiği eşikle aynı ölçekte olmalı.
    expect(formatFileSize(150 * 1024 * 1024)).toBe("150,0 MB");
  });

  it("GB'ye kadar yükselir", () => {
    expect(formatFileSize(2 * 1024 * 1024 * 1024)).toBe("2,0 GB");
  });
});

describe("formatDuration", () => {
  it("saat ve dakikayı birlikte yazar", () => {
    expect(formatDuration(3725)).toBe("1 sa 2 dk");
  });

  it("45 dakikalık analiz timeout'unu dakika olarak yazar", () => {
    expect(formatDuration(45 * 60)).toBe("45 dk");
  });

  it("bir dakikanın altını saniye olarak yazar", () => {
    expect(formatDuration(30)).toBe("30 sn");
  });

  it("negatif değerleri sıfıra sabitler", () => {
    expect(formatDuration(-5)).toBe("0 sn");
  });
});

describe("formatDateTime", () => {
  it("geçersiz tarihte tire döner", () => {
    expect(formatDateTime("bozuk-tarih")).toBe("—");
  });

  it("ISO 8601 string'i ayrıştırır", () => {
    expect(formatDateTime("2026-08-11T10:05:00Z")).toContain("2026");
  });
});
