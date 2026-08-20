"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, KeyRound, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { DATASET_TYPE_LABELS_TR, datasetTypeSchema } from "@/lib/api/schemas";
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
      text_column: firstSheet?.columns.find((column) => column.is_likely_text)?.name ?? "",
      model: models.default_model,
      prompt_version: models.default_prompt_version,
      top_n: 20,
      max_cost_usd: 10,
      openrouter_api_key: "",
      dataset_type: "GENERIC",
      role_column: "",
      // Gerçek dökümlerdeki yaygın değerler; kullanıcı düzenleyebilir.
      role_user_values_raw: "Kullanıcı, user, kullanici",
      session_id_column: "",
      timestamp_column: "",
      message_type_column: "",
      allowed_message_types_raw: "text",
    },
  });

  // watch() yerine useWatch(): watch her render'da yeniden okuyan bir
  // fonksiyon döndürüyor ve React Compiler bunu optimize edemediği için
  // react-hooks/incompatible-library uyarısı veriyor. useWatch abonelik
  // tabanlı ve yalnızca ilgili alan değiştiğinde yeniden render ediyor.
  const selectedSheetName = useWatch({ control, name: "sheet_name" });
  const selectedColumn = useWatch({ control, name: "text_column" });
  const datasetType = useWatch({ control, name: "dataset_type" });
  const messageTypeColumn = useWatch({ control, name: "message_type_column" });
  const selectedSheet = sheets.find((sheet) => sheet.name === selectedSheetName) ?? firstSheet;

  const columnNames = (selectedSheet?.columns ?? []).map((column) => column.name);
  /** Radix Select boş string değeri kabul etmiyor; "kullanılmasın" için sentinel. */
  const NONE = "__none__";
  const optionalColumnItems = [
    { label: "Kullanılmasın", value: NONE },
    ...columnNames.map((name) => ({ label: name, value: name })),
  ];

  const resetChatbotColumns = () => {
    setValue("role_column", "");
    setValue("session_id_column", "");
    setValue("timestamp_column", "");
    setValue("message_type_column", "");
  };

  const onSubmit = handleSubmit((values) => {
    createAnalysis.mutate(
      {
        request: toAnalysisRequest(upload.upload_id, values),
        openRouterApiKey: values.openrouter_api_key,
      },
      {
        onSuccess: (created) => router.push(`/analizler/${created.analysis_id}`),
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
          <CardDescription>Mesaj metinlerini içeren sayfayı ve kolonu seçin.</CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          <div className="space-y-2">
            <Label htmlFor="dataset_type">Veri kümesi türü</Label>
            <Controller
              control={control}
              name="dataset_type"
              render={({ field }) => (
                <Select
                  value={field.value}
                  onValueChange={(next) => {
                    const parsed = datasetTypeSchema.safeParse(next);
                    if (parsed.success) field.onChange(parsed.data);
                  }}
                  items={datasetTypeSchema.options.map((value) => ({
                    label: DATASET_TYPE_LABELS_TR[value],
                    value,
                  }))}
                >
                  <SelectTrigger id="dataset_type" className="w-full sm:w-80">
                    <SelectValue placeholder="Veri türü seçin" />
                  </SelectTrigger>
                  <SelectContent>
                    {datasetTypeSchema.options.map((value) => (
                      <SelectItem key={value} value={value}>
                        {DATASET_TYPE_LABELS_TR[value]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
            <p className="text-xs text-muted-foreground">
              Chatbot dökümü seçilirse bot cevapları ve sistem olayları gönderen kolonuna göre
              otomatik elenir; oturum ve zaman kolonları rapora oturum sayıları ve günlük trend
              ekler.
            </p>
          </div>

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
                        sheet?.columns.find((c) => c.is_likely_text)?.name ?? "",
                        { shouldValidate: true },
                      );
                      // Chatbot kolon eşlemesi de sayfaya özgüdür.
                      resetChatbotColumns();
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
              onChange={(name) => setValue("text_column", name, { shouldValidate: true })}
            />
            {errors.text_column && (
              <p className="text-sm text-destructive">{errors.text_column.message}</p>
            )}
          </div>

          {datasetType === "CHATBOT_LOG" && (
            <>
              <Separator />
              <div className="space-y-6">
                <p className="text-sm font-medium">Chatbot kolon eşlemesi</p>

                <div className="grid gap-6 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="role_column">Gönderen / rol kolonu</Label>
                    <Controller
                      control={control}
                      name="role_column"
                      render={({ field }) => (
                        <Select
                          value={field.value || undefined}
                          onValueChange={(next) => {
                            if (typeof next === "string") field.onChange(next);
                          }}
                          items={columnNames.map((name) => ({ label: name, value: name }))}
                        >
                          <SelectTrigger id="role_column" className="w-full">
                            <SelectValue placeholder="Kolon seçin (örn. direction)" />
                          </SelectTrigger>
                          <SelectContent>
                            {columnNames.map((name) => (
                              <SelectItem key={name} value={name}>
                                {name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    />
                    {errors.role_column && (
                      <p className="text-sm text-destructive">{errors.role_column.message}</p>
                    )}
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="role_user_values_raw">Kullanıcı değerleri</Label>
                    <Input
                      id="role_user_values_raw"
                      placeholder="Kullanıcı, user"
                      {...register("role_user_values_raw")}
                    />
                    {errors.role_user_values_raw ? (
                      <p className="text-sm text-destructive">
                        {errors.role_user_values_raw.message}
                      </p>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        Rol kolonunda kullanıcı mesajı sayılacak değerler; virgülle ayırın. Diğer
                        tüm satırlar (bot, sistem) analize alınmaz.
                      </p>
                    )}
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="session_id_column">Oturum kolonu (opsiyonel)</Label>
                    <Controller
                      control={control}
                      name="session_id_column"
                      render={({ field }) => (
                        <Select
                          value={field.value || NONE}
                          onValueChange={(next) => {
                            if (typeof next === "string") field.onChange(next === NONE ? "" : next);
                          }}
                          items={optionalColumnItems}
                        >
                          <SelectTrigger id="session_id_column" className="w-full">
                            <SelectValue placeholder="Kullanılmasın" />
                          </SelectTrigger>
                          <SelectContent>
                            {optionalColumnItems.map((item) => (
                              <SelectItem key={item.value} value={item.value}>
                                {item.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    />
                    <p className="text-xs text-muted-foreground">
                      Seçilirse rapor, her soru ve temadan etkilenen benzersiz oturum sayısını
                      içerir.
                    </p>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="timestamp_column">Zaman kolonu (opsiyonel)</Label>
                    <Controller
                      control={control}
                      name="timestamp_column"
                      render={({ field }) => (
                        <Select
                          value={field.value || NONE}
                          onValueChange={(next) => {
                            if (typeof next === "string") field.onChange(next === NONE ? "" : next);
                          }}
                          items={optionalColumnItems}
                        >
                          <SelectTrigger id="timestamp_column" className="w-full">
                            <SelectValue placeholder="Kullanılmasın" />
                          </SelectTrigger>
                          <SelectContent>
                            {optionalColumnItems.map((item) => (
                              <SelectItem key={item.value} value={item.value}>
                                {item.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    />
                    <p className="text-xs text-muted-foreground">
                      Seçilirse rapor, soru ve temalar için günlük (YYYY-AA-GG) trend serileri
                      içerir.
                    </p>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="message_type_column">Mesaj tipi kolonu (opsiyonel)</Label>
                    <Controller
                      control={control}
                      name="message_type_column"
                      render={({ field }) => (
                        <Select
                          value={field.value || NONE}
                          onValueChange={(next) => {
                            if (typeof next === "string") field.onChange(next === NONE ? "" : next);
                          }}
                          items={optionalColumnItems}
                        >
                          <SelectTrigger id="message_type_column" className="w-full">
                            <SelectValue placeholder="Kullanılmasın" />
                          </SelectTrigger>
                          <SelectContent>
                            {optionalColumnItems.map((item) => (
                              <SelectItem key={item.value} value={item.value}>
                                {item.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    />
                  </div>

                  {messageTypeColumn && (
                    <div className="space-y-2">
                      <Label htmlFor="allowed_message_types_raw">İzin verilen mesaj tipleri</Label>
                      <Input
                        id="allowed_message_types_raw"
                        placeholder="text"
                        {...register("allowed_message_types_raw")}
                      />
                      {errors.allowed_message_types_raw && (
                        <p className="text-sm text-destructive">
                          {errors.allowed_message_types_raw.message}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Analiz ayarları</CardTitle>
          <CardDescription>Model ve sonuç sınırlarını belirleyin.</CardDescription>
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
                Yalnızca yapılandırılmış çıktı desteği doğrulanmış modeller listelenir.
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
              {errors.top_n && <p className="text-sm text-destructive">{errors.top_n.message}</p>}
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
                <p className="text-sm text-destructive">{errors.max_cost_usd.message}</p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Tahmini maliyet bu sınırı aşarsa iş, model çağrıları başlamadan durdurulur.
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
              <p className="text-sm text-destructive">{errors.openrouter_api_key.message}</p>
            ) : (
              <p className="text-xs text-muted-foreground">
                Anahtar yalnızca bu analiz için kullanılır, veritabanına yazılmaz ve işlem bitince
                silinir.
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

      <Button type="submit" disabled={createAnalysis.isPending} className="w-full" size="lg">
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
