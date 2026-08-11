import type { Metadata } from "next";

import { ProgressScreen } from "@/features/analysis/progress/progress-screen";

export const metadata: Metadata = {
  title: "Analiz",
};

export default async function AnalysisPage(props: PageProps<"/analizler/[analysisId]">) {
  // Next 16'da params bir Promise; senkron erişim kaldırıldı.
  const { analysisId } = await props.params;

  return <ProgressScreen analysisId={analysisId} />;
}
