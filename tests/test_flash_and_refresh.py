"""Tests for ``StatusLine.flash`` and pane auto-refresh after ops.

Two related but separable features (2026-05-22):

* **flash** is the transient-status API on StatusLine. Replaces the
  "notify-toast" feedback that immediate user-action handlers used to
  emit ("Rename rejected", "Logged: NEW (ascended from OLD)", etc.).
  The flash holds for a configurable timeout and survives cursor
  moves; refresh_from() respects the active flash.

* **pane auto-refresh** is the post-op hook in ``_on_plan_complete``
  that re-shows the contents pane's current path. Before this, the
  user had to press Down/Tab/etc. to see the new state. Now ops
  appear/disappear from the pane immediately on queue drain.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.kind_chooser import KindChooserDialog
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.status_line import (
    DEFAULT_FLASH_TIMEOUT,
    StatusLine,
)
from wtree.widgets.tree_pane import TreePane


def _status_text(status: StatusLine) -> str:
    """Extract the visible text from a Static-derived StatusLine.

    Textual 8.x's ``Static`` doesn't expose ``renderable`` publicly;
    ``render()`` returns whatever the widget is currently displaying.
    Stringifying that yields the plain text (markup tags pass
    through, but for our assertions ``in`` checks ignore them).
    """
    return str(status.render())


# ---------------------------------------------------------------------------
# StatusLine.flash - unit tests
# ---------------------------------------------------------------------------


async def test_flash_shows_message(tmp_path: Path) -> None:
    """flash() puts the message into the StatusLine."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusLine)
        status.flash("hello world", timeout=10.0)
        await pilot.pause()
        assert "hello world" in _status_text(status)


async def test_flash_clears_after_timeout(tmp_path: Path) -> None:
    """After ``timeout`` seconds the flash clears and StatusLine reverts."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusLine)
        # Tiny timeout so the test doesn't drag.
        status.flash("transient", timeout=0.05)
        await pilot.pause()
        assert "transient" in _status_text(status)
        # Wait past the timeout.
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert "transient" not in _status_text(status)


async def test_flash_replaces_active_flash(tmp_path: Path) -> None:
    """A second flash() while one is active replaces the first."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusLine)
        status.flash("first", timeout=10.0)
        await pilot.pause()
        status.flash("second", timeout=10.0)
        await pilot.pause()
        rendered = _status_text(status)
        assert "second" in rendered
        assert "first" not in rendered


async def test_flash_holds_through_refresh_from(tmp_path: Path) -> None:
    """refresh_from() must not overwrite an active flash."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusLine)
        status.flash("sticky", timeout=10.0)
        await pilot.pause()
        # Simulate a cursor move / queue tick that calls refresh_from.
        status.refresh_from(app)
        await pilot.pause()
        assert "sticky" in _status_text(status)


async def test_flash_default_timeout_is_three_seconds() -> None:
    """The module constant matches the documented default."""
    assert DEFAULT_FLASH_TIMEOUT == 3.0


# ---------------------------------------------------------------------------
# WTreeApp.flash convenience
# ---------------------------------------------------------------------------


async def test_app_flash_routes_to_statusline(tmp_path: Path) -> None:
    """``app.flash("msg")`` shows up in the StatusLine."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.flash("via-app", timeout=10.0)
        await pilot.pause()
        status = app.query_one(StatusLine)
        assert "via-app" in _status_text(status)


# ---------------------------------------------------------------------------
# Flash integration with action handlers
# ---------------------------------------------------------------------------


