"use client";

import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { useUploadStatus } from "@/lib/api/hooks";
import { formatCount, formatFileSize } from "@/lib/format";

/**
 * Yükleme sonrası profilleme ekranı.
 *
 * Backend dosyayı doğrulayıp sayfa/kolon profilini çıkarana kadar durum
 * poll edilir (ADR §5 Aşama A). Sayfa ve kolon seçimi bir sonraki adımda.
 */
export function UploadStatusScreen({ uploadId }: { uploadId: string }) {
  const { data: upload, error, isPending } = useUploadStatus(uploadId);

  if (isPending) {
    return <CenteredCard title="Dosya bilgileri alınıyor" busy />;
  }

  if (error) {
    const message =
      error instanceof ApiError
        ? error.userMessage
        : "Dosya durumu alınamadı.";
    return <ErrorCard title="Dosyaya ulaşılamadı" message={message} />;
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
        busy
      />
    );
  }

  const profile = upload.profile;

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10 sm:px-6 sm:py-16">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="size-5 text-primary" aria-hidden="true" />
            <CardTitle>Dosya hazır</CardTitle>
          </div>
          <CardDescription>{upload.filename}</CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Stat label="Boyut" value={formatFileSize(upload.size_bytes)} />
            <Stat
              label="Sayfa"
              value={formatCount(profile?.sheets.length ?? 0)}
            />
            <Stat
              label="Toplam satır"
              value={formatCount(profile?.total_row_count ?? 0)}
            />
          </dl>

          {profile?.exceeds_row_limit && (
            <Alert>
              <AlertCircle className="size-4" aria-hidden="true" />
              <AlertTitle>Satır sınırı aşıldı</AlertTitle>
              <AlertDescription>
                Dosya varsayılan satır sınırının üzerinde. Analiz yalnızca ilk
                kayıtları kapsayabilir.
              </AlertDescription>
            </Alert>
          )}

          {/* Sayfa ve kolon seçimi bir sonraki adımda eklenecek. */}
          <Button disabled className="w-full">
            Sayfa ve kolon seçimi (sıradaki adım)
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-lg font-medium tabular-nums">{value}</dd>
    </div>
  );
}

function CenteredCard({
  title,
  description,
  busy,
}: {
  title: string;
  description?: string;
  busy?: boolean;
}) {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10 sm:px-6 sm:py-16">
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
          {busy && (
            <Loader2
              className="size-6 animate-spin text-muted-foreground"
              aria-hidden="true"
            />
          )}
          <p className="font-medium" role="status" aria-live="polite">
            {title}
          </p>
          {description && (
            <p className="max-w-sm text-sm text-muted-foreground">
              {description}
            </p>
          )}
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
      {/* Base UI, Radix'in asChild deseni yerine `render` prop'u kullanıyor:
          bileşeni başka bir elemanla kompoze etmek için render={<Link/>}. */}
      <Button variant="outline" className="mt-4" render={<Link href="/" />}>
        Yeni dosya yükle
      </Button>
    </div>
  );
}
