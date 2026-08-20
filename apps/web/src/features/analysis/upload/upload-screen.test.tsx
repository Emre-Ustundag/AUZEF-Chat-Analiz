// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LIMITS } from "@/lib/api/schemas";
import { createQueryClient } from "@/lib/api/query-client";

import { UploadScreen } from "./upload-screen";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const createUpload = vi.fn();
vi.mock("@/lib/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/endpoints")>()),
  createUpload: (...args: unknown[]) => createUpload(...args),
}));

function renderScreen() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <UploadScreen />
    </QueryClientProvider>,
  );
}

function xlsxFile(name = "veri.xlsx", size = 2048): File {
  const file = new File(["icerik"], name, {
    type: LIMITS.ACCEPTED_MIME_TYPE,
  });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

/** Gizli <input type="file"> — kullanıcı onu label üzerinden görür. */
function fileInput(container: HTMLElement): HTMLInputElement {
  const input = container.querySelector('input[type="file"]');
  if (!input) throw new Error("Dosya girdisi bulunamadı");
  return input as HTMLInputElement;
}

/**
 * Sürükle-bırakla dosya bırakır.
 *
 * userEvent'in dosya API'si accept özniteliğine uyduğu için desteklenmeyen
 * türleri hiç iletmiyor; sürükle-bırak ise böyle bir filtre uygulamaz ve
 * geçersiz dosyaların gerçekte geldiği yol budur.
 */
function dropFile(file: File) {
  const dropzone = screen
    .getByText("Excel veya CSV dosyasını buraya sürükleyin")
    .closest("div[class*='border-dashed']");
  if (!dropzone) throw new Error("Bırakma alanı bulunamadı");

  fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });
}

beforeEach(() => {
  push.mockReset();
  createUpload.mockReset();
});

