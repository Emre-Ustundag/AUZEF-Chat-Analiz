"use client";

import { AlertCircle, Ban, Loader2, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { ApiError } from "@/lib/api/client";
import { useAnalysisJob, useCancelAnalysis } from "@/lib/api/hooks";
import { ANALYSIS_STAGE_LABELS_TR } from "@/lib/api/schemas";
import { formatDuration, formatPercentage } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ReportScreen } from "@/features/analysis/report/report-screen";

import { StageStepper } from "./stage-stepper";

/**
 * Analiz ilerleme ekranı.
 *
 * ADR §2: WebSocket/SSE yok, durum 2-3 saniyede bir poll ediliyor ve iş
 * 45 dakikaya kadar sürebiliyor. Bu yüzden ekranın işi sadece bir çubuk
 * göstermek değil: kullanıcının işi iptal edebilmesi, hata durumunda ne
 * olduğunu anlaması ve sekmeyi kapatıp geri dönebileceğini bilmesi gerekiyor.
 */
export function ProgressScreen({ analysisId }: { analysisId: string }) {
  const { data: job, error, isPending } = useAnalysisJob(analysisId);
  const cancel = useCancelAnalysis();
  const [confirmingCancel, setConfirmingCancel] = useState(false);

  if (isPending) {
    return (
      <Shell>
        <Card>
          <CardContent className="flex items-center justify-center gap-3 py-12">
            <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden="true" />
            <p role="status" aria-live="polite">
              Analiz durumu alınıyor…
            </p>
          </CardContent>
        </Card>
      </Shell>
    );
  }

  if (error) {
    return (
      <Shell>
        <ErrorState
          title="Analize ulaşılamadı"
          message={error instanceof ApiError ? error.userMessage : "Analiz durumu alınamadı."}
        />
      </Shell>
    );
  }

  if (job.status === "failed") {
    const apiError = job.error ? new ApiError(job.error) : null;
    return (
      <Shell>
        <ErrorState
          title="Analiz tamamlanamadı"
          message={apiError?.userMessage ?? "Analiz sırasında bir hata oluştu."}
          retryAfterSeconds={apiError?.retryAfterSeconds}
          canRetry={apiError?.isRetryable ?? false}
        />
      </Shell>
    );
  }

  if (job.status === "cancelled") {
    return (
      <Shell>
        <Alert>
          <Ban className="size-4" aria-hidden="true" />
          <AlertTitle>Analiz iptal edildi</AlertTitle>
          <AlertDescription>
            Bu analiz siz iptal ettiğiniz için durduruldu. Yeni bir analiz başlatabilirsiniz.
          </AlertDescription>
        </Alert>
        <Link href="/" className={cn(buttonVariants({ variant: "outline" }), "mt-4")}>
          Yeni analiz başlat
        </Link>
      </Shell>
    );
  }

  if (job.status === "completed") {
    return <ReportScreen analysisId={analysisId} />;
  }

  const remaining = job.estimated_seconds_remaining;

  return (
    <Shell>
      <Card>
        <CardHeader>
          <CardTitle>Analiz sürüyor</CardTitle>
          <CardDescription>
            Bu işlem veri boyutuna göre 45 dakikaya kadar sürebilir. Sekmeyi kapatabilirsiniz; bu
            adrese geri dönerek duruma bakabilirsiniz.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          <div className="space-y-2">
            <div className="flex items-baseline justify-between gap-4">
              <span className="font-medium">{ANALYSIS_STAGE_LABELS_TR[job.status]}</span>
              <span className="tabular-nums text-sm text-muted-foreground">
                {formatPercentage(job.progress)}
              </span>
            </div>

            <Progress value={job.progress} />

            {remaining !== null && (
              <p className="text-sm text-muted-foreground">
                Tahmini kalan süre: {formatDuration(remaining)}
              </p>
            )}
          </div>

          <StageStepper status={job.status} />

          <div className="border-t pt-4">
            {/* İptal isteği başarısız olabilir: iş bu sırada bittiyse backend
                409 döner. Gösterilmezse düğme normale döner ve kullanıcı
                iptalin geçtiğini sanır. */}
            {cancel.isError && (
              <Alert variant="destructive" className="mb-4">
                <AlertCircle className="size-4" aria-hidden="true" />
                <AlertTitle>Analiz iptal edilemedi</AlertTitle>
                <AlertDescription>
                  {cancel.error instanceof ApiError
                    ? cancel.error.userMessage
                    : "İptal isteği gönderilemedi. Bağlantınızı kontrol edip tekrar deneyin."}
                </AlertDescription>
              </Alert>
            )}

            {confirmingCancel ? (
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-sm">Analiz iptal edilsin mi? Bu işlem geri alınamaz.</p>
                <div className="flex gap-2">
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={cancel.isPending}
                    onClick={() => cancel.mutate(analysisId)}
                  >
                    {cancel.isPending ? "İptal ediliyor" : "Evet, iptal et"}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setConfirmingCancel(false)}>
                    Vazgeç
                  </Button>
                </div>
              </div>
            ) : (
              <Button variant="outline" size="sm" onClick={() => setConfirmingCancel(true)}>
                <Ban className="size-4" aria-hidden="true" />
                Analizi iptal et
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto w-full max-w-2xl px-4 py-10 sm:px-6 sm:py-16">{children}</div>;
}

function ErrorState({
  title,
  message,
  retryAfterSeconds,
  canRetry,
}: {
  title: string;
  message: string;
  retryAfterSeconds?: number;
  canRetry?: boolean;
}) {
  return (
    <>
      <Alert variant="destructive">
        <AlertCircle className="size-4" aria-hidden="true" />
        <AlertTitle>{title}</AlertTitle>
        <AlertDescription>
          {message}
          {retryAfterSeconds !== undefined && (
            <> {formatDuration(retryAfterSeconds)} sonra tekrar deneyebilirsiniz.</>
          )}
        </AlertDescription>
      </Alert>

      <div className="mt-4 flex flex-wrap gap-2">
        {/* "Tekrar dene" yalnızca sağlayıcı kaynaklı geçici hatalarda
            gösteriliyor; geçersiz dosyada tekrar denemek anlamsız. */}
        {canRetry && (
          <Link href="/" className={buttonVariants({ variant: "outline" })}>
            <RotateCcw className="size-4" aria-hidden="true" />
            Tekrar dene
          </Link>
        )}
        <Link href="/" className={buttonVariants({ variant: canRetry ? "ghost" : "outline" })}>
          Yeni dosya yükle
        </Link>
      </div>
    </>
  );
}
