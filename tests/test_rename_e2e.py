"""End-to-end tests: press R, type new name, watch the file renamed.

Real filesystem + real NativeSource + real OperationQueue + real Pilot.
Mirrors the shape of the other e2e files. Rename's tighter constraints
(single-entry only, basename-only) get their own e2e coverage to
verify the action layer's rejection paths interact correctly with the
queue and status surfaces.
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input

from wtree.app import WTreeApp
from wtree.widgets.prompt import PromptDialog


async def _open_modal_and_submit(
    pilot, app: WTreeApp, new_name: str
) -> None:
    """Open the rename modal, set the input, submit."""
    await pilot.press("r")
    await pilot.pause()
    assert isinstance(app.screen, PromptDialog), (
        f"expected rename modal, got {app.screen}"
    )
    inp = app.screen.query_one(Input)
    inp.value = new_name
    await pilot.press("enter")
    await pilot.pause()


async def _drain_queue(app: WTreeApp) -> None:
    assert app.op_queue is not None
    await app.op_queue.wait_until_idle()


async def test_e2e_rename_file(tmp_path: Path) -> None:
    """Cursor on a file, R, type new name, original gone + new file there."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "before.txt").write_text("preserved")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        await _open_modal_and_submit(pilot, app, "after.txt")
        await _drain_queue(app)

    assert not (src / "before.txt").exists()
    assert (src / "after.txt").read_text() == "preserved"
    assert app.op_queue.completed[-1].all_succeeded


async def test_e2e_rename_dir(tmp_path: Path) -> None:
    """Rename a directory; subtree comes along."""
    root = tmp_path / "container"
    old = root / "old-name"
    (old / "child").mkdir(parents=True)
    (old / "child" / "data.txt").write_text("data")

    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await _open_modal_and_submit(pilot, app, "new-name")
        await _drain_queue(app)

    assert not old.exists()
    assert (root / "new-name" / "child" / "data.txt").read_text() == "data"


async def test_e2e_rename_with_tagged_set_rejected(tmp_path: Path) -> None:
    """With tags present, R is rejected; no modal, no plan."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "alpha.txt").write_text("a")
    (src / "beta.txt").write_text("b")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("space")  # tag row 0
        assert len(app.tagged_set) == 1
        await pilot.press("r")
        await pilot.pause()
        # No modal opened.
        assert not any(
            isinstance(s, PromptDialog) for s in app.screen_stack
        )

    # Files untouched.
    assert (src / "alpha.txt").read_text() == "a"
    assert (src / "beta.txt").read_text() == "b"
    # No plan.
    assert app.last_plan is None
    # Tag still present.
    assert len(app.tagged_set) == 1


async def test_e2e_rename_modal_default_is_basename(tmp_path: Path) -> None:
    """When the modal opens, it's pre-filled with the current basename."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "important.txt").write_text("x")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        modal_input = app.screen.query_one(Input)
        assert modal_input.value == "important.txt"
        await pilot.press("escape")  # cancel cleanly
        await pilot.pause()


async def test_e2e_rename_subtitle_returns_to_baseline(
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
        await _open_modal_and_submit(pilot, app, "y.txt")
        await _drain_queue(app)
        await pilot.pause()
        await pilot.pause()

    final = str(app.sub_title)
    assert "running" not in final
    assert "queued" not in final
    assert (src / "y.txt").read_text() == "x"
