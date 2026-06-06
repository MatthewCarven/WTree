"""Lint gate: pyflakes must be clean over the package and tests.

Keeps unused imports / locals from accruing between sweeps (todo.md
Code-health follow-up, swept 2026-06-07). Skips when pyflakes isn't
installed so the suite has no hard new dependency.

``wtree/error_handler.py`` is excluded: it's vendored verbatim from
the sibling Python ErrorHandler project (see its provenance header);
lint fixes there belong upstream, not in the vendored copy.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyflakes")

REPO = Path(__file__).resolve().parent.parent
VENDORED = {REPO / "wtree" / "error_handler.py"}


def _py_files(*roots: str) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        out.extend(
            p for p in sorted((REPO / root).rglob("*.py"))
            if p not in VENDORED and "__pycache__" not in p.parts
        )
    return out


def test_pyflakes_clean() -> None:
    files = _py_files("wtree", "tests")
    assert files, "no files collected - repo layout changed?"
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", *map(str, files)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "pyflakes found issues:\n" + proc.stdout + proc.stderr
    )
