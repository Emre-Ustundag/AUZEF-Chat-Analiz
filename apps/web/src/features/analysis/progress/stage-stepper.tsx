import { Check, Loader2 } from "lucide-react";

import { ANALYSIS_ACTIVE_STAGES, ANALYSIS_STAGE_LABELS_TR } from "@/lib/api/schemas";
import type { AnalysisStatus } from "@/lib/api/schemas";
import { cn } from "@/lib/utils";

/**
 * Aşama göstergesi.
 *
 * ADR §2: ilerleme her satırda değil, yalnızca aşama veya anlamlı yüzde
 * değişiminde yazılıyor. Yani yüzde uzun süre sabit kalabilir; kullanıcının
 * "takıldı mı?" diye düşünmemesi için hangi aşamada olduğu ayrıca ve açıkça
 * gösteriliyor.
 */
export function StageStepper({ status }: { status: AnalysisStatus }) {
  const currentIndex = ANALYSIS_ACTIVE_STAGES.indexOf(
    status as (typeof ANALYSIS_ACTIVE_STAGES)[number],
  );
  // Terminal durumlarda tüm aşamalar tamamlanmış sayılır.
  const isSettled = currentIndex === -1;

  return (
    <ol className="space-y-3">
      {ANALYSIS_ACTIVE_STAGES.map((stage, index) => {
        const isDone = isSettled ? status === "completed" : index < currentIndex;
        const isCurrent = !isSettled && index === currentIndex;

        return (
          <li key={stage} className="flex items-center gap-3">
            <span
              className={cn(
                "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs",
                isDone && "border-primary bg-primary text-primary-foreground",
                isCurrent && "border-primary text-primary",
                !isDone && !isCurrent && "border-border text-muted-foreground",
              )}
            >
              {isDone ? (
                <Check className="size-3.5" aria-hidden="true" />
              ) : isCurrent ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              ) : (
                index + 1
              )}
            </span>

            <span
              className={cn(
                "text-sm",
                isCurrent && "font-medium",
                !isDone && !isCurrent && "text-muted-foreground",
              )}
            >
              {ANALYSIS_STAGE_LABELS_TR[stage]}
            </span>

            {isCurrent && (
              // Ekran okuyucu yalnızca aktif aşamayı duyursun; tüm listeyi
              // her poll'da tekrar okuması gürültü olurdu.
              <span className="sr-only" role="status" aria-live="polite">
                Şu anki aşama: {ANALYSIS_STAGE_LABELS_TR[stage]}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
