"use client";

import { FileSpreadsheet, Upload } from "lucide-react";
import { useCallback, useId, useRef, useState } from "react";

import { formatCount, formatFileSize } from "@/lib/format";
import { LIMITS } from "@/lib/api/schemas";
import { cn } from "@/lib/utils";

interface FileDropzoneProps {
  onFileSelected: (file: File) => void;
  disabled?: boolean;
}

/**
 * Dosya seçme alanı: sürükle-bırak + tıklayarak seçme.
 *
 * Görünürde bir <div> olsa da gerçek kontrol gizli bir <input type="file">.
 * Böylece klavye ve ekran okuyucu desteği tarayıcıdan gelir; sürükle-bırak
 * yalnızca fare kullanıcıları için ek bir kolaylık, tek yol değil.
 */
export function FileDropzone({ onFileSelected, disabled }: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [isDraggingOver, setIsDraggingOver] = useState(false);

  const handleDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setIsDraggingOver(false);
      if (disabled) return;

      const file = event.dataTransfer.files[0];
      if (file) onFileSelected(file);
    },
    [disabled, onFileSelected],
  );

  return (
    <div
      onDrop={handleDrop}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setIsDraggingOver(true);
      }}
      onDragLeave={() => setIsDraggingOver(false)}
      className={cn(
        "rounded-lg border-2 border-dashed transition-colors",
        isDraggingOver ? "border-primary bg-accent" : "border-border",
        disabled && "opacity-60",
      )}
    >
      <label
        htmlFor={inputId}
        className={cn(
          "flex flex-col items-center gap-3 px-6 py-12 text-center",
          disabled ? "cursor-not-allowed" : "cursor-pointer",
        )}
      >
        <span className="flex size-12 items-center justify-center rounded-full bg-muted">
          {isDraggingOver ? (
            <FileSpreadsheet className="size-5 text-primary" aria-hidden="true" />
          ) : (
            <Upload className="size-5 text-muted-foreground" aria-hidden="true" />
          )}
        </span>

        <span className="space-y-1">
          <span className="block font-medium">
            Excel dosyasını buraya sürükleyin
          </span>
          <span className="block text-sm text-muted-foreground">
            veya seçmek için tıklayın
          </span>
        </span>

        <span className="text-xs text-muted-foreground">
          Yalnızca .xlsx · en fazla {formatFileSize(LIMITS.MAX_UPLOAD_BYTES)} ·{" "}
          {formatCount(LIMITS.MAX_ROWS)} satır
        </span>

        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept=".xlsx"
          disabled={disabled}
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onFileSelected(file);
            // Aynı dosya art arda seçilebilsin diye input sıfırlanıyor;
            // aksi halde ikinci seçimde change olayı hiç tetiklenmez.
            event.target.value = "";
          }}
        />
      </label>
    </div>
  );
}
