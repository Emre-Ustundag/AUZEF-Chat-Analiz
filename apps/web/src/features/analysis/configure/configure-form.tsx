"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, KeyRound, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { ApiError } from "@/lib/api/client";
import { useCreateAnalysis } from "@/lib/api/hooks";
import type { ModelList, Upload } from "@/lib/api/schemas";
import { formatCount } from "@/lib/format";

import { ColumnPicker } from "./column-picker";
import { configureFormSchema, toAnalysisRequest } from "./form-schema";
import type { ConfigureFormValues } from "./form-schema";

interface ConfigureFormProps {
  upload: Upload;
  models: ModelList;
}

export function ConfigureForm({ upload, models }: ConfigureFormProps) {
  const router = useRouter();
  const createAnalysis = useCreateAnalysis();

  const sheets = upload.profile?.sheets ?? [];
  const firstSheet = sheets[0];

  const {
    control,
    handleSubmit,
    setValue,
    register,
    formState: { errors },
  } = useForm<ConfigureFormValues>({
    resolver: zodResolver(configureFormSchema),
    defaultValues: {
      sheet_name: firstSheet?.name ?? "",
      // Backend'in metin tahmini varsayılan seçim olarak kullanılıyor;
      // kullanıcı yine de değiştirebilir.
      text_column:
        firstSheet?.columns.find((column) => column.is_likely_text)?.name ?? "",
      model: models.default_model,
      prompt_version: models.default_prompt_version,
      top_n: 20,
      max_cost_usd: 10,
      openrouter_api_key: "",
    },
  });

  // watch() yerine useWatch(): watch her render'da yeniden okuyan bir
  // fonksiyon döndürüyor ve React Compiler bunu optimize edemediği için
  // react-hooks/incompatible-library uyarısı veriyor. useWatch abonelik
  // tabanlı ve yalnızca ilgili alan değiştiğinde yeniden render ediyor.
  const selectedSheetName = useWatch({ control, name: "sheet_name" });
  const selectedColumn = useWatch({ control, name: "text_column" });
  const selectedSheet =
    sheets.find((sheet) => sheet.name === selectedSheetName) ?? firstSheet;

  const onSubmit = handleSubmit((values) => {
    createAnalysis.mutate(
      {
        request: toAnalysisRequest(upload.upload_id, values),
        openRouterApiKey: values.openrouter_api_key,
      },
      {
        onSuccess: (created) =>
          router.push(`/analizler/${created.analysis_id}`),
      },
    );
  });

  const submitError =
    createAnalysis.error instanceof ApiError
      ? createAnalysis.error.userMessage
      : createAnalysis.error
        ? "Analiz başlatılamadı."
        : null;

  return (
    // noValidate: doğrulama tek kaynaktan, Zod şemasından yapılıyor.
    // Tarayıcının yerleşik doğrulaması açık kalırsa min/max/step ihlalinde
    // submit olayı hiç tetiklenmez ve kullanıcı düğmeye basar ama hiçbir şey
    // olmaz — üstelik tarayıcının mesajı İngilizce ve bizim metinlerimizle
    // tutarsız olur. Alanlardaki min/step yalnızca sayı okunun adımı için.
    <form onSubmit={onSubmit} noValidate className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Analiz edilecek veri</CardTitle>
          <CardDescription>
            Mesaj metinlerini içeren sayfayı ve kolonu seçin.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          {sheets.length > 1 && (
            <div className="space-y-2">
              <Label htmlFor="sheet">Sayfa</Label>
              <Controller
                control={control}
                name="sheet_name"
                render={({ field }) => (
                  <Select
                    value={field.value}
                    onValueChange={(next) => {
                      if (typeof next !== "string") return;
                      field.onChange(next);
                      // Kolonlar sayfaya özgü; sayfa değişince önceki seçim
                      // yeni sayfada var olmayabilir, bu yüzden sıfırlanıyor.
                      const sheet = sheets.find((s) => s.name === next);
                      setValue(
                        "text_column",
                        sheet?.columns.find((c) => c.is_likely_text)?.name ??
                          "",
                        { shouldValidate: true },
                      );
                    }}
                    items={sheets.map((sheet) => ({
                      label: sheet.name,
                      value: sheet.name,
                    }))}
                  >
                    <SelectTrigger id="sheet" className="w-full sm:w-80">
                      <SelectValue placeholder="Sayfa seçin" />
                    </SelectTrigger>
                    <SelectContent>
                      {sheets.map((sheet) => (
                        <SelectItem key={sheet.name} value={sheet.name}>
                          {sheet.name} · {formatCount(sheet.row_count)} satır
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
          )}

          <div className="space-y-2">
            <Label>Metin kolonu</Label>
            <ColumnPicker
              columns={selectedSheet?.columns ?? []}
              value={selectedColumn || null}
              onChange={(name) =>
                setValue("text_column", name, { shouldValidate: true })
              }
            />
            {errors.text_column && (
              <p className="text-sm text-destructive">
                {errors.text_column.message}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Analiz ayarları</CardTitle>
          <CardDescription>
            Model ve sonuç sınırlarını belirleyin.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          <div className="grid gap-6 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="model">Model</Label>
              <Controller
                control={control}
                name="model"
                render={({ field }) => (
                  <Select
                    value={field.value}
                    onValueChange={(next) => {
                      if (typeof next === "string") field.onChange(next);
                    }}
                    items={models.models.map((model) => ({
                      label: model.label,
                      value: model.id,
                    }))}
                  >
                    <SelectTrigger id="model" className="w-full">
                      <SelectValue placeholder="Model seçin" />
                    </SelectTrigger>
                    <SelectContent>
                      {models.models.map((model) => (
                        <SelectItem key={model.id} value={model.id}>
                          {model.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              <p className="text-xs text-muted-foreground">
                Yalnızca yapılandırılmış çıktı desteği doğrulanmış modeller
                listelenir.
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="top_n">Gösterilecek soru sayısı</Label>
              <Input
                id="top_n"
                type="number"
                min={1}
                max={100}
                {...register("top_n", { valueAsNumber: true })}
              />
              {errors.top_n && (
                <p className="text-sm text-destructive">
                  {errors.top_n.message}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="max_cost_usd">Maliyet üst sınırı (USD)</Label>
              <Input
                id="max_cost_usd"
                type="number"
                min={0.1}
                // step="any": sabit bir adım (örn. 0.5) varsayılan 10 değerini
                // adım dizisinin dışında bırakıyordu.
                step="any"
                {...register("max_cost_usd", { valueAsNumber: true })}
              />
              {errors.max_cost_usd ? (
                <p className="text-sm text-destructive">
                  {errors.max_cost_usd.message}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Tahmini maliyet bu sınırı aşarsa iş, model çağrıları
                  başlamadan durdurulur.
                </p>
              )}
            </div>
          </div>

          <Separator />

          <div className="space-y-2">
            <Label htmlFor="openrouter_api_key">
              <KeyRound className="size-4" aria-hidden="true" />
              OpenRouter API anahtarı
            </Label>
            <Input
              id="openrouter_api_key"
              type="password"
              placeholder="sk-or-..."
              // Anahtar tarayıcı parola yöneticisine veya otomatik doldurma
              // geçmişine düşmesin.
              autoComplete="off"
              data-1p-ignore
              {...register("openrouter_api_key")}
            />
            {errors.openrouter_api_key ? (
              <p className="text-sm text-destructive">
                {errors.openrouter_api_key.message}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                Anahtar yalnızca bu analiz için kullanılır, veritabanına
                yazılmaz ve işlem bitince silinir.
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {submitError && (
        <Alert variant="destructive">
          <AlertCircle className="size-4" aria-hidden="true" />
          <AlertTitle>Analiz başlatılamadı</AlertTitle>
          <AlertDescription>{submitError}</AlertDescription>
        </Alert>
      )}

      <Button
        type="submit"
        disabled={createAnalysis.isPending}
        className="w-full"
        size="lg"
      >
        {createAnalysis.isPending ? (
          <>
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            Analiz başlatılıyor
          </>
        ) : (
          "Analizi başlat"
        )}
      </Button>
    </form>
  );
}
