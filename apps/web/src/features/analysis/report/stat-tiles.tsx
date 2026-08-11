import { Card, CardContent } from "@/components/ui/card";

/**
 * Manşet sayılar.
 *
 * Bunlar için grafik çizilmiyor: tek bir güncel değer stat tile'a aittir,
 * tek çubuklu bir grafik değere hiçbir şey katmaz.
 */
export function StatTiles({
  items,
}: {
  items: readonly { label: string; value: string; hint?: string }[];
}) {
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {items.map((item) => (
        <Card key={item.label}>
          <CardContent className="space-y-1 py-4">
            <p className="text-sm text-muted-foreground">{item.label}</p>
            {/* tabular-nums: kartlar yan yana dururken rakamlar hizalansın. */}
            <p className="text-2xl font-semibold tabular-nums tracking-tight">{item.value}</p>
            {item.hint && <p className="text-xs text-muted-foreground">{item.hint}</p>}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
