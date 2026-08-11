"use client";

import { AlertCircle, FileSpreadsheet, Loader2, ShieldCheck, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { ApiError } from "@/lib/api/client";
import { useCreateUpload } from "@/lib/api/hooks";
import { formatFileSize, formatPercentage } from "@/lib/format";

import { FileDropzone } from "./file-dropzone";
import { validateFile } from "./file-validation";

type ScreenError = { title: string; message: string };

export function UploadScreen() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<ScreenError | null>(null);

  const upload = useCreateUpload();

  const handleFileSelected = useCallback((selected: File) => {
    setError(null);

    // Doğrulama seçim anında yapılıyor, yükle düğmesine basıldığında değil:
    // kullanıcı yanlış dosyayı hemen öğrensin.
    const result = validateFile(selected);
    if (!result.ok) {
      setFile(null);
      setError({ title: "Dosya kabul edilmedi", message: result.message });
      return;
    }

    setFile(selected);
  }, []);

  const handleUpload = useCallback(() => {
    if (!file) return;
    setError(null);

    upload.mutate(file, {
      onSuccess: (created) => {
        // Profilleme işi backend'de sürüyor; bir sonraki ekran hazır olana
        // kadar durumu poll edecek.
        router.push(`/yuklemeler/${created.upload_id}`);
      },
      onError: (caught) => {
        // İptal kullanıcının kendi kararı, hata değil: dosya seçili kalıyor ki
        // tekrar denemek isterse baştan seçmek zorunda kalmasın.
        if (caught instanceof DOMException && caught.name === "AbortError") {
          return;
        }
        if (caught instanceof ApiError) {
          setError({ title: "Yükleme başarısız", message: caught.userMessage });
          return;
        }
        setError({
          title: "Yükleme başarısız",
          message: "Dosya yüklenirken beklenmeyen bir sorun oluştu.",
        });
      },
    });
  }, [file, router, upload]);

  const isUploading = upload.isPending;

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10 sm:px-6 sm:py-16">
      <div className="mb-8 space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          Chatbot mesajlarını analiz edin
        </h1>
        <p className="text-muted-foreground">
          Excel dosyanızı yükleyin; sık sorulan sorular ve ana temalar adet ve
          oranlarıyla çıkarılsın.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Veri dosyası</CardTitle>
          <CardDescription>
            Analiz edilecek mesajları içeren .xlsx dosyasını seçin. Sayfa ve
            kolon seçimini bir sonraki adımda yapacaksınız.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {error && (
            <Alert variant="destructive">
              <AlertCircle className="size-4" aria-hidden="true" />
              <AlertTitle>{error.title}</AlertTitle>
              <AlertDescription>{error.message}</AlertDescription>
            </Alert>
          )}

          {file === null ? (
            <FileDropzone
              onFileSelected={handleFileSelected}
              disabled={isUploading}
            />
          ) : (
            <div className="space-y-4">
              <div className="flex items-center gap-3 rounded-lg border p-3">
                <FileSpreadsheet
                  className="size-5 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  {/* break-all: uzun dosya adları kartı taşırmasın. */}
                  <p className="truncate font-medium">{file.name}</p>
                  <p className="text-sm text-muted-foreground">
                    {formatFileSize(file.size)}
                  </p>
                </div>
                {!isUploading && (
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setFile(null)}
                    aria-label="Seçilen dosyayı kaldır"
                  >
                    <X className="size-4" aria-hidden="true" />
                  </Button>
                )}
              </div>

              {isUploading && (
                <div
                  className="space-y-2"
                  role="status"
                  aria-live="polite"
                  aria-atomic="true"
                >
                  <Progress value={upload.progress?.percentage ?? 0} />
                  <p className="text-sm text-muted-foreground">
                    {upload.progress
                      ? `Yükleniyor — ${formatPercentage(upload.progress.percentage)} (${formatFileSize(upload.progress.loadedBytes)} / ${formatFileSize(upload.progress.totalBytes)})`
                      : "Yükleme hazırlanıyor…"}
                  </p>
                </div>
              )}

              {isUploading ? (
                <div className="flex gap-2">
                  <Button disabled className="flex-1">
                    <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                    Yükleniyor
                  </Button>
                  {/* 130 MB'lık bir yükleme dakikalar sürüyor; yanlış dosyayı
                      başlatan kullanıcı sekmeyi kapatmak zorunda kalmamalı. */}
                  <Button variant="outline" onClick={upload.cancel}>
                    Yüklemeyi iptal et
                  </Button>
                </div>
              ) : (
                <Button onClick={handleUpload} className="w-full">
                  Yükle ve devam et
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Kullanıcı gerçek öğrenci mesajlarını yüklüyor; verinin ne olacağını
          yüklemeden ÖNCE bilmeli (ADR §9). */}
      <div className="mt-6 flex gap-3 rounded-lg border bg-muted/40 p-4">
        <ShieldCheck
          className="size-5 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        <div className="space-y-1 text-sm text-muted-foreground">
          <p className="font-medium text-foreground">Veriniz nasıl işleniyor?</p>
          <p>
            Telefon, e-posta, T.C. ve öğrenci numarası gibi kişisel veriler dil
            modeline gönderilmeden önce maskelenir. Yüklenen dosya işlem
            bitiminde silinir; OpenRouter anahtarınız kalıcı olarak saklanmaz.
          </p>
        </div>
      </div>
    </div>
  );
}
