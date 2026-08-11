import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AppHeader } from "@/components/app-header";
import { Providers } from "./providers";

// latin-ext, Türkçe'nin ğ/ş/ı/İ karakterlerini içerir; yalnız "latin" ile
// bu harfler fallback fonta düşer.
const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin", "latin-ext"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin", "latin-ext"],
});

export const metadata: Metadata = {
  title: {
    default: "AUZEF Chat Analiz",
    template: "%s · AUZEF Chat Analiz",
  },
  description: "Chatbot mesajlarından sık sorulan soruları ve ana temaları çıkaran analiz aracı.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning zorunlu: next-themes .dark sınıfını sunucu
    // render'ından sonra istemcide ekliyor, aksi halde her yüklemede
    // hydration uyuşmazlığı uyarısı çıkar.
    <html
      lang="tr"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col">
        <Providers>
          <AppHeader />
          <main className="flex-1">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
