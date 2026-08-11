import { MessagesSquare } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Uygulama markası — TEK DEĞİŞTİRME NOKTASI.
 *
 * Kurum logosu geldiğinde yalnızca bu dosya değişir: buradaki ikon ve metin
 * bir <Image /> ile değiştirilir. Hiçbir sayfa logoyu doğrudan çizmez, bu
 * yüzden başka hiçbir dosyaya dokunmak gerekmez.
 *
 * Renkler semantik token üzerinden geliyor (currentColor / text-primary),
 * dolayısıyla palet değişimi de bu dosyayı etkilemez.
 */
export function Logo({
  className,
  showWordmark = true,
}: {
  className?: string;
  showWordmark?: boolean;
}) {
  return (
    <span className={cn("flex items-center gap-2", className)}>
      <MessagesSquare className="size-5 shrink-0 text-primary" aria-hidden="true" />
      {showWordmark && <span className="font-semibold tracking-tight">AUZEF Chat Analiz</span>}
    </span>
  );
}
