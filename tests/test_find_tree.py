"""Tests for Ctrl+F find-across-tree + Ctrl+G next-match (2026-05-23).

Distinct from the ``/`` incremental search:

* ``/`` searches *visible* rows in the focused pane (modeless inline
  bar).
* ``Ctrl+F`` walks the *entire* logged tree via `_walk_subtree`,
  collects basename substring matches, jumps to the first match
  via :meth:`TreePane.reveal_path`. ``Ctrl+G`` steps through the
  cached list with wrap.

Surfaces under test:

* :meth:`TreePane.reveal_path` — lazy-expands the chain from root to
  target and drops the cursor on the matching node.
* ``WTreeApp.action_find_tree`` — drives the prompt + walk + reveal.
* ``WTreeApp.action_next_match`` — cycles through the cached list.
* HelpScreen + menu wiring.
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input

from wtree.app import WTreeApp
from wtree.widgets.menu_bar import MENUS
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.tree_pane import TreePane


# ---------------------------------------------------------------------------
# TreePane.reveal_path — unit-ish coverage via the app
# ---------------------------------------------------------------------------


async def test_reveal_path_expands_chain(tmp_path: Path) -> None:
    """reveal_path opens each segment from root to target lazily."""
    (tmp_path / "outer" / "middle" / "inner").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        target = str(tmp_path / "outer" / "middle" / "inner")
        ok = await tree.reveal_path(target)
        assert ok
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == target


async def test_reveal_path_target_outside_root_returns_false(
    tmp_path: Path,
) -> None:
    """A target above (or beside) the root short-circuits to ``False``."""
    (tmp_path / "sub").mkdir()
    app = WTreeApp(root_path=str(tmp_path / "sub"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        # Parent of the logged root is outside.
        ok = await tree.reveal_path(str(tmp_path))
        assert not ok


async def test_reveal_path_missing_segment_returns_false(
    tmp_path: Path,
) -> None:
    """A target whose final component doesn't exist on disk returns False.

    The walk reaches the deepest matching parent and stops cleanly.
    """
    (tmp_path / "outer").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        # ``outer/nonexistent`` should not be findable.
        ok = await tree.reveal_path(str(tmp_path / "outer" / "nonexistent"))
        assert not ok


async def test_reveal_path_target_equals_root(tmp_path: Path) -> None:
    """reveal_path(target == root) lands cursor on the root."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        ok = await tree.reveal_path(str(tmp_path))
        assert ok
        assert tree.cursor_node is tree.root


# ---------------------------------------------------------------------------
# action_find_tree end-to-end
# ---------------------------------------------------------------------------


async def _drive_find(pilot, app: WTreeApp, query: str) -> None:
    """Trigger Ctrl+F, type ``query``, submit. Polls until the prompt
    closes and the worker stores its matches."""
    app.action_find_tree()
    # Wait for the PromptDialog to appear (action is @work).
    for _ in range(30):
        await pilot.pause()
        if isinstance(app.screen, PromptDialog):
            break
    assert isinstance(app.screen, PromptDialog)
    inp = app.screen.query_one(Input)
    inp.value = query
    await pilot.press("enter")
    # Wait for the worker to finish the walk + update cached matches.
    for _ in range(40):
        await pilot.pause()
        if not isinstance(app.screen, PromptDialog):
            break


