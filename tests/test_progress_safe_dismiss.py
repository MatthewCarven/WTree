"""Tests for ``ProgressScreen.safe_dismiss`` (double-dismiss guard).

Same latent crash shape as the ScanScreen launch crash fixed
2026-06-05: three callers race to pop the modal (the redraw timer's
plan-moved-on auto-dismiss, Esc, and minimize); Textual's ``dismiss``
pops unconditionally, so the loser pops the base ``_default`` screen
and raises ``ScreenStackError``. ``safe_dismiss`` gates on an
idempotency flag + actual stack membership.

Also pins the ScanScreen guard itself, which shipped untested.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from wtree.app import WTreeApp
from wtree.ops import OperationQueue
from wtree.ops.base import OperationKind
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.widgets.progress_screen import ProgressScreen
from wtree.widgets.scan_screen import ScanContext, ScanScreen


def _entry(name: str, kind: Kind, size: int = 0) -> Entry:
    return Entry(
        name=name, kind=kind, size=size,
        mtime=datetime(2026, 1, 1, 0, 0, 0),
    )


def _fake_queue() -> MagicMock:
    fake_plan = SimpleNamespace(
        kind=OperationKind.COPY, items=(), total_bytes=0,
    )
    queue = MagicMock(spec=OperationQueue)
    queue.running = fake_plan
    queue.depth = 1
    queue.cancel_requested = False
    queue.bytes_progress = (0, 0)
    queue.elapsed_seconds = 0.0
    queue.running_progress = (0, 1)
    return queue


@pytest.fixture
def mock_app(tmp_path: Path) -> WTreeApp:
    root = str(tmp_path)
    src = MockSource(
        contents={root: [_entry("file.txt", Kind.FILE, 100)]},
    )
    return WTreeApp(source=src, root_path=root)


# ---------------------------------------------------------------------------
# Static surface
# ---------------------------------------------------------------------------


def test_progress_screen_has_safe_dismiss() -> None:
    assert hasattr(ProgressScreen, "safe_dismiss")
    assert callable(ProgressScreen.safe_dismiss)


def test_no_bare_dismiss_callers_left() -> None:
    """Every dismiss site in progress_screen.py routes via safe_dismiss.

    Source-level pin so a future edit reintroducing a bare
    ``self.dismiss(None)`` outside ``safe_dismiss`` fails loudly.
    """
    import inspect
    import wtree.widgets.progress_screen as mod

    src = inspect.getsource(mod)
    guard_src = inspect.getsource(ProgressScreen.safe_dismiss)
    assert src.count("self.dismiss(None)") == 1
    assert "self.dismiss(None)" in guard_src


# ---------------------------------------------------------------------------
# Pilot integration
# ---------------------------------------------------------------------------


async def test_safe_dismiss_idempotent_double_call(
    mock_app: WTreeApp,
) -> None:
    """Two direct safe_dismiss calls pop once; base screen survives."""
    async with mock_app.run_test() as pilot:
        await pilot.pause()
        mock_app.op_queue = _fake_queue()
        mock_app.action_show_progress()
        await pilot.pause()
        dialog = next(
            s for s in mock_app.screen_stack
            if isinstance(s, ProgressScreen)
        )
        depth_before = len(mock_app.screen_stack)

        dialog.safe_dismiss()
        dialog.safe_dismiss()  # would ScreenStackError via bare dismiss
        await pilot.pause()

        assert not any(
            isinstance(s, ProgressScreen) for s in mock_app.screen_stack
        )
        assert len(mock_app.screen_stack) == depth_before - 1


async def test_esc_then_timer_race_no_crash(mock_app: WTreeApp) -> None:
    """Esc (queue already idle) + timer auto-dismiss both fire: one pop.

    Reproduces the crash shape: plan completes (queue.running moves
    off our plan), the user hits Esc in the same frame the redraw
    timer's _refresh fires. Both want to dismiss.
    """
    async with mock_app.run_test() as pilot:
        await pilot.pause()
        queue = _fake_queue()
        mock_app.op_queue = queue
        mock_app.action_show_progress()
        await pilot.pause()
        dialog = next(
            s for s in mock_app.screen_stack
            if isinstance(s, ProgressScreen)
        )
        depth_before = len(mock_app.screen_stack)

        # Plan moves on -> Esc takes the dismiss path (running is None,
        # so no cancel-request branch) and the timer callback follows.
        queue.running = None
        dialog.action_cancel_or_dismiss()
        dialog._refresh()
        dialog._refresh()
        await pilot.pause()

        assert not any(
            isinstance(s, ProgressScreen) for s in mock_app.screen_stack
        )
        assert len(mock_app.screen_stack) == depth_before - 1


async def test_minimize_then_timer_race_no_crash(
    mock_app: WTreeApp,
) -> None:
    """Minimize + timer auto-dismiss in the same frame: one pop."""
    async with mock_app.run_test() as pilot:
        await pilot.pause()
        queue = _fake_queue()
        mock_app.op_queue = queue
        mock_app.action_show_progress()
        await pilot.pause()
        dialog = next(
            s for s in mock_app.screen_stack
            if isinstance(s, ProgressScreen)
        )
        depth_before = len(mock_app.screen_stack)

        queue.running = None  # plan finished
        dialog.action_minimize()
        dialog._refresh()
        await pilot.pause()

        assert len(mock_app.screen_stack) == depth_before - 1
        # Minimize semantics intact: queue untouched.
        queue.request_cancel.assert_not_called()


async def test_first_esc_still_cancels_not_dismisses(
    mock_app: WTreeApp,
) -> None:
    """Guard must not swallow the cancel branch: first Esc on a
    running, uncancelled queue requests cancel and keeps the dialog."""
    async with mock_app.run_test() as pilot:
        await pilot.pause()
        queue = _fake_queue()
        mock_app.op_queue = queue
        mock_app.action_show_progress()
        await pilot.pause()
        dialog = next(
            s for s in mock_app.screen_stack
            if isinstance(s, ProgressScreen)
        )

        dialog.action_cancel_or_dismiss()
        await pilot.pause()

        queue.request_cancel.assert_called_once()
        assert any(
            isinstance(s, ProgressScreen) for s in mock_app.screen_stack
        )


# ---------------------------------------------------------------------------
# ScanScreen guard pins (shipped 2026-06-05 without dedicated tests)
# ---------------------------------------------------------------------------


def test_scan_screen_has_safe_dismiss() -> None:
    assert hasattr(ScanScreen, "safe_dismiss")
    assert callable(ScanScreen.safe_dismiss)


async def test_scan_screen_safe_dismiss_idempotent(
    mock_app: WTreeApp,
) -> None:
    async with mock_app.run_test() as pilot:
        await pilot.pause()
        ctx = ScanContext(path=str(mock_app._root_path), method_label="mock")
        screen = ScanScreen(ctx)
        mock_app.push_screen(screen)
        await pilot.pause()
        depth_before = len(mock_app.screen_stack)

        screen.safe_dismiss()
        screen.safe_dismiss()
        await pilot.pause()

        assert not any(
            isinstance(s, ScanScreen) for s in mock_app.screen_stack
        )
        assert len(mock_app.screen_stack) == depth_before - 1
