"""Tests for Ctrl+R refresh source (2026-05-23).

The user presses Ctrl+R when they think the on-disk state may have
drifted from what the panes show. Both panes re-scan against the
source:

* Contents pane: re-runs ``show_path`` against its current path.
* Tree pane: ``refresh_all`` snapshots expanded paths + cursor,
  re-roots, then re-walks the snapshot so the user's drilled-down
  context survives the refresh.

Tests:

* contents pane picks up a new file added on disk.
* tree pane picks up a new dir added on disk.
* tree pane drops a dir that was removed on disk.
* expansion state survives the refresh (the dir the user had
  drilled into stays open).
* cursor position survives the refresh (best-effort — falls back
  to root if the cursor's old path no longer exists).
* wiring: BINDING, Commands menu, Help screen.
* regression: reveal_path still works after the _walk_to_node
  refactor.
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.menu_bar import MENUS
from wtree.widgets.tree_pane import TreePane


async def _drive_refresh(pilot, app: WTreeApp) -> None:
    """Trigger Ctrl+R via ``action_refresh_source`` and wait for the
    worker to complete."""
    app.action_refresh_source()
    for _ in range(30):
        await pilot.pause()


# ---------------------------------------------------------------------------
# Contents-pane side
# ---------------------------------------------------------------------------


async def test_contents_pane_sees_new_file_after_refresh(tmp_path: Path) -> None:
    """A file created after mount appears in contents pane after Ctrl+R."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        assert contents.row_paths() == []

        (tmp_path / "fresh.txt").write_text("hi")
        await _drive_refresh(pilot, app)

        assert str(tmp_path / "fresh.txt") in contents.row_paths()


async def test_contents_pane_drops_deleted_file_after_refresh(
    tmp_path: Path,
) -> None:
    """A file removed after mount disappears from contents pane after Ctrl+R."""
    f = tmp_path / "doomed.txt"
    f.write_text("hi")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        assert str(f) in contents.row_paths()

        f.unlink()
        await _drive_refresh(pilot, app)

        assert str(f) not in contents.row_paths()


# ---------------------------------------------------------------------------
# Tree-pane side
# ---------------------------------------------------------------------------


async def test_tree_pane_sees_new_dir_after_refresh(tmp_path: Path) -> None:
    """A subdir created after mount appears in the tree pane after Ctrl+R."""
    (tmp_path / "alpha").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        assert {c.data for c in tree.root.children} == {str(tmp_path / "alpha")}

        (tmp_path / "beta").mkdir()
        await _drive_refresh(pilot, app)

        assert {c.data for c in tree.root.children} == {
            str(tmp_path / "alpha"),
            str(tmp_path / "beta"),
        }


async def test_tree_pane_drops_deleted_dir_after_refresh(tmp_path: Path) -> None:
    """A subdir removed after mount disappears from the tree pane."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        assert {c.data for c in tree.root.children} == {
            str(tmp_path / "alpha"),
            str(tmp_path / "beta"),
        }

        (tmp_path / "beta").rmdir()
        await _drive_refresh(pilot, app)

        assert {c.data for c in tree.root.children} == {str(tmp_path / "alpha")}


# ---------------------------------------------------------------------------
# Expansion + cursor preservation
# ---------------------------------------------------------------------------


async def test_expansion_state_preserved_across_refresh(tmp_path: Path) -> None:
    """A subdir the user had drilled into stays open after Ctrl+R."""
    (tmp_path / "alpha" / "child").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        # Expand alpha programmatically (Right-arrow would also work).
        alpha = next(c for c in tree.root.children if c.data == str(tmp_path / "alpha"))
        alpha.expand()
        await tree._populate(alpha)
        await pilot.pause()
        assert alpha.is_expanded

        await _drive_refresh(pilot, app)

        # After refresh: alpha is a fresh TreeNode but the path-based
        # restoration should leave it expanded.
        alpha_after = next(
            c for c in tree.root.children if c.data == str(tmp_path / "alpha")
        )
        assert alpha_after.is_expanded
        # And its child should be populated (we expanded the leaf too).
        assert {c.data for c in alpha_after.children} == {
            str(tmp_path / "alpha" / "child")
        }


async def test_cursor_position_preserved_across_refresh(tmp_path: Path) -> None:
    """If the cursor's path still exists post-refresh, the cursor lands there."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        # Move cursor onto beta.
        beta = next(c for c in tree.root.children if c.data == str(tmp_path / "beta"))
        tree.cursor_line = beta.line
        await pilot.pause()
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == str(tmp_path / "beta")

        await _drive_refresh(pilot, app)

        # Cursor should still be on beta.
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == str(tmp_path / "beta")


async def test_cursor_on_deleted_path_falls_through(tmp_path: Path) -> None:
    """If the cursor's path was deleted on disk, the refresh doesn't crash.

    The cursor lands wherever ``re_root`` puts it (typically root),
    which is acceptable v0 behaviour. No assertion on the exact final
    position — just that the refresh completed and the tree is sane.
    """
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        beta = next(c for c in tree.root.children if c.data == str(tmp_path / "beta"))
        tree.cursor_line = beta.line
        await pilot.pause()

        # Delete the dir the cursor was on, then refresh.
        (tmp_path / "beta").rmdir()
        await _drive_refresh(pilot, app)

        # Tree no longer has beta; the cursor must be somewhere valid.
        assert tree.cursor_node is not None
        # Either root or alpha — anywhere except the dead beta path.
        assert tree.cursor_node.data != str(tmp_path / "beta")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_bindings_include_ctrl_r() -> None:
    """BINDINGS contains the Ctrl+R entry."""
    assert ("ctrl+r", "refresh_source", "Refresh source") in WTreeApp.BINDINGS


def test_commands_menu_has_refresh_source() -> None:
    """Commands menu lists Refresh source with action refresh_source."""
    commands = MENUS[1]
    actions = [i.action for i in commands.items]
    assert "refresh_source" in actions
    item = next(i for i in commands.items if i.action == "refresh_source")
    assert item.label == "Refresh source"


def test_help_content_mentions_ctrl_r() -> None:
    """Help screen Application section mentions Ctrl+R."""
    from wtree.widgets.help import _help_content

    text = str(_help_content())
    assert "Ctrl+R" in text
    assert "Refresh source" in text


# ---------------------------------------------------------------------------
# Regression: reveal_path still works after the _walk_to_node refactor
# ---------------------------------------------------------------------------


async def test_reveal_path_still_walks_chain(tmp_path: Path) -> None:
    """After factoring _walk_to_node out, reveal_path still expands and
    lands the cursor (regression on the Ctrl+F support code)."""
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        target = str(tmp_path / "outer" / "inner")
        ok = await tree.reveal_path(target)
        assert ok
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == target


async def test_walk_to_node_returns_node_without_moving_cursor(
    tmp_path: Path,
) -> None:
    """_walk_to_node returns the matching node and leaves the cursor put.

    This is the property that makes refresh_all work without
    clobbering the user's cursor.
    """
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        cursor_before = tree.cursor_line

        node = await tree._walk_to_node(str(tmp_path / "outer" / "inner"))
        assert node is not None
        assert node.data == str(tmp_path / "outer" / "inner")
        # Cursor didn't move.
        assert tree.cursor_line == cursor_before
