import { formatCount, formatPercentage } from "@/lib/format";
import type { TopQuestion } from "@/lib/api/schemas";

/**
 * En sık sorulan sorular — sıralı yatay çubuk.
 *
 * Üç bilinçli karar:
 *
 * 1. TÜM ÇUBUKLAR TEK RENK. Sorular nominal kategoriler; aralarında doğal bir
 *    sıra yok. Çubuğu değerine göre koyulaştırmak, çubuk uzunluğunun zaten
 *    gösterdiği bilgiyi renge de kodlar ve tek serbest kanalı boşa harcar.
 *
 * 2. Yatay. Soru metinleri uzun ve Türkçe; dikey eksende okunamazdı.
 *
 * 3. Grafik kütüphanesi kullanılmadı. Bunlar eksen gerektirmeyen orantılı
 *    çubuklar; düz HTML hem erişilebilir hem de metin akışıyla birlikte
 *    doğru sarılıyor. Recharts, yol haritasındaki zaman serisi grafikleri
 *    için duruyor.
 *
 * Değerler her çubukta yazılı: bu liste aynı zamanda tablo görünümü görevi
 * görüyor, yani renk tek başına hiçbir bilgiyi taşımıyor.
 */
export function TopQuestionsChart({
  questions,
}: {
  questions: readonly TopQuestion[];
}) {
  if (questions.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Gösterilecek soru bulunamadı.
      </p>
    );
  }

  // Çubuk uzunlukları en büyük değere göre ölçekleniyor; toplam yerine
  // maksimuma göre ölçek, sıralı listede farkları görünür kılıyor.
  const max = Math.max(...questions.map((q) => q.count));

  return (
    <ol className="space-y-3">
      {questions.map((question, index) => (
        <li key={question.id} className="space-y-1.5">
          <div className="flex items-baseline justify-between gap-4">
            <span className="text-sm">
              <span className="mr-2 tabular-nums text-muted-foreground">
                {index + 1}.
              </span>
              {question.canonical_question}
            </span>
            <span className="shrink-0 tabular-nums text-sm text-muted-foreground">
              {formatCount(question.count)} · {formatPercentage(question.percentage)}
            </span>
          </div>

          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              // rounded-full: veri ucu yuvarlatılmış, taban hizasına sabit.
              className="h-full rounded-full bg-chart-1"
              style={{ width: `${(question.count / max) * 100}%` }}
            />
          </div>
        </li>
      ))}
    </ol>
  );
}
