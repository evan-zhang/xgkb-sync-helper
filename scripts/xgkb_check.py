#!/usr/bin/env python3
"""
xgkb_check.py — local quality gate for xgkb-sync-helper.

Runs only local, no-network checks:
  - Python syntax compilation
  - regression tests
  - CLI smoke checks
  - legacy import compatibility checks
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run_step(name: str, cmd: list[str], fail_fast: bool = False) -> bool:
    started = time.perf_counter()
    print(f"[xgkb-check] {name} ...")
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    elapsed = time.perf_counter() - started
    if proc.returncode == 0:
        print(f"[xgkb-check] OK {name} ({elapsed:.2f}s)")
        return True

    print(f"[xgkb-check] FAIL {name} ({elapsed:.2f}s)", file=sys.stderr)
    if proc.stdout:
        print(proc.stdout, file=sys.stderr)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if fail_fast:
        raise SystemExit(proc.returncode)
    return False


def build_steps(skip_tests: bool) -> list[tuple[str, list[str]]]:
    steps: list[tuple[str, list[str]]] = [
        (
            "py_compile",
            [PY, "-m", "py_compile", *map(str, sorted((ROOT / "scripts").glob("*.py"))),
             *map(str, sorted((ROOT / "tests").glob("*.py")))],
        ),
        (
            "legacy_imports",
            [
                PY,
                "-c",
                "import sys; sys.path.insert(0, 'scripts'); "
                "import xgkb_push, xgkb_retry, xgkb_sync_dir",
            ],
        ),
        (
            "cli_help_sync_full",
            [PY, "scripts/xgkb_sync_full.py", "--help"],
        ),
        (
            "cli_help_sync_dir",
            [PY, "scripts/xgkb_sync_dir.py", "--help"],
        ),
        (
            "cli_help_versions",
            [PY, "scripts/xgkb_versions.py", "--help"],
        ),
        (
            "cli_help_migrate",
            [PY, "scripts/migrate_json_to_sqlite.py", "migrate", "--help"],
        ),
    ]
    if not skip_tests:
        steps.insert(1, ("unit_tests", [PY, "-m", "unittest", "discover", "-s", "tests", "-v"]))
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local xgkb quality checks")
    parser.add_argument("--skip-tests", action="store_true", help="skip unittest regression suite")
    parser.add_argument("--fail-fast", action="store_true", help="stop on first failed step")
    args = parser.parse_args()

    started = time.perf_counter()
    results = []
    for name, cmd in build_steps(skip_tests=args.skip_tests):
        results.append(run_step(name, cmd, fail_fast=args.fail_fast))

    elapsed = time.perf_counter() - started
    passed = sum(1 for ok in results if ok)
    total = len(results)
    print(f"[xgkb-check] summary: {passed}/{total} passed ({elapsed:.2f}s)")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
