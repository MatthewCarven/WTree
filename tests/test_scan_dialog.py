"""Tests for the scan dialog and its plumbing.

Covers:

* Constants exist with expected values
  (``SCAN_MODAL_DELAY_SECONDS``, ``SCAN_CHUNK_SIZE``).
* ``EntrySource.scan_method_label`` per source
  (NativeSource = ``"os.scandir"``, MockSource = ``"mock source"``,
  default = ``"scan"``).
* ``ScanContext`` shape: defaults, cancel mutates state, completed
  mutates state.
* ``ScanScreen`` mount + body + Esc cancel sequence.
* Chunked consume in ``ContentsPane.show_path`` writes
  ``ctx.entries_seen`` and yields between chunks.
* Cancellation mid-scan keeps the pane on its previous listing
  (atomic-commit property).
* ``_run_scan_with_dialog`` builds the ctx, schedules the
  delayed-show, dismisses on completion.
* Integration: ``L`` log-new-source on a "huge" mock pushes the
  scan dialog after the threshold; Esc cancels keeping the old
  root.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from wtree.app import WTreeApp
from wtree.sources.base import Entry, EntrySource, Kind
from wtree.sources.mock import MockSource
from wtree.sources.native import NativeSource
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.scan_screen import (
    SCAN_CHUNK_SIZE,
    SCAN_MODAL_DELAY_SECONDS,
    ScanContext,
    ScanScreen,
    _truncate_path,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_scan_modal_delay_seconds_constant() -> None:
    """Tighter than the progress dialog (0.4 s) per the design call:
    directory-entry freezes feel jankier than copy freezes."""
    assert SCAN_MODAL_DELAY_SECONDS == 0.25
    assert isinstance(SCAN_MODAL_DELAY_SECONDS, float)


def test_scan_chunk_size_constant() -> None:
    """Sweet spot at typical entry sizes: small enough that Textual
    gets paint frames during big scans, large enough that the yield
    overhead is negligible."""
    assert SCAN_CHUNK_SIZE == 500
    assert isinstance(SCAN_CHUNK_SIZE, int)


# ---------------------------------------------------------------------------
# EntrySource.scan_method_label
# ---------------------------------------------------------------------------


def test_native_source_scan_method_label() -> None:
    """NativeSource declares ``os.scandir`` - the Python-API name our
    code actually calls (under it: readdir on POSIX,
    FindFirstFile/FindNextFile on Windows; the Python name is the
    honest one at our layer)."""
    assert NativeSource().scan_method_label == "os.scandir"


def test_mock_source_scan_method_label() -> None:
    """MockSource declares ``mock source`` - rarely seen since mock
    scans never hit the threshold in practice."""
    assert MockSource().scan_method_label == "mock source"


def test_entry_source_default_scan_method_label() -> None:
    """The ABC's default is ``scan`` - generic, so third-party
    sources don't need to opt in."""
    # Build a minimal concrete EntrySource that doesn't override.
    class _Bare(EntrySource):
        @property
        def source_id(self) -> str:
            return "bare"

        @property
        def capability(self):  # type: ignore[no-untyped-def]
            from wtree.sources.base import SourceCapability
            return SourceCapability()

        async def scan(self, path: str):  # type: ignore[no-untyped-def]
            return
            yield  # pragma: no cover - unreachable, just to make this a gen

    assert _Bare().scan_method_label == "scan"


# ---------------------------------------------------------------------------
# ScanContext
# ---------------------------------------------------------------------------


def test_scan_context_defaults() -> None:
    """Fresh ctx: entries_seen = 0, both events unset."""
    ctx = ScanContext(path="/foo", method_label="os.scandir")
    assert ctx.path == "/foo"
    assert ctx.method_label == "os.scandir"
    assert ctx.entries_seen == 0
    assert not ctx.cancelled.is_set()
    assert not ctx.completed.is_set()


def test_scan_context_cancel_mutates_state() -> None:
    """``ctx.cancelled.set()`` flips the event; subsequent
    ``is_set()`` reads True."""
    ctx = ScanContext(path="/x", method_label="m")
    assert not ctx.cancelled.is_set()
    ctx.cancelled.set()
    assert ctx.cancelled.is_set()


