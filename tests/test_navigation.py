"""Pilot tests for the navigation key bindings (todo.md item 4).

Covers ``design.md`` § Modality:

* Tab cycles focus between TreePane and ContentsPane.
* In TreePane: Backspace moves the cursor to the parent node.
* In ContentsPane:
  * ←/Backspace → go to parent dir (tree cursor moves up).
  * →/Enter → enter the directory under the cursor; file rows are no-ops.

The tree's cursor remains the source of truth — these tests assert that
the cursor lands on the right node and the contents pane follows via the
existing ``NodeHighlighted`` plumbing.
"""

from __future__ import annotations

import os
from datetime import datetime

from wtree.app import WTreeApp
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.tree_pane import TreePane


_MTIME = datetime(2026, 5, 20, 12, 0, 0)


def _entry(name: str, kind: Kind = Kind.FILE, size: int = 0) -> Entry:
    return Entry(
        name=name,
        kind=kind,
        size=size,
        mtime=_MTIME,
        permissions="-rw-r--r--",
    )


# ---------------------------------------------------------------------------
# Tab focus cycling
# ---------------------------------------------------------------------------


async def test_tab_switches_focus_from_tree_to_contents() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(contents={root: [_entry("alpha.txt", Kind.FILE, 1)]})
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # App boots with the tree focused (see ``WTreeApp.on_mount``).
        assert isinstance(pilot.app.focused, TreePane)
        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(pilot.app.focused, ContentsPane)


async def test_tab_switches_focus_back_to_tree() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(contents={root: [_entry("alpha.txt", Kind.FILE, 1)]})
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(pilot.app.focused, TreePane)


# ---------------------------------------------------------------------------
# TreePane Backspace → parent
# ---------------------------------------------------------------------------


async def test_backspace_in_tree_moves_cursor_to_parent() -> None:
    root = os.path.abspath(os.sep + "root")
    child = os.path.join(root, "sub")
    src = MockSource(
        contents={
            root: [_entry("sub", Kind.DIR)],
            child: [],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Step down into 'sub' so we have somewhere to go back from.
        await pilot.press("down")
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        assert contents.current_path == child
        await pilot.press("backspace")
        await pilot.pause()
        assert contents.current_path == root


async def test_backspace_in_tree_at_root_is_noop() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(contents={root: [_entry("alpha.txt", Kind.FILE, 1)]})
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Cursor starts at the root; backspace must not crash and must keep
        # the contents pane pointed at root.
        await pilot.press("backspace")
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        assert contents.current_path == root


# ---------------------------------------------------------------------------
# ContentsPane → / Enter — enter highlighted dir
# ---------------------------------------------------------------------------


async def test_right_in_contents_enters_highlighted_dir() -> None:
    root = os.path.abspath(os.sep + "root")
    sub = os.path.join(root, "sub")
    src = MockSource(
        contents={
            root: [_entry("sub", Kind.DIR)],
            sub: [_entry("inside.txt", Kind.FILE, 1)],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        contents.focus()
        await pilot.pause()
        # Cursor is on row 0 which is the 'sub' dir (dirs sort first).
        await pilot.press("right")
        await pilot.pause()
        assert contents.current_path == sub


async def test_enter_in_contents_enters_highlighted_dir() -> None:
    """``Enter`` mirrors ``→`` on a directory row."""
    root = os.path.abspath(os.sep + "root")
    sub = os.path.join(root, "sub")
    src = MockSource(
        contents={
            root: [_entry("sub", Kind.DIR)],
            sub: [],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        contents.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert contents.current_path == sub


async def test_right_in_contents_on_file_row_is_noop() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={root: [_entry("just_a_file.txt", Kind.FILE, 1)]}
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        contents.focus()
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        # File row — no navigation should occur.
        assert contents.current_path == root


# ---------------------------------------------------------------------------
# ContentsPane ← / Backspace — go to parent
# ---------------------------------------------------------------------------


async def test_left_in_contents_goes_to_parent() -> None:
    root = os.path.abspath(os.sep + "root")
    sub = os.path.join(root, "sub")
    src = MockSource(
        contents={
            root: [_entry("sub", Kind.DIR)],
            sub: [_entry("inside.txt", Kind.FILE, 1)],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        contents.focus()
        await pilot.pause()
        # Drill in first so there's somewhere to come back from.
        await pilot.press("right")
        await pilot.pause()
        assert contents.current_path == sub
        await pilot.press("left")
        await pilot.pause()
        assert contents.current_path == root


async def test_backspace_in_contents_goes_to_parent() -> None:
    """``Backspace`` is the design-canonical alias for ←."""
    root = os.path.abspath(os.sep + "root")
    sub = os.path.join(root, "sub")
    src = MockSource(
        contents={
            root: [_entry("sub", Kind.DIR)],
            sub: [],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        contents.focus()
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert contents.current_path == sub
        await pilot.press("backspace")
        await pilot.pause()
        assert contents.current_path == root


async def test_left_in_contents_at_root_is_noop() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(contents={root: [_entry("alpha.txt", Kind.FILE, 1)]})
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        contents.focus()
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        # Tree cursor was already at root; contents stays.
        assert contents.current_path == root
