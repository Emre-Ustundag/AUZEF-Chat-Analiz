"use client";

import { AlertCircle, Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfigureScreen } from "@/features/analysis/configure/configure-screen";
import { ApiError } from "@/lib/api/client";
import { useDeleteUpload, useUploadStatus } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";

/**
 * Yükleme sonrası profilleme durumu (ADR §5 Aşama A).
 *
 * Backend dosyayı doğrulayıp sayfa/kolon profilini çıkarana kadar durum
 * poll edilir. Profil hazır olunca yapılandırma ekranına devredilir.
 */
export function UploadStatusScreen({ uploadId }: { uploadId: string }) {
  const { data: upload, error, isPending } = useUploadStatus(uploadId);
  const remove = useDeleteUpload();
  const router = useRouter();

  if (isPending) {
    return <CenteredCard title="Dosya bilgileri alınıyor" />;
  }

  if (error) {
    return (
      <ErrorCard
        title="Dosyaya ulaşılamadı"
        message={error instanceof ApiError ? error.userMessage : "Dosya durumu alınamadı."}
      />
    );
  }

  if (upload.status === "failed") {
    return (
      <ErrorCard
        title="Dosya işlenemedi"
        message={
          upload.error
            ? // Kullanıcıya backend'in ham detail'i değil kendi Türkçe
              // metnimiz gösteriliyor.
              new ApiError(upload.error).userMessage
            : "Dosya doğrulanamadı."
        }
      />
    );
  }

  if (upload.status !== "ready") {
    return (
      <CenteredCard
        title="Dosya doğrulanıyor"
        description="Sayfalar, kolonlar ve satır sayısı çıkarılıyor. Bu işlem dosya boyutuna göre biraz sürebilir."
      >
        {/* ADR §6: DELETE /uploads/{id} iptal ve cleanup içindir. Vazgeçen
            kullanıcı için kayıt silinmezse yüklenen dosya sunucuda lifecycle
            süresi dolana kadar bekler. */}
        <Button
          variant="outline"
          size="sm"
          disabled={remove.isPending}
          onClick={() => remove.mutate(uploadId, { onSuccess: () => router.push("/") })}
        >
          {remove.isPending ? "Vazgeçiliyor" : "Vazgeç"}
        </Button>
        {remove.isError && (
          <p className="text-sm text-destructive" role="alert">
            Yükleme iptal edilemedi. Bu adresten ayrılabilir veya tekrar deneyebilirsiniz.
          </p>
        )}
      </CenteredCard>
    );
  }

  return <ConfigureScreen upload={upload} />;
}

function CenteredCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10 sm:px-6 sm:py-16">
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden="true" />
          <p className="font-medium" role="status" aria-live="polite">
            {title}
          </p>
          {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
          {children}
        </CardContent>
      </Card>
    </div>
  );
}

function ErrorCard({ title, message }: { title: string; message: string }) {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10 sm:px-6 sm:py-16">
      <Alert variant="destructive">
        <AlertCircle className="size-4" aria-hidden="true" />
        <AlertTitle>{title}</AlertTitle>
        <AlertDescription>{message}</AlertDescription>
      </Alert>
      {/* Base UI dokümanı: link'ler render prop'uyla Button'a sarılmamalı —
          Button buton semantiği dayatıyor ve <a> kendi semantiğini kaybediyor.
          Doğrusu <a>'yı doğrudan buttonVariants ile biçimlendirmek. */}
      <Link href="/" className={cn(buttonVariants({ variant: "outline" }), "mt-4")}>
        Yeni dosya yükle
      </Link>
    </div>
  );
}
