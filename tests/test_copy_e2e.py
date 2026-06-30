"""End-to-end tests: press C, set a destination, watch bytes land.

Real filesystem (tmp_path) + real NativeSource + real OperationQueue +
real Pilot. These are the only tests that verify the full chain works -
the unit tests above cover each piece in isolation but a wiring bug
between them would slip through.

Implementation note: setting ``Input.value`` directly via the widget
API is faster than ``pilot.press()`` typing char-by-char and avoids
the slash-character key-name quirk. The user-facing flow is the same;
the Input fires ``Submitted`` on Enter regardless of how its ``value``
was set.
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input

from wtree.app import WTreeApp
from wtree.widgets.prompt import PromptDialog


async def _open_modal_and_submit(pilot, app: WTreeApp, destination: str) -> None:
    """Open the copy modal (assumes C is bound), set its input, submit."""
    await pilot.press("c")
    await pilot.pause()
    assert isinstance(app.screen, PromptDialog), f"expected modal, got {app.screen}"
    inp = app.screen.query_one(Input)
    inp.value = destination
    await pilot.press("enter")
    await pilot.pause()


async def _drain_queue(app: WTreeApp) -> None:
    """Block until the app's op_queue has finished every enqueued plan."""
    assert app.op_queue is not None
    await app.op_queue.wait_until_idle()


async def test_e2e_copy_cursor_entry_lands_bytes(tmp_path: Path) -> None:
    """Cursor on a file, C, type a destination, file appears at destination."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "hello.txt").write_text("greetings")
    dst = tmp_path / "dst"
    dst.mkdir()

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        await _open_modal_and_submit(pilot, app, str(dst))
        await _drain_queue(app)

    assert (dst / "hello.txt").read_text() == "greetings"
    assert app.op_queue.completed[-1].all_succeeded
    assert app.last_result is not None
    assert app.last_result.all_succeeded


async def test_e2e_dir_with_subtree(tmp_path: Path) -> None:
    """Tag a directory, copy it, verify the whole subtree lands."""
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
        # Rows in tmp_path: dst/, src/  (dirs sorted alphabetically).
        # Cursor starts at row 0 = dst/. Tag dst (no - we want src!).
        # Move to row 1 = src and tag it.
        await pilot.press("down")
        await pilot.press("space")
        assert len(app.tagged_set) == 1
        await _open_modal_and_submit(pilot, app, str(dst))
        await _drain_queue(app)

    assert (dst / "src" / "a.txt").read_text() == "a"
    assert (dst / "src" / "deep" / "b.txt").read_text() == "b"
    # Tagged set was cleared post-enqueue.
    assert len(app.tagged_set) == 0


async def test_e2e_two_copies_serialize(tmp_path: Path) -> None:
    """Two C presses, two destinations, both files end up there."""
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
        await pilot.press("down")  # move cursor to two.txt
        await _open_modal_and_submit(pilot, app, str(dst_b))
        await _drain_queue(app)

    assert (dst_a / "one.txt").read_text() == "1"
    assert (dst_b / "two.txt").read_text() == "2"
    assert len(app.op_queue.completed) == 2
    # FIFO: first completion is the one we enqueued first.
    first_dst = app.op_queue.completed[0].plan.items[0].dst_path
    second_dst = app.op_queue.completed[1].plan.items[0].dst_path
    assert str(dst_a) in first_dst
    assert str(dst_b) in second_dst


async def test_e2e_subtitle_returns_to_baseline_when_idle(tmp_path: Path) -> None:
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
        # Give the on_plan_complete callback a chance to update subtitle.
        await pilot.pause()
        await pilot.pause()

    final = str(app.sub_title)
    assert "running" not in final
    assert "queued" not in final
    assert (dst / "x.txt").read_text() == "x"


# ---------------------------------------------------------------------------
# Validation-on-Enter for Copy/Move destinations (2026-06-30, Session 7)
# ---------------------------------------------------------------------------

import os as _os  # noqa: E402

import pytest as _pytest  # noqa: E402

from wtree.app import WTreeApp as _App  # noqa: E402

_needs_perms = _pytest.mark.skipif(
    hasattr(_os, "geteuid") and _os.geteuid() == 0, reason="root ignores mode bits"
)


def test_destination_ok_for_existing_dir(tmp_path):
    assert _App._destination_error(str(tmp_path)) is None


def test_destination_ok_for_new_subdir(tmp_path):
    # The executor makedirs missing leaf dirs, so a not-yet-existing dest is
    # fine as long as its nearest existing ancestor is a writable directory.
    assert _App._destination_error(str(tmp_path / "new1" / "new2")) is None


def test_destination_rejects_file(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    err = _App._destination_error(str(f))
    assert err and "not a directory" in err


@_needs_perms
def test_destination_rejects_readonly_parent(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    _os.chmod(ro, 0o500)
    try:
        err = _App._destination_error(str(ro / "sub"))
        assert err and "not writable" in err
    finally:
        _os.chmod(ro, 0o700)
