"""Tests for the MC-style StatusLine + KeyBar.

The widgets themselves are mostly rendering, so most tests are pilot-
driven integration: launch the app and check what's in the bottom rows
under various conditions (idle, copy running, queue draining).
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input

from wtree.app import WTreeApp
from wtree.widgets.keybar import KeyBar
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.status_line import StatusLine


# ---------------------------------------------------------------------------
# KeyBar - rendering
# ---------------------------------------------------------------------------


async def test_keybar_lists_all_ten_fkeys(tmp_path: Path) -> None:
    """The cheat sheet shows F1-F10 with their canonical labels, even
    the ones not yet wired."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(KeyBar)
        rendered = str(bar.render())
        for label in ("Help", "Ren", "View", "Edit", "Copy", "Move",
                      "New", "Del", "Menu", "Quit"):
            assert label in rendered, f"missing label {label!r}"
        # Every F-number from 1..10 present at least once.
        for n in range(1, 11):
            assert str(n) in rendered


# ---------------------------------------------------------------------------
# StatusLine - idle state shows cursor entry
# ---------------------------------------------------------------------------


async def test_statusline_shows_cursor_entry_when_idle(
    tmp_path: Path,
) -> None:
    """With a file under the contents-pane cursor, the status line
    surfaces its path + size."""
    (tmp_path / "alpha.txt").write_text("hello world")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        status = app.query_one(StatusLine)
        text = str(status.render())
        assert "alpha.txt" in text
        # Size column shows up - 11 bytes formatted as "11 B".
        assert "11 B" in text


async def test_statusline_empty_when_no_cursor(tmp_path: Path) -> None:
    """Empty directory + no cursor entry - status line is blank-ish."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        status = app.query_one(StatusLine)
        text = str(status.render()).strip()
        # We allow the current_path to show in dim - just verify nothing
        # exploded and there's no queue-running gibberish.
        assert "Copy:" not in text
        assert "Queued" not in text


# ---------------------------------------------------------------------------
# StatusLine - running state shows queue progress
# ---------------------------------------------------------------------------


async def test_statusline_shows_running_op_with_progress(
    tmp_path: Path,
) -> None:
    """During a copy the status line reads 'Copy: N/M items'."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    (src / "b.txt").write_text("b")
    (src / "c.txt").write_text("c")
    dst = tmp_path / "dst"
    dst.mkdir()

    app = WTreeApp(root_path=str(tmp_path))
    # Snapshot the status line each time on_item_progress fires.
    snapshots: list[str] = []

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        # Tag the 'src' dir (it's row 0 because dirs sort first).
        await pilot.press("space")
        # Cache the StatusLine reference NOW - once the modal opens,
        # app.query_one(StatusLine) would search the modal screen and
        # miss the main-screen widget.
        status_widget = app.query_one(StatusLine)
        # Bind a small hook into the queue's progress callback to
        # snapshot mid-flight - the worker is fast, so we can't rely on
        # pilot.pause catching us mid-plan reliably.
        original_cb = app.op_queue._on_item_progress  # noqa: SLF001
        def snapshot(item, q):
            original_cb(item, q)
            snapshots.append(str(status_widget.render()))
        app.op_queue._on_item_progress = snapshot  # noqa: SLF001

        await pilot.press("c")
        await pilot.pause()
        modal_input = app.screen.query_one(Input)
        modal_input.value = str(dst)
        await pilot.press("enter")
        await pilot.pause()
        await app.op_queue.wait_until_idle()
        await pilot.pause()
        # Capture the post-drain status text while the app is still up.
        final = str(status_widget.render())

    # At least one snapshot during the run should contain "Copy" and
    # the items-done counter format "N/M".
    assert any("Copy" in s and "/" in s for s in snapshots), (
        f"no progress snapshots found in {snapshots!r}"
    )
    # After idle, the status line should NOT still say "Copy: N items".
    assert "items" not in final or "Copy" not in final


async def test_statusline_refreshes_on_cursor_move(tmp_path: Path) -> None:
    """Pressing Down on the contents pane swaps the status line to
    the newly-highlighted entry."""
    (tmp_path / "a.txt").write_text("aaaa")
    (tmp_path / "b.txt").write_text("bbbbbbbb")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        status = app.query_one(StatusLine)
        before = str(status.render())
        await pilot.press("down")
        await pilot.pause()
        after = str(status.render())
        assert before != after
        assert "a.txt" in before and "a.txt" not in after.split("b.txt")[-1]
        assert "b.txt" in after


async def test_keybar_wired_set_includes_f6(tmp_path: Path) -> None:
    """After binding Move (M / F6), F6 should be in the wired set so the
    KeyBar renders it bold rather than dim. Pure structural assertion -
    rendered style is checked above; this nails down the constant."""
    from wtree.widgets.keybar import _WIRED
    assert 5 in _WIRED   # Copy
    assert 6 in _WIRED   # Move (this is what just landed)
    assert 10 in _WIRED  # Quit
    # F-keys still un-bound should NOT be in the set yet - guards
    # against accidentally bulk-enabling them.
    assert 1 not in _WIRED
    assert 7 not in _WIRED


async def test_keybar_wired_set_includes_f8(tmp_path: Path) -> None:
    """After binding Delete (D / Del / F8), F8 joins the wired set."""
    from wtree.widgets.keybar import _WIRED
    assert 5 in _WIRED   # Copy
    assert 6 in _WIRED   # Move
    assert 8 in _WIRED   # Delete (newly landed)
    assert 10 in _WIRED  # Quit
    # F-keys still un-bound should NOT be in the set yet.
    assert 1 not in _WIRED
    assert 7 not in _WIRED


async def test_keybar_wired_set_includes_f2(tmp_path: Path) -> None:
    """After binding Rename (R / F2), F2 joins the wired set."""
    from wtree.widgets.keybar import _WIRED
    assert 2 in _WIRED   # Rename (newly landed)
    assert 5 in _WIRED   # Copy
    assert 6 in _WIRED   # Move
    assert 8 in _WIRED   # Delete
    assert 10 in _WIRED  # Quit
    # F-keys still un-bound should NOT be in the set yet.
    assert 1 not in _WIRED
    assert 3 not in _WIRED
    assert 4 not in _WIRED
    assert 7 not in _WIRED
    assert 9 not in _WIRED
