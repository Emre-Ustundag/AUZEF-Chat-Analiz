"use client";

import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { useModels } from "@/lib/api/hooks";
import type { Upload } from "@/lib/api/schemas";
import { formatCount, formatFileSize } from "@/lib/format";

import { ConfigureForm } from "./configure-form";

/**
 * Sayfa/kolon seçimi ve analiz ayarları ekranı (ADR §5 Aşama A sonu).
 *
 * Model listesi yüklenene kadar form kurulmuyor: varsayılan model ve prompt
 * sürümü listeden geliyor, form önce kurulup sonra effect ile yamansaydı
 * hem gereksiz karmaşık hem de kullanıcı boş bir seçim görürdü.
 */
export function ConfigureScreen({ upload }: { upload: Upload }) {
  const { data: models, error, isPending } = useModels();

  const profile = upload.profile;

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-10 sm:px-6 sm:py-12">
      <div className="mb-6 space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Analizi yapılandırın</h1>
        <p className="text-muted-foreground">
          Hangi kolonun analiz edileceğini seçin ve analiz ayarlarını belirleyin.
        </p>
      </div>

      <Card className="mb-6">
        <CardHeader>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="size-5 text-primary" aria-hidden="true" />
            <CardTitle className="text-base">{upload.filename}</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Stat label="Boyut" value={formatFileSize(upload.size_bytes)} />
            <Stat label="Sayfa" value={formatCount(profile?.sheets.length ?? 0)} />
            <Stat label="Toplam satır" value={formatCount(profile?.total_row_count ?? 0)} />
          </dl>

          {profile?.exceeds_row_limit && (
            <Alert className="mt-4">
              <AlertCircle className="size-4" aria-hidden="true" />
              <AlertTitle>Büyük dosya</AlertTitle>
              <AlertDescription>
                Dosya alışılmış boyutun üzerinde. Tüm satırlar analiz edilir; analiz uzun sürer ve
                maliyeti yüksek olur.
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      {isPending && (
        <Card>
          <CardContent className="flex items-center justify-center gap-3 py-12">
            <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden="true" />
            <p role="status" aria-live="polite">
              Kullanılabilir modeller alınıyor…
            </p>
          </CardContent>
        </Card>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="size-4" aria-hidden="true" />
          <AlertTitle>Modeller yüklenemedi</AlertTitle>
          <AlertDescription>
            {error instanceof ApiError
              ? error.userMessage
              : "Model listesi alınamadı. Sayfayı yenilemeyi deneyin."}
          </AlertDescription>
        </Alert>
      )}

      {models && <ConfigureForm upload={upload} models={models} />}
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
