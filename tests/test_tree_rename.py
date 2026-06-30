"""Tree-pane action parity (2026-06-30, Session 4).

Brings Rename in line with the 2026-06-11 Selection rule: with the tree
pane focused, ``R`` renames the directory node under the tree cursor (the
logged root is refused); contents-focused behaviour is unchanged. Also pins
the Backspace disambiguation (cursor on root -> ascend, otherwise
cursor-to-parent) and the contents-pane right-arrow nudge on a file row.

The tree pane has focus on mount.
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.sources.base import Kind
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.status_line import StatusLine
from wtree.widgets.tree_pane import TreePane


def _stage(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "inner.txt").write_text("x")
    (root / "beta.txt").write_text("y")
    return root


async def _tree_cursor_to_alpha(pilot, app) -> str:
    tree = app.query_one(TreePane)
    tree.focus()
    await pilot.pause()
    await pilot.press("down")  # root -> alpha (first child dir)
    await pilot.pause()
    node = tree.cursor_node
    assert node is not None and node.data is not None
    return node.data


# ---------------------------------------------------------------------------
# Rename from the tree pane
# ---------------------------------------------------------------------------


async def test_rename_from_tree_targets_dir_node(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        alpha = await _tree_cursor_to_alpha(pilot, app)
        assert alpha.endswith("alpha")
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        title = str(app.screen._title)
        assert "alpha" in title
        # The DIR itself, NOT the contents pane's first child (inner.txt).
        assert "inner.txt" not in title
        await pilot.press("escape")
        await pilot.pause()


async def test_rename_root_refused_with_nudge(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        tree.focus()
        await pilot.pause()
        assert tree.cursor_node is tree.root
        await pilot.press("r")
        await pilot.pause()
        assert not isinstance(app.screen, PromptDialog)  # refused
        status = app.query_one(StatusLine)
        assert status._flash_message is not None
        assert "root" in status._flash_message


async def test_rename_from_contents_unchanged(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # contents pane
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        # Row 0 of root's listing is alpha/.
        assert "alpha" in str(app.screen._title)
        await pilot.press("escape")
        await pilot.pause()


async def test_resolver_tree_dir(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        alpha = await _tree_cursor_to_alpha(pilot, app)
        target = app._resolve_rename_target()
        assert target is not None
        path, kind = target
        assert path == alpha
        assert kind is Kind.DIR


async def test_resolver_root_returns_none(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        tree.focus()
        await pilot.pause()
        assert app._resolve_rename_target() is None


# ---------------------------------------------------------------------------
# Backspace disambiguation
# ---------------------------------------------------------------------------


async def test_backspace_on_root_ascends(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    app = WTreeApp(root_path=str(sub))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        tree.focus()
        await pilot.pause()
        assert tree.cursor_node is tree.root
        await pilot.press("backspace")
        await pilot.pause()
        await pilot.pause()
        # Re-rooted at the parent, exactly like Left-on-root.
        assert tree.root.data == str(tmp_path)
        assert app._root_path == str(tmp_path)


async def test_backspace_on_nonroot_moves_to_parent(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _tree_cursor_to_alpha(pilot, app)
        tree = app.query_one(TreePane)
        original_root = tree.root.data
        await pilot.press("backspace")
        await pilot.pause()
        # Cursor moved to the parent (root); NO re-root.
        assert tree.cursor_node is tree.root
        assert tree.root.data == original_root
        assert app._root_path == original_root


# ---------------------------------------------------------------------------
# Contents-pane right-arrow nudge on a file row
# ---------------------------------------------------------------------------


async def test_right_on_file_row_nudges(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # contents pane
        await pilot.pause()
        await pilot.press("down")  # alpha/ (row 0) -> beta.txt (row 1, file)
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        assert contents._row_kinds[contents.cursor_row] is not Kind.DIR
        await pilot.press("right")
        await pilot.pause()
        status = app.query_one(StatusLine)
        assert status._flash_message is not None
        assert "directories" in status._flash_message
