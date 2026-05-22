"""End-to-end tests for V / F3 action_view.

The viewer screen itself is tested in ``test_viewer.py``; these tests
verify the action layer: cursor validation, kind dispatch, and the
push-screen flow.
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.viewer import ViewerScreen


async def test_v_on_file_opens_viewer(tmp_path: Path) -> None:
    """V (or F3) on a file row opens the viewer with that file."""
    (tmp_path / "show.txt").write_text("visible", encoding="utf-8")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, ViewerScreen)
        # Dismiss to leave the app in a clean state for teardown.
        await pilot.press("escape")
        await pilot.pause()


async def test_f3_alias_also_opens_viewer(tmp_path: Path) -> None:
    """F3 is bound to the same action as V."""
    (tmp_path / "x.txt").write_text("y", encoding="utf-8")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, ViewerScreen)
        await pilot.press("escape")
        await pilot.pause()


async def test_v_on_directory_does_not_open_viewer(tmp_path: Path) -> None:
    """V on a DIR row should not push the viewer - directories have
    Enter for navigation, not V."""
    (tmp_path / "subdir").mkdir()
    (tmp_path / "after.txt").write_text("z", encoding="utf-8")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        # Row 0 is subdir/ (dirs sort first).
        await pilot.press("v")
        await pilot.pause()
        # No viewer on the stack.
        assert not any(
            isinstance(s, ViewerScreen) for s in app.screen_stack
        )


async def test_v_with_empty_pane_does_not_open_viewer(tmp_path: Path) -> None:
    """V with no cursor entry emits a warning and doesn't open anything."""
    # tmp_path is empty.
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        assert not any(
            isinstance(s, ViewerScreen) for s in app.screen_stack
        )


async def test_v_then_esc_returns_focus_to_pane(tmp_path: Path) -> None:
    """After dismissing the viewer, the underlying app screen is active
    again (no lingering modal on the stack)."""
    (tmp_path / "small.txt").write_text("hi", encoding="utf-8")

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, ViewerScreen)
        await pilot.press("escape")
        await pilot.pause()
        # Default screen is back on top - no ViewerScreen remains in stack.
        assert not any(
            isinstance(s, ViewerScreen) for s in app.screen_stack
        )
