"""The Selection rule follows the FOCUSED pane (2026-06-11 fix).

Field report: tree cursor highlighted on a folder, D pressed - the
confirm dialog named the first row of that folder's *listing* (the
contents pane's cursor), not the folder itself. `_resolve_selection_tags`
always read the contents pane; `action_properties` had long documented
the focused-pane convention as "the existing op convention". These pin
the alignment: tree-focused ops act on the highlighted dir, the tagged
set still always wins, and contents-focused behaviour is unchanged.

The tree pane has focus on mount (every older op test starts with an
explicit Tab to the contents pane - which is why this slipped through).
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.confirm import ConfirmDialog
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.tree_pane import TreePane


def _stage(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "inner.txt").write_text("x")
    (root / "beta.txt").write_text("y")
    return root


async def _cursor_onto_first_child(pilot, app) -> str:
    """Move the tree cursor from the root onto its first child dir."""
    tree = app.query_one(TreePane)
    tree.focus()
    await pilot.pause()
    await pilot.press("down")
    await pilot.pause()
    node = tree.cursor_node
    assert node is not None and node.data is not None
    return node.data


async def test_delete_from_tree_targets_highlighted_dir(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        cursor_path = await _cursor_onto_first_child(pilot, app)
        assert cursor_path.endswith("alpha")
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        # The dialog must name the highlighted dir, not its first child.
        title = str(app.screen._title)
        assert "alpha" in title
        assert "inner.txt" not in title
        await pilot.press("escape")
        await pilot.pause()


async def test_copy_from_tree_targets_highlighted_dir(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        cursor_path = await _cursor_onto_first_child(pilot, app)
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        assert cursor_path in str(app.screen._title)
        await pilot.press("escape")
        await pilot.pause()


async def test_tagged_set_still_wins_when_tree_focused(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        tagged = str(root / "beta.txt")
        app.tagged_set.add(app._source.source_id, tagged)
        await _cursor_onto_first_child(pilot, app)
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        title_and_body = str(app.screen._title) + " ".join(app.screen._body)
        assert "beta.txt" in title_and_body
        assert "alpha" not in title_and_body
        await pilot.press("escape")
        await pilot.pause()


async def test_contents_focus_keeps_row_cursor_behaviour(tmp_path: Path) -> None:
    """Tab to the contents pane -> ops act on the row cursor (unchanged)."""
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # contents pane
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        # Row 0 of root's listing is alpha/ - the contents cursor target.
        assert "alpha" in str(app.screen._title)
        await pilot.press("escape")
        await pilot.pause()


async def test_resolver_direct_tree_focus(tmp_path: Path) -> None:
    """Unit-flavoured: resolver returns the tree cursor's dir path."""
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        cursor_path = await _cursor_onto_first_child(pilot, app)
        tags = app._resolve_selection_tags()
        assert [t.path for t in tags] == [cursor_path]