async def test_find_tree_walks_full_tree(tmp_path: Path) -> None:
    """Ctrl+F finds entries below collapsed subtrees, not just visible ones."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "photos" / "docs").mkdir(parents=True)
    (tmp_path / "photos" / "alpha").mkdir()
    (tmp_path / "other").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _drive_find(pilot, app, "docs")

        assert app._tree_find_query == "docs"
        assert set(app._tree_find_matches) == {
            str(tmp_path / "docs"),
            str(tmp_path / "photos" / "docs"),
        }
        # Cursor landed on the first match.
        tree = app.query_one(TreePane)
        assert tree.cursor_node is not None
        assert tree.cursor_node.data in app._tree_find_matches


async def test_find_tree_case_insensitive(tmp_path: Path) -> None:
    """Substring match is case-insensitive on both sides."""
    (tmp_path / "Photos").mkdir()
    (tmp_path / "DOWNLOADS").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _drive_find(pilot, app, "PHOTO")
        assert app._tree_find_matches == [str(tmp_path / "Photos")]


async def test_find_tree_skips_root(tmp_path: Path) -> None:
    """The root itself never matches (it's the parent, not a result)."""
    # Create a child with the same basename as the root - that one
    # should match but the root itself shouldn't.
    root = tmp_path / "samesame"
    root.mkdir()
    (root / "samesame").mkdir()
    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _drive_find(pilot, app, "samesame")
        # Only the child is a match.
        assert app._tree_find_matches == [str(root / "samesame")]


async def test_find_tree_no_matches(tmp_path: Path) -> None:
    """Empty match list still caches the query so Ctrl+G can flash."""
    (tmp_path / "alpha").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _drive_find(pilot, app, "nothing-matches-this")
        assert app._tree_find_query == "nothing-matches-this"
        assert app._tree_find_matches == []


async def test_find_tree_empty_query_cancels(tmp_path: Path) -> None:
    """Whitespace-only query is treated as cancelled."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_find_tree()
        for _ in range(30):
            await pilot.pause()
            if isinstance(app.screen, PromptDialog):
                break
        inp = app.screen.query_one(Input)
        inp.value = "   "
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()
            if not isinstance(app.screen, PromptDialog):
                break
        # No matches cached, no query stored.
        assert app._tree_find_matches == []
        assert app._tree_find_query is None


# ---------------------------------------------------------------------------
# action_next_match (Ctrl+G)
# ---------------------------------------------------------------------------


async def test_next_match_steps_through_cache(tmp_path: Path) -> None:
    """Ctrl+G advances to the next cached match; wraps at the end."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "photos" / "docs").mkdir(parents=True)
    (tmp_path / "work" / "docs").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _drive_find(pilot, app, "docs")
        n = len(app._tree_find_matches)
        assert n == 3

        first_match = app._tree_find_matches[0]
        tree = app.query_one(TreePane)
        assert tree.cursor_node.data == first_match
        assert app._tree_find_idx == 0

        # Step forward N times and verify we cycle through.
        seen = [first_match]
        for _ in range(n):
            app.action_next_match()
            for _ in range(15):
                await pilot.pause()
            seen.append(tree.cursor_node.data)

        # After n+1 presses (1 initial + n steps) we've wrapped back to the start.
        assert seen[0] == seen[-1]
        # The middle steps cover each match exactly once.
        assert sorted(set(seen[:-1])) == sorted(app._tree_find_matches)


async def test_next_match_with_no_search_active_flashes(tmp_path: Path) -> None:
    """Ctrl+G before any Ctrl+F is a flash-only no-op (doesn't crash)."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # No matches cached.
        assert app._tree_find_matches == []
        app.action_next_match()
        for _ in range(10):
            await pilot.pause()
        # No state mutation; still no matches.
        assert app._tree_find_matches == []


# ---------------------------------------------------------------------------
# Wiring: bindings + menu + help
# ---------------------------------------------------------------------------


def test_bindings_include_find_tree_and_next_match() -> None:
    """BINDINGS table carries the two new entries."""
    assert ("ctrl+f", "find_tree", "Find tree") in WTreeApp.BINDINGS
    assert ("ctrl+g", "next_match", "Next match") in WTreeApp.BINDINGS


def test_commands_menu_has_find_tree_and_next_match() -> None:
    """Commands menu lists the two new items (find_tree / next_match)."""
    commands = MENUS[1]
    actions = [i.action for i in commands.items]
    assert "find_tree" in actions
    assert "next_match" in actions


def test_help_content_mentions_ctrl_f_and_ctrl_g() -> None:
    """The Help modal's keymap reference includes Ctrl+F and Ctrl+G."""
    from wtree.widgets.help import _help_content

    text = str(_help_content())
    assert "Ctrl+F" in text
    assert "Ctrl+G" in text
