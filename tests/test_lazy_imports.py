"""Every import written inside a function resolves.

Many modules (admin route handlers above all) import lazily, inside the
function body. Such an import breaks only when the function runs, so a removed
module or a renamed name slips past ruff, mypy and the tests that don't call
that handler — as happened with ``from services.license import ...`` in the
link extraction handler. This test resolves all of them statically.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ["admin", "core", "crawlers", "models", "services", "utils"]
MODULES = ["api.py", "config.py", "main.py"]


def _source_files() -> list[Path]:
    files = [ROOT / m for m in MODULES if (ROOT / m).exists()]
    for pkg in PACKAGES:
        files += sorted((ROOT / pkg).rglob("*.py"))
    return files


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    return ".".join(parts)


def _guarded_by_import_error(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True for imports inside `try: ... except ImportError` — optional on purpose."""
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                names = {getattr(handler.type, "id", None)} | {
                    getattr(e, "id", None) for e in getattr(handler.type, "elts", [])
                }
                # Only an ImportError handler marks the import optional: handlers
                # wrap their whole body in `except Exception`, which must not hide it.
                if names & {"ImportError", "ModuleNotFoundError"}:
                    return True
    return False


def _lazy_imports() -> list[tuple[str, int, str, str | None]]:
    """(file, line, module, name) for each import made inside a function."""
    found = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        package = _module_name(path)
        if path.name != "__init__.py":
            package = package.rpartition(".")[0]
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if _guarded_by_import_error(node, parents):
                    continue
                rel = str(path.relative_to(ROOT))
                if isinstance(node, ast.Import):
                    found += [(rel, node.lineno, alias.name, None) for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if node.level:
                        base = package.split(".") if package else []
                        base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                        module = ".".join([*base, module] if module else base)
                    found += [(rel, node.lineno, module, alias.name) for alias in node.names]
    return sorted(set(found))


IMPORTS = _lazy_imports()


def test_lazy_imports_are_found():
    # Guards the collector itself: the codebase has dozens of lazy imports.
    assert len(IMPORTS) > 50


@pytest.mark.parametrize(("file", "line", "module", "name"), IMPORTS, ids=lambda v: str(v))
def test_lazy_import_resolves(file, line, module, name):
    try:
        imported = importlib.import_module(module)
    except ImportError as exc:
        pytest.fail(f"{file}:{line}: cannot import {module!r}: {exc}")
    if name is not None and name != "*" and not hasattr(imported, name):
        try:
            importlib.import_module(f"{module}.{name}")  # `from package import submodule`
        except ImportError:
            pytest.fail(f"{file}:{line}: {module!r} has no {name!r}")
