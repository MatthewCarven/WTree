"""Tests for incremental search (``/``).

Two layers:

* SearchBar widget unit tests - activation/deactivation, query
  mutation, match-info rendering. Driven via direct method calls and
  ``post_message`` so we don't need a pane to test the widget.
* Pilot-driven e2e: press ``/`` in a pane, type, watch the cursor
  jump. Both panes covered. Edge cases: empty query, no match, wrap,
  Esc restores, Enter commits.

Mirrors the shape of the other ``*_e2e.py`` test files: real
filesystem, real NativeSource, real Pilot.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


from wtree.app import WTreeApp
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.search_bar import SearchBar
from wtree.widgets.status_line import StatusLine
from wtree.widgets.tree_pane import TreePane


def _now() -> datetime:
    return datetime(2026, 5, 22, 18, 0, 0)


# ---------------------------------------------------------------------------
# SearchTarget protocol - unit tests
# ---------------------------------------------------------------------------


async def test_contents_pane_iter_searchable_yields_basenames(
    tmp_path: Path,
) -> None:
    """iter_searchable returns (row, basename) for non-error rows."""
    (tmp_path / "apple.txt").write_text("a")
    (tmp_path / "banana.txt").write_text("b")
    (tmp_path / "cherry").mkdir()

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        rows = list(pane.iter_searchable())
        # Labels are basenames (no trailing slash for dirs).
        labels = sorted(label for _, label in rows)
        assert labels == ["apple.txt", "banana.txt", "cherry"]
        # Row indices are 0-based and dense.
        indices = sorted(row for row, _ in rows)
        assert indices == [0, 1, 2]


async def test_tree_pane_iter_searchable_yields_visible_only(
    tmp_path: Path,
) -> None:
    """Tree iter_searchable doesn't walk collapsed subtrees."""
    (tmp_path / "alpha" / "inner").mkdir(parents=True)
    (tmp_path / "beta").mkdir()
    (tmp_path / "gamma").mkdir()

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        labels = sorted(label for _, label in tree.iter_searchable())
        # Root + three children visible; "inner" is inside an
        # unexpanded "alpha" so it should NOT be in the list.
        assert "alpha" in labels
        assert "beta" in labels
        assert "gamma" in labels
        assert "inner" not in labels


# ---------------------------------------------------------------------------
# SearchBar widget unit tests
# ---------------------------------------------------------------------------


async def test_search_bar_activate_takes_focus(tmp_path: Path) -> None:
    """activate() shows the bar and gives it keyboard focus."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(SearchBar)
        # Initially hidden, no focus.
        assert not bar.has_class("-active")
        assert app.focused is not bar

        bar.activate()
        await pilot.pause()
        assert bar.has_class("-active")
        assert app.focused is bar


async def test_search_bar_deactivate_clears_state(tmp_path: Path) -> None:
    """deactivate() hides the bar and resets internal state."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(SearchBar)
        bar.activate()
        bar.query = "stale"
        bar.update_match_info(3, 2)
        await pilot.pause()

        bar.deactivate()
        await pilot.pause()
        assert not bar.has_class("-active")
        assert bar.query == ""


# ---------------------------------------------------------------------------
# Action wiring - ``/`` activates the bar
# ---------------------------------------------------------------------------


async def test_slash_activates_search_bar_in_contents(
    tmp_path: Path,
) -> None:
    """Pressing / in the contents pane shows the search bar."""
    (tmp_path / "foo.txt").write_text("x")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        bar = app.query_one(SearchBar)
        assert bar.has_class("-active")
        assert app.focused is bar
        # StatusLine hidden while search is active.
        assert not app.query_one(StatusLine).display


async def test_slash_activates_search_bar_in_tree(tmp_path: Path) -> None:
    """Pressing / in the tree pane shows the search bar."""
    (tmp_path / "subdir").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Tree pane is focused by default after on_mount.
        await pilot.press("slash")
        await pilot.pause()
        bar = app.query_one(SearchBar)
        assert bar.has_class("-active")


# ---------------------------------------------------------------------------
# Typing matches: cursor jumps in the contents pane
# ---------------------------------------------------------------------------


async def test_typing_jumps_contents_cursor_to_first_match(
    tmp_path: Path,
) -> None:
    """Type 'ban' -> cursor moves to the row whose basename contains it."""
    (tmp_path / "apple.txt").write_text("a")
    (tmp_path / "banana.txt").write_text("b")
    (tmp_path / "cherry.txt").write_text("c")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        # Cursor starts at row 0 (apple.txt).
        assert pane.cursor_row == 0

        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("b")
        await pilot.press("a")
        await pilot.press("n")
        await pilot.pause()

        # Cursor jumped to banana row.
        assert pane.cursor_row == 1
        # Bar shows the query and match info.
        bar = app.query_one(SearchBar)
        assert bar.query == "ban"
        assert bar.match_total == 1
        assert bar.match_idx == 1


async def test_typing_is_case_insensitive(tmp_path: Path) -> None:
    """'REP' matches 'report.txt'."""
    (tmp_path / "alpha.txt").write_text("a")
    (tmp_path / "report.txt").write_text("r")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("R")
        await pilot.press("E")
        await pilot.press("P")
        await pilot.pause()
        assert pane.cursor_row == 1  # report.txt is row 1


