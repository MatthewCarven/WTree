"""``MenuScreen`` - the modal overlay that handles F9 menu navigation.

Pushed by :meth:`WTreeApp.action_menu_bar` when the user presses F9.
Renders a top row that visually mirrors :class:`MenuBar` (with the
active top-level menu highlighted) plus a dropdown column showing
the active menu's items and their keyboard shortcuts. Owns all key
handling while open.

Navigation contract (matches MC):

* **Up / Down** move the selection within the open dropdown.
* **Left / Right** rotate between top-level menus (wrap).
* **Enter** dismisses the screen with the selected item's
  ``action`` name. Selecting a separator is a no-op (the selection
  skips separator rows).
* **Esc** dismisses with ``None`` - menu cancelled, nothing
  dispatched.
* **Letter accelerators**: typing a letter that matches an item's
  ``accelerator`` in the open menu activates that item directly
  (same as Up/Down to it + Enter).

The dispatch contract: dismiss returns a ``str`` (the action name)
or ``None``. The app's ``action_menu_bar`` awaits the dismiss and
calls ``getattr(self, f"action_{name}")()``, so menu items are
trivially "press the key" equivalents.
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget

from wtree.widgets.menu_bar import MENUS, render_menu_row


class _DropdownPanel(Widget):
    """The dropdown column under the active menu name.

    Owned by :class:`MenuScreen`; rendered as a child widget so the
    Rich Text can be styled (reverse-video on the cursor row,
    separators rendered as a divider line). Re-renders whenever the
    parent updates its reactive ``active_menu`` or ``cursor_idx``.

    Width is fixed at a value wide enough to fit the longest item
    label plus its shortcut hint plus a couple of padding chars.
    Could be derived from the items but a constant is simpler.
    """

    DEFAULT_CSS = """
    _DropdownPanel {
        background: $panel;
        color: $text;
        border: thick $primary;
        width: 32;
        height: auto;
        padding: 0 1;
    }
    """

    active_menu: reactive[int] = reactive(0)
    cursor_idx: reactive[int] = reactive(0)

    def render(self) -> Text:
        """Render the items of the current menu, one per row."""
        text = Text()
        menu = MENUS[self.active_menu]
        for idx, item in enumerate(menu.items):
            if idx > 0:
                text.append("\n")
            if item.separator:
                # Render as a dim horizontal rule. The width inside
                # the border ~ panel width minus padding.
                text.append("─" * 28, style="dim")
                continue
            is_selected = idx == self.cursor_idx
            style = "reverse bold" if is_selected else ""
            # Label first letter underlined for the accelerator
            # cue; remaining chars rendered with the row's style.
            text.append(item.label[0], style=style + " underline")
            text.append(item.label[1:], style=style)
            # Pad with spaces so the shortcut right-aligns at a
            # consistent column. Aim for the label + spaces to take
            # 18 cols, leaving room for ~10 cols of shortcut.
            pad = max(1, 18 - len(item.label))
            text.append(" " * pad, style=style)
            text.append(item.shortcut, style=style + " dim" if not is_selected else style)
        return text


class MenuScreen(ModalScreen[str | None]):
    """Modal that hosts the active menu UI.

    Dismisses with the selected item's ``action`` string on Enter,
    or with ``None`` on Esc. The app dispatches the action by
    convention: ``getattr(self, f"action_{returned_name}")()``.
    """

    DEFAULT_CSS = """
    MenuScreen {
        align: left top;
    }

    MenuScreen > Vertical {
        width: auto;
        height: auto;
    }

    MenuScreen #menu-top-row {
        dock: top;
        height: 1;
        background: $boost;
        color: $text;
    }
    """

    # ``BINDINGS`` are not used here - we own ``on_key`` directly so
    # the letter-accelerator branch can compete with the named keys
    # without binding-table priority churn.

    active_menu: reactive[int] = reactive(0)

    def __init__(self, *, initial_menu: int = 0) -> None:
        """``initial_menu`` is which top-level menu opens first.

        Defaults to 0 (File). A future "Alt+letter from outside the
        menu" jump might pass a different starting index to land
        straight on the user's chosen menu.
        """
        super().__init__()
        self._initial_menu = initial_menu
        # Cursor position within the active dropdown. Reset on each
        # menu switch to skip leading separators if any (currently
        # none lead, but the helper handles it).
        self._cursor_idx = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            # The top row is a child Widget rather than a Static so
            # it re-renders on ``active_menu`` reactive changes.
            yield _MenuTopRow(id="menu-top-row")
            yield _DropdownPanel()

    def on_mount(self) -> None:
        """Initialise reactive state and re-render."""
        self.active_menu = self._initial_menu
        self._cursor_idx = self._first_selectable_idx(self.active_menu)
        self._sync_children()

    # ------------------------------------------------------------------
    # Reactive watchers - propagate state to children
    # ------------------------------------------------------------------

    def watch_active_menu(self, _old: int, _new: int) -> None:
        """When the active menu rotates, reset cursor and refresh."""
        self._cursor_idx = self._first_selectable_idx(_new)
        self._sync_children()

    def _sync_children(self) -> None:
        """Push the current ``active_menu`` + cursor into the children.

        Safe to call before mount completes via try/except in case
        composers haven't placed the widgets yet.
        """
        try:
            top = self.query_one("#menu-top-row", _MenuTopRow)
            top.active_menu = self.active_menu
            top.refresh()
            panel = self.query_one(_DropdownPanel)
            panel.active_menu = self.active_menu
            panel.cursor_idx = self._cursor_idx
            panel.refresh()
        except Exception:  # noqa: BLE001 - early-mount safety
            pass

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    async def on_key(self, event: events.Key) -> None:
        """Custom key handling - own everything while the menu is open.

        Letter accelerators get a chance against the named keys
        first; this is why we don't use ``BINDINGS``. Inside the
        open menu, ``c`` activates Copy (the first item whose
        accelerator is ``c``) rather than ever doing anything else.
        Outside-the-open-menu accelerators (top-level menu opener
        letters) are NOT handled here because we always have a menu
        open while this screen is up.
        """
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.dismiss(None)
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self._activate_current()
            return
        if event.key == "up":
            event.stop()
            event.prevent_default()
            self._move_cursor(-1)
            return
        if event.key == "down":
            event.stop()
            event.prevent_default()
            self._move_cursor(1)
            return
        if event.key == "left":
            event.stop()
            event.prevent_default()
            self.active_menu = (self.active_menu - 1) % len(MENUS)
            return
        if event.key == "right":
            event.stop()
            event.prevent_default()
            self.active_menu = (self.active_menu + 1) % len(MENUS)
            return
        # Letter accelerator - find the matching item in the current
        # menu and activate it.
        if event.is_printable and event.character is not None:
            ch = event.character.lower()
            menu = MENUS[self.active_menu]
            for idx, item in enumerate(menu.items):
                if item.separator:
                    continue
                if item.accelerator == ch:
                    event.stop()
                    event.prevent_default()
                    self._cursor_idx = idx
                    self._sync_children()
                    self._activate_current()
                    return
        # Anything else - swallow so it doesn't propagate to the
        # underlying app (which would otherwise see the keypress
        # while the menu is open).
        event.stop()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _first_selectable_idx(self, menu_idx: int) -> int:
        """Return the first non-separator item index in the given menu."""
        menu = MENUS[menu_idx]
        for idx, item in enumerate(menu.items):
            if not item.separator:
                return idx
        return 0

    def _move_cursor(self, direction: int) -> None:
        """Move the cursor up/down, skipping separators and wrapping."""
        menu = MENUS[self.active_menu]
        n = len(menu.items)
        if n == 0:
            return
        idx = self._cursor_idx
        # Step until we hit a non-separator, max ``n`` tries.
        for _ in range(n):
            idx = (idx + direction) % n
            if not menu.items[idx].separator:
                self._cursor_idx = idx
                self._sync_children()
                return
        # All separators? Stay where we are.

    def _activate_current(self) -> None:
        """Dismiss with the current item's action name."""
        menu = MENUS[self.active_menu]
        if self._cursor_idx < 0 or self._cursor_idx >= len(menu.items):
            self.dismiss(None)
            return
        item = menu.items[self._cursor_idx]
        if item.separator:
            return  # Selecting a separator is a no-op; stay open.
        self.dismiss(item.action)


class _MenuTopRow(Widget):
    """The top row of the modal - the menu names with the active one
    highlighted.

    Reactive ``active_menu`` so the parent screen can re-bind it on
    Left/Right and have the row repaint without explicit refresh
    calls.
    """

    DEFAULT_CSS = """
    _MenuTopRow {
        dock: top;
        height: 1;
        background: $boost;
        color: $text;
    }
    """

    active_menu: reactive[int] = reactive(0)

    def render(self) -> Text:
        """Render the row mirroring :class:`MenuBar` with one menu active."""
        return render_menu_row(active_idx=self.active_menu)
