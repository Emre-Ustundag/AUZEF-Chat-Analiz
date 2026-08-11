"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useState, type ReactNode } from "react";

import { createQueryClient } from "@/lib/api/query-client";

/**
 * QueryClient useState ile bileşen içinde kuruluyor, modül seviyesinde değil.
 *
 * Modül seviyesindeki tek bir istemci sunucuda tüm istekler arasında
 * paylaşılır; bu, bir kullanıcının analiz verisinin başka bir kullanıcıya
 * sızmasına yol açabilir.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createQueryClient);

  return (
    // shadcn init, projedeki `prefers-color-scheme` medya sorgusunu silip
    // yerine `.dark` sınıf varyantı koyuyor. Sınıfı <html>'e ekleyen bir
    // mekanizma olmadan koyu tema sessizce çalışmaz. defaultTheme="system"
    // ile önceki davranış korunuyor, üstüne elle seçim de mümkün oluyor.
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ThemeProvider>
  );
}