async def test_no_match_leaves_cursor_and_flags_bar(tmp_path: Path) -> None:
    """A query with no matches keeps the cursor put; bar reports no_match."""
    (tmp_path / "apple.txt").write_text("a")
    (tmp_path / "banana.txt").write_text("b")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        starting_row = pane.cursor_row

        await pilot.press("slash")
        await pilot.pause()
        # 'xyz' doesn't match anything.
        await pilot.press("x")
        await pilot.press("y")
        await pilot.press("z")
        await pilot.pause()

        assert pane.cursor_row == starting_row
        bar = app.query_one(SearchBar)
        assert bar.no_match


# ---------------------------------------------------------------------------
# Down / Up step through matches with wrap
# ---------------------------------------------------------------------------


async def test_down_steps_through_matches(tmp_path: Path) -> None:
    """Multiple matches: Down advances; wrap to start at end."""
    (tmp_path / "report-1.txt").write_text("a")
    (tmp_path / "report-2.txt").write_text("b")
    (tmp_path / "report-3.txt").write_text("c")
    (tmp_path / "other.txt").write_text("x")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        pane = app.query_one(ContentsPane)

        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("r")
        await pilot.press("e")
        await pilot.press("p")
        await pilot.pause()
        first = pane.cursor_row

        await pilot.press("down")
        await pilot.pause()
        second = pane.cursor_row
        assert second != first

        await pilot.press("down")
        await pilot.pause()
        third = pane.cursor_row
        assert third != second

        # Wrap: one more Down returns to first match.
        await pilot.press("down")
        await pilot.pause()
        assert pane.cursor_row == first


async def test_up_steps_backward_with_wrap(tmp_path: Path) -> None:
    """Up on the first match wraps to the last."""
    (tmp_path / "report-1.txt").write_text("a")
    (tmp_path / "report-2.txt").write_text("b")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        pane = app.query_one(ContentsPane)

        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("r")
        await pilot.press("e")
        await pilot.press("p")
        await pilot.pause()
        first = pane.cursor_row

        # Up from first match wraps to last.
        await pilot.press("up")
        await pilot.pause()
        last = pane.cursor_row
        assert last != first


# ---------------------------------------------------------------------------
# Esc cancels, Enter commits
# ---------------------------------------------------------------------------


async def test_esc_restores_cursor(tmp_path: Path) -> None:
    """Esc restores the cursor to where it was at /-press."""
    (tmp_path / "apple.txt").write_text("a")
    (tmp_path / "banana.txt").write_text("b")
    (tmp_path / "cherry.txt").write_text("c")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        # Move to row 2 (cherry).
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        pre_search_row = pane.cursor_row

        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("a")  # 'a' matches apple - cursor jumps
        await pilot.pause()
        assert pane.cursor_row != pre_search_row

        await pilot.press("escape")
        await pilot.pause()
        # Cursor restored.
        assert pane.cursor_row == pre_search_row
        # Bar hidden.
        bar = app.query_one(SearchBar)
        assert not bar.has_class("-active")
        # StatusLine visible.
        assert app.query_one(StatusLine).display


async def test_enter_commits_keeps_cursor(tmp_path: Path) -> None:
    """Enter exits search, leaving the cursor at the current match."""
    (tmp_path / "apple.txt").write_text("a")
    (tmp_path / "banana.txt").write_text("b")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        pane = app.query_one(ContentsPane)
        assert pane.cursor_row == 0

        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        match_row = pane.cursor_row
        assert match_row == 1  # banana row

        await pilot.press("enter")
        await pilot.pause()
        # Cursor stayed.
        assert pane.cursor_row == match_row
        # Bar hidden.
        bar = app.query_one(SearchBar)
        assert not bar.has_class("-active")


# ---------------------------------------------------------------------------
# Backspace shrinks, empty query is no-op
# ---------------------------------------------------------------------------


async def test_backspace_shrinks_query(tmp_path: Path) -> None:
    """Backspace removes the last character."""
    (tmp_path / "apple.txt").write_text("a")
    (tmp_path / "banana.txt").write_text("b")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("b")
        await pilot.press("a")
        await pilot.press("n")
        await pilot.pause()
        bar = app.query_one(SearchBar)
        assert bar.query == "ban"

        await pilot.press("backspace")
        await pilot.pause()
        assert bar.query == "ba"
        await pilot.press("backspace")
        await pilot.press("backspace")
        await pilot.pause()
        assert bar.query == ""


# ---------------------------------------------------------------------------
# Tree-pane search
# ---------------------------------------------------------------------------


async def test_search_in_tree_pane_jumps_cursor(tmp_path: Path) -> None:
    """Tree-pane search moves the tree cursor onto a matching child."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "gamma").mkdir()

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Tree pane is focused by default after on_mount.
        tree = app.query_one(TreePane)
        # Move cursor to root first.
        starting_line = tree.cursor_line

        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("b")
        await pilot.press("e")
        await pilot.press("t")
        await pilot.pause()

        # Cursor moved to a non-root visible line.
        assert tree.cursor_line != starting_line
        # Confirm the labelled node is "beta".
        labels = {line: label for line, label in tree.iter_searchable()}
        assert labels[tree.cursor_line] == "beta"
