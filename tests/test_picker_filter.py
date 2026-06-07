"""Tests for picker type-to-filter (/) and files-greyed (f).

design.md 2026-06-07, closing picker phase 2. Layers:

* ``compute_matches`` units (the helper now shared by pane search and
  the picker filter).
* ``populate_dir_node(include_files=)`` - dim ``data=None`` file
  leaves after the dirs; default off keeps dir-only.
* Filter pilot integration - / activates the picker's own SearchBar;
  typing jumps the cursor and dims non-matches; Down cycles; Esc
  restores + clears; Enter commits in place; greyed files never match.
* Files-toggle pilot integration - f overlays / hides; files are
  non-selectable; expansion + cursor survive the rebuild.
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.dir_picker import DirPickerScreen, _PickerTree
from wtree.widgets.search_bar import SearchBar, compute_matches


# ---------------------------------------------------------------------------
# compute_matches units
# ---------------------------------------------------------------------------


def test_compute_matches_case_insensitive() -> None:
    rows = [(0, "Alpha"), (2, "beta"), (5, "ALPHABET")]
    matches, idx = compute_matches(rows, "alpha")
    assert matches == [0, 5]
    assert idx == 0


def test_compute_matches_anchor_picks_at_or_after() -> None:
    rows = [(0, "x1"), (3, "x2"), (7, "x3")]
    matches, idx = compute_matches(rows, "x", anchor=4)
    assert matches == [0, 3, 7]
    assert idx == 2  # first match at-or-after line 4 is line 7


def test_compute_matches_anchor_wraps_to_first() -> None:
    rows = [(0, "x1"), (3, "x2")]
    _, idx = compute_matches(rows, "x", anchor=9)
    assert idx == 0


def test_compute_matches_empty() -> None:
    assert compute_matches([(0, "abc")], "zzz") == ([], 0)


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


def _stage(tmp_path: Path) -> str:
    """root/ with apple/, banana/, cherry/ dirs + notes.txt, apple.log."""
    root = tmp_path / "root"
    for d in ("apple", "banana", "cherry"):
        (root / d).mkdir(parents=True)
    (root / "banana" / "split").mkdir()
    (root / "notes.txt").write_text("x")
    (root / "apple.log").write_text("x")
    return str(root)


async def _push_picker(app, pilot, root: str) -> DirPickerScreen:
    picker = DirPickerScreen(app._source, start_root=root)
    app.push_screen(picker)
    await pilot.pause()
    return picker


# ---------------------------------------------------------------------------
# Filter integration
# ---------------------------------------------------------------------------


async def test_slash_activates_and_jumps(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = await _push_picker(app, pilot, root)
        tree = picker.query_one(_PickerTree)
        bar = picker.query_one("#picker-search", SearchBar)

        await pilot.press("slash")
        await pilot.pause()
        assert bar.has_class("-active")

        await pilot.press("c")
        await pilot.pause()
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == str(Path(root) / "cherry")
        assert bar.match_total == 1


async def test_filter_dims_non_matches(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = await _push_picker(app, pilot, root)
        tree = picker.query_one(_PickerTree)

        await pilot.press("slash")
        await pilot.press("b")  # banana
        await pilot.pause()

        assert tree._filter_match_ids is not None
        match_ids = tree._filter_match_ids
        by_path = {
            c.data: c.id for c in tree.root.children if c.data is not None
        }
        assert by_path[str(Path(root) / "banana")] in match_ids
        assert by_path[str(Path(root) / "apple")] not in match_ids

        # render_label dims the non-match and not the match.
        apple = next(
            c for c in tree.root.children
            if c.data == str(Path(root) / "apple")
        )
        banana = next(
            c for c in tree.root.children
            if c.data == str(Path(root) / "banana")
        )
        from rich.style import Style

        apple_label = tree.render_label(apple, Style(), Style())
        banana_label = tree.render_label(banana, Style(), Style())
        assert any(
            span.style == "dim" or "dim" in str(span.style)
            for span in apple_label.spans
        )
        assert not any(
            "dim" in str(span.style) for span in banana_label.spans
        )


async def test_escape_restores_and_clears(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = await _push_picker(app, pilot, root)
        tree = picker.query_one(_PickerTree)
        bar = picker.query_one("#picker-search", SearchBar)
        pre = tree.cursor_line

        await pilot.press("slash")
        await pilot.press("c")
        await pilot.pause()
        assert tree.cursor_line != pre

        await pilot.press("escape")
        await pilot.pause()
        assert not bar.has_class("-active")
        assert tree._filter_match_ids is None
        assert tree.cursor_line == pre
        # Picker itself still open (Esc consumed by the bar).
        assert app.screen is picker


async def test_commit_leaves_cursor(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = await _push_picker(app, pilot, root)
        tree = picker.query_one(_PickerTree)

        await pilot.press("slash")
        await pilot.press("c")
        await pilot.pause()
        target = tree.cursor_node.data
        await pilot.press("enter")  # commit search
        await pilot.pause()
        assert tree.cursor_node.data == target
        assert tree._filter_match_ids is None
        # Second Enter picks it.
        await pilot.press("enter")
        await pilot.pause()


async def test_down_cycles_matches(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = await _push_picker(app, pilot, root)
        tree = picker.query_one(_PickerTree)
        bar = picker.query_one("#picker-search", SearchBar)

        await pilot.press("slash")
        await pilot.press("a")  # apple, banana (both contain 'a')
        await pilot.pause()
        assert bar.match_total == 2
        first = tree.cursor_node.data
        await pilot.press("down")
        await pilot.pause()
        second = tree.cursor_node.data
        assert second != first
        await pilot.press("down")
        await pilot.pause()
        assert tree.cursor_node.data == first  # wrapped


async def test_greyed_files_never_match(tmp_path: Path) -> None:
    """notes.txt is visible (files on) but '/notes' finds nothing."""
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = await _push_picker(app, pilot, root)
        bar = picker.query_one("#picker-search", SearchBar)

        await pilot.press("f")  # show files
        await pilot.pause()
        await pilot.pause()
        await pilot.press("slash")
        for ch in "notes":
            await pilot.press(ch)
        await pilot.pause()
        assert bar.no_match


# ---------------------------------------------------------------------------
# Files-greyed toggle
# ---------------------------------------------------------------------------


def _file_rows(tree: _PickerTree) -> list[str]:
    return [
        str(c.label) for c in tree.root.children if c.data is None
    ]


async def test_f_toggles_file_rows(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = await _push_picker(app, pilot, root)
        tree = picker.query_one(_PickerTree)
        assert _file_rows(tree) == []  # default off

        await pilot.press("f")
        await pilot.pause()
        await pilot.pause()
        rows = _file_rows(tree)
        assert "apple.log" in rows and "notes.txt" in rows
        # Dirs still first; files dim-styled at populate time.
        labels = [str(c.label) for c in tree.root.children]
        assert labels.index("apple") < labels.index("apple.log")

        await pilot.press("f")
        await pilot.pause()
        await pilot.pause()
        assert _file_rows(tree) == []


async def test_file_row_not_selectable(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = await _push_picker(app, pilot, root)
        tree = picker.query_one(_PickerTree)
        await pilot.press("f")
        await pilot.pause()
        await pilot.pause()

        file_node = next(
            c for c in tree.root.children
            if c.data is None and str(c.label) == "notes.txt"
        )
        tree.cursor_line = file_node.line
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is picker  # not dismissed


async def test_toggle_preserves_expansion_and_cursor(
    tmp_path: Path,
) -> None:
    root = _stage(tmp_path)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = await _push_picker(app, pilot, root)
        tree = picker.query_one(_PickerTree)
        split = str(Path(root) / "banana" / "split")
        assert await tree.reveal_path(split)
        await pilot.pause()
        assert tree.cursor_node.data == split

        await pilot.press("f")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # banana still expanded, cursor back on split.
        banana = next(
            c for c in tree.root.children
            if c.data == str(Path(root) / "banana")
        )
        assert banana.is_expanded
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == split
