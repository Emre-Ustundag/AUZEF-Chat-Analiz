"use client";

import { AlertCircle, Download, FileJson, Loader2 } from "lucide-react";
import { useCallback } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { useAnalysisReport, useExportAnalysis } from "@/lib/api/hooks";
import type { ExportFormat } from "@/lib/api/schemas";
import { formatCount, formatDateTime, formatPercentage, formatUsd } from "@/lib/format";

import { QuestionsTable } from "./questions-table";
import { StatTiles } from "./stat-tiles";
import { ThemeDistribution } from "./theme-distribution";
import { TopQuestionsChart } from "./top-questions-chart";

/**
 * Analiz sonuç ekranı.
 *
 * Rapordaki tüm sayılar backend'de mesajların gerçek frekanslarından
 * deterministik olarak hesaplanıyor (ADR §4); arayüz hiçbirini yeniden
 * hesaplamıyor, olduğu gibi gösteriyor.
 */
export function ReportScreen({ analysisId }: { analysisId: string }) {
  const { data: report, error, isPending } = useAnalysisReport(analysisId, true);
  const exportMutation = useExportAnalysis();

  const runExport = useCallback(
    (format: ExportFormat) => exportMutation.mutate({ analysisId, format }),
    [analysisId, exportMutation],
  );

  // Hangi düğmenin beklediği: iki format tek mutation'ı paylaşıyor, ayrımı
  // çalışan isteğin kendi değişkeni veriyor.
  const exportPending = exportMutation.isPending ? exportMutation.variables.format : null;

  if (isPending) {
    return (
      <Shell>
        <Card>
          <CardContent className="flex items-center justify-center gap-3 py-16">
            <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden="true" />
            <p role="status" aria-live="polite">
              Rapor hazırlanıyor…
            </p>
          </CardContent>
        </Card>
      </Shell>
    );
  }

  if (error) {
    return (
      <Shell>
        <Alert variant="destructive">
          <AlertCircle className="size-4" aria-hidden="true" />
          <AlertTitle>Rapor alınamadı</AlertTitle>
          <AlertDescription>
            {error instanceof ApiError ? error.userMessage : "Analiz raporu yüklenemedi."}
          </AlertDescription>
        </Alert>
      </Shell>
    );
  }

  const pre = report.preprocessing_summary;
  const src = report.source_summary;

  return (
    <Shell>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Analiz sonuçları</h1>
          <p className="text-sm text-muted-foreground">
            {src.filename} · {src.sheet_name} · {src.text_column} kolonu ·{" "}
            {formatDateTime(report.generated_at)}
          </p>
          {src.row_filters.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Filtreler:{" "}
              {src.row_filters
                .map((rowFilter) => `${rowFilter.column} = ${rowFilter.allowed_values.join(" | ")}`)
                .join("; ")}
            </p>
          )}
        </div>

        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={exportPending === "xlsx"}
            onClick={() => runExport("xlsx")}
          >
            {exportPending === "xlsx" ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Download className="size-4" aria-hidden="true" />
            )}
            Excel
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={exportPending === "json"}
            onClick={() => runExport("json")}
          >
            {exportPending === "json" ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <FileJson className="size-4" aria-hidden="true" />
            )}
            JSON
          </Button>
        </div>
      </div>

      {exportMutation.isError && (
        <Alert variant="destructive" className="mb-6">
          <AlertCircle className="size-4" aria-hidden="true" />
          <AlertTitle>Dosya indirilemedi</AlertTitle>
          <AlertDescription>
            {exportMutation.error instanceof ApiError
              ? exportMutation.error.userMessage
              : "Dışa aktarma sırasında beklenmeyen bir sorun oluştu."}
          </AlertDescription>
        </Alert>
      )}

      {report.warnings.length > 0 && (
        <Alert className="mb-6">
          <AlertCircle className="size-4" aria-hidden="true" />
          <AlertTitle>Analiz uyarıları</AlertTitle>
          <AlertDescription>
            <ul className="list-inside list-disc">
              {report.warnings.map((warning) => (
                <li key={warning.code}>{warning.message}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <div className="space-y-6">
        <StatTiles
          items={[
            {
              label: "Toplam satır",
              value: formatCount(src.total_rows),
              hint: "Dosyadaki tüm kayıtlar",
            },
            {
              label: "Analiz edilen",
              value: formatCount(pre.analyzed_count),
              hint: `${formatCount(pre.discarded_count)} kayıt elendi`,
            },
            {
              label: "Benzersiz mesaj",
              value: formatCount(pre.unique_count),
              hint: `${formatCount(pre.duplicate_count)} tekrar birleştirildi`,
            },
            {
              label: report.cost_source === "provider" ? "Gerçek maliyet" : "Hesaplanan maliyet",
              value: formatUsd(report.estimated_cost_usd),
              hint: `${formatCount(report.token_usage.total_tokens)} token`,
            },
          ]}
        />

        <Card>
          <CardHeader>
            <CardTitle>Genel değerlendirme</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="leading-relaxed">{report.executive_summary}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>En sık sorulan sorular</CardTitle>
            <CardDescription>
              Oranlar analiz edilen {formatCount(pre.analyzed_count)} kayda göre hesaplandı.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <TopQuestionsChart questions={report.top_questions} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Tema dağılımı</CardTitle>
            <CardDescription>Sorular ana temalara göre gruplandı.</CardDescription>
          </CardHeader>
          <CardContent>
            <ThemeDistribution themes={report.themes} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Soru ayrıntıları</CardTitle>
            <CardDescription>Örnek mesajlarda kişisel veriler maskelenmiştir.</CardDescription>
          </CardHeader>
          <CardContent>
            <QuestionsTable questions={report.top_questions} />
          </CardContent>
        </Card>

        {/* İzlenebilirlik (ADR §8): sonucu hangi model ve hangi prompt sürümü
            üretti. Sonuç tartışmaya açıldığında ilk sorulacak şey bu. */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Analiz künyesi</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
              <Meta label="Model" value={report.model} />
              <Meta label="Prompt sürümü" value={report.prompt_version} />
              <Meta label="Prompt özeti" value={report.prompt_hash} mono />
              <Meta
                label="Maskelenen kayıt"
                value={`${formatCount(pre.redacted_count)} (${formatPercentage(
                  pre.analyzed_count > 0 ? (pre.redacted_count / pre.analyzed_count) * 100 : 0,
                )})`}
              />
              <Meta label="Şema sürümü" value={report.schema_version} />
              <Meta
                label="Girdi / çıktı token"
                value={`${formatCount(report.token_usage.prompt_tokens)} / ${formatCount(
                  report.token_usage.completion_tokens,
                )}`}
              />
              {report.token_usage.cached_tokens > 0 ? (
                <Meta
                  label="Cache'den okunan token"
                  value={formatCount(report.token_usage.cached_tokens)}
                />
              ) : null}
              {report.token_usage.cache_write_tokens > 0 ? (
                <Meta
                  label="Cache'e yazılan token"
                  value={formatCount(report.token_usage.cache_write_tokens)}
                />
              ) : null}
              {report.pricing_snapshot ? (
                <Meta
                  label="Fiyat kaynağı"
                  value={
                    report.pricing_snapshot.source === "openrouter"
                      ? "OpenRouter canlı katalog"
                      : "Yerel yedek katalog"
                  }
                />
              ) : null}
            </dl>
          </CardContent>
        </Card>
      </div>
    </Shell>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={`truncate ${mono ? "font-mono text-xs" : ""}`}>{value}</dd>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto w-full max-w-5xl px-4 py-10 sm:px-6 sm:py-12">{children}</div>;
}
