"use client";

import { Badge } from "@/components/ui/badge";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatCount, formatDecimal } from "@/lib/format";
import type { ColumnProfile } from "@/lib/api/schemas";
import { cn } from "@/lib/utils";

interface ColumnPickerProps {
  columns: readonly ColumnProfile[];
  value: string | null;
  onChange: (columnName: string) => void;
}

/**
 * Metin kolonu seçimi.
 *
 * Kolonlar profil istatistikleriyle birlikte gösteriliyor: kullanıcı hangi
 * kolonun mesaj metni olduğuna dolu/boş oranı, benzersizlik ve örnek
 * değerlere bakarak karar veriyor. Backend'in `is_likely_text` tahmini
 * yalnızca bir işaret; seçim her zaman kullanıcıda.
 */
export function ColumnPicker({ columns, value, onChange }: ColumnPickerProps) {
  return (
    <RadioGroup
      value={value}
      onValueChange={(next) => {
        if (typeof next === "string") onChange(next);
      }}
      className="w-full"
    >
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10" />
              <TableHead>Kolon</TableHead>
              <TableHead className="text-right">Dolu</TableHead>
              <TableHead className="text-right">Boş</TableHead>
              <TableHead className="text-right">Benzersiz</TableHead>
              <TableHead className="text-right">Ort. uzunluk</TableHead>
              <TableHead>Örnek değerler</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {columns.map((column) => {
              const isSelected = value === column.name;

              return (
                <TableRow
                  key={column.name}
                  data-state={isSelected ? "selected" : undefined}
                  className={cn("cursor-pointer", isSelected && "bg-muted/50")}
                  onClick={() => onChange(column.name)}
                >
                  <TableCell>
                    <RadioGroupItem
                      value={column.name}
                      aria-label={`${column.name} kolonunu seç`}
                    />
                  </TableCell>

                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{column.name}</span>
                      {column.is_likely_text && (
                        <Badge variant="secondary">metin</Badge>
                      )}
                    </div>
                  </TableCell>

                  {/* tabular-nums: sayılar sütun halinde hizalansın. */}
                  <TableCell className="text-right tabular-nums">
                    {formatCount(column.non_empty_count)}
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right tabular-nums",
                      column.empty_count > 0 && "text-muted-foreground",
                    )}
                  >
                    {formatCount(column.empty_count)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCount(column.unique_count)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatDecimal(column.avg_length)}
                  </TableCell>

                  <TableCell className="max-w-xs">
                    <span className="line-clamp-2 text-sm text-muted-foreground">
                      {column.sample_values.join(" · ") || "—"}
                    </span>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </RadioGroup>
  );
}
