import { formatCount, formatPercentage } from "@/lib/format";
import type { Theme } from "@/lib/api/schemas";

/**
 * Tema dağılımı — parça-bütün, %100 yığılmış yatay çubuk.
 *
 * Neden pasta/donut değil: yakın değerleri karşılaştırmak pasta diliminde
 * gözle yapılamaz. Yığılmış çubuk hem sıralamayı hem payı doğru okutuyor.
 *
 * Renk temanın KİMLİĞİNE bağlı (slot sırası), büyüklüğüne değil. Bir tema
 * listeden çıkarsa kalan temaların rengi değişmemeli; okuyucu "sınav yeşildi"
 * diye öğrendiyse bu bilgi bozulmamalı.
 *
 * Slot sayısı 6 ile sınırlı. Fazlası "Diğer"e katlanıyor: 7. bir renk
 * üretmek, renk körlüğü altında mevcut bir slottan ayırt edilemez.
 */

// Tailwind sınıf adları statik olmak zorunda; dinamik `bg-chart-${i}` derlenmez.
const SLOT_CLASSES = [
  "bg-chart-1",
  "bg-chart-2",
  "bg-chart-3",
  "bg-chart-4",
  "bg-chart-5",
  "bg-chart-6",
] as const;

const MAX_SLOTS = SLOT_CLASSES.length;

interface Segment {
  id: string;
  name: string;
  count: number;
  percentage: number;
  className: string;
}

export function foldThemes(themes: readonly Theme[]): Segment[] {
  if (themes.length <= MAX_SLOTS) {
    return themes.map((theme, index) => ({
      id: theme.id,
      name: theme.name,
      count: theme.count,
      percentage: theme.percentage,
      className: SLOT_CLASSES[index],
    }));
  }

  // Son slot "Diğer" için ayrılıyor, yani ilk MAX_SLOTS-1 tema kendi rengini
  // koruyor.
  const kept = themes.slice(0, MAX_SLOTS - 1);
  const rest = themes.slice(MAX_SLOTS - 1);

  return [
    ...kept.map((theme, index) => ({
      id: theme.id,
      name: theme.name,
      count: theme.count,
      percentage: theme.percentage,
      className: SLOT_CLASSES[index],
    })),
    {
      id: "__diger__",
      name: `Diğer (${rest.length} tema)`,
      count: rest.reduce((sum, t) => sum + t.count, 0),
      percentage: rest.reduce((sum, t) => sum + t.percentage, 0),
      className: SLOT_CLASSES[MAX_SLOTS - 1],
    },
  ];
}

export function ThemeDistribution({ themes }: { themes: readonly Theme[] }) {
  const segments = foldThemes(themes);
  const total = segments.reduce((sum, s) => sum + s.count, 0);

  if (total === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Gösterilecek tema bulunamadı.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {/* gap-0.5: segmentler arasında 2px yüzey boşluğu, bitişik renkler
          birbirine karışmasın. */}
      <div
        className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full"
        role="img"
        aria-label={`Tema dağılımı: ${segments
          .map((s) => `${s.name} ${formatPercentage(s.percentage)}`)
          .join(", ")}`}
      >
        {segments.map((segment) => (
          <div
            key={segment.id}
            className={`h-full first:rounded-l-full last:rounded-r-full ${segment.className}`}
            style={{ width: `${(segment.count / total) * 100}%` }}
          />
        ))}
      </div>

      {/* Açıklama listesi her zaman var: kimlik hiçbir zaman yalnızca renkle
          taşınmıyor, ad ve sayı da yazılı. */}
      <ul className="grid gap-2 sm:grid-cols-2">
        {segments.map((segment) => (
          <li key={segment.id} className="flex items-center gap-2 text-sm">
            <span
              className={`size-3 shrink-0 rounded-sm ${segment.className}`}
              aria-hidden="true"
            />
            <span className="min-w-0 flex-1 truncate">{segment.name}</span>
            <span className="shrink-0 tabular-nums text-muted-foreground">
              {formatCount(segment.count)} · {formatPercentage(segment.percentage)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
