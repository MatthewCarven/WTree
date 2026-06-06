"""E2E pilot tests for Ctrl+A tag-all-in-dir and ``+`` / ``-`` glob tagging.

These cover the bulk-tagging gestures landed 2026-05-22 for the tagging
polish pass. The recursive tree-pane Space gesture is covered in a
separate module (``test_tree_recursive_tag.py``) because its async walk
shape diverges enough to deserve its own fixtures.
"""

from __future__ import annotations

import os
from datetime import datetime

from wtree.app import WTreeApp
from wtree.sources.base import Entry, Kind, ScanError
from wtree.sources.mock import MockSource
from wtree.widgets.prompt import PromptDialog


_MTIME = datetime(2026, 5, 22, 12, 0, 0)


def _entry(name: str, kind: Kind = Kind.FILE, size: int = 0) -> Entry:
    return Entry(
        name=name,
        kind=kind,
        size=size,
        mtime=_MTIME,
        permissions="-rw-r--r--",
    )


# ---------------------------------------------------------------------------
# Ctrl+A — tag every taggable row in the contents pane's current dir
# ---------------------------------------------------------------------------


async def test_ctrl_a_tags_every_row_in_current_dir() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={
            root: [
                _entry("a.txt", Kind.FILE, 1),
                _entry("b.txt", Kind.FILE, 2),
                _entry("sub", Kind.DIR),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        # All three entries got tagged.
        assert len(app.tagged_set) == 3
        for name in ("a.txt", "b.txt", "sub"):
            assert app.tagged_set.contains("mock", os.path.join(root, name))


async def test_ctrl_a_is_idempotent_when_already_fully_tagged() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={root: [_entry("a.txt", Kind.FILE, 1)]},
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert len(app.tagged_set) == 1
        # Second press is a no-op (count unchanged).
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert len(app.tagged_set) == 1


async def test_ctrl_a_skips_error_rows() -> None:
    """Error rows (non-taggable by design) must not be tagged by Ctrl+A."""
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={
            root: [
                ScanError(path=root, message="boom", cause="OSError"),
                _entry("ok.txt", Kind.FILE, 1),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        # Only the real entry got tagged — the error row is skipped.
        assert len(app.tagged_set) == 1
        assert app.tagged_set.contains("mock", os.path.join(root, "ok.txt"))


async def test_ctrl_a_on_empty_dir_is_a_noop() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(contents={root: []})
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert len(app.tagged_set) == 0


async def test_ctrl_a_then_ctrl_u_round_trips_cleanly() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={
            root: [
                _entry("a.txt", Kind.FILE, 1),
                _entry("b.txt", Kind.FILE, 2),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert len(app.tagged_set) == 2
        await pilot.press("ctrl+u")
        await pilot.pause()
        assert len(app.tagged_set) == 0


# ---------------------------------------------------------------------------
# `+` and `-` — glob-by-pattern tagging via PromptDialog
# ---------------------------------------------------------------------------


async def _submit_pattern(pilot, pattern: str) -> None:
    """Helper: type ``pattern`` into the active PromptDialog and submit."""
    # The prompt's Input is focused on mount; set value directly per worklog
    # note ("pilot press() is slow for long strings").
    dlg = pilot.app.screen
    assert isinstance(dlg, PromptDialog)
    from textual.widgets import Input

    inp = dlg.query_one(Input)
    inp.value = pattern
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


async def test_plus_pattern_tags_basename_matches() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={
            root: [
                _entry("photo1.png", Kind.FILE, 1),
                _entry("photo2.png", Kind.FILE, 2),
                _entry("notes.txt", Kind.FILE, 3),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("plus")
        await pilot.pause()
        await _submit_pattern(pilot, "*.png")
        # Both PNGs tagged; notes.txt untouched.
        assert len(app.tagged_set) == 2
        assert app.tagged_set.contains("mock", os.path.join(root, "photo1.png"))
        assert app.tagged_set.contains("mock", os.path.join(root, "photo2.png"))
        assert not app.tagged_set.contains("mock", os.path.join(root, "notes.txt"))


async def test_minus_pattern_untags_basename_matches() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={
            root: [
                _entry("a.tmp", Kind.FILE, 1),
                _entry("b.tmp", Kind.FILE, 2),
                _entry("keep.txt", Kind.FILE, 3),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Tag everything first.
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert len(app.tagged_set) == 3
        # Then untag the *.tmp files.
        await pilot.press("minus")
        await pilot.pause()
        await _submit_pattern(pilot, "*.tmp")
        assert len(app.tagged_set) == 1
        assert app.tagged_set.contains("mock", os.path.join(root, "keep.txt"))


async def test_plus_pattern_cancel_via_escape_is_a_noop() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(contents={root: [_entry("a.txt", Kind.FILE, 1)]})
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("plus")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.tagged_set) == 0


async def test_plus_pattern_with_no_matches_is_a_noop() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(contents={root: [_entry("a.txt", Kind.FILE, 1)]})
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("plus")
        await pilot.pause()
        await _submit_pattern(pilot, "*.png")
        # Nothing matched the glob — tagged set stays empty.
        assert len(app.tagged_set) == 0


async def test_plus_pattern_empty_input_is_cancelled() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(contents={root: [_entry("a.txt", Kind.FILE, 1)]})
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("plus")
        await pilot.pause()
        await _submit_pattern(pilot, "")
        assert len(app.tagged_set) == 0


async def test_plus_pattern_skips_error_rows() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={
            root: [
                ScanError(path=root, message="boom", cause="OSError"),
                _entry("match.txt", Kind.FILE, 1),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("plus")
        await pilot.pause()
        await _submit_pattern(pilot, "*.txt")
        # Only the real entry tagged; error row never considered.
        assert len(app.tagged_set) == 1
        assert app.tagged_set.contains("mock", os.path.join(root, "match.txt"))
