#!/usr/bin/env python3
"""Strict Quality Gate runner for harddaysengine.

Every step must exit 0. ``radon cc`` always exits 0 by itself, so its step runs radon in JSON
mode and fails when any block is graded C or worse (cyclomatic complexity > 10).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

RADON_ALLOWED_RANKS = frozenset({"A", "B"})


@dataclass(frozen=True, slots=True)
class GateStep:
    name: str
    run: Callable[[], int]


def _command(cmd: list[str]) -> Callable[[], int]:
    def run() -> int:
        return subprocess.run(cmd, check=False).returncode

    return run


def _radon_gate() -> int:
    """Fail when any function, method or class is graded C or worse by radon."""
    proc = subprocess.run(
        ["uv", "run", "radon", "cc", "src/harddaysengine", "-j"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return proc.returncode
    report: dict[str, list[dict[str, object]] | dict[str, str]] = json.loads(proc.stdout)
    offenders: list[str] = []
    for path, blocks in report.items():
        if isinstance(blocks, dict):  # radon reports a parse failure as {"error": "..."}
            offenders.append(f"{path}: radon error: {blocks.get('error')}")
            continue
        offenders.extend(
            f"{path}:{block['lineno']} {block['name']} rank {block['rank']} "
            f"(complexity {block['complexity']})"
            for block in blocks
            if block["rank"] not in RADON_ALLOWED_RANKS
        )
    for line in offenders:
        print(line)
    block_count = sum(len(b) for b in report.values() if isinstance(b, list))
    print(f"{block_count} blocks analysed, {len(offenders)} graded C or worse.")
    return 1 if offenders else 0


STEPS: tuple[GateStep, ...] = (
    GateStep(
        "Ruff Format Check",
        _command(["uv", "run", "ruff", "format", "--check", "src", "tests", "scripts"]),
    ),
    GateStep(
        "Ruff Lint & McCabe Complexity (<=10)",
        _command(["uv", "run", "ruff", "check", "src", "tests", "scripts"]),
    ),
    GateStep("Mypy Strict Type Check", _command(["uv", "run", "mypy", "src"])),
    GateStep("Import-Linter Architectural Boundaries", _command(["uv", "run", "lint-imports"])),
    GateStep("Radon Cyclomatic Complexity Gate (Grade A/B only)", _radon_gate),
    GateStep("Vulture Dead Code Check", _command(["uv", "run", "vulture", "src/harddaysengine"])),
    GateStep("Pytest Deterministic Test Suite", _command(["uv", "run", "pytest", "-m", "not gl"])),
)


def main() -> int:
    for step in STEPS:
        print(f"\n=== [GATE] {step.name} ===", flush=True)
        returncode = step.run()
        if returncode != 0:
            print(f"\n[FAILED] Quality Gate halted at: {step.name}", file=sys.stderr)
            return returncode

    print("\n=== [PASSED] All harddaysengine Quality Gates succeeded. ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