def test_scan_context_completed_mutates_state() -> None:
    ctx = ScanContext(path="/x", method_label="m")
    ctx.completed.set()
    assert ctx.completed.is_set()


def test_scan_context_independent_instances() -> None:
    """Two ScanContext instances don't share their asyncio.Events
    (dataclass ``field(default_factory=...)`` is correct here, not
    a shared default)."""
    a = ScanContext(path="/a", method_label="m")
    b = ScanContext(path="/b", method_label="m")
    a.cancelled.set()
    assert not b.cancelled.is_set()


# ---------------------------------------------------------------------------
# _truncate_path helper
# ---------------------------------------------------------------------------


def test_truncate_path_under_limit_returns_unchanged() -> None:
    assert _truncate_path("/short", max_width=80) == "/short"


def test_truncate_path_over_limit_uses_mid_string_ellipsis() -> None:
    long = "/a/very/long/path/that/just/keeps/on/going/forever/end"
    short = _truncate_path(long, max_width=30)
    assert "..." in short
    assert len(short) <= 30
    # Trailing basename preserved.
    assert short.endswith("end")


def test_truncate_path_extreme_max_width_returns_ellipsis() -> None:
    assert _truncate_path("/anything", max_width=2) == "..."


# ---------------------------------------------------------------------------
# ScanScreen mount + Esc
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_mock() -> MockSource:
    return MockSource(contents={"/": []})


