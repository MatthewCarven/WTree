"""Tests for the menu polish pass (design.md 2026-06-07).

Dropdown positioned under its menu name; Down/Up wrap pinned (the old
todo item turned out fixed by an earlier refactor - pin it so it can't
regress); last-menu-wins F9 memory; Alt+letter jumps; and mouse - the
pre-decided semantics ("MenuBar entries become clickable proxies for
their key bindings") driven through Textual's real mouse pipeline via
``pilot.click`` with widget-relative offsets. No raw pointer plumbing
needed - the pilot synthesises genuine Click events.
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.menu_bar import (
    MENUS,
    MenuBar,
    menu_index_at,
    menu_name_spans,
    render_menu_row,
)
from wtree.widgets.menu_screen import MenuScreen, _DropdownPanel
from wtree.widgets.status_line import StatusLine


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


def test_spans_match_rendered_row() -> None:
    plain = render_menu_row().plain
    for (start, end), menu in zip(menu_name_spans(), MENUS):
        assert plain[start:end] == menu.name


def test_index_at_hits_and_misses() -> None:
    spans = menu_name_spans()
    for idx, (start, end) in enumerate(spans):
        assert menu_index_at(start) == idx
        assert menu_index_at(end - 1) == idx
    assert menu_index_at(0) is None          # leading gutter
    assert menu_index_at(spans[0][1]) is None  # gap between names


# ---------------------------------------------------------------------------
# Dropdown positioning
# ---------------------------------------------------------------------------


async def test_dropdown_margin_follows_menu(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        panel = app.screen.query_one(_DropdownPanel)
        spans = menu_name_spans()
        assert panel.styles.margin.left == max(0, spans[0][0] - 2)

        await pilot.press("right")  # Commands
        await pilot.pause()
        assert panel.styles.margin.left == max(0, spans[1][0] - 2)
        await pilot.press("escape")
        await pilot.pause()


# ---------------------------------------------------------------------------
# Wrap pin (fixed by an earlier refactor; pinned here)
# ---------------------------------------------------------------------------


async def test_down_on_last_wraps_to_first(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        n = len(MENUS[0].items)
        last_selectable = max(
            i for i, it in enumerate(MENUS[0].items) if not it.separator
        )
        for _ in range(n * 2):  # drive well past the end
            await pilot.press("down")
        # Land somewhere valid, then position on last and step once.
        screen._cursor_idx = last_selectable
        screen._sync_children()
        await pilot.press("down")
        await pilot.pause()
        first_selectable = next(
            i for i, it in enumerate(MENUS[0].items) if not it.separator
        )
        assert screen._cursor_idx == first_selectable

        await pilot.press("up")   # symmetric wrap back to last
        await pilot.pause()
        assert screen._cursor_idx == last_selectable
        await pilot.press("escape")
        await pilot.pause()


# ---------------------------------------------------------------------------
# Last-menu-wins + Alt jumps
# ---------------------------------------------------------------------------


async def test_f9_reopens_last_menu(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("right")   # Commands
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        assert screen.active_menu == 1  # remembered Commands
        await pilot.press("escape")
        await pilot.pause()


async def test_alt_c_opens_commands_directly(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("alt+c")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        assert screen.active_menu == 1
        await pilot.press("escape")
        await pilot.pause()


# ---------------------------------------------------------------------------
# Mouse - clickable proxies via the real Click pipeline
# ---------------------------------------------------------------------------


async def test_click_bar_opens_that_menu(tmp_path: Path) -> None:
    """Click 'Commands' on the passive bar -> MenuScreen at Commands."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        start, _ = menu_name_spans()[1]
        await pilot.click(MenuBar, offset=(start + 1, 0))
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        assert screen.active_menu == 1
        await pilot.press("escape")
        await pilot.pause()


async def test_click_bar_gap_is_noop(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click(MenuBar, offset=(0, 0))  # leading gutter
        await pilot.pause()
        assert not isinstance(app.screen, MenuScreen)


async def test_click_top_row_switches_menu(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        start, _ = menu_name_spans()[2]  # Help
        await pilot.click("#menu-top-row", offset=(start + 1, 0))
        await pilot.pause()
        assert screen.active_menu == 2
        await pilot.press("escape")
        await pilot.pause()


async def test_click_dropdown_item_dispatches(tmp_path: Path) -> None:
    """Click 'Progress dialog' in Commands -> action runs (idle flash)."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("alt+c")   # Commands menu open
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        panel = screen.query_one(_DropdownPanel)
        item_idx = next(
            i for i, it in enumerate(MENUS[1].items)
            if it.action == "show_progress"
        )
        gutter = panel.content_region.y - panel.region.y
        await pilot.click(_DropdownPanel, offset=(3, gutter + item_idx))
        await pilot.pause()
        await pilot.pause()

        assert not isinstance(app.screen, MenuScreen)  # menu closed
        status = app.query_one(StatusLine)
        assert status._flash_message == "No operation in progress"
        assert status._flash_severity == "warning"


async def test_click_separator_keeps_menu_open(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")      # File menu has the separator
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MenuScreen)
        sep_idx = next(
            i for i, it in enumerate(MENUS[0].items) if it.separator
        )
        panel = screen.query_one(_DropdownPanel)
        gutter = panel.content_region.y - panel.region.y
        await pilot.click(_DropdownPanel, offset=(3, gutter + sep_idx))
        await pilot.pause()
        assert app.screen is screen  # still open
        await pilot.press("escape")
        await pilot.pause()
