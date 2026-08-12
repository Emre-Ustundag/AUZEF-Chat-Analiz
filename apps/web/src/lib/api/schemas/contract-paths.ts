import * as fs from "node:fs";
import * as path from "node:path";

/**
 * Sözleşme artefaktlarının konumu.
 *
 * Dosyalar `import` ile DEĞİL `node:fs` ile okunuyor: `resolveJsonModule`
 * apps/web dışındaki dosyalarda tsc'yi kirletir ve dizini çalışma anında
 * okumak, fixture eklerken test kodunu değiştirmemeyi sağlar.
 */
const REPO_ROOT = path.resolve(import.meta.dirname, "../../../../../..");

export const FIXTURE_DIR = path.join(REPO_ROOT, "tests", "fixtures", "contract");
export const OPENAPI_PATH = path.join(REPO_ROOT, "docs", "api", "openapi.json");

export function readJson<T = unknown>(filePath: string): T {
  return JSON.parse(fs.readFileSync(filePath, "utf8")) as T;
}

export function readFixture<T = unknown>(fileName: string): T {
  return readJson<T>(path.join(FIXTURE_DIR, fileName));
}

export interface ManifestCase {
  id: string;
  method: string;
  path: string;
  status: number;
  model: string | null;
  file: string | null;
}

export interface Manifest {
  contract_version: string;
  /** Sözleşmede donmuş sınırlar; `LIMITS` sabitlerinin karşılığı. */
  limits: { max_rows: number };
  cases: ManifestCase[];
}

export interface ConstraintCase {
  model: string;
  base: string;
  field: string;
  value: unknown;
  valid: boolean;
}

export function readManifest(): Manifest {
  return readFixture<Manifest>("manifest.json");
}

export function readConstraints(): ConstraintCase[] {
  return readFixture<{ cases: ConstraintCase[] }>("constraints.json").cases;
}
