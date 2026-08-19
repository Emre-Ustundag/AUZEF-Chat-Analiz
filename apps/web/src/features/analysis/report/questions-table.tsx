import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatCount, formatPercentage } from "@/lib/format";
import type { TopQuestion } from "@/lib/api/schemas";

/**
 * Soru tablosu — grafiğin tablo görünümü.
 *
 * Erişilebilirlik gereği: grafikteki her bilgi tabloda da var, dolayısıyla
 * hiçbir şey yalnızca renkle veya yalnızca çubuk uzunluğuyla taşınmıyor.
 *
 * Örnek mesajlar backend'de PII redaksiyonundan geçmiş ve kırpılmış olarak
 * geliyor (ADR §5); arayüz ham kullanıcı metni göstermiyor.
 */
export function QuestionsTable({ questions }: { questions: readonly TopQuestion[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Soru</TableHead>
            <TableHead className="text-right">Adet</TableHead>
            <TableHead className="text-right">Oran</TableHead>
            <TableHead>Örnek mesajlar</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {questions.map((question) => (
            <TableRow key={question.id}>
              <TableCell className="font-medium">{question.canonical_question}</TableCell>
              <TableCell className="text-right tabular-nums">
                {formatCount(question.count)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatPercentage(question.percentage)}
              </TableCell>
              <TableCell className="max-w-sm">
                <span className="line-clamp-2 text-sm text-muted-foreground">
                  {question.redacted_examples.join(" · ") || "—"}
                </span>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