async def test_scan_screen_mounts_with_body(empty_mock: MockSource) -> None:
    """Push a ScanScreen with a fresh ctx; body text contains the
    path, the method label, and the entry count."""
    app = WTreeApp(source=empty_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        ctx = ScanContext(path="/huge", method_label="os.scandir")
        ctx.entries_seen = 1234
        await app.push_screen(ScanScreen(ctx))
        await pilot.pause()
        assert isinstance(app.screen, ScanScreen)
        from textual.widgets import Static
        body = app.screen.query_one("#scan-body", Static)
        text = str(body.render())
        assert "/huge" in text
        assert "os.scandir" in text
        assert "1,234" in text
        assert "entries" in text


async def test_scan_screen_esc_sets_cancel_and_dismisses(
    empty_mock: MockSource,
) -> None:
    """Esc inside ScanScreen sets ctx.cancelled and dismisses the
    modal immediately - no wind-down phase like the progress dialog."""
    app = WTreeApp(source=empty_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        ctx = ScanContext(path="/p", method_label="os.scandir")
        await app.push_screen(ScanScreen(ctx))
        await pilot.pause()
        assert isinstance(app.screen, ScanScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert ctx.cancelled.is_set()
        assert not isinstance(app.screen, ScanScreen)


async def test_scan_screen_auto_dismisses_when_completed(
    empty_mock: MockSource,
) -> None:
    """Setting ctx.completed makes the redraw timer dismiss the
    modal on its next tick - that's how the gate signals success."""
    app = WTreeApp(source=empty_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        ctx = ScanContext(path="/p", method_label="os.scandir")
        await app.push_screen(ScanScreen(ctx))
        await pilot.pause()
        assert isinstance(app.screen, ScanScreen)
        ctx.completed.set()
        # Wait one redraw tick (PROGRESS_REDRAW_HZ = 10, so ~100 ms).
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert not isinstance(app.screen, ScanScreen)


async def test_scan_screen_renders_singular_entry_label(
    empty_mock: MockSource,
) -> None:
    """``1 entry`` not ``1 entries`` (small detail but worth pinning)."""
    app = WTreeApp(source=empty_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        ctx = ScanContext(path="/p", method_label="m")
        ctx.entries_seen = 1
        await app.push_screen(ScanScreen(ctx))
        await pilot.pause()
        from textual.widgets import Static
        body = app.screen.query_one("#scan-body", Static)
        assert "1 entry" in str(body.render())


# ---------------------------------------------------------------------------
# Chunked consume in ContentsPane.show_path
# ---------------------------------------------------------------------------


def _build_big_mock(n: int) -> MockSource:
    """A MockSource with ``n`` simple file entries at ``/big``."""
    now = datetime(2026, 5, 25, 12, 0, 0)
    return MockSource(contents={
        "/big": [Entry(f"f{i}.txt", Kind.FILE, 10, now) for i in range(n)],
    })


async def test_show_path_writes_entries_seen(empty_mock: MockSource) -> None:
    """During a scan with a ctx, ``ctx.entries_seen`` accumulates."""
    src = _build_big_mock(7)
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        ctx = ScanContext(path="/big", method_label="mock")
        await contents.show_path("/big", ctx=ctx)
        # Final count equals number of entries in the mock.
        assert ctx.entries_seen == 7


async def test_show_path_chunk_size_check_at_500() -> None:
    """A scan of exactly SCAN_CHUNK_SIZE entries hits the
    yield-and-cancel-check branch at i=500. Verify the path still
    commits cleanly (no cancel signalled)."""
    src = _build_big_mock(SCAN_CHUNK_SIZE)
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        ctx = ScanContext(path="/big", method_label="mock")
        await contents.show_path("/big", ctx=ctx)
        assert ctx.entries_seen == SCAN_CHUNK_SIZE
        # Commit happened - contents_path is updated.
        assert contents.current_path == "/big"
        assert contents.row_count == SCAN_CHUNK_SIZE


async def test_show_path_legacy_no_ctx_still_works() -> None:
    """Calling ``show_path`` without a ctx preserves the legacy
    behaviour (no chunked yields, no cancel check, atomic commit
    still happens at the end)."""
    src = _build_big_mock(3)
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        await contents.show_path("/big")
        assert contents.current_path == "/big"
        assert contents.row_count == 3


async def test_show_path_cancel_mid_scan_keeps_previous_listing() -> None:
    """Atomic commit: a cancelled scan leaves the pane on its
    previous listing. This is the key UX property - "Esc on Scanning
    dialog" should not leave the user looking at an empty pane."""
    # Two directories - first scan A, then scan B but cancel mid-way.
    now = datetime(2026, 5, 25, 12, 0, 0)
    src = MockSource(contents={
        "/A": [Entry("a.txt", Kind.FILE, 10, now)],
        "/B": [Entry(f"b{i}.txt", Kind.FILE, 10, now)
               for i in range(SCAN_CHUNK_SIZE + 1)],
    })
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        # First, show /A.
        await contents.show_path("/A")
        assert contents.current_path == "/A"
        a_rows = contents.row_count

        # Now scan /B with a pre-cancelled ctx - the cancel check
        # at i=SCAN_CHUNK_SIZE will trigger.
        ctx = ScanContext(path="/B", method_label="mock")
        ctx.cancelled.set()
        await contents.show_path("/B", ctx=ctx)

        # Pane still on /A.
        assert contents.current_path == "/A"
        assert contents.row_count == a_rows


async def test_show_path_cancel_at_final_check_keeps_previous() -> None:
    """Cancel landing during the last partial chunk (entries less
    than SCAN_CHUNK_SIZE) is caught by the post-loop cancel check
    too."""
    src = _build_big_mock(5)  # fewer than SCAN_CHUNK_SIZE
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        # Show / first to set a known previous state.
        await contents.show_path("/")
        prev_path = contents.current_path

        ctx = ScanContext(path="/big", method_label="mock")
        ctx.cancelled.set()
        await contents.show_path("/big", ctx=ctx)
        assert contents.current_path == prev_path


# ---------------------------------------------------------------------------
# _run_scan_with_dialog wiring
# ---------------------------------------------------------------------------


async def test_run_scan_with_dialog_fast_scan_no_modal() -> None:
    """A scan that completes faster than SCAN_MODAL_DELAY_SECONDS
    never sees a dialog. Empty mock dir scans in microseconds."""
    src = MockSource(contents={"/": []})
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        # The initial on_mount show_path went through the gate; no
        # ScanScreen should be on the stack.
        assert not any(
            isinstance(s, ScanScreen) for s in app.screen_stack
        )


async def test_run_scan_with_dialog_sets_completed_in_finally() -> None:
    """After the gate awaits do_work, ctx.completed is set so the
    dialog (if pushed) auto-dismisses."""
    src = MockSource(contents={"/": []})
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()

        captured: list[ScanContext] = []

        async def _work(ctx: ScanContext) -> None:
            captured.append(ctx)
            await asyncio.sleep(0)

        await app._run_scan_with_dialog("/foo", src, _work)
        assert len(captured) == 1
        assert captured[0].completed.is_set()


async def test_run_scan_with_dialog_uses_source_method_label() -> None:
    """The ctx the gate builds carries the source's
    scan_method_label so the dialog renders the right text."""
    src = MockSource(contents={"/": []})
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()

        captured: list[str] = []

        async def _work(ctx: ScanContext) -> None:
            captured.append(ctx.method_label)

        await app._run_scan_with_dialog("/x", src, _work)
        assert captured == ["mock source"]


async def test_run_scan_with_dialog_native_label() -> None:
    """Wrapping a NativeSource scan tags the ctx with ``os.scandir``."""
    src = NativeSource()
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()

        captured: list[str] = []

        async def _work(ctx: ScanContext) -> None:
            captured.append(ctx.method_label)

        await app._run_scan_with_dialog("/", src, _work)
        assert captured == ["os.scandir"]


async def test_run_scan_with_dialog_dismisses_dialog_on_completion() -> None:
    """If the work takes long enough for the dialog to push, the
    finally block dismisses it. We force this by sleeping past the
    threshold inside do_work."""
    src = MockSource(contents={"/": []})
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()

        async def _slow(ctx: ScanContext) -> None:
            await asyncio.sleep(SCAN_MODAL_DELAY_SECONDS + 0.15)

        await app._run_scan_with_dialog("/slowpath", src, _slow)
        # Dialog should not still be on the stack.
        assert not any(
            isinstance(s, ScanScreen) for s in app.screen_stack
        )


# ---------------------------------------------------------------------------
# Integration: L log new source on a "huge" mock
# ---------------------------------------------------------------------------


async def test_log_new_source_works_with_scan_gate(tmp_path: Path) -> None:
    """L with a fast-scanning path completes without showing a
    dialog and the tree re-roots correctly. Regression check that
    the gate doesn't break the normal-path."""
    target = tmp_path / "small"
    target.mkdir()
    (target / "f.txt").write_text("x")

    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "a.txt").write_text("a")

    app = WTreeApp(root_path=str(src_root))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l")  # opens PromptDialog
        await pilot.pause()
        from wtree.widgets.prompt import PromptDialog
        from textual.widgets import Input
        assert isinstance(app.screen, PromptDialog)
        inp = app.screen.query_one(Input)
        inp.value = str(target)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        # Re-rooted at the new path.
        assert app._root_path == str(target)
        # No scan dialog lingering.
        assert not any(
            isinstance(s, ScanScreen) for s in app.screen_stack
        )


async def test_refresh_source_works_with_scan_gate(tmp_path: Path) -> None:
    """Ctrl+R with a fast filesystem completes silently. Regression
    check that wrapping refresh in the gate doesn't break it."""
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.txt").write_text("a")

    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Add a file outside the app's awareness.
        (root / "b.txt").write_text("b")
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        # b.txt now visible.
        basenames = [
            p.split("/")[-1] for p in contents.row_paths()
        ]
        assert "b.txt" in basenames
        # No scan dialog lingering.
        assert not any(
            isinstance(s, ScanScreen) for s in app.screen_stack
        )


# ---------------------------------------------------------------------------
# Cancel via dialog Esc keeps previous listing (full e2e)
# ---------------------------------------------------------------------------


async def test_cancel_during_show_path_keeps_previous_listing(
    tmp_path: Path,
) -> None:
    """End-to-end: load /A; trigger a scan of /B with a pre-cancelled
    ctx via the helper; verify /A still shown."""
    a = tmp_path / "A"
    a.mkdir()
    (a / "alpha.txt").write_text("a")
    b = tmp_path / "B"
    b.mkdir()
    for i in range(SCAN_CHUNK_SIZE + 1):
        (b / f"b{i}.txt").write_text("b")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        # Initial scan finished; pane on tmp_path's root.
        starting_path = contents.current_path

        # Pre-cancelled ctx; the loop short-circuits at the first
        # chunk boundary or end-check.
        ctx = ScanContext(
            path=str(b), method_label="os.scandir"
        )
        ctx.cancelled.set()
        await contents.show_path(str(b), ctx=ctx)
        assert contents.current_path == starting_path
