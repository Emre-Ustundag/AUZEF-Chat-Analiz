"""v3.1 KB migration'ını gerçek LOCAL geliştirme ortamına uygular (production değil).

Akış: version kontrolü → bakım penceresi (frontend ingress durdurulur) →
gerçek kaynak parmak izi + indeks yakalama → fresh backup → preflight →
apply (+ gerçek index-sync) → verify (DB / Meili / Qdrant / guard smoke) →
PASS: baseline export · FAIL: kayıt + rollback + geri dönüş doğrulaması →
bakım penceresi kapanır (yalnız sistem tutarlıysa).

Mutasyonlar yalnız ``kb_migration_v31.py`` (backup/apply/verify/rollback)
üzerinden yapılır; bu script doğrulama ve kayıt tutar.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.dry_run_kb_consolidation import workbook_tables  # noqa: E402

RUNNER = ROOT / "scripts" / "kb_migration_v31_runner.py"
PROBE = ROOT / "scripts" / "kb_migration_v31_index_rehearsal_probe.py"
CLI = ROOT / "scripts" / "kb_migration_v31.py"
CONSOLIDATION_DRY_RUN = ROOT / "scripts" / "dry_run_kb_consolidation.py"
PLAN = ROOT / "outputs" / "kb-consolidation-v3.1-final-dry-run-guard-20260917" / "kb-mutation-plan-v3.1-final.json"
PLAN_CONFIRM = "0a900d1a8f9e5d93"
LIVE_DATABASE = "auzef_bot"
REAL = "__real__"
EXPECTED_PRE = {"active_qna": 311, "aliases": 2696, "guards": 0}
EXPECTED_POST = {"active_qna": 326, "aliases": 2695, "guards": 11}
CONTAINER_WORK = "/tmp/kb_migration_v31_local"


class Abort(RuntimeError):
    """Mutasyon öncesi durdurma (rollback gerekmez)."""


class VerifyFailed(RuntimeError):
    """Mutasyon sonrası kritik doğrulama başarısız (rollback gerekir)."""


def blake(path: Path) -> str:
    return hashlib.blake2b(path.read_bytes(), digest_size=32).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-root", type=Path, required=True)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--base-plan", type=Path, required=True, help="Konsolidasyon dry-run'ının v3.1 temel planı")
    parser.add_argument("--qna-csv", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True, help="Git dışı kalıcı backup kökü")
    parser.add_argument("--out-dir", type=Path, required=True, help="Rapor ve baseline export dizini")
    parser.add_argument("--confirm", required=True)
    return parser.parse_args()


class LocalMigration:
    def __init__(self, options: argparse.Namespace):
        self.o = options
        self.stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.backup_dir = options.backup_root / f"kb-migration-v31-{self.stamp}"
        self.out = options.out_dir
        self.work = self.backup_dir / "run"
        self.plan = json.loads(PLAN.read_text(encoding="utf-8"))
        self.report: dict[str, Any] = {"started_at": datetime.now(timezone.utc).isoformat(), "steps": {}}
        self.mutated = False
        self.frontend_stopped = False

    # ---- altyapı ---------------------------------------------------------

    def run_cmd(self, command: list[str], cwd: Path | None = None, input_bytes: bytes | None = None,
                check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(command, cwd=cwd or self.o.compose_root, input=input_bytes, capture_output=True, check=check)

    def compose(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess:
        return self.run_cmd(["docker", "compose", *args], **kwargs)

    def probe(self, mode: str, **payload: Any) -> dict[str, Any]:
        body = json.dumps({"mode": mode, "runner_source": RUNNER.read_text(encoding="utf-8"), **payload},
                          ensure_ascii=False).encode("utf-8")
        result = self.compose("exec", "-T", "backend", "python", "-c", PROBE.read_text(encoding="utf-8"),
                              input_bytes=body, check=False)
        lines = [line for line in result.stdout.decode("utf-8").splitlines() if line.startswith('{"type": "probe"')]
        if result.returncode != 0 or not lines:
            raise RuntimeError(f"probe {mode} başarısız: {result.stderr.decode('utf-8')[-3000:]}")
        return json.loads(lines[-1])["result"]

    def cli(self, mode: str, out: Path, *extra: str) -> int:
        result = self.run_cmd([sys.executable, str(CLI), mode, "--compose-root", str(self.o.compose_root),
                               "--out-dir", str(out), *extra], cwd=ROOT, check=False)
        (self.work / f"cli-{mode}-{out.name}.log").write_bytes(result.stdout + result.stderr)
        return result.returncode

    def step(self, key: str, value: Any, ok: bool, failure: type[Exception] | None = None) -> Any:
        self.report["steps"][key] = {"ok": ok, "at": datetime.now(timezone.utc).isoformat(), "result": value}
        self.save()
        print(f"[{'PASS' if ok else 'FAIL'}] {key}", flush=True)
        if not ok:
            raise (failure or (VerifyFailed if self.mutated else Abort))(key)
        return value

    def save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "migration-run.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    def read(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def retained_319_aliases(self) -> dict[str, str]:
        moved = {int(a["case_no"]) for a in self.plan["alias_mutations"]}
        return {
            str(int(row["Vaka no"])): str(row["CSV'deki birebir alias"])
            for row in workbook_tables(self.o.workbook)["Alias Haritası"]
            if str(row["Şu an bağlı olduğu QnA"]).strip() == "Af başvurusu nasıl yapılır?" and int(row["Vaka no"]) not in moved
        }

    def copy_capture(self, label: str) -> dict[str, str]:
        target = self.backup_dir / "index-captures"
        target.mkdir(parents=True, exist_ok=True)
        digests = {}
        for suffix in ("meili.json", "qdrant-points.json", "qdrant-vectors.npy"):
            name = f"{label}-{suffix}"
            self.compose("cp", f"backend:{CONTAINER_WORK}/{name}", str(target / name))
            digests[name] = blake(target / name)
        return digests

    # ---- akış ------------------------------------------------------------

    def run(self) -> int:
        self.work.mkdir(parents=True, exist_ok=True)
        status = "FAIL"
        try:
            self.version_gate()
            self.maintenance_on()
            self.capture_pre()
            self.fresh_backup()
            self.preflight()
            self.apply()
            self.verify()
            self.export_baseline()
            status = "PASS"
        except Abort as exc:
            self.report["abort"] = {"step": str(exc), "trace": traceback.format_exc(), "mutated": self.mutated}
            status = "ABORTED_NO_MUTATION" if not self.mutated else "FAIL"
            if self.mutated:
                self.rollback()
        except Exception as exc:  # noqa: BLE001
            self.report["failure"] = {"step": str(exc), "trace": traceback.format_exc(), "mutated": self.mutated}
            if self.mutated:
                self.rollback()
        finally:
            self.report["status"] = status
            self.maintenance_off(consistent=status == "PASS" or self.report.get("rollback", {}).get("status") == "PASS"
                                 or not self.mutated)
            # Konteynerdeki geçici indeks yakalamaları backup dizinine kopyalandı; kalıcı kopya orada.
            self.compose("exec", "-T", "backend", "rm", "-rf", CONTAINER_WORK, check=False)
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.save()
            self.write_markdown()
        print(f"LOCAL KB MIGRATION V3.1 FINAL: {'PASS' if status == 'PASS' else 'FAIL'}")
        return 0 if status == "PASS" else 1

    def version_gate(self) -> None:
        git = lambda *a: self.run_cmd(["git", *a], cwd=ROOT).stdout.decode().strip()  # noqa: E731
        tracked_dirty = git("status", "--porcelain", "--untracked-files=no")
        head, remote = git("rev-parse", "HEAD"), git("rev-parse", "@{u}")
        tools = ["scripts/kb_migration_v31.py", "scripts/kb_migration_v31_runner.py",
                 "scripts/kb_migration_v31_index_rehearsal_probe.py", "scripts/apply_kb_migration_v31_local.py",
                 "scripts/dry_run_kb_consolidation.py", str(PLAN.relative_to(ROOT))]
        unversioned = [t for t in tools if not git("ls-files", t)]
        chatbot_head = self.run_cmd(["git", "rev-parse", "HEAD"]).stdout.decode().strip()
        chatbot_dirty = self.run_cmd(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.decode().strip()
        code_same = {}
        for rel in ("services/routing_guards.py", "services/answer_pipeline.py", "services/providers.py",
                    "core/database.py", "core/deps.py", "routers/qna.py"):
            host = hashlib.sha1((self.o.compose_root / "backend" / rel).read_bytes()).hexdigest()
            ctr = self.compose("exec", "-T", "backend", "sha1sum", rel).stdout.decode().split()[0]
            code_same[rel] = host == ctr
        plan_digest = blake(PLAN)
        self.report.update(analysis_commit=head, analysis_branch=git("rev-parse", "--abbrev-ref", "HEAD"),
                           chatbot_commit=chatbot_head, plan_digest=plan_digest)
        self.step("version_gate", {
            "analysis_head": head, "analysis_upstream": remote, "tracked_dirty": tracked_dirty, "unversioned_tools": unversioned,
            "chatbot_head": chatbot_head, "chatbot_tracked_dirty": chatbot_dirty, "container_code_matches_chatbot_head": code_same,
            "plan_digest": plan_digest, "confirm": self.o.confirm,
        }, not tracked_dirty and head == remote and not unversioned and not chatbot_dirty and all(code_same.values())
            and plan_digest[:16] == PLAN_CONFIRM == self.o.confirm)

    def maintenance_on(self) -> None:
        last_edit = self.compose("exec", "-T", "db", "psql", "-U", "admin", "-d", LIVE_DATABASE, "-Atc",
                                 "SELECT coalesce(max(updated_at)::text, '') FROM qna").stdout.decode().strip()
        published = self.compose("ps", "--format", "{{.Service}} {{.Publishers}}").stdout.decode()
        self.compose("stop", "frontend")
        self.frontend_stopped = True
        state = self.run_cmd(["docker", "inspect", "-f", "{{.State.Running}}", "auzef_frontend"]).stdout.decode().strip()
        backend_host_ports = [line for line in published.splitlines()
                              if not line.startswith("frontend") and "0.0.0.0" in line]
        self.step("maintenance_window_on", {
            "method": "frontend (nginx ingress, tek host portu 80/443) durduruldu; admin panel ve /api erişilemez",
            "frontend_running": state, "other_host_published_services": backend_host_ports, "last_qna_edit": last_edit,
        }, state == "false" and not backend_host_ports)

    def capture_pre(self) -> None:
        fingerprint = self.probe("fingerprint", expected_database=LIVE_DATABASE)
        (self.backup_dir / "pre-real-fingerprint.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2))
        counts = {k: fingerprint["db"][k] for k in EXPECTED_PRE}
        consistency = self.probe("consistency", name=REAL, expected_database=LIVE_DATABASE, label="pre", work_dir=CONTAINER_WORK)
        captures = self.copy_capture("pre")
        self.report["pre_fingerprint"] = fingerprint
        self.step("pre_real_state", {"fingerprint": fingerprint, "consistency": consistency, "index_capture": captures},
                  counts == EXPECTED_PRE and consistency["status"] == "PASS")

    def fresh_backup(self) -> None:
        backup = self.backup_dir / "backup"
        code = self.cli("backup", backup, "--pg-database", LIVE_DATABASE)
        manifest = self.read(backup / "manifest.json") if (backup / "manifest.json").exists() else {}
        full_dump = self.backup_dir / "auzef_bot-full.dump"
        with full_dump.open("wb") as handle:
            subprocess.run(["docker", "compose", "exec", "-T", "db", "pg_dump", "-U", "admin", "-d", LIVE_DATABASE, "-Fc"],
                           cwd=self.o.compose_root, stdout=handle, check=True)
        listing = self.compose("exec", "-T", "db", "pg_restore", "--list", input_bytes=full_dump.read_bytes()).stdout.decode()
        table_listing = self.compose("exec", "-T", "db", "pg_restore", "--list",
                                     input_bytes=(backup / manifest.get("pg_dump_file", "missing")).read_bytes()
                                     if manifest else b"", check=False).stdout.decode()
        sequences = {}
        for name in ("qna_id_seq", "qna_queries_id_seq"):
            row = self.compose("exec", "-T", "db", "psql", "-U", "admin", "-d", LIVE_DATABASE, "-Atc",
                               f"SELECT last_value, is_called FROM {name}").stdout.decode().strip()
            sequences[name] = row
        extra = {
            "full_admin_dump": str(full_dump), "full_admin_dump_blake2b": blake(full_dump),
            "full_dump_has_qna_tables": all(f"TABLE DATA public {t} " in listing for t in ("qna", "qna_queries", "qna_routing_guards")),
            "table_dump_sequence_entries": [line for line in table_listing.splitlines() if "SEQUENCE" in line],
            "sequences": sequences,
            "rollback_metadata": {"snapshot": str(backup / "snapshot.json"), "manifest": str(backup / "manifest.json"),
                                  "index_captures": str(self.backup_dir / "index-captures"),
                                  "pre_fingerprint": str(self.backup_dir / "pre-real-fingerprint.json")},
        }
        (self.backup_dir / "backup-extra.json").write_text(json.dumps(extra, ensure_ascii=False, indent=2))
        self.report["backup"] = {"dir": str(backup), "manifest": manifest, **extra}
        pre_digest = self.report["pre_fingerprint"]["db"]["snapshot_digest"]
        self.step("fresh_backup", self.report["backup"],
                  code == 0 and manifest.get("snapshot_digest") == pre_digest and extra["full_dump_has_qna_tables"]
                  and manifest.get("counts") == {"qna": len(self.read(backup / "snapshot.json")["qna"]), "aliases": 2696, "guards": 0})

    def preflight(self) -> None:
        consolidation_out = self.work / "consolidation-dry-run"
        result = self.run_cmd([sys.executable, str(CONSOLIDATION_DRY_RUN), "--workbook", str(self.o.workbook),
                               "--base-plan", str(self.o.base_plan), "--qna-csv", str(self.o.qna_csv),
                               "--backend-root", str(self.o.compose_root / "backend"),
                               "--compose-root", str(self.o.compose_root), "--output-dir", str(consolidation_out)],
                              cwd=ROOT, check=False)
        consolidation = self.read(consolidation_out / "dry-run-report.json")
        regenerated = self.read(consolidation_out / "kb-mutation-plan-v3.1-final.json")
        committed = dict(self.plan)
        regenerated.pop("generated_at"), committed.pop("generated_at")
        migration_dry = self.work / "migration-dry-run"
        code = self.cli("dry-run", migration_dry, "--plan", str(PLAN))
        dry = self.read(migration_dry / "migration-dry-run-report.json")
        backup = self.read(self.backup_dir / "backup" / "snapshot.json")
        pre_index = self.probe("migration-assertions", phase="pre", name=REAL, expected_database=LIVE_DATABASE,
                               plan=self.plan, backup_snapshot=backup)
        after = self.probe("db-info", expected_database=LIVE_DATABASE)
        self.step("preflight", {
            "consolidation_dry_run": {"status": consolidation["status"], "passes": consolidation["passes"],
                                      "errors": consolidation["errors"], "exit": result.returncode},
            "regenerated_plan_equals_committed": regenerated == committed,
            "migration_dry_run": {"status": dry["status"], "preflight_errors": dry["preflight_errors"], "units": len(dry["units"]),
                                  "integrity": dry["integrity"]["status"], "integrity_passes": dry["integrity"]["passes"],
                                  "rollback_rehearsal": dry["rollback_rehearsal"]["status"], "live_unchanged": dry["live_unchanged"]},
            "index_pre_alias_positions": {k: pre_index[k] for k in ("status", "alias_moves_checked", "alias_moves_ok", "new11", "named_regressions", "failures")},
            "db_digest_still_backup": after["snapshot_digest"] == backup["digest"],
        }, result.returncode == 0 and consolidation["status"] == "PASS" and len(consolidation["passes"]) == 9
            and regenerated == committed and code == 0 and dry["status"] == "PASS" and not dry["preflight_errors"]
            and len(dry["units"]) == 43 and pre_index["status"] == "PASS" and pre_index["alias_moves_ok"] == 47
            and after["snapshot_digest"] == backup["digest"])

    def apply(self) -> None:
        apply_dir = self.work / "apply"
        self.mutated = True  # apply çağrısıyla birlikte yazma başlayabilir
        code = self.cli("apply", apply_dir, "--plan", str(PLAN), "--backup-dir", str(self.backup_dir / "backup"),
                        "--confirm", self.o.confirm)
        report = self.read(apply_dir / "apply-report.json") if (apply_dir / "apply-report.json").exists() else {}
        if report.get("status") == "ABORTED":
            self.mutated = False
        journal_path = apply_dir / "journal.ndjson"
        journal = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()] if journal_path.exists() else []
        sync_path = apply_dir / "index-sync-report.json"
        sync = self.read(sync_path) if sync_path.exists() else {"status": "NO_RESULT"}
        order = [e.get("kind") for e in journal]
        self.report["journal"] = str(journal_path)
        self.report["created_ids"] = report.get("integrity", {}).get("created_ids", {})
        self.report["index_ids"] = report.get("index_ids", [])
        self.step("apply", {
            "exit": code, "status": report.get("status"), "integrity": report.get("integrity", {}).get("status"),
            "integrity_passes": report.get("integrity", {}).get("passes"), "committed": sum(e["status"] == "COMMITTED" for e in journal),
            "failed": [e for e in journal if e["status"] != "COMMITTED"], "unit_order": order,
            "ex319": [e for e in journal if e.get("unit") == "guard_only:EX-319"], "created_ids": self.report["created_ids"],
            "index_sync": {k: sync.get(k) for k in ("status", "meili_index", "qdrant_collection", "active", "removed", "failures")},
            "index_ids": self.report["index_ids"], "after_digest": report.get("after_digest"),
        }, code == 0 and report.get("status") == "APPLIED" and len(journal) == 43
            and all(e["status"] == "COMMITTED" for e in journal)
            and order == sorted(order, key=["guard_only", "update", "alias_move", "create", "promotion"].index)
            and sync.get("status") == "PASS" and sync.get("meili_index") == "auzef_qna_index"
            and sync.get("qdrant_collection") == "auzef_qna_vectors" and len(self.report["index_ids"]) == 57
            and any(e.get("skipped_content_update", {}).get("status") == "BLOCKED_CASE_214" for e in journal))

    def verify(self) -> None:
        verify_dir = self.work / "verify"
        code = self.cli("verify", verify_dir, "--plan", str(PLAN), "--backup-dir", str(self.backup_dir / "backup"))
        verify = self.read(verify_dir / "verify-report.json")
        self.step("db_integrity", {"status": verify["status"], "passes": verify["integrity"]["passes"],
                                   "failures": verify["integrity"]["failures"]}, code == 0 and verify["status"] == "PASS")
        backup = self.read(self.backup_dir / "backup" / "snapshot.json")
        post = self.probe("consistency", name=REAL, expected_database=LIVE_DATABASE, label="post",
                          work_dir=CONTAINER_WORK, reencode_qna_ids=self.report["index_ids"])
        self.step("index_consistency", post, post["status"] == "PASS"
                  and {k: post["db"][k] for k in EXPECTED_POST} == EXPECTED_POST
                  and post["meili"]["index"] == "auzef_qna_index" and post["meili"]["documents"] == 326
                  and post["qdrant"]["collection"] == "auzef_qna_vectors" and post["qdrant"]["points"] == post["qdrant"]["expected"])
        assertions = self.probe("migration-assertions", phase="post", name=REAL, expected_database=LIVE_DATABASE,
                                plan=self.plan, backup_snapshot=backup, created_ids=self.report["created_ids"])
        self.step("index_migration_assertions", assertions, assertions["status"] == "PASS"
                  and assertions["alias_moves_ok"] == 47 and len(assertions["created_searchable"]) == 15)
        smoke = self.probe("smoke", name=REAL, expected_database=LIVE_DATABASE, plan=self.plan, backup_snapshot=backup,
                           created_ids=self.report["created_ids"], retained_319_aliases=self.retained_319_aliases())
        self.step("guard_runtime_smoke", smoke, smoke["status"] == "PASS" and smoke["counts"]["guarded_qna"] == 11
                  and sorted(smoke["results"].get("temporal_counterfactual_demonstrated_refs", [])) == ["NEW-10", "NEW-12", "NEW-13"])

    def export_baseline(self) -> None:
        data = self.probe("export-baseline", expected_database=LIVE_DATABASE)
        baseline = self.out / "baseline"
        baseline.mkdir(parents=True, exist_ok=True)
        files = {}

        def dump(name: str, payload: Any) -> None:
            path = baseline / name
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            files[name] = blake(path)

        active = [row for row in data["qna"] if row["status"] == 1]
        dump("qna-canonical.json", sorted(data["qna"], key=lambda r: r["id"]))
        dump("qna-aliases.json", sorted(data["aliases"], key=lambda r: r["id"]))
        dump("qna-routing-guards.json", sorted(data["guards"], key=lambda r: r["qna_id"]))
        csv_path = baseline / "qna-canonical-active.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["id", "question", "answer", "tags", "updated_by"])
            for row in sorted(active, key=lambda r: r["id"]):
                writer.writerow([row["id"], row["question_text"], row["answer_text"], ", ".join(row["tags"]), row["updated_by"] or ""])
        files[csv_path.name] = blake(csv_path)
        alias_csv = baseline / "qna-aliases.csv"
        with alias_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["alias_id", "qna_id", "query_text"])
            for row in sorted(data["aliases"], key=lambda r: (r["qna_id"], r["id"])):
                writer.writerow([row["id"], row["qna_id"], row["query_text"]])
        files[alias_csv.name] = blake(alias_csv)

        post_fingerprint = self.probe("fingerprint", expected_database=LIVE_DATABASE)
        journal = [json.loads(line) for line in Path(self.report["journal"]).read_text(encoding="utf-8").splitlines()]
        assertions = self.report["steps"]["index_migration_assertions"]["result"]
        smoke = self.report["steps"]["guard_runtime_smoke"]["result"]
        migration_report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "environment": "local development (docker compose auzefchatbot); NOT production",
            "plan": {"path": str(PLAN.relative_to(ROOT)), "blake2b": self.report["plan_digest"], "confirm": PLAN_CONFIRM},
            "git": {"analysis_branch": self.report["analysis_branch"], "analysis_commit": self.report["analysis_commit"],
                    "chatbot_commit": self.report["chatbot_commit"]},
            "pre_migration_digest": self.report["pre_fingerprint"]["db"]["snapshot_digest"],
            "post_migration_digest": data["snapshot_digest"],
            "backup": {"dir": str(self.backup_dir), "table_dump": self.report["backup"]["manifest"].get("pg_dump_file"),
                       "table_dump_blake2b": self.report["backup"]["manifest"].get("pg_dump_blake2b"),
                       "snapshot_file_blake2b": self.report["backup"]["manifest"].get("snapshot_file_blake2b"),
                       "full_admin_dump": self.report["backup"]["full_admin_dump"],
                       "full_admin_dump_blake2b": self.report["backup"]["full_admin_dump_blake2b"],
                       "sequences_before": self.report["backup"]["sequences"]},
            "counts": {"pre": {k: self.report["pre_fingerprint"]["db"][k] for k in EXPECTED_PRE},
                       "post": {"active_qna": len(active), "aliases": len(data["aliases"]), "guards": len(data["guards"])}},
            "units": [{k: e.get(k) for k in ("unit", "kind", "ref", "qna_id", "created", "guard_ref", "status")} for e in journal],
            "skipped_content_updates": [e["skipped_content_update"] for e in journal if e.get("skipped_content_update")],
            "created_qna_ids": self.report["created_ids"],
            "alias_moves": [{k: m[k] for k in ("case", "alias", "source", "target", "target_ref", "meili", "qdrant", "ok")}
                            for m in assertions.get("moves", [])] or
                           [move for e in journal for move in (e.get("alias_moves") or [])],
            "new11": assertions["new11"],
            "meili_fingerprint_pre": self.report["pre_fingerprint"]["meili"], "meili_fingerprint_post": post_fingerprint["meili"],
            "qdrant_fingerprint_pre": self.report["pre_fingerprint"]["qdrant"], "qdrant_fingerprint_post": post_fingerprint["qdrant"],
            "guard_verify": {"guards": data["guards"], "smoke_counts": smoke["counts"],
                             "temporal_demonstrated": smoke["results"].get("temporal_counterfactual_demonstrated_refs"),
                             "ex319": smoke["results"]["ex319"]},
            "sequences_after": data["sequences"],
            "baseline_files": files,
        }
        dump("migration-report.json", migration_report)
        self.report["baseline"] = {"dir": str(baseline), "files": files, "post_fingerprint": post_fingerprint}
        self.step("baseline_export", {"dir": str(baseline), "files": files},
                  len(active) == 326 and len(data["aliases"]) == 2695 and len(data["guards"]) == 11
                  and post_fingerprint["db"]["snapshot_digest"] == data["snapshot_digest"])

    def rollback(self) -> None:
        record: dict[str, Any] = {}
        try:
            record["failure_fingerprint"] = self.probe("fingerprint", expected_database=LIVE_DATABASE)
        except Exception as exc:  # noqa: BLE001
            record["failure_fingerprint_error"] = str(exc)
        (self.backup_dir / "failure-state.json").write_text(json.dumps(
            {"report": self.report, "fingerprint": record.get("failure_fingerprint")}, ensure_ascii=False, indent=2, default=str))
        rollback_dir = self.work / "rollback"
        code = self.cli("rollback", rollback_dir, "--plan", str(PLAN), "--backup-dir", str(self.backup_dir / "backup"))
        result = self.read(rollback_dir / "rollback-report.json") if (rollback_dir / "rollback-report.json").exists() else {}
        sync_path = rollback_dir / "rollback-index-sync-report.json"
        sync = self.read(sync_path) if sync_path.exists() else {"status": "NO_RESULT"}
        record.update(exit=code, db=result, index_sync=sync)
        try:
            consistency = self.probe("consistency", name=REAL, expected_database=LIVE_DATABASE, label="rollback", work_dir=CONTAINER_WORK)
            comparison = self.probe("compare-states", work_dir=CONTAINER_WORK, left="pre", right="rollback")
            record.update(consistency=consistency, compare_pre=comparison)
        except Exception as exc:  # noqa: BLE001
            record["verify_error"] = str(exc)
        backup_digest = self.report.get("backup", {}).get("manifest", {}).get("snapshot_digest")
        record["status"] = "PASS" if (
            code == 0 and result.get("status") == "ROLLED_BACK" and result.get("restored_digest") == backup_digest
            and sync.get("status") == "PASS" and record.get("consistency", {}).get("status") == "PASS"
            and record.get("compare_pre", {}).get("status") == "PASS"
        ) else "FAIL"
        self.report["rollback"] = record
        print(f"[{record['status']}] rollback", flush=True)
        self.save()

    def maintenance_off(self, consistent: bool) -> None:
        if not self.frontend_stopped:
            return
        if not consistent:
            self.report["maintenance_window"] = "AÇIK BIRAKILDI — sistem tutarlı değil, manuel inceleme gerekli"
            return
        self.compose("start", "frontend")
        state = self.run_cmd(["docker", "inspect", "-f", "{{.State.Running}}", "auzef_frontend"], check=False).stdout.decode().strip()
        self.report["maintenance_window"] = f"kapatıldı (frontend running={state})"
        print(f"[{'PASS' if state == 'true' else 'FAIL'}] maintenance_window_off", flush=True)

    def write_markdown(self) -> None:
        steps = self.report["steps"]
        lines = [f"# LOCAL KB MIGRATION V3.1 — {self.report['status']}", "",
                 f"- Başlangıç/bitiş: {self.report['started_at']} → {self.report.get('finished_at')}",
                 f"- Plan digest: `{self.report.get('plan_digest')}`",
                 f"- Analiz commit: `{self.report.get('analysis_commit')}` · Chatbot commit: `{self.report.get('chatbot_commit')}`",
                 f"- Backup: `{self.backup_dir}`", f"- Bakım penceresi: {self.report.get('maintenance_window')}", "",
                 "| Adım | Sonuç |", "|---|---|"]
        lines += [f"| {key} | {'PASS' if value['ok'] else 'FAIL'} |" for key, value in steps.items()]
        if self.report.get("rollback"):
            lines += ["", f"Rollback: **{self.report['rollback']['status']}**"]
        if self.report.get("created_ids"):
            lines += ["", "| Ref | QnA id |", "|---|---|", *[f"| {k} | {v} |" for k, v in self.report["created_ids"].items()]]
        (self.out / "MIGRATION-RUN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    return LocalMigration(parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
