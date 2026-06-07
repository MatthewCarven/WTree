"""Tests for cursor preservation across refreshes (design.md 2026-06-07).

Contents pane: re-showing the SAME path (auto-refresh, Ctrl+R, editor
return) restores the cursor - to the same entry if it survives, else
to the same row index clamped (delete row 5 -> cursor lands on the new
row 5 = the next entry). Showing a DIFFERENT path still resets to 0.

Tree pane: ``refresh_paths`` snapshots the cursor's backing path and
reveal_path-restores it; a deleted entry falls back to the nearest
surviving ancestor.

The e2e at the bottom is the daily-driver scenario the item came from:
delete an entry with D and watch the cursor land on its next sibling.
"""

from __future__ import annotations

import os
from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.tree_pane import TreePane


def _stage(tmp_path: Path) -> str:
    root = tmp_path / "root"
    root.mkdir()
    for name in ("aaa.txt", "bbb.txt", "ccc.txt", "ddd.txt"):
        (root / name).write_text("x")
    (root / "subdir").mkdir()
    (root / "subdir" / "inner").mkdir()
    return str(root)


def _cursor_basename(pane: ContentsPane) -> str:
    return os.path.basename(pane._row_paths[pane.cursor_row])


# ---------------------------------------------------------------------------
# Contents pane
# ---------------------------------------------------------------------------


async def test_same_path_restores_surviving_entry(tmp_path: Path) -> None:
    """Cursor follows the entry, not the index, when it survives."""
    root = _stage(tmp_path)
    app = WTreeApp(root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        # Cursor onto ccc.txt.
        target_row = pane._row_paths.index(str(Path(root) / "ccc.txt"))
        pane.move_cursor(row=target_row, column=0)
        await pilot.pause()

        # A new entry that sorts ABOVE ccc.txt shifts the indices.
        (Path(root) / "abc.txt").write_text("x")
        await pane.show_path(root)
        await pilot.pause()

        assert _cursor_basename(pane) == "ccc.txt"
        assert pane.cursor_row != target_row  # index shifted, entry kept


async def test_same_path_clamps_when_deleted(tmp_path: Path) -> None:
    """Delete the cursor row -> cursor lands on the next entry."""
    root = _stage(tmp_path)
    app = WTreeApp(root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        row = pane._row_paths.index(str(Path(root) / "bbb.txt"))
        pane.move_cursor(row=row, column=0)
        await pilot.pause()

        (Path(root) / "bbb.txt").unlink()
        await pane.show_path(root)
        await pilot.pause()

        assert pane.cursor_row == row
        assert _cursor_basename(pane) == "ccc.txt"  # next sibling


async def test_clamp_to_last_when_tail_deleted(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        last = pane.row_count - 1
        pane.move_cursor(row=last, column=0)
        await pilot.pause()
        victim = pane._row_paths[last]
        assert os.path.basename(victim) == "ddd.txt"  # files sort last

        (Path(victim)).unlink()
        await pane.show_path(root)
        await pilot.pause()

        assert pane.cursor_row == pane.row_count - 1
        assert _cursor_basename(pane) == "ccc.txt"


async def test_navigation_still_resets_to_top(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        pane.move_cursor(row=2, column=0)
        await pilot.pause()

        await pane.show_path(str(Path(root) / "subdir"))
        await pilot.pause()
        assert pane.cursor_row == 0


# ---------------------------------------------------------------------------
# Tree pane
# ---------------------------------------------------------------------------


async def test_tree_cursor_survives_refresh(tmp_path: Path) -> None:
    """Cursor on a node inside a refreshed dir is restored."""
    root = _stage(tmp_path)
    app = WTreeApp(root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        sub = str(Path(root) / "subdir")
        assert await tree.reveal_path(sub)
        await pilot.pause()
        assert tree.cursor_node.data == sub

        await tree.refresh_paths([root])
        await pilot.pause()

        assert tree.cursor_node is not None
        assert tree.cursor_node.data == sub


async def test_tree_cursor_falls_back_to_ancestor(tmp_path: Path) -> None:
    """Cursor on a deleted dir falls back to its parent."""
    root = _stage(tmp_path)
    app = WTreeApp(root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        sub = str(Path(root) / "subdir")
        inner = str(Path(root) / "subdir" / "inner")
        assert await tree.reveal_path(inner)
        await pilot.pause()
        assert tree.cursor_node.data == inner

        os.rmdir(inner)
        await tree.refresh_paths([sub])
        await pilot.pause()

        assert tree.cursor_node is not None
        assert tree.cursor_node.data == sub  # nearest survivor


# ---------------------------------------------------------------------------
# E2E - the scenario the todo item came from
# ---------------------------------------------------------------------------


async def test_delete_keystroke_lands_on_next_sibling(
    tmp_path: Path,
) -> None:
    """D + confirm on row N -> auto-refresh -> cursor on new row N."""
    root = _stage(tmp_path)
    app = WTreeApp(root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        pane.focus()
        await pilot.pause()
        row = pane._row_paths.index(str(Path(root) / "bbb.txt"))
        pane.move_cursor(row=row, column=0)
        await pilot.pause()

        await pilot.press("d")        # delete dialog
        await pilot.pause()
        await pilot.press("enter")    # confirm
        await pilot.pause()
        for _ in range(8):            # let queue + auto-refresh settle
            await pilot.pause()

        assert not (Path(root) / "bbb.txt").exists()
        assert pane.cursor_row == row
        assert _cursor_basename(pane) == "ccc.txt"
