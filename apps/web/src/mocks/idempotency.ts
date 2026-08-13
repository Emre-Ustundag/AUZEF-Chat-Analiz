import { createHash } from "node:crypto";

import type { AnalysisRequest } from "@/lib/api/schemas";

/**
 * Bu sözleşmedeki ASCII alan adları için canonical JSON üretir: object
 * anahtarları JS code-unit sırasındadır ve gereksiz boşluk yoktur. Bu, genel
 * amaçlı bir RFC 8785 uygulaması değildir; yalnızca doğrulanmış API
 * modellerimizin JSON alt kümesini kapsar.
 */
export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }

  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0));

    return `{${entries
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }

  const encoded = JSON.stringify(value);
  if (encoded === undefined) {
    throw new TypeError("Canonical JSON yalnızca JSON değerlerini kabul eder.");
  }
  return encoded;
}

function sha256(value: string | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

/** Header'lar (özellikle BYOK anahtarı) bilinçli olarak fingerprint'e girmez. */
export function analysisFingerprint(request: AnalysisRequest): string {
  return sha256(canonicalJson(request));
}

/** ADR-0002 #3'teki iki aşamalı upload fingerprint'i. */
export async function uploadFingerprint(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const metadata = {
    file_sha256: sha256(bytes),
    filename: file.name,
    mime_type: file.type,
    size: file.size,
  };

  return sha256(canonicalJson(metadata));
}
