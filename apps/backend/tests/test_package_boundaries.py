"""Domain/service paketlerinin framework adapter'larından bağımsızlığı."""

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

FORBIDDEN_IMPORTS = {
    "domain": {"fastapi", "starlette", "celery", "app.api", "app.services", "app.workers"},
    "services": {"fastapi", "starlette", "celery", "app.api", "app.workers"},
}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(APP_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(["app", *parts])


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    current_module = _module_name(path)
    current_package = (
        current_module if path.name == "__init__.py" else current_module.rsplit(".", 1)[0]
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package_parts = current_package.split(".")
                anchor = package_parts[: len(package_parts) - (node.level - 1)]
                imported_parts = [*anchor, *(node.module or "").split(".")]
                imports.add(".".join(part for part in imported_parts if part))
            elif node.module:
                imports.add(node.module)
    return imports


@pytest.mark.parametrize("package", ["domain", "services"])
def test_package_dependency_direction(package: str) -> None:
    violations: list[str] = []
    for path in sorted((APP_ROOT / package).rglob("*.py")):
        for imported in _imports(path):
            for forbidden in FORBIDDEN_IMPORTS[package]:
                if imported == forbidden or imported.startswith(f"{forbidden}."):
                    violations.append(f"{path.relative_to(APP_ROOT)} -> {imported}")
    assert violations == []
