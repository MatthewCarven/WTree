"""Tests for the F1 / Help menu About modal.

Three surfaces under test:

* :class:`HelpScreen` itself - the read-only modal with About info
  and a keymap reference. Verify the content surface and the
  dismiss-key contract (Esc / Q).
* The F1 binding on :class:`WTreeApp` - pressing F1 should push the
  HelpScreen as a modal screen.
* The Help menu - the third top-level menu, with an About item that
  dispatches ``action_help``.
"""

from __future__ import annotations

from pathlib import Path

from wtree import __version__
from wtree.app import WTreeApp
from wtree.widgets.help import HelpScreen, _help_content
from wtree.widgets.keybar import KeyBar, _WIRED
from wtree.widgets.menu_bar import MENUS
from wtree.widgets.menu_screen import MenuScreen


# ---------------------------------------------------------------------------
# Pure content (no app needed)
# ---------------------------------------------------------------------------


def test_help_content_includes_version() -> None:
    """The body shows the WTree version string so users know what they have."""
    text = str(_help_content())
    assert f"v{__version__}" in text


def test_help_content_has_all_sections() -> None:
    """Every keymap section header is present in the rendered body."""
    text = str(_help_content())
    for header in (
        "Navigation",
        "Tagging",
        "File operations",
        "Search",
        "Application",
        "Selection rule",
    ):
        assert header in text, f"missing section header: {header!r}"


def test_help_content_lists_core_bindings() -> None:
    """Spot-check that key bindings users actually need are listed."""
    text = str(_help_content())
    for binding in (
        "Tab",
        "Space",
        "Ctrl+A",
        "Ctrl+U",
        "Ctrl+I",
        "F5",
        "F6",
        "F9",
        "F10",
    ):
        assert binding in text, f"missing binding label: {binding!r}"


def test_help_content_documents_properties_row() -> None:
    """The Ctrl+I Properties row landed in the Application section (2026-05-25)."""
    text = str(_help_content())
    assert "Ctrl+I" in text
    assert "Properties" in text


# ---------------------------------------------------------------------------
# F1 opens the modal
# ---------------------------------------------------------------------------


async def test_f1_opens_help_screen(tmp_path: Path) -> None:
    """Pressing F1 pushes :class:`HelpScreen` as a modal."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)


async def test_question_mark_opens_help_screen(tmp_path: Path) -> None:
    """``?`` is the XTree primary binding for Help - also opens the modal."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)


async def test_esc_dismisses_help_screen(tmp_path: Path) -> None:
    """Esc on the help modal pops it off the screen stack."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert not any(isinstance(s, HelpScreen) for s in app.screen_stack)


async def test_q_dismisses_help_screen(tmp_path: Path) -> None:
    """``Q`` is the other dismiss key - mirrors the Viewer modal's contract."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("q")
        await pilot.pause()
        await pilot.pause()
        assert not any(isinstance(s, HelpScreen) for s in app.screen_stack)


# ---------------------------------------------------------------------------
# KeyBar reports F1 as wired
# ---------------------------------------------------------------------------


def test_keybar_wired_includes_f1() -> None:
    """The full F-row 1-10 is now wired - the keybar shows them all bold."""
    assert _WIRED == frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10})


async def test_keybar_renders_help_label(tmp_path: Path) -> None:
    """Sanity: KeyBar renders ``Help`` text (label position aside)."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(KeyBar)
        rendered = str(bar.render())
        assert "Help" in rendered


# ---------------------------------------------------------------------------
# Help menu (third top-level)
# ---------------------------------------------------------------------------


def test_menus_has_help_as_third() -> None:
    """``Help`` is the third top-level menu, after File and Commands."""
    assert [m.name for m in MENUS] == ["File", "Commands", "Help"]


def test_help_menu_has_about_item() -> None:
    """Help menu contains an About item that dispatches ``action_help``."""
    help_menu = MENUS[2]
    labels = [i.label for i in help_menu.items]
    assert labels == ["About"]
    about = help_menu.items[0]
    assert about.action == "help"
    assert about.accelerator == "a"


async def test_menu_help_about_opens_help_screen(tmp_path: Path) -> None:
    """End-to-end: F9 -> Right -> Right (to Help) -> Enter opens HelpScreen."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        # File (0) -> Commands (1) -> Help (2)
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert screen.active_menu == 2
        # About is the only item, cursor already on it.
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)


async def test_menu_help_letter_accelerator_opens_about(tmp_path: Path) -> None:
    """F9 -> Right -> Right (Help) -> 'a' activates About directly."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
