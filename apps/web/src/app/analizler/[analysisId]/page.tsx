import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Analiz",
};

export default async function AnalysisPage(
  props: PageProps<"/analizler/[analysisId]">,
) {
  const { analysisId } = await props.params;

  // İlerleme ve dashboard ekranları sıradaki adımda.
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-16 text-center sm:px-6">
      <p className="text-muted-foreground">
        Analiz başlatıldı. İlerleme ekranı sıradaki adımda eklenecek.
      </p>
      <p className="mt-2 font-mono text-xs text-muted-foreground">
        {analysisId}
      </p>
    </div>
  );
}
