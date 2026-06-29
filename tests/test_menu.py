"""Tests for the F9 menu bar.

Two surfaces:

* :class:`MenuBar` is the always-visible top chrome row. It's
  passive (no focus, no key handling); we just verify it renders
  the right menus + accelerator highlights.
* :class:`MenuScreen` is the modal pushed by F9. It owns key
  handling while open: arrows / Enter / Esc / letter accelerators.
  Dismisses with the chosen item's ``action`` name (or ``None``).

End-to-end: pressing F9 then navigating to Copy then pressing Enter
should call ``action_copy`` (which opens the Copy modal).
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.menu_bar import MENUS, MenuBar, render_menu_row
from wtree.widgets.menu_screen import MenuScreen
from wtree.widgets.prompt import PromptDialog


# ---------------------------------------------------------------------------
# MenuBar (passive widget)
# ---------------------------------------------------------------------------


async def test_menu_bar_renders_both_menus(tmp_path: Path) -> None:
    """The always-visible bar shows File + Commands + Help with accelerators."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(MenuBar)
        rendered = str(bar.render())
        assert "File" in rendered
        assert "Commands" in rendered
        assert "Help" in rendered


async def test_menus_definition_has_expected_items() -> None:
    """MENUS module-global: File, Commands, Help.

    File: New, View, Edit, Copy, Move, Rename, Delete, Properties,
          separator, Quit.
    Commands: Search, Find tree, Next match, Log new source, Switch
    drive, Refresh
              source, Untag all.
    Help: About.

    Items grew over 2026-05-23 sessions: Help (F1), Find tree + Next
    match (Ctrl+F), Log new source (L), Refresh source (Ctrl+R). And
    2026-05-25: Properties (Ctrl+I) added to the File menu.
    """
    assert [m.name for m in MENUS] == ["File", "Commands", "Help"]
    file_items = [i.label for i in MENUS[0].items]
    assert "New" in file_items
    assert "Copy" in file_items
    assert "Quit" in file_items
    assert "Properties" in file_items
    # Separator is an empty-label item with separator=True.
    assert any(i.separator for i in MENUS[0].items)
    commands_items = [i.label for i in MENUS[1].items]
    assert commands_items == [
        "Search",
        "Find tree",
        "Next match",
        "Log new source",
        "Switch drive",
        "Refresh source",
        "Progress dialog",
        "Last operation",
        "Untag all",
    ]
    help_items = [i.label for i in MENUS[2].items]
    assert help_items == ["About"]


async def test_menu_row_renderer_active_idx_highlights() -> None:
    """render_menu_row(active_idx=N) highlights the Nth top-level."""
    passive = str(render_menu_row(active_idx=None))
    active_file = str(render_menu_row(active_idx=0))
    active_commands = str(render_menu_row(active_idx=1))
    # All three contain both menu names.
    for s in (passive, active_file, active_commands):
        assert "File" in s
        assert "Commands" in s


# ---------------------------------------------------------------------------
# F9 activates the menu screen
# ---------------------------------------------------------------------------


async def test_f9_opens_menu_screen(tmp_path: Path) -> None:
    """Pressing F9 pushes :class:`MenuScreen` as a modal."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)


async def test_esc_closes_menu_screen(tmp_path: Path) -> None:
    """Esc on the MenuScreen dismisses it with no action dispatched."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        # No menu screen on the stack.
        assert not any(isinstance(s, MenuScreen) for s in app.screen_stack)


# ---------------------------------------------------------------------------
# Navigation within the menu
# ---------------------------------------------------------------------------


async def test_right_rotates_to_next_menu(tmp_path: Path) -> None:
    """Right arrow moves from File to Commands."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        assert screen.active_menu == 0  # File
        await pilot.press("right")
        await pilot.pause()
        assert screen.active_menu == 1  # Commands


async def test_left_rotates_wraps(tmp_path: Path) -> None:
    """Left from File wraps to the last menu (Help, since 2026-05-23)."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        assert screen.active_menu == 0
        await pilot.press("left")
        await pilot.pause()
        # Wraps to the last menu - Help (index 2) since 2026-05-23.
        assert screen.active_menu == len(MENUS) - 1


async def test_down_moves_cursor_in_dropdown(tmp_path: Path) -> None:
    """Down advances the cursor through the active menu's items."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        # File menu starts at item 0 (New).
        assert screen._cursor_idx == 0
        await pilot.press("down")
        await pilot.pause()
        assert screen._cursor_idx == 1


async def test_down_skips_separator(tmp_path: Path) -> None:
    """The cursor doesn't land on separator rows."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        # File items: New, View, Edit, Copy, Move, Rename, Delete,
        # Properties, [SEPARATOR], Quit. The separator is at index 8.
        # Step Down 8 times should land on Quit (index 9), skipping
        # the separator.
        for _ in range(8):
            await pilot.press("down")
        await pilot.pause()
        item = MENUS[0].items[screen._cursor_idx]
        assert item.label == "Quit"


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


async def test_letter_accelerator_activates_item(tmp_path: Path) -> None:
    """Pressing 'c' inside File menu activates Copy directly (no Enter)."""
    (tmp_path / "alpha.txt").write_text("a")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        # 'c' is Copy's accelerator inside the File menu.
        await pilot.press("c")
        await pilot.pause()
        await pilot.pause()
        # The menu dismissed and action_copy was dispatched -
        # which pushes the Copy destination prompt.
        assert isinstance(app.screen, PromptDialog)


async def test_enter_activates_current_item(tmp_path: Path) -> None:
    """With the cursor on Copy (file menu row 3), Enter dispatches it."""
    (tmp_path / "alpha.txt").write_text("a")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        # Step to Copy (index 3: New, View, Edit, Copy).
        for _ in range(3):
            await pilot.press("down")
        await pilot.pause()
        assert MENUS[0].items[screen._cursor_idx].label == "Copy"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        # Copy prompt opens.
        assert isinstance(app.screen, PromptDialog)


async def test_commands_menu_search_action(tmp_path: Path) -> None:
    """Navigating to Commands -> Search and Enter activates search."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        # Switch to Commands menu.
        await pilot.press("right")
        await pilot.pause()
        assert screen.active_menu == 1
        # First item is Search - already selected.
        item = MENUS[1].items[screen._cursor_idx]
        assert item.label == "Search"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        # Search bar should now be active.
        from wtree.widgets.search_bar import SearchBar
        bar = app.query_one(SearchBar)
        assert bar.has_class("-active")


async def test_untag_all_from_commands_menu(tmp_path: Path) -> None:
    """Tag something, then Commands -> Untag all should clear the set."""
    (tmp_path / "x.txt").write_text("x")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("space")  # tag
        assert len(app.tagged_set) == 1

        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("right")  # Commands
        await pilot.pause()
        # Commands menu items: Search (0), Find tree (1), Next match (2),
        # Log new source (3), Switch drive (4), Refresh source (5),
        # Progress dialog (6), Last operation (7), Untag all (8). The cursor
        # starts on Search; press Down eight times to reach Untag all.
        for _ in range(8):
            await pilot.press("down")
            await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert len(app.tagged_set) == 0
