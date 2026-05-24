"""Tests for tree-pane Left / Right arrow keys (2026-05-23).

Textual 8.x's ``Tree`` ships no ``left`` / ``right`` bindings — for the
first half of the project we relied on the contents pane's drill-in
gesture and Tab-cycling. Closing that ergonomic gap so the tree pane
behaves like every other tree view in the wild:

* ``right`` on a collapsed dir expands it (with lazy-load).
* ``right`` on an already-expanded dir descends to the first child.
* ``right`` on a non-expandable node (error leaf) is a no-op.
* ``right`` on an empty expanded dir is a no-op.
* ``left`` on an expanded dir collapses it.
* ``left`` on a collapsed non-root dir moves the cursor to its parent.
* ``left`` on the root still posts :class:`AscendRequested` (regression
  guard — this gesture predates the new arrow bindings).
* ``space`` still posts :class:`TagRequested` (regression guard).
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.tree_pane import TreePane


# ---------------------------------------------------------------------------
# Right arrow
# ---------------------------------------------------------------------------


async def test_right_expands_collapsed_dir(tmp_path: Path) -> None:
    """Right on a collapsed dir expands + populates it.

    Lazy-load fires inline (``_populate`` is awaited in ``on_key``), so
    the children land before the next paint.
    """
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        await pilot.press("down")  # cursor onto "outer"
        await pilot.pause()
        outer = tree.cursor_node
        assert outer is not None
        assert outer.data == str(tmp_path / "outer")
        assert not outer.is_expanded

        await pilot.press("right")
        await pilot.pause()

        assert outer.is_expanded
        assert {c.data for c in outer.children} == {
            str(tmp_path / "outer" / "inner")
        }


async def test_right_on_expanded_dir_descends_to_first_child(tmp_path: Path) -> None:
    """Right on an already-expanded dir moves cursor to its first child.

    XTree-style drill-in. Useful when the user has just expanded a
    folder and wants to keep going inward without lifting fingers from
    the arrow row.
    """
    (tmp_path / "outer" / "a").mkdir(parents=True)
    (tmp_path / "outer" / "b").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        await pilot.press("down")
        await pilot.pause()
        # Expand "outer" via the new right-arrow binding.
        await pilot.press("right")
        await pilot.pause()
        # Second right: drill into the first child (lexicographic: "a").
        await pilot.press("right")
        await pilot.pause()
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == str(tmp_path / "outer" / "a")


async def test_right_on_empty_expanded_dir_is_noop(tmp_path: Path) -> None:
    """Right on an expanded dir with no children does nothing.

    There's no first child to descend to. The cursor stays put.
    """
    (tmp_path / "empty").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        await pilot.press("down")
        await pilot.pause()
        # First right expands "empty" (which is empty, so children=[]).
        await pilot.press("right")
        await pilot.pause()
        empty_node = tree.cursor_node
        assert empty_node is not None
        assert empty_node.is_expanded
        # Second right: cursor stays on "empty" - no children to descend to.
        await pilot.press("right")
        await pilot.pause()
        assert tree.cursor_node is empty_node


async def test_right_on_error_leaf_is_noop(tmp_path: Path) -> None:
    """Right on an error placeholder (data is None) doesn't try to expand.

    The error leaf has ``allow_expand=False`` and ``data=None``.
    Pressing right should silently no-op rather than fall through to
    Textual's default (which doesn't exist in 8.x anyway).
    """
    from textual.widgets.tree import TreeNode

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        # Inject an error placeholder leaf so we can land the cursor on it.
        tree.root.add_leaf("⚠ permission denied", data=None)
        await pilot.pause()
        # The error leaf is the last visible row under the root.
        error_node = tree.root.children[-1]
        assert error_node.data is None
        tree.cursor_line = error_node.line
        await pilot.pause()
        assert tree.cursor_node is error_node

        await pilot.press("right")
        await pilot.pause()

        # Cursor stays on the error leaf; no expansion attempted.
        assert tree.cursor_node is error_node
        assert not error_node.is_expanded


# ---------------------------------------------------------------------------
# Left arrow
# ---------------------------------------------------------------------------


async def test_left_on_expanded_node_collapses(tmp_path: Path) -> None:
    """Left on an expanded dir collapses it in place. Cursor stays put."""
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        await pilot.press("down")  # onto outer
        await pilot.pause()
        await pilot.press("right")  # expand outer
        await pilot.pause()
        outer = tree.cursor_node
        assert outer is not None
        assert outer.is_expanded

        await pilot.press("left")
        await pilot.pause()

        assert not outer.is_expanded
        # Cursor doesn't move when we just collapse.
        assert tree.cursor_node is outer


async def test_left_on_collapsed_node_jumps_to_parent(tmp_path: Path) -> None:
    """Left on a collapsed non-root dir moves the cursor to its parent.

    XTree / Finder pattern: pressing left "out of" a row walks the
    cursor up the tree. After expanding outer and stepping into inner,
    a left then collapse-of-outer + another left lands the cursor at
    the root.
    """
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        await pilot.press("down")  # onto outer
        await pilot.pause()
        await pilot.press("right")  # expand outer; cursor on outer
        await pilot.pause()
        await pilot.press("right")  # descend onto inner
        await pilot.pause()
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == str(tmp_path / "outer" / "inner")

        # Inner is collapsed (leaf, no children). Left -> jump to parent.
        await pilot.press("left")
        await pilot.pause()
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == str(tmp_path / "outer")


# ---------------------------------------------------------------------------
# Regression: existing left-on-root + space gestures still work
# ---------------------------------------------------------------------------


async def test_left_on_root_still_ascends(tmp_path: Path) -> None:
    """Pre-existing left-on-root ascend gesture survives the new bindings."""
    deep = tmp_path / "deep_under"
    deep.mkdir()
    app = WTreeApp(root_path=str(deep))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        # Cursor starts on the root after mount.
        assert tree.cursor_node is tree.root

        await pilot.press("left")
        # Ascend posts a message + the app re-roots the tree.
        for _ in range(10):
            await pilot.pause()
            if tree.root.data == str(tmp_path):
                break

        assert tree.root.data == str(tmp_path)


async def test_space_still_posts_tag_request(tmp_path: Path) -> None:
    """Space on a backed node still toggles the subtree tag (regression)."""
    (tmp_path / "subdir").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # cursor onto subdir
        await pilot.pause()
        await pilot.press("space")
        for _ in range(10):
            await pilot.pause()
            if app.tagged_set.contains(
                app._source.source_id, str(tmp_path / "subdir")
            ):
                break
        assert app.tagged_set.contains(
            app._source.source_id, str(tmp_path / "subdir")
        )