async def test_rename_with_tagged_set_flashes(tmp_path: Path) -> None:
    """R with tags present surfaces via flash, not just a toast."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.txt").write_text("x")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("space")  # tag the file
        assert len(app.tagged_set) == 1
        await pilot.press("r")
        await pilot.pause()
        await pilot.pause()
        status = app.query_one(StatusLine)
        rendered = _status_text(status)
        # The flash text contains the rejection nudge.
        assert "Rename" in rendered
        assert "tags" in rendered


async def test_ascend_at_filesystem_root_flashes(tmp_path: Path) -> None:
    """Left at the FS root flashes the no-parent nudge."""
    app = WTreeApp(root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()
        status = app.query_one(StatusLine)
        assert "filesystem root" in _status_text(status)


async def test_ascend_success_flashes_logged_message(tmp_path: Path) -> None:
    """Successful ascend flashes 'Logged: NEW (ascended from OLD)'."""
    sub = tmp_path / "sub"
    sub.mkdir()
    app = WTreeApp(root_path=str(sub))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        await pilot.pause()
        status = app.query_one(StatusLine)
        rendered = _status_text(status)
        assert "Logged" in rendered
        assert "ascended" in rendered


# ---------------------------------------------------------------------------
# Pane auto-refresh after ops
# ---------------------------------------------------------------------------


async def _make_new(pilot, app, *, dir_or_file: str, name: str) -> None:
    """Helper: drive the chooser then prompt to create an entry."""
    await pilot.press("n")
    await pilot.pause()
    assert isinstance(app.screen, KindChooserDialog)
    await pilot.press(dir_or_file)
    await pilot.pause()
    assert isinstance(app.screen, PromptDialog)
    from textual.widgets import Input
    inp = app.screen.query_one(Input)
    inp.value = name
    await pilot.press("enter")
    await pilot.pause()


async def test_make_new_pane_auto_refreshes(tmp_path: Path) -> None:
    """After Make-new completes, the new file appears in the pane WITHOUT
    the user pressing any additional keys."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _make_new(pilot, app, dir_or_file="f", name="auto.txt")
        assert app.op_queue is not None
        await app.op_queue.wait_until_idle()
        await pilot.pause()
        await pilot.pause()

        # New file exists on disk.
        assert (tmp_path / "auto.txt").is_file()
        # AND the pane reflects it.
        pane = app.query_one(ContentsPane)
        paths = pane._row_paths
        assert any(p and p.endswith("auto.txt") for p in paths), paths


async def test_delete_pane_auto_refreshes(tmp_path: Path) -> None:
    """After Delete completes, the deleted row vanishes without the user
    pressing anything else."""
    (tmp_path / "doomed.txt").write_text("bye")
    (tmp_path / "keep.txt").write_text("stays")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        # Tag doomed.txt (first row alphabetically).
        await pilot.press("space")
        # Press D and confirm.
        await pilot.press("d")
        await pilot.pause()
        # Confirm dialog defaults to Yes via Enter.
        await pilot.press("enter")
        await pilot.pause()
        assert app.op_queue is not None
        await app.op_queue.wait_until_idle()
        await pilot.pause()
        await pilot.pause()

        # File deleted on disk.
        assert not (tmp_path / "doomed.txt").exists()
        # Pane no longer lists it.
        pane = app.query_one(ContentsPane)
        paths = pane._row_paths
        assert not any(p and p.endswith("doomed.txt") for p in paths)
        # The kept file is still visible.
        assert any(p and p.endswith("keep.txt") for p in paths)


async def test_auto_refresh_survives_failure(tmp_path: Path) -> None:
    """If the contents pane's current_path was deleted by the op, the
    refresh attempt shouldn't crash - it just shows nothing or stays
    on the (now-empty) directory. We don't yet handle the
    'current_path deleted under us' case fancily, but the refresh
    must not raise."""
    target = tmp_path / "dir-to-delete"
    target.mkdir()

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        # Tag the dir.
        await pilot.press("space")
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("enter")  # confirm
        await pilot.pause()
        assert app.op_queue is not None
        await app.op_queue.wait_until_idle()
        await pilot.pause()
        await pilot.pause()
        # The dir is gone; the app didn't crash.
        assert not target.exists()
        # The contents pane is still alive (we can query it).
        pane = app.query_one(ContentsPane)
        assert pane is not None
