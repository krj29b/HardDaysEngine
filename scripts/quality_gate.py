#!/usr/bin/env python3
"""Strict Quality Gate runner for harddaysengine.

Every step must exit 0. Static analysis gates run concurrently with buffered output so their
diagnostics never interleave, followed by the parallel ``pytest-xdist`` deterministic test suite
(ADR-072). ``radon cc`` always exits 0 by itself, so its step runs radon in JSON mode and fails
when any block is graded C or worse (cyclomatic complexity > 10).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

RADON_ALLOWED_RANKS = frozenset({"A", "B"})


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class GateStep:
    name: str
    run: Callable[[], GateResult]


def _command(name: str, cmd: list[str]) -> GateStep:
    def run() -> GateResult:
        t0 = time.monotonic()
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        return GateResult(
            name=name,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_s=time.monotonic() - t0,
        )

    return GateStep(name=name, run=run)


def _radon_gate() -> GateResult:
    """Fail when any function, method or class is graded C or worse by radon."""
    name = "Radon Cyclomatic Complexity Gate (Grade A/B only)"
    t0 = time.monotonic()
    proc = subprocess.run(
        ["uv", "run", "radon", "cc", "src/harddaysengine", "-j"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return GateResult(name, proc.returncode, proc.stdout, proc.stderr, time.monotonic() - t0)
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
    block_count = sum(len(b) for b in report.values() if isinstance(b, list))
    lines = [*offenders, f"{block_count} blocks analysed, {len(offenders)} graded C or worse."]
    stdout = "\n".join(lines) + "\n"
    return GateResult(name, 1 if offenders else 0, stdout, "", time.monotonic() - t0)


STATIC_STEPS: tuple[GateStep, ...] = (
    _command(
        "Ruff Format Check",
        ["uv", "run", "ruff", "format", "--check", "src", "tests", "scripts"],
    ),
    _command(
        "Ruff Lint & McCabe Complexity (<=10)",
        ["uv", "run", "ruff", "check", "src", "tests", "scripts"],
    ),
    _command(
        "Pyrefly Type Check",
        ["uv", "run", "pyrefly", "check", "--progress-bar", "no"],
    ),
    _command("Import-Linter Architectural Boundaries", ["uv", "run", "lint-imports"]),
    GateStep("Radon Cyclomatic Complexity Gate (Grade A/B only)", _radon_gate),
    _command("Vulture Dead Code Check", ["uv", "run", "vulture", "src/harddaysengine"]),
)

PYTEST_CMD: tuple[str, ...] = (
    "uv",
    "run",
    "pytest",
    "-n",
    "auto",
    "--dist",
    "worksteal",
    "-m",
    "not gl",
    "--cov=harddaysengine",
    "--cov-report=term-missing",
)


def _emit_result(result: GateResult) -> None:
    print(f"\n=== [GATE] {result.name} ({result.elapsed_s:.2f}s) ===", flush=True)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr, flush=True)


def _run_static_gates() -> int:
    with ThreadPoolExecutor(max_workers=len(STATIC_STEPS)) as pool:
        futures = [pool.submit(step.run) for step in STATIC_STEPS]
        results = [future.result() for future in futures]

    failed_name: str | None = None
    failed_code = 0
    for result in results:
        _emit_result(result)
        if result.returncode != 0 and failed_name is None:
            failed_name = result.name
            failed_code = result.returncode
    if failed_name is not None:
        print(f"\n[FAILED] Quality Gate halted at: {failed_name}", file=sys.stderr)
        return failed_code
    return 0


def main() -> int:
    t0 = time.monotonic()
    static_rc = _run_static_gates()
    if static_rc != 0:
        return static_rc

    print("\n=== [GATE] Pytest Deterministic Test Suite (parallel) ===", flush=True)
    pytest_rc = subprocess.run(list(PYTEST_CMD), check=False).returncode
    if pytest_rc != 0:
        print(
            "\n[FAILED] Quality Gate halted at: Pytest Deterministic Test Suite",
            file=sys.stderr,
        )
        return pytest_rc

    total_s = time.monotonic() - t0
    print(f"\n=== [PASSED] All harddaysengine Quality Gates succeeded in {total_s:.2f}s. ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
