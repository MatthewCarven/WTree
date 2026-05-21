"""End-to-end tests: press D, confirm, watch files disappear.

Real filesystem + real NativeSource + real OperationQueue + real Pilot.
Mirrors ``test_copy_e2e.py`` / ``test_move_e2e.py`` shape - one of these
per operation pins down the full chain (Selection rule -> confirm ->
planner -> queue -> executor -> filesystem).

Delete-specific assertions vs copy/move:
* source no longer exists at original path post-drain
* no destination to inspect; we instead check that the source vanished
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.confirm import ConfirmDialog


async def _open_confirm_and_yes(pilot, app: WTreeApp) -> None:
    """Open the delete confirm dialog and press Y."""
    await pilot.press("d")
    await pilot.pause()
    assert isinstance(app.screen, ConfirmDialog), (
        f"expected confirm dialog, got {app.screen}"
    )
    await pilot.press("y")
    await pilot.pause()


async def _drain_queue(app: WTreeApp) -> None:
    assert app.op_queue is not None
    await app.op_queue.wait_until_idle()


async def test_e2e_delete_cursor_entry_removes_file(tmp_path: Path) -> None:
    """Cursor on a file, D, Y, file is gone."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "doomed.txt").write_text("erase me")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        await _open_confirm_and_yes(pilot, app)
        await _drain_queue(app)

    assert not (src / "doomed.txt").exists()
    assert app.op_queue.completed[-1].all_succeeded
    assert app.last_result is not None
    assert app.last_result.all_succeeded


async def test_e2e_delete_dir_with_subtree(tmp_path: Path) -> None:
    """Tag a directory, delete it, whole subtree gone (one PlanItem)."""
    src_root = tmp_path / "container"
    target = src_root / "target"
    (target / "deep").mkdir(parents=True)
    (target / "a.txt").write_text("a")
    (target / "deep" / "b.txt").write_text("b")

    app = WTreeApp(root_path=str(src_root))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        # Cursor at row 0 = target dir (only entry in container).
        await pilot.press("space")  # tag it
        assert len(app.tagged_set) == 1
        await _open_confirm_and_yes(pilot, app)
        await _drain_queue(app)

    # Whole subtree gone.
    assert not target.exists()
    # Container survives.
    assert src_root.exists()
    # Tagged set cleared on enqueue.
    assert len(app.tagged_set) == 0
    # Headline contract: one PlanItem deleted a whole subtree.
    completed = app.op_queue.completed[-1]
    assert len(completed.plan.items) == 1


async def test_e2e_delete_n_keeps_file(tmp_path: Path) -> None:
    """Pressing N on the confirm dialog leaves the file alone."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "safe.txt").write_text("keep me")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("n")
        await pilot.pause()
        await pilot.pause()
        await _drain_queue(app)

    # File survives.
    assert (src / "safe.txt").read_text() == "keep me"
    # No plan was enqueued.
    assert app.last_plan is None
    assert app.op_queue is not None
    assert app.op_queue.completed == []


async def test_e2e_two_deletes_serialize(tmp_path: Path) -> None:
    """Two D presses, both files relocated FIFO."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "one.txt").write_text("1")
    (src / "two.txt").write_text("2")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # cursor row 0 = one.txt
        await pilot.pause()

        await _open_confirm_and_yes(pilot, app)
        # Contents pane doesn't auto-refresh - press down to move to
        # row 1 (still pointing at two.txt's still-real DataTable row).
        await pilot.press("down")
        await _open_confirm_and_yes(pilot, app)
        await _drain_queue(app)

    assert not (src / "one.txt").exists()
    assert not (src / "two.txt").exists()
    assert len(app.op_queue.completed) == 2


async def test_e2e_delete_subtitle_returns_to_baseline(
    tmp_path: Path,
) -> None:
    """After the queue drains, subtitle no longer mentions running/queued."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.txt").write_text("x")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await _open_confirm_and_yes(pilot, app)
        await _drain_queue(app)
        await pilot.pause()
        await pilot.pause()

    final = str(app.sub_title)
    assert "running" not in final
    assert "queued" not in final
    assert not (src / "x.txt").exists()
