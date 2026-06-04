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


# ---------------------------------------------------------------------------
# Scan-dialog cancel-UI: ctx-chunked populate + gated expansion
# ---------------------------------------------------------------------------

from datetime import datetime

from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.widgets.scan_screen import ScanContext


def _dir_mock() -> MockSource:
    now = datetime(2026, 6, 4, 12, 0, 0)
    return MockSource(
        contents={
            "/big": [Entry("sub", Kind.DIR, 4096, now)],
            "/big/sub": [
                Entry("x", Kind.DIR, 4096, now),
                Entry("y", Kind.DIR, 4096, now),
            ],
        }
    )


async def _push_mock_picker(app, pilot, results):
    app.push_screen(
        DirPickerScreen(app._source, start_root="/big", reveal_target="/big"),
        callback=results.append,
    )
    await pilot.pause()
    await pilot.pause()
    return app.screen.query_one(_PickerTree)


async def test_picker_populate_cancel_leaves_node_empty():
    src = _dir_mock()
    app = WTreeApp(source=src, root_path="/big")
    results: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = await _push_mock_picker(app, pilot, results)
        sub = tree.root.children[0]
        assert str(sub.label) == "sub"
        ctx = ScanContext(path="/big/sub", method_label="mock")
        ctx.cancelled.set()  # pre-cancelled
        await tree._populate(sub, ctx=ctx)
        # Atomic: no children added, marker dropped so a re-expand retries.
        assert list(sub.children) == []
        assert sub.id not in tree._loaded


async def test_picker_populate_ctx_counts_and_commits():
    src = _dir_mock()
    app = WTreeApp(source=src, root_path="/big")
    results: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = await _push_mock_picker(app, pilot, results)
        sub = tree.root.children[0]
        ctx = ScanContext(path="/big/sub", method_label="mock")
        await tree._populate(sub, ctx=ctx)
        assert ctx.entries_seen == 2
        assert {str(c.label) for c in sub.children} == {"x", "y"}


async def test_picker_expand_routes_through_scan_gate(tmp_path: Path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    results: list = []
    calls: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        orig = app._run_scan_with_dialog

        async def spy(path, source, do_work, **kw):
            calls.append(path)
            return await orig(path, source, do_work, **kw)

        app._run_scan_with_dialog = spy
        await _push_picker(app, pilot, tmp_path, results)
        await pilot.press("down")     # onto 'a'
        await pilot.pause()
        await pilot.press("right")    # expand -> gated populate
        await pilot.pause()
        await pilot.pause()
        # The interactive expand of 'a' went through the gate.
        assert str(tmp_path / "a") in calls
        # And it actually populated (child 'b' present).
        tree = app.screen.query_one(_PickerTree)
        a_node = tree.root.children[0]
        assert {str(c.label) for c in a_node.children} == {"b"}


async def test_populate_dir_node_helper_direct():
    """The extracted shared helper, exercised directly: dir-only commit on a
    fresh node, and an atomic cancel that leaves the node empty + un-marked."""
    from wtree.widgets.scan_screen import populate_dir_node

    src = _dir_mock()
    app = WTreeApp(source=src, root_path="/big")
    results: list = []
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = await _push_mock_picker(app, pilot, results)

        # Commit on a fresh node.
        node = tree.root.add("probe", data="/big/sub", allow_expand=True)
        loaded: set[int] = set()
        await populate_dir_node(node, src, loaded)
        assert {str(c.label) for c in node.children} == {"x", "y"}
        assert node.id in loaded

        # Pre-cancelled: atomic - no children, marker dropped.
        node2 = tree.root.add("probe2", data="/big/sub", allow_expand=True)
        ctx = ScanContext(path="/big/sub", method_label="mock")
        ctx.cancelled.set()
        loaded2: set[int] = set()
        await populate_dir_node(node2, src, loaded2, ctx=ctx)
        assert list(node2.children) == []
        assert node2.id not in loaded2
