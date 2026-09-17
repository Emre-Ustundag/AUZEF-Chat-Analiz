"""v3.1 migration'ının izole Meili/Qdrant + disposable DB üzerinde uçtan uca provası.

Gerçek kaynaklara yazmaz. Akış:
  gerçek kaynak parmak izi → disposable DB clone → test index/collection →
  pre-migration seed + tutarlılık → backup → apply (index-sync test hedeflerine) →
  verify + tutarlılık + alias/NEW-11 regresyonları + answer pipeline smoke →
  rollback (index-sync dahil) → seed durumuyla karşılaştırma → temizlik →
  gerçek kaynak parmak izi karşılaştırması.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "kb_migration_v31_runner.py"
PROBE = ROOT / "scripts" / "kb_migration_v31_index_rehearsal_probe.py"
CLI = ROOT / "scripts" / "kb_migration_v31.py"
LIVE_DATABASE = "auzef_bot"
EXPECTED = {"pre": {"active_qna": 311, "aliases": 2696, "guards": 0},
            "post": {"active_qna": 326, "aliases": 2695, "guards": 11}}


class StepFailed(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--compose-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True, help="backup/journal gibi büyük ara çıktılar")
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--workbook", type=Path, required=True, help="v3.1-final workbook (vaka 214 gibi yerinde kalan alias'lar)")
    return parser.parse_args()


class Rehearsal:
    def __init__(self, options: argparse.Namespace):
        self.options = options
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        self.name = f"qna_migration_v31_test_{stamp}"
        self.database = f"auzef_migration_v31_test_{stamp}"
        self.container_work = f"/tmp/{self.name}"
        self.report: dict[str, Any] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "disposable_database": self.database,
            "meili_index": self.name,
            "qdrant_collection": self.name,
            "steps": {},
        }
        self.plan = json.loads(options.plan.read_text(encoding="utf-8"))
        self.clone_url: str | None = None
        self.database_created = False

    # ---- altyapı ---------------------------------------------------------

    def compose(self, *args: str, input_bytes: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", "compose", *args], cwd=self.options.compose_root, input=input_bytes,
                              capture_output=True, check=check)

    def probe(self, mode: str, *, redirected: bool, **payload: Any) -> dict[str, Any]:
        command = ["exec", "-T"]
        if redirected:
            command += ["-e", f"DATABASE_URL={self.clone_url}"]
        command += ["backend", "python", "-c", PROBE.read_text(encoding="utf-8")]
        body = json.dumps({"mode": mode, "runner_source": RUNNER.read_text(encoding="utf-8"), **payload},
                          ensure_ascii=False).encode("utf-8")
        result = self.compose(*command, input_bytes=body, check=False)
        lines = [line for line in result.stdout.decode("utf-8").splitlines() if line.startswith('{"type": "probe"')]
        if result.returncode != 0 or not lines:
            raise StepFailed(f"probe {mode} başarısız: {result.stderr.decode('utf-8')[-3000:]}")
        return json.loads(lines[-1])["result"]

    def cli(self, mode: str, out: Path, *extra: str) -> tuple[int, str]:
        command = [sys.executable, str(CLI), mode, "--compose-root", str(self.options.compose_root),
                   "--out-dir", str(out), "--exec-env", f"DATABASE_URL={self.clone_url}", *extra]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        (self.options.work_dir / f"cli-{mode}-{out.name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        return result.returncode, result.stdout + result.stderr

    def index_flags(self) -> list[str]:
        return ["--meili-index", self.name, "--qdrant-collection", self.name]

    def step(self, key: str, value: Any, ok: bool) -> Any:
        self.report["steps"][key] = {"ok": ok, "result": value}
        self.save()
        print(f"[{'PASS' if ok else 'FAIL'}] {key}", flush=True)
        if not ok:
            raise StepFailed(key)
        return value

    def save(self) -> None:
        self.options.out_dir.mkdir(parents=True, exist_ok=True)
        (self.options.out_dir / "rehearsal-report.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )

    # ---- akış ------------------------------------------------------------

    def run(self) -> int:
        self.options.work_dir.mkdir(parents=True, exist_ok=True)
        real_before = self.probe("fingerprint", redirected=False, expected_database=LIVE_DATABASE)
        self.step("real_resources_before", real_before,
                  self.name not in real_before["meili"]["indexes"] and self.name not in real_before["qdrant"]["collections"])
        try:
            self.flow()
            self.report["status"] = "PASS"
        except Exception as exc:  # noqa: BLE001 - rapora yazılır, temizlik yine çalışır
            self.report["status"] = "FAIL"
            self.report["error"] = f"{exc}\n{traceback.format_exc()}"
        finally:
            self.cleanup()
            real_after = self.probe("fingerprint", redirected=False, expected_database=LIVE_DATABASE)
            untouched = {
                "db_snapshot_digest": real_before["db"]["snapshot_digest"] == real_after["db"]["snapshot_digest"],
                "meili_documents_digest": real_before["meili"]["documents_digest"] == real_after["meili"]["documents_digest"],
                "meili_settings_digest": real_before["meili"]["settings_digest"] == real_after["meili"]["settings_digest"],
                "meili_updated_at": real_before["meili"]["updated_at"] == real_after["meili"]["updated_at"],
                "meili_index_list": real_before["meili"]["indexes"] == real_after["meili"]["indexes"],
                "qdrant_points_digest": real_before["qdrant"]["points_digest"] == real_after["qdrant"]["points_digest"],
                "qdrant_collection_list": real_before["qdrant"]["collections"] == real_after["qdrant"]["collections"],
            }
            ok = all(untouched.values())
            self.report["steps"]["real_resources_after"] = {"ok": ok, "result": real_after, "comparison": untouched}
            print(f"[{'PASS' if ok else 'FAIL'}] real_resources_after", flush=True)
            if not ok:
                self.report["status"] = "FAIL"
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.save()
        return 0 if self.report["status"] == "PASS" else 1

    def flow(self) -> None:
        work = self.options.work_dir
        # 1) Disposable DB clone
        self.compose("exec", "-T", "db", "psql", "-U", "admin", "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                     "-qc", f"CREATE DATABASE {self.database}")
        self.database_created = True
        # pg_dump tek transaction snapshot'ı alır; canlı DB'ye yalnız okuma yapılır.
        self.compose("exec", "-T", "db", "sh", "-c",
                     f"pg_dump -U admin -d {LIVE_DATABASE} | psql -q -U admin -d {self.database} -v ON_ERROR_STOP=1")
        base = self.compose("exec", "-T", "backend", "sh", "-c", 'echo "${DATABASE_URL%/*}"').stdout.decode().strip()
        self.clone_url = f"{base}/{self.database}"
        live_digest = self.report["steps"]["real_resources_before"]["result"]["db"]["snapshot_digest"]
        clone = self.probe("db-info", redirected=True, expected_database=self.database)
        pre_counts = {k: clone[k] for k in ("active_qna", "aliases", "guards")}
        self.step("disposable_db_clone", {**{k: v for k, v in clone.items() if k != "active_ids"}},
                  clone["snapshot_digest"] == live_digest and pre_counts == EXPECTED["pre"])

        # 2) İzole test kaynakları
        created = self.probe("create-resources", redirected=True, name=self.name)
        self.step("test_resources_created", created, created["meili_settings_match_real"])

        # 3) Pre-migration seed (migration aracının kendi index-sync'iyle, tüm aktif QnA)
        code, output = self.cli("index-sync", work / "seed", *self.index_flags(),
                                "--ids", ",".join(str(i) for i in clone["active_ids"]))
        seed_sync = json.loads((work / "seed" / "index-sync-report.json").read_text(encoding="utf-8"))
        self.step("seed_index_sync", {k: seed_sync.get(k) for k in ("status", "meili_index", "qdrant_collection", "active", "failures")},
                  code == 0 and seed_sync["status"] == "PASS" and seed_sync["meili_index"] == self.name
                  and seed_sync["qdrant_collection"] == self.name and seed_sync["active"] == 311)
        seed = self.probe("consistency", redirected=True, name=self.name, expected_database=self.database,
                          label="seed", work_dir=self.container_work)
        self.step("seed_consistency", seed, seed["status"] == "PASS" and seed["db"]["active_qna"] == 311)

        # 4) Backup (disposable DB) + pre regresyonları
        code, _ = self.cli("backup", work / "backup", "--pg-database", self.database)
        manifest = json.loads((work / "backup" / "manifest.json").read_text(encoding="utf-8"))
        self.step("backup", manifest, code == 0 and manifest["snapshot_digest"] == live_digest)
        backup = json.loads((work / "backup" / "snapshot.json").read_text(encoding="utf-8"))
        pre = self.probe("migration-assertions", redirected=True, phase="pre", name=self.name,
                         expected_database=self.database, plan=self.plan, backup_snapshot=backup)
        self.step("pre_migration_alias_positions", pre,
                  pre["status"] == "PASS" and pre["alias_moves_checked"] == 47 and pre["alias_moves_ok"] == 47)

        # 5) Gerçek apply (commit) → index-sync test hedeflerine
        code, output = self.cli("apply", work / "apply", "--plan", str(self.options.plan), "--backup-dir", str(work / "backup"),
                                "--confirm", self.options.confirm, *self.index_flags())
        apply_report = json.loads((work / "apply" / "apply-report.json").read_text(encoding="utf-8"))
        journal = [json.loads(line) for line in (work / "apply" / "journal.ndjson").read_text(encoding="utf-8").splitlines()]
        apply_sync = json.loads((work / "apply" / "index-sync-report.json").read_text(encoding="utf-8"))
        order = [entry["kind"] for entry in journal]
        self.step("apply", {
            "status": apply_report["status"], "integrity": apply_report["integrity"]["status"],
            "integrity_passes": apply_report["integrity"]["passes"], "committed_units": len(journal),
            "unit_order": order, "created_ids": apply_report["integrity"]["created_ids"],
            "ex319": [e for e in journal if e["unit"] == "guard_only:EX-319"],
            "index_sync": {k: apply_sync.get(k) for k in ("status", "meili_index", "qdrant_collection", "active", "removed", "failures")},
            "index_ids": apply_report["index_ids"],
        }, code == 0 and apply_report["status"] == "APPLIED" and len(journal) == 43
            and all(e["status"] == "COMMITTED" for e in journal)
            and order == sorted(order, key=["guard_only", "update", "alias_move", "create", "promotion"].index)
            and apply_sync["status"] == "PASS" and apply_sync["meili_index"] == self.name
            and len(apply_report["index_ids"]) == 57)
        created_ids = apply_report["integrity"]["created_ids"]

        code, _ = self.cli("verify", work / "verify", "--plan", str(self.options.plan), "--backup-dir", str(work / "backup"))
        verify = json.loads((work / "verify" / "verify-report.json").read_text(encoding="utf-8"))
        self.step("db_integrity", {"status": verify["status"], "passes": verify["integrity"]["passes"],
                                   "failures": verify["integrity"]["failures"]}, code == 0 and verify["status"] == "PASS")

        post = self.probe("consistency", redirected=True, name=self.name, expected_database=self.database,
                          label="post", work_dir=self.container_work, reencode_qna_ids=apply_report["index_ids"])
        self.step("post_index_consistency", post, post["status"] == "PASS"
                  and {k: post["db"][k] for k in ("active_qna", "aliases", "guards")} == EXPECTED["post"]
                  and post["meili"]["documents"] == 326)
        assertions = self.probe("migration-assertions", redirected=True, phase="post", name=self.name,
                                expected_database=self.database, plan=self.plan, backup_snapshot=backup,
                                created_ids=created_ids)
        self.step("post_migration_index_assertions", assertions, assertions["status"] == "PASS"
                  and assertions["alias_moves_ok"] == 47 and len(assertions["created_searchable"]) == 15
                  and set(assertions["docs_with_self_canonical_alias"]) <= set(pre["docs_with_self_canonical_alias"])
                  and set(assertions["multi_owner_alias_texts"]) <= set(pre["multi_owner_alias_texts"]))

        smoke = self.probe("smoke", redirected=True, name=self.name, expected_database=self.database,
                           plan=self.plan, backup_snapshot=backup, created_ids=created_ids,
                           retained_319_aliases=self.retained_319_aliases())
        self.step("guard_runtime_smoke", smoke, smoke["status"] == "PASS" and smoke["counts"]["guarded_qna"] == 11)

        # 6) Rollback (DB + index-sync)
        code, _ = self.cli("rollback", work / "rollback", "--plan", str(self.options.plan), "--backup-dir", str(work / "backup"),
                           *self.index_flags())
        rollback = json.loads((work / "rollback" / "rollback-report.json").read_text(encoding="utf-8"))
        rollback_sync = json.loads((work / "rollback" / "rollback-index-sync-report.json").read_text(encoding="utf-8"))
        self.step("rollback", {"status": rollback["status"], "restored_digest": rollback.get("restored_digest"),
                               "counts": rollback.get("counts"),
                               "index_sync": {k: rollback_sync.get(k) for k in ("status", "meili_index", "qdrant_collection", "active", "removed", "failures")}},
                  code == 0 and rollback["status"] == "ROLLED_BACK" and rollback.get("restored_digest") == manifest["snapshot_digest"]
                  and rollback_sync["status"] == "PASS" and sorted(rollback_sync["removed"]) == sorted(created_ids.values()))
        after = self.probe("consistency", redirected=True, name=self.name, expected_database=self.database,
                           label="rollback", work_dir=self.container_work)
        self.step("rollback_index_consistency", after, after["status"] == "PASS"
                  and {k: after["db"][k] for k in ("active_qna", "aliases", "guards")} == EXPECTED["pre"])
        comparison = self.probe("compare-states", redirected=True, work_dir=self.container_work, left="seed", right="rollback")
        self.step("rollback_equals_seed", comparison, comparison["status"] == "PASS")
        pre_again = self.probe("migration-assertions", redirected=True, phase="pre", name=self.name,
                               expected_database=self.database, plan=self.plan, backup_snapshot=backup)
        self.step("rollback_alias_positions", {k: pre_again[k] for k in ("status", "alias_moves_ok", "new11", "failures")},
                  pre_again["status"] == "PASS" and pre_again["alias_moves_ok"] == 47)

    def retained_319_aliases(self) -> dict[str, str]:
        """Workbook'ta QnA 319'a bağlı olup planda taşınmayan vakalar (205, 206, 214)."""
        sys.path.insert(0, str(ROOT))
        from scripts.dry_run_kb_consolidation import workbook_tables

        moved = {int(a["case_no"]) for a in self.plan["alias_mutations"]}
        rows = workbook_tables(self.options.workbook)["Alias Haritası"]
        return {
            str(int(row["Vaka no"])): str(row["CSV'deki birebir alias"])
            for row in rows
            if str(row["Şu an bağlı olduğu QnA"]).strip() == "Af başvurusu nasıl yapılır?" and int(row["Vaka no"]) not in moved
        }

    def cleanup(self) -> None:
        result: dict[str, Any] = {}
        try:
            result["resources"] = self.probe("cleanup", redirected=False, name=self.name, work_dir=self.container_work)
        except Exception as exc:  # noqa: BLE001
            result["resources"] = {"status": "FAIL", "error": str(exc)}
        if self.database_created:
            drop = self.compose("exec", "-T", "db", "psql", "-U", "admin", "-d", "postgres", "-qc",
                                f"DROP DATABASE IF EXISTS {self.database} WITH (FORCE)", check=False)
            result["database_dropped"] = drop.returncode == 0
        listing = self.compose("exec", "-T", "db", "psql", "-U", "admin", "-d", "postgres", "-Atc",
                               "SELECT datname FROM pg_database ORDER BY 1", check=False).stdout.decode().split()
        result["database_remaining"] = self.database in listing
        ok = result["resources"].get("status") == "PASS" and not result["database_remaining"]
        self.report["steps"]["cleanup"] = {"ok": ok, "result": result}
        print(f"[{'PASS' if ok else 'FAIL'}] cleanup", flush=True)
        if not ok:
            self.report["status"] = "FAIL"
        self.save()


def main() -> int:
    return Rehearsal(parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
