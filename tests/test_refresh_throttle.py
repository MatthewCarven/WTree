"""Tests for the Ctrl+R throttle + Refreshing header (design.md 2026-06-07).

Rapid re-presses coalesce (window + in-flight guard, both silent);
the scan-dialog gate gets header="Refreshing" so a slow re-scan's
dialog says what's happening; the "Source refreshed." confirmation
is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import REFRESH_THROTTLE_SECONDS, WTreeApp
from wtree.widgets.status_line import StatusLine
from wtree.widgets.tree_pane import TreePane


def test_throttle_constant() -> None:
    assert REFRESH_THROTTLE_SECONDS == 0.2


def _stage(tmp_path: Path) -> str:
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "f.txt").write_text("x")
    return str(root)


def _spy_refresh_all(monkeypatch) -> list[int]:
    calls: list[int] = []
    original = TreePane.refresh_all

    async def counting(self, *a, **kw):
        calls.append(1)
        return await original(self, *a, **kw)

    monkeypatch.setattr(TreePane, "refresh_all", counting)
    return calls


async def test_rapid_double_press_coalesces(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _spy_refresh_all(monkeypatch)
    app = WTreeApp(root_path=_stage(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.press("ctrl+r")  # inside the window
        await pilot.pause()
        await pilot.pause()
        assert len(calls) == 1


async def test_press_after_window_runs_again(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _spy_refresh_all(monkeypatch)
    app = WTreeApp(root_path=_stage(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        # Age the window artificially rather than sleeping the suite.
        app._last_refresh_started -= REFRESH_THROTTLE_SECONDS + 0.05
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.pause()
        assert len(calls) == 2


async def test_in_flight_guard_absorbs_press(
    tmp_path: Path, monkeypatch
) -> None:
    """A press while the refresh is still running no-ops even when the
    window has aged out (slow refresh on a big tree)."""
    import asyncio

    calls: list[int] = []
    release = asyncio.Event()
    original = TreePane.refresh_all

    async def slow(self, *a, **kw):
        calls.append(1)
        await release.wait()
        return await original(self, *a, **kw)

    monkeypatch.setattr(TreePane, "refresh_all", slow)
    app = WTreeApp(root_path=_stage(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        app._last_refresh_started -= REFRESH_THROTTLE_SECONDS + 0.05
        await pilot.press("ctrl+r")  # in-flight: must be absorbed
        await pilot.pause()
        release.set()
        await pilot.pause()
        await pilot.pause()
        assert len(calls) == 1
        assert app._refresh_running is False  # flag cleared via finally


async def test_gate_called_with_refreshing_header(
    tmp_path: Path, monkeypatch
) -> None:
    headers: list[str] = []
    original = WTreeApp._run_scan_with_dialog

    async def spying(self, *a, header="Scanning", **kw):
        headers.append(header)
        return await original(self, *a, header=header, **kw)

    monkeypatch.setattr(WTreeApp, "_run_scan_with_dialog", spying)
    app = WTreeApp(root_path=_stage(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        headers.clear()  # ignore mount-time scans
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.pause()
        assert headers == ["Refreshing", "Refreshing"]  # contents + tree


async def test_confirmation_flash_unchanged(tmp_path: Path) -> None:
    app = WTreeApp(root_path=_stage(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.pause()
        status = app.query_one(StatusLine)
        assert status._flash_message == "Source refreshed."
        assert status._flash_severity == "info"
