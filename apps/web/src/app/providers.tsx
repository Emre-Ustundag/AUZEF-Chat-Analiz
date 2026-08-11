"use client";

import { QueryClientProvider } from "@tanstack/react-query";
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
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
