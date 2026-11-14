"""Layering and determinism rules from AGENTS.md §3, enforced on the source tree.

``.importlinter`` checks the import graph. This test adds what import-linter cannot see:
imports hidden under ``TYPE_CHECKING`` or ``importlib.import_module``, banned stdlib modules
(``random``, ``time``...), and float literals / ``float()`` calls in the simulation kernel.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import pymoonstone

SRC = Path(pymoonstone.__file__).parent

# Every package under src/pymoonstone must belong to a declared layer.
LAYERS = ("cli", "app", "render", "assets", "sim")

FORBIDDEN_BY_LAYER: dict[str, tuple[str, ...]] = {
    "sim": (
        "pymoonstone.assets",
        "pymoonstone.render",
        "pymoonstone.app",
        "pymoonstone.cli",
        "pygame",
        "moderngl",
        "PIL",
        "amitools",
        "random",
        "time",
        "datetime",
        "os",
        "io",
        "pathlib",
        "shutil",
        "subprocess",
        "socket",
        "threading",
        "secrets",
        "uuid",
    ),
    "assets": ("pymoonstone.render", "pymoonstone.app", "pymoonstone.cli", "pygame", "moderngl"),
    "render": ("pymoonstone.app", "pymoonstone.cli", "pygame", "amitools"),
    "app": ("pymoonstone.cli",),
}


def _imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    """Every absolute module a tree imports, from any statement or ``import_module`` call."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
            if node.module == "pymoonstone":
                found.extend((node.lineno, f"pymoonstone.{alias.name}") for alias in node.names)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.append((node.lineno, node.args[0].value))
    return found


def _is_float_usage(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, float | complex)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"float", "complex"}
    )


def _float_usages(tree: ast.AST) -> list[int]:
    """Line numbers of float literals and ``float(...)`` calls."""
    return [node.lineno for node in ast.walk(tree) if _is_float_usage(node)]


def _matches(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_every_package_is_a_declared_layer() -> None:
    packages = sorted(p.name for p in SRC.iterdir() if (p / "__init__.py").is_file())
    assert packages == sorted(LAYERS)


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_BY_LAYER))
def test_layer_does_not_import_forbidden_modules(layer: str) -> None:
    forbidden = FORBIDDEN_BY_LAYER[layer]
    offenders = [
        f"{path.relative_to(SRC)}:{line} imports {module}"
        for path in sorted((SRC / layer).rglob("*.py"))
        for line, module in _imported_modules(_parse(path))
        if _matches(module, forbidden)
    ]
    assert offenders == []


def test_sim_has_no_floats() -> None:
    offenders = [
        f"{path.relative_to(SRC)}:{line}"
        for path in sorted((SRC / "sim").rglob("*.py"))
        for line in _float_usages(_parse(path))
    ]
    assert offenders == []


def test_checker_catches_hidden_imports_and_floats() -> None:
    sample = ast.parse(
        "from typing import TYPE_CHECKING\n"
        "import importlib\n"
        "if TYPE_CHECKING:\n"
        "    from pymoonstone.render.canvas import Canvas\n"
        "def f():\n"
        "    import random\n"
        "    importlib.import_module('pygame.mixer')\n"
        "    return float(3) + 0.5\n"
    )
    modules = [m for _, m in _imported_modules(sample) if _matches(m, FORBIDDEN_BY_LAYER["sim"])]
    assert modules == ["pymoonstone.render.canvas", "random", "pygame.mixer"]
    assert sorted(_float_usages(sample)) == [8, 8]
