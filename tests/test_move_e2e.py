"""End-to-end tests: press M, set a destination, watch bytes move.

Real filesystem (tmp_path) + real NativeSource + real OperationQueue +
real Pilot. Same shape as ``test_copy_e2e.py`` - one of these per
operation pins down the full chain (Selection rule -> modal -> planner
-> queue -> executor -> filesystem).

Move-specific assertions versus copy:
* source no longer exists at the original path post-drain
* the queue sees one PlanItem per top-level tag, not one per leaf
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input

from wtree.app import WTreeApp
from wtree.widgets.prompt import PromptDialog


async def _open_modal_and_submit(pilot, app: WTreeApp, destination: str) -> None:
    """Open the move modal (assumes M is bound), set its input, submit."""
    await pilot.press("m")
    await pilot.pause()
    assert isinstance(app.screen, PromptDialog), (
        f"expected modal, got {app.screen}"
    )
    inp = app.screen.query_one(Input)
    inp.value = destination
    await pilot.press("enter")
    await pilot.pause()


async def _drain_queue(app: WTreeApp) -> None:
    assert app.op_queue is not None
    await app.op_queue.wait_until_idle()


async def test_e2e_move_cursor_entry_relocates_file(tmp_path: Path) -> None:
    """Cursor on a file, M, type a destination, file ends up at dst
    AND is gone from src."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "moveme.txt").write_text("relocating")
    dst = tmp_path / "dst"
    dst.mkdir()

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        await _open_modal_and_submit(pilot, app, str(dst))
        await _drain_queue(app)

    # File moved, not copied.
    assert (dst / "moveme.txt").read_text() == "relocating"
    assert not (src / "moveme.txt").exists()
    assert app.op_queue.completed[-1].all_succeeded
    assert app.last_result is not None
    assert app.last_result.all_succeeded


async def test_e2e_move_dir_with_subtree(tmp_path: Path) -> None:
    """Tag a directory, move it, verify the whole subtree relocated
    and the source is empty."""
    src = tmp_path / "src"
    (src / "deep").mkdir(parents=True)
    (src / "a.txt").write_text("a")
    (src / "deep" / "b.txt").write_text("b")
    dst = tmp_path / "dst"
    dst.mkdir()

    # Launch with parent so 'src' is a child row we can tag.
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        # Rows in tmp_path sorted alpha: dst/, src/.
        # Cursor starts at row 0 = dst. Move down to row 1 = src and tag it.
        await pilot.press("down")
        await pilot.press("space")
        assert len(app.tagged_set) == 1
        await _open_modal_and_submit(pilot, app, str(dst))
        await _drain_queue(app)

    # Whole subtree relocated.
    assert (dst / "src" / "a.txt").read_text() == "a"
    assert (dst / "src" / "deep" / "b.txt").read_text() == "b"
    # Source dir gone entirely.
    assert not src.exists()
    # Tagged set was cleared post-enqueue.
    assert len(app.tagged_set) == 0
    # Headline contract: one PlanItem moved a whole subtree.
    completed = app.op_queue.completed[-1]
    assert len(completed.plan.items) == 1


async def test_e2e_two_moves_serialize(tmp_path: Path) -> None:
    """Two M presses, two destinations, both files relocated FIFO."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "one.txt").write_text("1")
    (src / "two.txt").write_text("2")
    dst_a = tmp_path / "a"
    dst_b = tmp_path / "b"
    dst_a.mkdir()
    dst_b.mkdir()

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # contents pane, cursor row 0 = one.txt
        await pilot.pause()

        await _open_modal_and_submit(pilot, app, str(dst_a))
        # Press down to advance the cursor to row 1 = two.txt. The
        # contents pane doesn't auto-refresh after a move (no FS-
        # watching in v0), so row 0 still shows the now-moved one.txt;
        # advancing the cursor reliably points at the still-present file.
        await pilot.press("down")
        await _open_modal_and_submit(pilot, app, str(dst_b))
        await _drain_queue(app)

    assert (dst_a / "one.txt").read_text() == "1"
    assert (dst_b / "two.txt").read_text() == "2"
    assert not (src / "one.txt").exists()
    assert not (src / "two.txt").exists()
    assert len(app.op_queue.completed) == 2


async def test_e2e_move_subtitle_returns_to_baseline(tmp_path: Path) -> None:
    """After the queue drains, subtitle no longer mentions running/queued."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.txt").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await _open_modal_and_submit(pilot, app, str(dst))
        await _drain_queue(app)
        await pilot.pause()
        await pilot.pause()

    final = str(app.sub_title)
    assert "running" not in final
    assert "queued" not in final
    assert (dst / "x.txt").read_text() == "x"
