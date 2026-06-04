"""DirPickerScreen - the Copy/Move destination browser.

Driven through a real ``WTreeApp`` + ``NativeSource`` on ``tmp_path``. The
picker is pushed with ``app.push_screen(..., callback=...)`` (no worker
needed) and its dismiss value captured in a list; navigation is real
keystrokes via the pilot. A couple of tests exercise the app-level browse
loop wiring (Ctrl+B from the Copy prompt opens the picker, Esc returns).
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input

from wtree.app import WTreeApp
from wtree.widgets.dir_picker import DirPickerScreen, _PickerTree
from wtree.widgets.prompt import PromptDialog


async def _push_picker(app, pilot, tmp: Path, results: list, **kw):
    app.push_screen(
        DirPickerScreen(
            app._source,
            start_root=str(tmp),
            reveal_target=str(tmp),
            **kw,
        ),
        callback=results.append,
    )
    await pilot.pause()
    await pilot.pause()
    assert isinstance(app.screen, DirPickerScreen)


# ---------------------------------------------------------------------------
# Navigation + pick
# ---------------------------------------------------------------------------


async def test_picker_pick_subdir(tmp_path: Path):
    (tmp_path / "dest").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "a.txt").write_text("x")  # file: must NOT appear in the tree

    app = WTreeApp(root_path=str(tmp_path))
    results: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_picker(app, pilot, tmp_path, results)
        # Root cursor; first child is 'dest' (alpha sort, before 'other').
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert results == [str(tmp_path / "dest")]


async def test_picker_dir_only_excludes_files(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "f1.txt").write_text("x")
    (tmp_path / "f2.log").write_text("y")

    app = WTreeApp(root_path=str(tmp_path))
    results: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_picker(app, pilot, tmp_path, results)
        tree = app.screen.query_one(_PickerTree)
        labels = {str(c.label) for c in tree.root.children}
    assert labels == {"sub"}  # files filtered out


async def test_picker_escape_cancels(tmp_path: Path):
    (tmp_path / "dest").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    results: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_picker(app, pilot, tmp_path, results)
        await pilot.press("escape")
        await pilot.pause()
    assert results == [None]


async def test_picker_drill_in_and_pick_nested(tmp_path: Path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    results: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_picker(app, pilot, tmp_path, results)
        await pilot.press("down")      # onto 'a'
        await pilot.pause()
        await pilot.press("right")     # expand 'a'
        await pilot.pause()
        await pilot.press("right")     # drill into first child 'b'
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert results == [str(tmp_path / "a" / "b")]


# ---------------------------------------------------------------------------
# Make-new folder ('n')
# ---------------------------------------------------------------------------


async def test_picker_make_folder_creates_and_selects(tmp_path: Path):
    app = WTreeApp(root_path=str(tmp_path))
    results: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_picker(app, pilot, tmp_path, results)
        await pilot.press("n")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        app.screen.query_one(Input).value = "newdir"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        # Back in the picker, the new dir is created + selected -> Enter picks.
        assert isinstance(app.screen, DirPickerScreen)
        await pilot.press("enter")
        await pilot.pause()
    assert (tmp_path / "newdir").is_dir()
    assert results == [str(tmp_path / "newdir")]


async def test_picker_make_folder_rejects_existing_then_accepts(tmp_path: Path):
    (tmp_path / "taken").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    results: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_picker(app, pilot, tmp_path, results)
        await pilot.press("n")
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one(Input).value = "taken"      # already exists
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)        # re-prompted
        app.screen.query_one(Input).value = "fresh"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, DirPickerScreen)
    assert (tmp_path / "fresh").is_dir()
    assert (tmp_path / "taken").is_dir()


async def test_picker_make_folder_subpath(tmp_path: Path):
    app = WTreeApp(root_path=str(tmp_path))
    results: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_picker(app, pilot, tmp_path, results)
        await pilot.press("n")
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one(Input).value = "x/y/z"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
    assert (tmp_path / "x" / "y" / "z").is_dir()


# ---------------------------------------------------------------------------
# App browse-loop wiring (Ctrl+B from the Copy destination prompt)
# ---------------------------------------------------------------------------


async def test_copy_prompt_ctrl_b_opens_picker_then_esc_returns(tmp_path: Path):
    (tmp_path / "a.txt").write_text("data")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")          # focus contents pane (cursor entry)
        await pilot.pause()
        await pilot.press("c")            # Copy -> destination prompt
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        await pilot.press("ctrl+b")       # browse
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, DirPickerScreen)
        await pilot.press("escape")       # cancel browse -> back to prompt
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
