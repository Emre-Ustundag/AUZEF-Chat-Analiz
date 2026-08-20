"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  AlertCircle,
  KeyRound,
  Loader2,
  MessageSquareText,
  MessagesSquare,
  Plus,
  Trash2,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { Controller, useFieldArray, useForm, useWatch } from "react-hook-form";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
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
import type { ColumnProfile, ConversationConfig, ModelList, Upload } from "@/lib/api/schemas";
import { formatCount } from "@/lib/format";

import { ColumnPicker } from "./column-picker";
import { FilterValuesInput } from "./filter-values-input";
import { configureFormSchema, toAnalysisRequest } from "./form-schema";
import type { ConfigureFormInput, ConfigureFormValues } from "./form-schema";

interface ConfigureFormProps {
  upload: Upload;
  models: ModelList;
}

const KNOWN_COLUMNS = {
  text: "message_text_clean",
  sessionId: "session_id",
  messageOrder: "message_order",
  role: "direction",
  messageType: "message_type",
} as const;

function exactColumn(columns: readonly ColumnProfile[], name: string): string {
  return columns.some((column) => column.name === name) ? name : "";
}

function suggestedTextColumn(columns: readonly ColumnProfile[]): string {
  return (
    exactColumn(columns, KNOWN_COLUMNS.text) ||
    columns.find((column) => column.is_likely_text)?.name ||
    ""
  );
}

function suggestedConversationConfig(columns: readonly ColumnProfile[]): ConversationConfig {
  return {
    session_id_column: exactColumn(columns, KNOWN_COLUMNS.sessionId),
    message_order_column: exactColumn(columns, KNOWN_COLUMNS.messageOrder),
    role_column: exactColumn(columns, KNOWN_COLUMNS.role),
    message_type_column: exactColumn(columns, KNOWN_COLUMNS.messageType),
    user_role_values: ["Kullanıcı"],
    assistant_role_values: ["Bot"],
    target_message_types: ["text"],
    context_message_types: ["text", "quick_reply", "single-choice"],
    max_context_turns: 4,
    max_context_tokens: 1000,
  };
}

