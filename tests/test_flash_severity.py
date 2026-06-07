"""Tests for severity-styled flash (design.md 2026-06-07).

``flash(severity=)`` maps info -> plain, warning -> yellow,
error -> bold red. The message body is markup-escaped before the
severity wrapper is applied, so user paths containing ``[i]`` render
literally instead of styling (or breaking) the line.
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.dir_picker import DriveChooserScreen
from wtree.widgets.status_line import StatusLine
from wtree.widgets.tree_pane import TreePane


# ---------------------------------------------------------------------------
# _styled_flash unit
# ---------------------------------------------------------------------------


def test_info_renders_plain() -> None:
    assert StatusLine._styled_flash("hello", "info") == "hello"


def test_warning_wraps_yellow() -> None:
    assert (
        StatusLine._styled_flash("careful", "warning")
        == "[yellow]careful[/yellow]"
    )


def test_error_wraps_bold_red() -> None:
    assert (
        StatusLine._styled_flash("broken", "error")
        == "[bold red]broken[/bold red]"
    )


def test_unknown_severity_renders_plain_not_raises() -> None:
    assert StatusLine._styled_flash("x", "catastrophic") == "x"


def test_message_markup_is_escaped() -> None:
    """A path with [i] must not be eaten as italics markup."""
    out = StatusLine._styled_flash(r"C:\foo[i]\bar", "warning")
    assert out.startswith("[yellow]")
    assert "[i]".join([]) == "" and r"\[i]" in out  # escaped form survives


# ---------------------------------------------------------------------------
# Integration - severity rides the call sites
# ---------------------------------------------------------------------------


async def test_view_nothing_is_warning(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))  # empty dir - no cursor entry
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_view()
        await pilot.pause()
        status = app.query_one(StatusLine)
        assert status._flash_message == "View: nothing under the cursor."
        assert status._flash_severity == "warning"


async def test_switch_drive_unavailable_is_error(
    tmp_path: Path, monkeypatch
) -> None:
    root_a = tmp_path / "a"
    root_a.mkdir()
    gone = str(tmp_path / "unplugged")
    import wtree.app as app_mod

    monkeypatch.setattr(
        app_mod, "list_drive_anchors",
        lambda current=None, **kw: [str(root_a), gone],
    )
    app = WTreeApp(root_path=str(root_a))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert isinstance(app.screen, DriveChooserScreen)
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        status = app.query_one(StatusLine)
        assert status._flash_severity == "error"
        assert "not available" in status._flash_message


async def test_cancel_stays_info(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(TreePane).focus()
        await pilot.pause()
        await pilot.press("l")       # Log prompt
        await pilot.pause()
        await pilot.press("escape")  # cancel
        await pilot.pause()
        status = app.query_one(StatusLine)
        assert status._flash_message == "Log: cancelled."
        assert status._flash_severity == "info"
