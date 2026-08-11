// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Button } from "@/components/ui/button";

/**
 * Bileşen testi altyapısının kendisini doğrular.
 *
 * Varsayılan test ortamı node; bileşen testleri dosya başındaki
 * `@vitest-environment jsdom` satırıyla ortam değiştirir. Bu dosya o
 * anahtarın, jest-dom matcher'larının ve RTL temizliğinin çalıştığını
 * gösterir — ilk ekran testi yazılırken altyapı hatasıyla uğraşılmasın diye.
 */
describe("bileşen testi altyapısı", () => {
  it("jsdom ortamına geçer", () => {
    expect(typeof document).toBe("object");
  });

  it("bileşen render eder ve jest-dom matcher'ları çalışır", () => {
    render(<Button>Analizi başlat</Button>);

    const button = screen.getByRole("button", { name: "Analizi başlat" });
    expect(button).toBeInTheDocument();
    expect(button).toBeEnabled();
  });

  it("kullanıcı etkileşimini işler", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();

    render(<Button onClick={onClick}>Yükle</Button>);
    await user.click(screen.getByRole("button", { name: "Yükle" }));

    expect(onClick).toHaveBeenCalledOnce();
  });

  it("Türkçe karakterleri doğru eşleştirir", () => {
    render(<Button>Sınav içeriği güncelleştirildi</Button>);

    expect(
      screen.getByRole("button", { name: "Sınav içeriği güncelleştirildi" }),
    ).toBeInTheDocument();
  });
});
