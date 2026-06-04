"""End-to-end tests for Make-new: press N, pick D/F, type a name, watch
the entry land on the real filesystem.

Real filesystem + real NativeSource + real OperationQueue + real Pilot.
Mirrors the shape of the other ``*_e2e.py`` files. Make-new's twist:
the action drives TWO modal screens in sequence (chooser then prompt),
so each test walks through both.
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input

from wtree.app import WTreeApp
from wtree.widgets.kind_chooser import KindChooserDialog
from wtree.widgets.prompt import PromptDialog


async def _make_new(
    pilot, app: WTreeApp, *, dir_or_file: str, name: str
) -> None:
    """Walk the chooser + prompt sequence and submit.

    ``dir_or_file`` is the literal keystroke - "d" or "f" - so the
    test reads like the user's actual key presses.
    """
    await pilot.press("n")
    await pilot.pause()
    assert isinstance(app.screen, KindChooserDialog), (
        f"expected chooser, got {type(app.screen).__name__}"
    )
    await pilot.press(dir_or_file)
    await pilot.pause()
    assert isinstance(app.screen, PromptDialog), (
        f"expected prompt, got {type(app.screen).__name__}"
    )
    inp = app.screen.query_one(Input)
    inp.value = name
    await pilot.press("enter")
    await pilot.pause()


async def _drain_queue(app: WTreeApp) -> None:
    assert app.op_queue is not None
    await app.op_queue.wait_until_idle()


async def test_e2e_make_new_dir(tmp_path: Path) -> None:
    """N -> D -> 'newdir' -> Enter; directory exists on disk."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _make_new(pilot, app, dir_or_file="d", name="newdir")
        await _drain_queue(app)

    leaf = tmp_path / "newdir"
    assert leaf.is_dir()
    assert app.op_queue.completed[-1].all_succeeded


async def test_e2e_make_new_file(tmp_path: Path) -> None:
    """N -> F -> 'new.txt' -> Enter; empty file exists on disk."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _make_new(pilot, app, dir_or_file="f", name="new.txt")
        await _drain_queue(app)

    leaf = tmp_path / "new.txt"
    assert leaf.is_file()
    assert leaf.read_bytes() == b""


async def test_e2e_make_new_lenient_subdirs(tmp_path: Path) -> None:
    """N -> F -> 'a/b/c.txt' -> Enter; intermediate dirs are created."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _make_new(pilot, app, dir_or_file="f", name="a/b/c.txt")
        await _drain_queue(app)

    leaf = tmp_path / "a" / "b" / "c.txt"
    assert leaf.is_file()
    assert (tmp_path / "a").is_dir()
    assert (tmp_path / "a" / "b").is_dir()


async def test_e2e_make_new_chooser_cancel_no_op(tmp_path: Path) -> None:
    """N -> Esc on the chooser: nothing lands, no plan."""
    # Seed with one file so we can assert nothing else appeared.
    (tmp_path / "before.txt").write_text("kept")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, KindChooserDialog)
        await pilot.press("escape")
        await pilot.pause()

    # No new files appeared.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["before.txt"]
    assert app.last_plan is None


async def test_e2e_make_new_prompt_cancel_no_op(tmp_path: Path) -> None:
    """N -> F -> Esc on the name prompt: nothing lands, no plan."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        await pilot.press("escape")
        await pilot.pause()

    assert list(tmp_path.iterdir()) == []
    assert app.last_plan is None


async def test_e2e_make_new_clobber_skip_keeps_original(tmp_path: Path) -> None:
    """Make-new onto an existing dir surfaces ConflictDialog; Skip (the
    default) leaves the existing entry untouched and enqueues nothing."""
    from wtree.widgets.conflict import ConflictDialog

    (tmp_path / "exists").mkdir()
    (tmp_path / "exists" / "marker.txt").write_text("preserved")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _make_new(pilot, app, dir_or_file="d", name="exists")
        await pilot.pause()
        assert isinstance(app.screen, ConflictDialog)
        await pilot.press("enter")  # commit; default is Skip
        await pilot.pause()

    # Skip drops the only item -> nothing to do, original intact.
    assert (tmp_path / "exists" / "marker.txt").read_text() == "preserved"


async def test_e2e_make_new_clobber_overwrite_replaces(tmp_path: Path) -> None:
    """Overwrite clears the existing entry and drops an empty new one in
    its place (replace, not merge)."""
    from wtree.widgets.conflict import ConflictDialog

    (tmp_path / "exists").mkdir()
    (tmp_path / "exists" / "marker.txt").write_text("gone")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _make_new(pilot, app, dir_or_file="d", name="exists")
        await pilot.pause()
        assert isinstance(app.screen, ConflictDialog)
        await pilot.press("o")      # set current row -> Overwrite
        await pilot.press("enter")  # commit
        await pilot.pause()
        await _drain_queue(app)

    leaf = tmp_path / "exists"
    assert leaf.is_dir()
    # The old contents are gone - replaced by an empty dir.
    assert list(leaf.iterdir()) == []


async def test_e2e_make_new_clobber_rename_duplicates(tmp_path: Path) -> None:
    """Rename creates 'exists (1)' and leaves the original alone."""
    from wtree.widgets.conflict import ConflictDialog

    (tmp_path / "exists").mkdir()
    (tmp_path / "exists" / "marker.txt").write_text("preserved")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _make_new(pilot, app, dir_or_file="d", name="exists")
        await pilot.pause()
        assert isinstance(app.screen, ConflictDialog)
        await pilot.press("r")      # set current row -> Rename
        await pilot.press("enter")  # commit
        await pilot.pause()
        await _drain_queue(app)

    assert (tmp_path / "exists" / "marker.txt").read_text() == "preserved"
    assert (tmp_path / "exists (1)").is_dir()


async def test_e2e_make_new_subtitle_returns_to_baseline(
    tmp_path: Path,
) -> None:
    """After the queue drains, subtitle no longer mentions running/queued."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _make_new(pilot, app, dir_or_file="f", name="x.txt")
        await _drain_queue(app)
        await pilot.pause()
        await pilot.pause()

    final = str(app.sub_title)
    assert "running" not in final
    assert "queued" not in final
    assert (tmp_path / "x.txt").is_file()