function ColumnSelectField({
  id,
  label,
  columns,
  value,
  onChange,
  error,
}: {
  id: string;
  label: string;
  columns: readonly ColumnProfile[];
  value: string;
  onChange: (value: string) => void;
  error?: string;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Select
        value={value || null}
        onValueChange={(next) => {
          if (typeof next === "string") onChange(next);
        }}
        items={columns.map((column) => ({ label: column.name, value: column.name }))}
      >
        <SelectTrigger id={id} className="w-full" aria-invalid={error ? true : undefined}>
          <SelectValue placeholder="Kolon seçin" />
        </SelectTrigger>
        <SelectContent>
          {columns.map((column) => (
            <SelectItem key={column.name} value={column.name}>
              {column.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

function ValuesInputField({
  id,
  label,
  value,
  onChange,
  placeholder,
  error,
}: {
  id: string;
  label: string;
  value: readonly string[];
  onChange: (value: string[]) => void;
  placeholder: string;
  error?: string;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={value.join(", ")}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        onChange={(event) => onChange(event.target.value.split(",").map((item) => item.trim()))}
      />
      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : (
        <p className="text-xs text-muted-foreground">Tam eşleşme; değerleri virgülle ayırın.</p>
      )}
    </div>
  );
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
  } = useForm<ConfigureFormInput, unknown, ConfigureFormValues>({
    resolver: zodResolver(configureFormSchema),
    defaultValues: {
      sheet_name: firstSheet?.name ?? "",
      // Backend'in metin tahmini varsayılan seçim olarak kullanılıyor;
      // kullanıcı yine de değiştirebilir.
      text_column: suggestedTextColumn(firstSheet?.columns ?? []),
      row_filters: [],
      analysis_mode: "message",
      conversation_config: null,
      model: models.default_model,
      prompt_version: models.default_prompt_version,
      top_n: 20,
      max_cost_usd: 10,
      openrouter_api_key: "",
    },
  });

  const {
    fields: rowFilterFields,
    append: appendRowFilter,
    remove: removeRowFilter,
  } = useFieldArray({ control, name: "row_filters" });

  // watch() yerine useWatch(): watch her render'da yeniden okuyan bir
  // fonksiyon döndürüyor ve React Compiler bunu optimize edemediği için
  // react-hooks/incompatible-library uyarısı veriyor. useWatch abonelik
  // tabanlı ve yalnızca ilgili alan değiştiğinde yeniden render ediyor.
  const selectedSheetName = useWatch({ control, name: "sheet_name" });
  const selectedColumn = useWatch({ control, name: "text_column" });
  const selectedMode = useWatch({ control, name: "analysis_mode" }) ?? "message";
  const conversationConfig = useWatch({ control, name: "conversation_config" });
  const selectedSheet = sheets.find((sheet) => sheet.name === selectedSheetName) ?? firstSheet;
  const mappedColumns = new Set(
    conversationConfig
      ? [
          conversationConfig.session_id_column,
          conversationConfig.message_order_column,
          conversationConfig.role_column,
          conversationConfig.message_type_column,
        ]
      : [],
  );
  const rowFilterColumns =
    selectedMode === "contextual_user_turns"
      ? (selectedSheet?.columns ?? []).filter((column) => !mappedColumns.has(column.name))
      : (selectedSheet?.columns ?? []);

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
                      setValue("text_column", suggestedTextColumn(sheet?.columns ?? []), {
                        shouldValidate: true,
                      });
                      // Filtre kolonları da sayfaya özgü; eski sayfanın
                      // kolonlarını yeni sayfaya sessizce taşımayız.
                      setValue("row_filters", [], { shouldValidate: true });
                      if (selectedMode === "contextual_user_turns") {
                        setValue(
                          "conversation_config",
                          suggestedConversationConfig(sheet?.columns ?? []),
                          { shouldValidate: true },
                        );
                      }
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

          <Separator />

          <fieldset className="space-y-3">
            <legend className="text-sm font-medium">Analiz biçimi</legend>
            <Controller
              control={control}
              name="analysis_mode"
              render={({ field }) => (
                <RadioGroup
                  value={field.value ?? "message"}
                  onValueChange={(next) => {
                    if (next !== "message" && next !== "contextual_user_turns") return;
                    field.onChange(next);
                    setValue("row_filters", [], { shouldValidate: true });

                    if (next === "contextual_user_turns") {
                      setValue(
                        "conversation_config",
                        suggestedConversationConfig(selectedSheet?.columns ?? []),
                        { shouldValidate: true },
                      );
                      setValue("prompt_version", "faq_analysis/v4", { shouldValidate: true });
                    } else {
                      setValue("conversation_config", null, { shouldValidate: true });
                      setValue("prompt_version", models.default_prompt_version, {
                        shouldValidate: true,
                      });
                    }
                  }}
                  className="grid gap-3 sm:grid-cols-2"
                >
                  <Label
                    htmlFor="analysis_mode_message"
                    className="flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition-colors has-data-checked:border-primary has-data-checked:bg-primary/5"
                  >
                    <RadioGroupItem id="analysis_mode_message" value="message" className="mt-0.5" />
                    <span className="space-y-1">
                      <span className="flex items-center gap-2 font-medium">
                        <MessageSquareText className="size-4" aria-hidden="true" />
                        Bağımsız mesajlar
                      </span>
                      <span className="block text-xs leading-relaxed font-normal text-muted-foreground">
                        Her satırı ayrı bir mesaj olarak analiz eder. Mevcut ve varsayılan davranış.
                      </span>
                    </span>
                  </Label>
                  <Label
                    htmlFor="analysis_mode_contextual"
                    className="flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition-colors has-data-checked:border-primary has-data-checked:bg-primary/5"
                  >
                    <RadioGroupItem
                      id="analysis_mode_contextual"
                      value="contextual_user_turns"
                      className="mt-0.5"
                    />
                    <span className="space-y-1">
                      <span className="flex items-center gap-2 font-medium">
                        <MessagesSquare className="size-4" aria-hidden="true" />
                        Bağlamsal kullanıcı turları
                      </span>
                      <span className="block text-xs leading-relaxed font-normal text-muted-foreground">
                        Kullanıcı mesajlarını, aynı oturumdaki önceki kullanıcı ve bot mesajlarıyla
                        birlikte yorumlar.
                      </span>
                    </span>
                  </Label>
                </RadioGroup>
              )}
            />
          </fieldset>

          {selectedMode === "contextual_user_turns" && (
            <div className="space-y-5 rounded-xl border bg-muted/30 p-4 sm:p-5">
              <div className="space-y-1">
                <h3 className="font-medium">Konuşma eşlemesi</h3>
                <p className="text-sm text-muted-foreground">
                  Bilinen AUZEF kolonları bulunduğunda otomatik seçilir. Her seçimi dosyanızın
                  yapısına göre değiştirebilirsiniz; yalnızca kullanıcı turları sonuçlarda sayılır.
                </p>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <Controller
                  control={control}
                  name="conversation_config.session_id_column"
                  render={({ field }) => (
                    <ColumnSelectField
                      id="session_id_column"
                      label="Oturum kimliği kolonu"
                      columns={selectedSheet?.columns ?? []}
                      value={field.value ?? ""}
                      onChange={field.onChange}
                      error={errors.conversation_config?.session_id_column?.message}
                    />
                  )}
                />
                <Controller
                  control={control}
                  name="conversation_config.message_order_column"
                  render={({ field }) => (
                    <ColumnSelectField
                      id="message_order_column"
                      label="Mesaj sırası kolonu"
                      columns={selectedSheet?.columns ?? []}
                      value={field.value ?? ""}
                      onChange={field.onChange}
                      error={errors.conversation_config?.message_order_column?.message}
                    />
                  )}
                />
                <Controller
                  control={control}
                  name="conversation_config.role_column"
                  render={({ field }) => (
                    <ColumnSelectField
                      id="role_column"
                      label="Gönderen rolü kolonu"
                      columns={selectedSheet?.columns ?? []}
                      value={field.value ?? ""}
                      onChange={field.onChange}
                      error={errors.conversation_config?.role_column?.message}
                    />
                  )}
                />
                <Controller
                  control={control}
                  name="conversation_config.message_type_column"
                  render={({ field }) => (
                    <ColumnSelectField
                      id="message_type_column"
                      label="Mesaj türü kolonu"
                      columns={selectedSheet?.columns ?? []}
                      value={field.value ?? ""}
                      onChange={field.onChange}
                      error={errors.conversation_config?.message_type_column?.message}
                    />
                  )}
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <Controller
                  control={control}
                  name="conversation_config.user_role_values"
                  render={({ field }) => (
                    <ValuesInputField
                      id="user_role_values"
                      label="Kullanıcı rol değerleri"
                      value={field.value ?? ["Kullanıcı"]}
                      onChange={field.onChange}
                      placeholder="Kullanıcı"
                      error={errors.conversation_config?.user_role_values?.message}
                    />
                  )}
                />
                <Controller
                  control={control}
                  name="conversation_config.assistant_role_values"
                  render={({ field }) => (
                    <ValuesInputField
                      id="assistant_role_values"
                      label="Bot rol değerleri"
                      value={field.value ?? ["Bot"]}
                      onChange={field.onChange}
                      placeholder="Bot"
                      error={errors.conversation_config?.assistant_role_values?.message}
                    />
                  )}
                />
                <Controller
                  control={control}
                  name="conversation_config.target_message_types"
                  render={({ field }) => (
                    <ValuesInputField
                      id="target_message_types"
                      label="Analiz edilecek mesaj türleri"
                      value={field.value ?? ["text"]}
                      onChange={field.onChange}
                      placeholder="text, quick_reply"
                      error={errors.conversation_config?.target_message_types?.message}
                    />
                  )}
                />
                <Controller
                  control={control}
                  name="conversation_config.context_message_types"
                  render={({ field }) => (
                    <ValuesInputField
                      id="context_message_types"
                      label="Bağlama alınacak mesaj türleri"
                      value={field.value ?? ["text", "quick_reply", "single-choice"]}
                      onChange={field.onChange}
                      placeholder="text, quick_reply, single-choice"
                      error={errors.conversation_config?.context_message_types?.message}
                    />
                  )}
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="max_context_turns">Önceki bağlam turu</Label>
                  <Input
                    id="max_context_turns"
                    type="number"
                    min={1}
                    max={8}
                    aria-invalid={errors.conversation_config?.max_context_turns ? true : undefined}
                    {...register("conversation_config.max_context_turns", { valueAsNumber: true })}
                  />
                  {errors.conversation_config?.max_context_turns ? (
                    <p className="text-sm text-destructive">
                      {errors.conversation_config.max_context_turns.message}
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      Her kullanıcı turu için 1–8 tur.
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="max_context_tokens">Bağlam token sınırı</Label>
                  <Input
                    id="max_context_tokens"
                    type="number"
                    min={128}
                    max={4000}
                    step={128}
                    aria-invalid={errors.conversation_config?.max_context_tokens ? true : undefined}
                    {...register("conversation_config.max_context_tokens", { valueAsNumber: true })}
                  />
                  {errors.conversation_config?.max_context_tokens ? (
                    <p className="text-sm text-destructive">
                      {errors.conversation_config.max_context_tokens.message}
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      128–4000 token; varsayılan 1000.
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          <Separator />

          <div className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="space-y-1">
                <Label>Satır filtreleri (isteğe bağlı)</Label>
                <p className="text-xs text-muted-foreground">
                  Yalnızca belirli kolon değerlerine uyan satırları analiz edin. Filtreler arasında
                  VE, virgülle ayrılan değerler arasında VEYA uygulanır.
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={rowFilterFields.length >= 5}
                onClick={() => appendRowFilter({ column: "", allowed_values: [] })}
              >
                <Plus className="size-4" aria-hidden="true" />
                Filtre ekle
              </Button>
            </div>

            {rowFilterFields.map((rowFilterField, index) => (
              <div
                key={rowFilterField.id}
                className="grid gap-3 rounded-lg border p-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] sm:items-start"
              >
                <div className="space-y-2">
                  <Label htmlFor={`row_filter_${index}_column`}>Kolon</Label>
                  <Controller
                    control={control}
                    name={`row_filters.${index}.column`}
                    render={({ field }) => (
                      <Select
                        value={field.value}
                        onValueChange={(next) => {
                          if (typeof next === "string") field.onChange(next);
                        }}
                        items={rowFilterColumns.map((column) => ({
                          label: column.name,
                          value: column.name,
                        }))}
                      >
                        <SelectTrigger id={`row_filter_${index}_column`} className="w-full">
                          <SelectValue placeholder="Kolon seçin" />
                        </SelectTrigger>
                        <SelectContent>
                          {rowFilterColumns.map((column) => (
                            <SelectItem key={column.name} value={column.name}>
                              {column.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  />
                  {errors.row_filters?.[index]?.column && (
                    <p className="text-sm text-destructive">
                      {errors.row_filters[index]?.column?.message}
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor={`row_filter_${index}_values`}>Kabul edilen değerler</Label>
                  <Controller
                    control={control}
                    name={`row_filters.${index}.allowed_values`}
                    render={({ field }) => (
                      <FilterValuesInput
                        id={`row_filter_${index}_values`}
                        values={field.value}
                        onChange={field.onChange}
                        placeholder="Örn. Kullanıcı"
                        aria-describedby={`row_filter_${index}_values_help`}
                      />
                    )}
                  />
                  {errors.row_filters?.[index]?.allowed_values ? (
                    <p className="text-sm text-destructive">
                      {errors.row_filters[index]?.allowed_values?.message}
                    </p>
                  ) : (
                    <p
                      id={`row_filter_${index}_values_help`}
                      className="text-xs text-muted-foreground"
                    >
                      Tam eşleşme kullanılır. Her değeri Enter veya virgülle ekleyin; virgül içeren
                      bir değeri yapıştırıp Enter ile tamamlayın.
                    </p>
                  )}
                </div>

                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`${index + 1}. filtreyi kaldır`}
                  className="sm:mt-7"
                  onClick={() => removeRowFilter(index)}
                >
                  <Trash2 className="size-4" aria-hidden="true" />
                </Button>
              </div>
            ))}

            {typeof errors.row_filters?.message === "string" && (
              <p className="text-sm text-destructive">{errors.row_filters.message}</p>
            )}
          </div>
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
                  Tahmin; veri, model çıktısı ve kategori birleşimine göre bir aralıktır. Seçtiğiniz
                  tavan hem başlamadan önce hem de işlem sırasında gerçek kullanımla denetlenir.
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
