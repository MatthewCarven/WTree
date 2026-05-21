"""Smoke tests for ``NativeSource``.

Each test creates a real temp directory, scans it, and asserts the entries
match what we put there. Async-mode-auto via pytest-asyncio (configured in
``pyproject.toml``) — no per-test decorator needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from wtree.sources.base import Entry, Kind, ScanError, ScanResult
from wtree.sources.native import NativeSource


async def _collect(stream: AsyncIterator[ScanResult]) -> list[ScanResult]:
    return [item async for item in stream]


async def test_scan_empty_dir(tmp_path: Path) -> None:
    src = NativeSource()
    results = await _collect(src.scan(str(tmp_path)))
    assert results == []


async def test_scan_one_file_and_one_dir(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("hello")
    (tmp_path / "beta").mkdir()

    src = NativeSource()
    results = await _collect(src.scan(str(tmp_path)))

    assert all(isinstance(r, Entry) for r in results), \
        f"expected only Entry objects, got: {[type(r).__name__ for r in results]}"

    by_name = {r.name: r for r in results if isinstance(r, Entry)}
    assert set(by_name) == {"alpha.txt", "beta"}

    assert by_name["alpha.txt"].kind is Kind.FILE
    assert by_name["alpha.txt"].size == len("hello")
    assert by_name["alpha.txt"].mtime is not None

    assert by_name["beta"].kind is Kind.DIR


async def test_scan_missing_dir_yields_scan_error(tmp_path: Path) -> None:
    src = NativeSource()
    bogus = str(tmp_path / "does-not-exist")
    results = await _collect(src.scan(bogus))

    assert len(results) == 1, f"expected exactly one ScanError, got {results!r}"
    err = results[0]
    assert isinstance(err, ScanError)
    assert err.path == bogus


def test_source_id_is_native() -> None:
    assert NativeSource().source_id == "native"


def test_capability_reports_permissions_supported() -> None:
    cap = NativeSource().capability
    assert cap.permissions is True
    assert cap.link_target is True
