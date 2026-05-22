"""Tests for Left-on-root ascend (re-root the tree at the parent dir).

The gesture per the 2026-05-22 design conversation: pressing Left while
the tree pane's cursor is on the root row widens the "logged disk"
upward, XTree-style. Left on any non-root node keeps Textual's default
collapse-or-cursor-to-parent behaviour.

Mirrors the shape of the other ``*_e2e.py`` files: real filesystem,
real NativeSource, real Pilot. Pure-unit-test the edge cases that need
a controlled filesystem; pilot-test the end-to-end keystroke flow.
"""

from __future__ import annotations

import os
from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.tree_pane import TreePane


async def test_left_on_root_ascends(tmp_path: Path) -> None:
    """Cursor on root + Left -> tree re-rooted at parent; new root
    label shows the parent path; old root appears as a child."""
    sub = tmp_path / "sub"
    sub.mkdir()

    app = WTreeApp(root_path=str(sub))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        # Cursor should default to root after on_mount.
        assert tree.cursor_node is tree.root
        assert tree.root.data == str(sub)

        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()

        # New root is the tmp_path (parent of ``sub``).
        assert tree.root.data == str(tmp_path)
        # App's _root_path also moved.
        assert app._root_path == str(tmp_path)
        # The old root path appears as a child of the new root.
        child_paths = {child.data for child in tree.root.children}
        assert str(sub) in child_paths


async def test_left_on_root_lands_cursor_on_old_root(tmp_path: Path) -> None:
    """After ascending, the cursor should land on the row that
    represents the previous root - so the user can immediately
    Right-arrow back into it."""
    sub = tmp_path / "sub"
    sub.mkdir()

    app = WTreeApp(root_path=str(sub))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()

        # Cursor is on the child node whose data is the old root path.
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == str(sub)


async def test_left_on_root_keeps_contents_with_old_root(tmp_path: Path) -> None:
    """After ascend, contents pane stays on the OLD root's contents.

    The cursor lands on the row representing the old root (now a
    child of the new tree root); the cursor-driven NodeHighlighted
    event sets the contents pane to that row's data, which is the old
    root. Net effect: tree widens upward, the user's working context
    in the contents pane stays stable. They press Up on the tree to
    put the cursor on the new tree root and see the parent's contents.
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "neighbour.txt").write_text("hi")

    app = WTreeApp(root_path=str(sub))
    async with app.run_test() as pilot:
        await pilot.pause()
        from wtree.widgets.contents_pane import ContentsPane
        contents = app.query_one(ContentsPane)
        assert contents.current_path == str(sub)

        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()

        # Contents pane still on the old root (the row the cursor
        # landed on after the ascend).
        assert contents.current_path == str(sub)

        # Pressing Up moves the cursor to the new tree root, which
        # fires NodeHighlighted and refreshes contents to the parent.
        await pilot.press("up")
        await pilot.pause()
        assert contents.current_path == str(tmp_path)


async def test_left_on_filesystem_root_no_op(tmp_path: Path) -> None:
    """At the filesystem root (no parent), Left does nothing and a
    nudge fires. Tested by ascending until we hit it."""
    app = WTreeApp(root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        assert tree.root.data == "/"
        before = app._root_path

        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()

        # No-op: root path unchanged.
        assert app._root_path == before
        assert tree.root.data == "/"


async def test_left_on_non_root_node_collapses(tmp_path: Path) -> None:
    """Left on a non-root expanded node uses Textual's default
    collapse behaviour - it should NOT trigger ascend."""
    sub = tmp_path / "sub"
    nested = sub / "nested"
    nested.mkdir(parents=True)

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        # Move cursor down onto the ``sub`` row (a child of root).
        await pilot.press("down")
        await pilot.pause()
        assert tree.cursor_node is not None
        assert tree.cursor_node is not tree.root
        before_root = tree.root.data

        # Expand it so Left has something to collapse.
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()

        # Root is unchanged - ascend did NOT fire.
        assert tree.root.data == before_root
        assert app._root_path == before_root


async def test_left_on_root_preserves_tagged_set(tmp_path: Path) -> None:
    """Tags survive an ascend - they're absolute paths that don't
    invalidate when the visible window widens."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "tagged.txt").write_text("x")

    app = WTreeApp(root_path=str(sub))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        await pilot.press("space")  # tag tagged.txt
        assert len(app.tagged_set) == 1
        tagged_path_before = next(iter(app.tagged_set)).path

        # Now ascend. Re-focus the tree pane first.
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()

        # Tag survives.
        assert len(app.tagged_set) == 1
        assert next(iter(app.tagged_set)).path == tagged_path_before


async def test_two_consecutive_ascends(tmp_path: Path) -> None:
    """Pressing Left-on-root twice walks up two levels."""
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)

    app = WTreeApp(root_path=str(deep))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)

        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()
        # Cursor lands on the old root row; move it back to the new
        # root so a second Left fires ascend again.
        await pilot.press("up")
        await pilot.pause()
        assert tree.cursor_node is tree.root

        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()

        # Two levels up from tmp_path/a/b should be tmp_path.
        assert tree.root.data == str(tmp_path)
        assert app._root_path == str(tmp_path)


async def test_ascend_with_trailing_slash_root(tmp_path: Path) -> None:
    """A root path with a trailing slash should still ascend
    correctly. ``os.path.dirname`` handles this naturally - this test
    documents the behaviour rather than guarding a special case."""
    sub = tmp_path / "sub"
    sub.mkdir()

    # Pass root_path with trailing separator; WTreeApp.__init__ runs
    # os.path.abspath which normalises it.
    app = WTreeApp(root_path=str(sub) + os.sep)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()
        # abspath strips the trailing separator on the way in, so the
        # parent computation is the same as without it.
        assert app._root_path == str(tmp_path)