describe("UploadScreen", () => {
  it("başlangıçta sürükle-bırak alanını gösterir", () => {
    renderScreen();

    expect(screen.getByText("Excel veya CSV dosyasını buraya sürükleyin")).toBeInTheDocument();
  });

  it("sınırları kullanıcıya yükleme öncesi gösterir", () => {
    renderScreen();

    // 150 MB ve 100.000 satır Türkçe biçimde görünmeli.
    expect(screen.getByText(/150,0 MB/)).toBeInTheDocument();
    expect(screen.getByText(/100\.000 satır/)).toBeInTheDocument();
  });

  it("seçilen geçerli dosyanın adını ve boyutunu gösterir", async () => {
    const user = userEvent.setup();
    const { container } = renderScreen();

    await user.upload(fileInput(container), xlsxFile("sinav_mesajlari.xlsx"));

    expect(screen.getByText("sinav_mesajlari.xlsx")).toBeInTheDocument();
    expect(screen.getByText("2,0 KB")).toBeInTheDocument();
  });

  it("sürüklenen desteklenmeyen dosyayı yükleme başlamadan reddeder", async () => {
    // Bilerek sürükle-bırak yolu: dosya seçicide accept=".xlsx" zaten
    // filtreliyor, geçersiz dosya gerçek hayatta buradan geliyor.
    renderScreen();

    dropFile(xlsxFile("rapor.pdf"));

    expect(await screen.findByText("Dosya kabul edilmedi")).toBeInTheDocument();
    // Kritik nokta: sunucuya hiç istek gitmemeli.
    expect(createUpload).not.toHaveBeenCalled();
  });

  it("sınırı aşan dosyayı yükleme başlamadan reddeder", async () => {
    const user = userEvent.setup();
    const { container } = renderScreen();

    await user.upload(fileInput(container), xlsxFile("buyuk.xlsx", LIMITS.MAX_UPLOAD_BYTES + 1));

    expect(await screen.findByText(/Dosya boyutu sınırı aşıldı/)).toBeInTheDocument();
    expect(createUpload).not.toHaveBeenCalled();
  });

  it("boş dosyayı reddeder", async () => {
    const user = userEvent.setup();
    const { container } = renderScreen();

    await user.upload(fileInput(container), xlsxFile("bos.xlsx", 0));

    expect(await screen.findByText(/Seçilen dosya boş/)).toBeInTheDocument();
    expect(createUpload).not.toHaveBeenCalled();
  });

  it("seçilen dosya kaldırılabilir", async () => {
    const user = userEvent.setup();
    const { container } = renderScreen();

    await user.upload(fileInput(container), xlsxFile());
    await user.click(screen.getByRole("button", { name: "Seçilen dosyayı kaldır" }));

    expect(screen.getByText("Excel veya CSV dosyasını buraya sürükleyin")).toBeInTheDocument();
  });

  it("yükleme başarılı olunca profil sayfasına yönlendirir", async () => {
    createUpload.mockResolvedValue({
      upload_id: "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
      status: "queued",
    });

    const user = userEvent.setup();
    const { container } = renderScreen();

    await user.upload(fileInput(container), xlsxFile());
    await user.click(screen.getByRole("button", { name: /Yükle ve devam et/ }));

    await waitFor(() => {
      expect(push).toHaveBeenCalledWith("/yuklemeler/3f2504e0-4f89-41d3-9a0c-0305e82c3301");
    });
  });

  it("sunucu hatasında Türkçe mesaj gösterir ve yönlendirmez", async () => {
    const { ApiError } = await import("@/lib/api/client");
    createUpload.mockRejectedValue(
      new ApiError({
        type: "/errors/upload-too-large",
        title: "Dosya boyutu sınırı aşıldı",
        status: 413,
        code: "UPLOAD_TOO_LARGE",
        detail: "technical detail from backend",
        trace_id: "x",
        errors: [],
      }),
    );

    const user = userEvent.setup();
    const { container } = renderScreen();

    await user.upload(fileInput(container), xlsxFile());
    await user.click(screen.getByRole("button", { name: /Yükle ve devam et/ }));

    await waitFor(() => {
      expect(screen.getByText("Yükleme başarısız")).toBeInTheDocument();
    });

    // Backend'in teknik İngilizce detayı kullanıcıya gösterilmemeli.
    expect(screen.queryByText("technical detail from backend")).not.toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("süren yükleme iptal edilebilir ve iptal hata olarak gösterilmez", async () => {
    // Gerçek dosyalar ~130 MB; iptal edilemeyen bir yüklemede yanlış dosyayı
    // başlatan kullanıcının tek çıkışı sekmeyi kapatmak olurdu.
    // Gerçek createUpload'ın davranışı: istek signal abort edilene kadar
    // sürer, abort edilince AbortError ile reddeder.
    createUpload.mockImplementation(
      (_file: File, options: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener("abort", () => {
            reject(new DOMException("Yükleme iptal edildi.", "AbortError"));
          });
        }),
    );

    const user = userEvent.setup();
    const { container } = renderScreen();

    await user.upload(fileInput(container), xlsxFile());
    await user.click(screen.getByRole("button", { name: /Yükle ve devam et/ }));

    const cancelButton = await screen.findByRole("button", {
      name: /Yüklemeyi iptal et/,
    });
    expect(createUpload).toHaveBeenCalled();
    expect(createUpload.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);

    await user.click(cancelButton);

    // İptal kullanıcının kendi kararı: hata kutusu çıkmamalı, dosya seçili
    // kalmalı ki tekrar denemek isterse baştan seçmesin.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Yükle ve devam et/ })).toBeInTheDocument(),
    );
    expect(screen.queryByText("Yükleme başarısız")).not.toBeInTheDocument();
    expect(screen.getByText("veri.xlsx")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("veri gizliliği bilgisini yükleme öncesi gösterir", () => {
    renderScreen();

    // Kullanıcı gerçek öğrenci mesajı yüklüyor; ne olacağını önceden bilmeli.
    expect(screen.getByText("Veriniz nasıl işleniyor?")).toBeInTheDocument();
  });
});
