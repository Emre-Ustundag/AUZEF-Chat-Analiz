"""AUZEF KB konsolidasyon v3.1-final migration CLI'ı.

Runner (``kb_migration_v31_runner.py``) backend konteynerinde çalışır; bu
CLI plan/backup dosyalarını hazırlar, çıktıları diske yazar ve güvenlik
kapılarını uygular.

Akış:
  dry-run   → canlı DB'de rollback edilen tam rehearsal (yazma yok)
  backup    → satır snapshot'ı + pg_dump (apply için zorunlu)
  apply     → --confirm <plan digest> ister; birim başına commit + journal,
              ardından index-sync ve verify
  verify    → canlı DB'yi backup + plana karşı doğrular
  rollback  → snapshot farkıyla geri alır, digest eşitliğini doğrular, indeksi eşitler
  index-sync→ verilen QnA id'lerini Meili/Qdrant'ta DB ile eşitler ve doğrular
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNNER_PATH = Path(__file__).with_name("kb_migration_v31_runner.py")
BACKUP_TABLES = ("qna", "qna_queries", "qna_routing_guards", "qna_tags")


def file_digest(path: Path) -> str:
    return hashlib.blake2b(path.read_bytes(), digest_size=32).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["dry-run", "backup", "apply", "verify", "rollback", "index-sync"])
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--compose-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--confirm", help="apply için plan dosyasının blake2b digest'inin ilk 16 karakteri")
    parser.add_argument("--skip-index", action="store_true", help="Yalnız izole rehearsal DB'si için")
    parser.add_argument("--ids", help="index-sync için virgülle ayrılmış QnA id'leri")
    parser.add_argument("--exec-env", action="append", default=[], metavar="KEY=VALUE",
                        help="Backend runner'ına geçirilecek ortam değişkeni (ör. rehearsal DATABASE_URL)")
    parser.add_argument("--pg-database", help="pg_dump hedef DB adı (varsayılan: db konteynerindeki POSTGRES_DB)")
    return parser.parse_args(argv)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"HATA: {message}")


def run_runner(options: argparse.Namespace, payload: dict[str, Any], journal: Path | None = None) -> tuple[int, dict[str, Any]]:
    command = ["docker", "compose", "exec", "-T"]
    for item in options.exec_env:
        command += ["-e", item]
    command += ["backend", "python", "-c", RUNNER_PATH.read_text(encoding="utf-8")]
    process = subprocess.Popen(
        command, cwd=options.compose_root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8"
    )
    assert process.stdin and process.stdout
    process.stdin.write(json.dumps(payload, ensure_ascii=False))
    process.stdin.close()
    result: dict[str, Any] = {}
    handle = journal.open("a", encoding="utf-8") if journal else None
    try:
        for line in process.stdout:
            message = json.loads(line)
            if message["type"] == "journal" and handle:
                handle.write(line)
                handle.flush()
                print(f"  {message['status']:<9} {message['unit']}", flush=True)
            elif message["type"] in {"result", "snapshot"}:
                result = message
    finally:
        if handle:
            handle.close()
    return process.wait(), result


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_plan(options: argparse.Namespace) -> dict[str, Any]:
    require(options.plan is not None and options.plan.is_file(), "--plan dosyası gerekli")
    return json.loads(options.plan.read_text(encoding="utf-8"))


def load_backup(options: argparse.Namespace) -> dict[str, Any]:
    require(options.backup_dir is not None, "--backup-dir gerekli")
    manifest_path = options.backup_dir / "manifest.json"
    require(manifest_path.is_file(), f"backup manifest yok: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot_path = options.backup_dir / "snapshot.json"
    require(file_digest(snapshot_path) == manifest["snapshot_file_blake2b"], "snapshot.json manifest ile eşleşmiyor")
    dump_path = options.backup_dir / manifest["pg_dump_file"]
    require(dump_path.is_file() and file_digest(dump_path) == manifest["pg_dump_blake2b"], "pg_dump dosyası manifest ile eşleşmiyor")
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def render_dry_run_markdown(report: dict[str, Any], plan_digest: str) -> str:
    units = report.get("units", [])
    by_kind: dict[str, int] = {}
    for unit in units:
        by_kind[unit.get("kind", "?")] = by_kind.get(unit.get("kind", "?"), 0) + 1
    guarded = [u for u in units if u.get("guard_ref")]
    integrity = report.get("integrity", {})
    rollback = report.get("rollback_rehearsal", {})
    lines = [
        "# AUZEF KB v3.1-final migration — dry-run",
        "",
        f"Sonuç: **{report['status']}** · canlı DB değişmedi: **{report['live_unchanged']}**",
        "",
        f"Plan digest: `{plan_digest}`",
        f"Snapshot digest: `{report.get('before_digest')}`",
        "",
        "## Önkoşullar",
        "",
        "- Hata yok" if not report.get("preflight_errors") else "",
        *[f"- `{e['code']}`: {json.dumps({k: v for k, v in e.items() if k != 'code'}, ensure_ascii=False)}"
          for e in report.get("preflight_errors", [])],
        "",
        "## Birimler (her biri ayrı transaction)",
        "",
        "| Tür | Adet |",
        "|---|---|",
        *[f"| {kind} | {count} |" for kind, count in by_kind.items()],
        "",
        f"Toplam alias taşıması: {sum(len(u.get('alias_moves') or []) for u in units)}",
        f"Guard yazılan birim: {len(guarded)}",
        "",
        "| Birim | QnA id | Guard | Alias | Durum |",
        "|---|---|---|---|---|",
        *[f"| {u['unit']} | {u.get('qna_id', '')}{' (yeni)' if u.get('created') else ''} | {u.get('guard_ref', '')} | "
          f"{len(u.get('alias_moves') or [])} | {u['status']}{' — ' + u['error'] if u.get('error') else ''} |" for u in units],
        "",
        "Not: Yeni kayıt id'leri rollback edilen transaction'dan gelir; gerçek apply'da farklı olacaktır.",
        "",
        "## Bütünlük kontrolleri",
        "",
        f"- Durum: **{integrity.get('status')}** ({integrity.get('passes')} kontrol geçti)",
        *[f"- BAŞARISIZ `{f['check']}`: {json.dumps({k: v for k, v in f.items() if k != 'check'}, ensure_ascii=False)}"
          for f in integrity.get("failures", [])],
        "",
        "## Rollback rehearsal",
        "",
        f"- Durum: **{rollback.get('status')}**",
        f"- Geri yükleme sonrası digest: `{rollback.get('restored_digest', rollback.get('error'))}`",
        f"- İşlem sayıları: `{json.dumps(rollback.get('counts', {}), ensure_ascii=False)}`",
        "",
        "## İndeks planı",
        "",
        f"- Yeniden indekslenecek QnA: {len(report.get('index_plan', {}).get('reindex_ids', []))}",
        f"- Strateji: {report.get('index_plan', {}).get('strategy', '')}",
        "",
    ]
    return "\n".join(line for line in lines if line is not None)


def mode_dry_run(options: argparse.Namespace) -> int:
    plan = load_plan(options)
    plan_digest = file_digest(options.plan)
    code, message = run_runner(options, {"mode": "dry-run", "plan": plan})
    require(bool(message), "runner sonuç üretmedi")
    report = {**message["report"], "plan_file": str(options.plan), "plan_blake2b": plan_digest,
              "generated_at": datetime.now(timezone.utc).isoformat()}
    options.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(options.out_dir / "migration-dry-run-report.json", report)
    (options.out_dir / "migration-dry-run-report.md").write_text(render_dry_run_markdown(report, plan_digest), encoding="utf-8")
    print(json.dumps({"status": report["status"], "live_unchanged": report["live_unchanged"],
                      "units": len(report.get("units", [])), "integrity": report.get("integrity", {}).get("status"),
                      "rollback_rehearsal": report.get("rollback_rehearsal", {}).get("status"),
                      "plan_confirm": plan_digest[:16], "out_dir": str(options.out_dir)}, ensure_ascii=False, indent=2))
    return code


def mode_backup(options: argparse.Namespace) -> int:
    backup_dir = options.out_dir
    require(not backup_dir.exists() or not any(backup_dir.iterdir()), f"backup dizini boş değil: {backup_dir}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    code, message = run_runner(options, {"mode": "snapshot"})
    require(code == 0 and message.get("type") == "snapshot", "snapshot alınamadı")
    snapshot = message["snapshot"]
    write_json(backup_dir / "snapshot.json", snapshot)

    database = options.pg_database or "$POSTGRES_DB"
    tables = " ".join(f"-t {table}" for table in BACKUP_TABLES)
    dump_path = backup_dir / "admin-qna-tables.dump"
    with dump_path.open("wb") as handle:
        subprocess.run(
            ["docker", "compose", "exec", "-T", "db", "sh", "-c",
             f'pg_dump -U "$POSTGRES_USER" -d "{database}" -Fc {tables}'],
            cwd=options.compose_root, stdout=handle, check=True,
        )
    listing = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "pg_restore", "--list"],
        cwd=options.compose_root, input=dump_path.read_bytes(), capture_output=True, check=True,
    ).stdout.decode("utf-8")
    missing = [table for table in BACKUP_TABLES if f"TABLE DATA public {table} " not in listing]
    require(not missing, f"pg_dump içinde tablo verisi eksik: {missing}")
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_digest": snapshot["digest"],
        "snapshot_file_blake2b": file_digest(backup_dir / "snapshot.json"),
        "pg_dump_file": dump_path.name,
        "pg_dump_blake2b": file_digest(dump_path),
        "pg_dump_tables": list(BACKUP_TABLES),
        "counts": {key: len(snapshot[key]) for key in ("qna", "aliases", "guards")},
        "restore_hint": "Önce rollback modu denenir. O başarısızsa bu dump bakım penceresinde, uygulama "
                        "durdurulmuş hâlde ve önce ayrı bir rehearsal DB'sinde doğrulanarak restore edilir; "
                        "ardından index-sync tüm aktif QnA id'leriyle çalıştırılır.",
    }
    write_json(backup_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def mode_apply(options: argparse.Namespace) -> int:
    plan = load_plan(options)
    plan_digest = file_digest(options.plan)
    require(options.confirm == plan_digest[:16], f"--confirm plan digest'inin ilk 16 karakteri olmalı ({plan_digest[:16]})")
    backup = load_backup(options)
    options.out_dir.mkdir(parents=True, exist_ok=True)
    journal = options.out_dir / "journal.ndjson"
    require(not journal.exists(), f"journal zaten var, önceki apply'ı incele: {journal}")
    code, message = run_runner(options, {"mode": "apply", "plan": plan, "backup_snapshot": backup}, journal)
    report = message.get("report", {"status": "NO_RESULT"})
    write_json(options.out_dir / "apply-report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "integrity"}, ensure_ascii=False, indent=2))
    if code != 0:
        print("Apply tamamlanmadı. Geri almak için: rollback --plan ... --backup-dir ...", file=sys.stderr)
        return code
    if options.skip_index:
        return 0
    return index_sync(options, report["index_ids"], "index-sync-report.json")


def index_sync(options: argparse.Namespace, ids: list[int], filename: str) -> int:
    code, message = run_runner(options, {"mode": "index-sync", "ids": ids})
    report = message.get("report", {"status": "NO_RESULT"})
    options.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(options.out_dir / filename, report)
    print(json.dumps({k: report.get(k) for k in ("status", "active", "removed", "failures")}, ensure_ascii=False, indent=2))
    return code


def mode_verify(options: argparse.Namespace) -> int:
    plan, backup = load_plan(options), load_backup(options)
    code, message = run_runner(options, {"mode": "verify", "plan": plan, "backup_snapshot": backup})
    options.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(options.out_dir / "verify-report.json", message.get("report"))
    integrity = message.get("report", {}).get("integrity", {})
    print(json.dumps({"status": integrity.get("status"), "passes": integrity.get("passes"),
                      "failures": integrity.get("failures")}, ensure_ascii=False, indent=2))
    return code


def mode_rollback(options: argparse.Namespace) -> int:
    plan, backup = load_plan(options), load_backup(options)
    code, message = run_runner(options, {"mode": "rollback", "plan": plan, "backup_snapshot": backup})
    report = message.get("report", {"status": "NO_RESULT"})
    options.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(options.out_dir / "rollback-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if code != 0 or options.skip_index:
        return code
    return index_sync(options, report["reindex_ids"], "rollback-index-sync-report.json")


def mode_index_sync(options: argparse.Namespace) -> int:
    require(bool(options.ids), "--ids gerekli")
    return index_sync(options, [int(item) for item in options.ids.split(",")], "index-sync-report.json")


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    # Başka bir DB'ye yönlendirilmiş rehearsal, paylaşılan Meili/Qdrant'a asla yazmamalı.
    redirected = any(item.split("=", 1)[0].endswith("DATABASE_URL") for item in options.exec_env)
    require(not (redirected and (options.mode == "index-sync" or not options.skip_index) and options.mode in {"apply", "rollback", "index-sync"}),
            "DATABASE_URL yönlendirilmişken indeks senkronu yapılamaz; --skip-index kullan")
    handlers = {
        "dry-run": mode_dry_run,
        "backup": mode_backup,
        "apply": mode_apply,
        "verify": mode_verify,
        "rollback": mode_rollback,
        "index-sync": mode_index_sync,
    }
    return handlers[options.mode](options)


if __name__ == "__main__":
    raise SystemExit(main())
