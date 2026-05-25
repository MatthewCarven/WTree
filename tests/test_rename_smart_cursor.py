"""Tests for the smart-cursor behaviour in the Rename modal.

When the user presses ``R`` on ``report.txt``, the modal opens
pre-filled with the basename ``report.txt`` but with the stem
``report`` *selected* — typing any character then replaces the stem
while ``.txt`` is preserved. Mirrors Finder / Windows Explorer.

This file covers:

* the pure helper :func:`wtree.ops.rename.select_range_for_rename`
  across every documented edge case (dotfile, multi-dot, trailing dot,
  no-extension, directory);
* the :class:`PromptDialog` "select_initial" parameter — selection is
  applied on mount, cursor lands at ``end``;
* the action-layer wiring — pressing R on a real entry sets the
  modal's selection to the right range.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from textual.widgets import Input

from wtree.app import WTreeApp
from wtree.ops.rename import select_range_for_rename
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.widgets.prompt import PromptDialog


# ---------------------------------------------------------------------------
# Pure-helper unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,kind,expected",
    [
        # Normal file with extension: select the stem, keep ``.ext``.
        ("report.txt", Kind.FILE, (0, 6)),
        ("notes.md", Kind.FILE, (0, 5)),
        ("a.b", Kind.FILE, (0, 1)),
        # Multi-dot: only the LAST extension is preserved.
        # ``foo.tar.gz`` selects ``foo.tar``, keeps ``.gz``.
        ("foo.tar.gz", Kind.FILE, (0, 7)),
        # Dotfile (leading dot only): no real extension, select all.
        (".bashrc", Kind.FILE, (0, 7)),
        (".gitignore", Kind.FILE, (0, 10)),
        # Lone dot: degenerate dotfile-ish; select all.
        (".", Kind.FILE, (0, 1)),
        # No dot at all: select all (Makefile, script, etc.).
        ("Makefile", Kind.FILE, (0, 8)),
        ("README", Kind.FILE, (0, 6)),
        # Trailing dot: no real extension, select all.
        ("foo.", Kind.FILE, (0, 4)),
        ("weird.", Kind.FILE, (0, 6)),
        # Directory: select all even if it has a dot in the name.
        ("archive.zip", Kind.DIR, (0, 11)),
        ("my.project", Kind.DIR, (0, 10)),
        ("mydir", Kind.DIR, (0, 5)),
        # Empty: degenerate, return (0, 0).
        ("", Kind.FILE, (0, 0)),
        ("", Kind.DIR, (0, 0)),
    ],
)
def test_select_range_for_rename(
    name: str, kind: Kind, expected: tuple[int, int]
) -> None:
    """Stem-detection rule cases (Finder / Explorer convention)."""
    assert select_range_for_rename(name, kind) == expected


def test_select_range_for_rename_other_kinds_treated_like_file() -> None:
    """Non-FILE non-DIR kinds (SYMLINK, OTHER) behave like files —
    Finder treats a symlink with an extension the same as a file."""
    # SYMLINK to a .txt: select stem
    assert select_range_for_rename("link.txt", Kind.SYMLINK) == (0, 4)
    # OTHER (socket, fifo) with no real extension: select all
    assert select_range_for_rename("socket", Kind.OTHER) == (0, 6)


# ---------------------------------------------------------------------------
# PromptDialog: select_initial wiring
# ---------------------------------------------------------------------------


class _DialogHostApp(WTreeApp):
    """Bare WTreeApp - tests push the PromptDialog directly."""


@pytest.fixture
def empty_mock() -> MockSource:
    return MockSource(contents={"/": []})


async def test_prompt_dialog_select_initial_applies_selection(
    empty_mock: MockSource,
) -> None:
    """Pushing a PromptDialog with select_initial=(0, 6) on a 10-char
    initial value selects characters 0..6 and parks the cursor at 6."""
    app = _DialogHostApp(source=empty_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(
            PromptDialog(
                title="Test",
                initial="report.txt",
                select_initial=(0, 6),
            )
        )
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        inp = app.screen.query_one(Input)
        assert inp.value == "report.txt"
        # Selection covers stem; cursor at end of selection.
        assert tuple(inp.selection) == (0, 6)
        assert inp.cursor_position == 6


async def test_prompt_dialog_no_select_initial_lands_cursor_at_end(
    empty_mock: MockSource,
) -> None:
    """Default behaviour (select_initial=None) lands cursor at end-of-
    text; selection is empty. Preserves the long-standing "Save As"
    UX for non-Rename callers (Copy/Move dest, glob prompts, etc.)."""
    app = _DialogHostApp(source=empty_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(
            PromptDialog(title="Test", initial="hello")
        )
        await pilot.pause()
        inp = app.screen.query_one(Input)
        assert inp.cursor_position == 5
        assert inp.selection.is_empty


async def test_prompt_dialog_select_initial_clamps_out_of_bounds(
    empty_mock: MockSource,
) -> None:
    """Out-of-range select_initial values get clamped to [0, len].
    Defensive — a buggy caller passing (-1, 99) shouldn't crash the
    modal; the worst case is "selects nothing" or "selects the
    whole thing"."""
    app = _DialogHostApp(source=empty_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(
            PromptDialog(
                title="Test", initial="abc", select_initial=(-1, 99)
            )
        )
        await pilot.pause()
        inp = app.screen.query_one(Input)
        assert tuple(inp.selection) == (0, 3)
        assert inp.cursor_position == 3


async def test_prompt_dialog_select_initial_ignored_when_initial_empty(
    empty_mock: MockSource,
) -> None:
    """select_initial with no initial text is a no-op — there's
    nothing to select."""
    app = _DialogHostApp(source=empty_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(
            PromptDialog(
                title="Test", initial="", select_initial=(0, 5)
            )
        )
        await pilot.pause()
        inp = app.screen.query_one(Input)
        assert inp.value == ""
        assert inp.selection.is_empty


# ---------------------------------------------------------------------------
# action_rename: integration via pilot
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 25, 12, 0, 0)


@pytest.fixture
def file_with_ext_mock() -> MockSource:
    """Directory with a single file whose basename has a clear stem."""
    now = _now()
    return MockSource(
        contents={
            "/": [
                Entry("report.txt", Kind.FILE, 100, now),
            ],
        }
    )


@pytest.fixture
def multi_kind_mock() -> MockSource:
    """A row of entries covering each smart-cursor case the user might
    actually press R on: stem+ext file, dotfile, no-ext file, dir."""
    now = _now()
    return MockSource(
        contents={
            "/": [
                Entry("report.txt", Kind.FILE, 100, now),
                Entry(".bashrc", Kind.FILE, 50, now),
                Entry("Makefile", Kind.FILE, 80, now),
                Entry("mydir", Kind.DIR, 4096, now),
            ],
        }
    )


async def test_action_rename_pre_selects_stem_for_extension_file(
    file_with_ext_mock: MockSource,
) -> None:
    """Pressing R on ``report.txt`` opens the modal with the stem
    ``report`` selected and the cursor at offset 6."""
    app = WTreeApp(source=file_with_ext_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        inp = app.screen.query_one(Input)
        assert inp.value == "report.txt"
        assert tuple(inp.selection) == (0, 6)
        assert inp.cursor_position == 6
        await pilot.press("escape")  # clean cancel
        await pilot.pause()


async def test_action_rename_selects_whole_name_for_dotfile(
    multi_kind_mock: MockSource,
) -> None:
    """``.bashrc`` is a dotfile — leading dot is identity, not an
    extension. Select all so typing replaces the whole name."""
    app = WTreeApp(source=multi_kind_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")  # row 1 = .bashrc
        await pilot.press("r")
        await pilot.pause()
        inp = app.screen.query_one(Input)
        assert inp.value == ".bashrc"
        assert tuple(inp.selection) == (0, 7)
        await pilot.press("escape")
        await pilot.pause()


async def test_action_rename_selects_whole_name_for_no_extension(
    multi_kind_mock: MockSource,
) -> None:
    """``Makefile`` has no dot — select all."""
    app = WTreeApp(source=multi_kind_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("down")  # row 2 = Makefile
        await pilot.press("r")
        await pilot.pause()
        inp = app.screen.query_one(Input)
        assert inp.value == "Makefile"
        assert tuple(inp.selection) == (0, 8)
        await pilot.press("escape")
        await pilot.pause()


async def test_action_rename_selects_whole_name_for_directory(
    multi_kind_mock: MockSource,
) -> None:
    """Directories never have extensions by convention — select all."""
    app = WTreeApp(source=multi_kind_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        # Contents pane lists dirs first by default sort, so mydir is
        # likely at row 0. Walk the rows to find it.
        from wtree.widgets.contents_pane import ContentsPane
        contents = app.query_one(ContentsPane)
        # Move to find mydir (Kind.DIR).
        for _ in range(10):
            cur = contents.cursor_entry()
            if cur is not None and cur[0].endswith("mydir"):
                break
            await pilot.press("down")
            await pilot.pause()
        cur = contents.cursor_entry()
        assert cur is not None and cur[0].endswith("mydir")
        await pilot.press("r")
        await pilot.pause()
        inp = app.screen.query_one(Input)
        assert inp.value == "mydir"
        assert tuple(inp.selection) == (0, 5)
        await pilot.press("escape")
        await pilot.pause()


# ---------------------------------------------------------------------------
# End-to-end: stem-replace preserves the extension on real disk
# ---------------------------------------------------------------------------


async def test_e2e_rename_typing_replaces_stem_preserves_ext(
    tmp_path: Path,
) -> None:
    """Press R on ``report.txt``; the stem is selected. Type ``draft``;
    Textual's Input replaces the selected stem with that text so the
    field reads ``draft.txt``. Press Enter; the file lands at
    ``draft.txt``, ``.txt`` preserved. Mirrors Finder / Explorer."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "report.txt").write_text("hello")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        inp = app.screen.query_one(Input)
        # Sanity: stem should be selected.
        assert tuple(inp.selection) == (0, 6)
        # Typing five letters replaces the selected stem.
        await pilot.press("d")
        await pilot.press("r")
        await pilot.press("a")
        await pilot.press("f")
        await pilot.press("t")
        await pilot.pause()
        assert inp.value == "draft.txt"
        await pilot.press("enter")
        await pilot.pause()
        # Drain the queue so the rename actually lands on disk.
        assert app.op_queue is not None
        await app.op_queue.wait_until_idle()

    assert not (src / "report.txt").exists()
    assert (src / "draft.txt").read_text() == "hello"
