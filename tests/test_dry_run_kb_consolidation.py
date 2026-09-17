from __future__ import annotations

from pathlib import Path

from scripts.dry_run_kb_consolidation import GUARD_COLUMNS, GUARD_TABLE, guard_capability


def backend(tmp_path: Path, *, with_code: bool) -> Path:
    services = tmp_path / "services"
    services.mkdir()
    if with_code:
        (services / "routing_guards.py").write_text(
            "class RoutingGuardPolicy: ...\ndef upsert_routing_guard(): ...\n", encoding="utf-8"
        )
        (services / "answer_pipeline.py").write_text("routing_policy = None\n", encoding="utf-8")
    return tmp_path


def snapshot(columns: set[str] | None) -> dict:
    if columns is None:
        return {"tables": ["qna", "qna_queries"], "columns": {}}
    return {"tables": ["qna", GUARD_TABLE], "columns": {GUARD_TABLE: sorted(columns)}}


def test_guard_available_when_table_columns_and_code_exist(tmp_path):
    result = guard_capability(backend(tmp_path, with_code=True), snapshot(set(GUARD_COLUMNS)))
    assert result["available"] is True
    assert result["missing_columns"] == []


def test_guard_missing_table_blocks(tmp_path):
    result = guard_capability(backend(tmp_path, with_code=True), snapshot(None))
    assert result["available"] is False
    assert result["schema_support"] is False


def test_guard_missing_column_blocks(tmp_path):
    result = guard_capability(
        backend(tmp_path, with_code=True), snapshot(set(GUARD_COLUMNS) - {"valid_until"})
    )
    assert result["available"] is False
    assert result["missing_columns"] == ["valid_until"]


def test_guard_code_only_in_tests_does_not_count(tmp_path):
    root = backend(tmp_path, with_code=False)
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text(
        "class RoutingGuardPolicy: ...\ndef upsert_routing_guard(): ...\nrouting_policy = 1\n",
        encoding="utf-8",
    )
    result = guard_capability(root, snapshot(set(GUARD_COLUMNS)))
    assert result["code_support"] is False
