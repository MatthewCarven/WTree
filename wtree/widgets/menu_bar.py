"""``MenuBar`` - always-visible MC-style top menu chrome row.

Per ``design.md`` § Keymap: "F9 opens the menu bar (MC convention) and
arrow keys navigate it." We satisfy the "always visible" half here -
the bar renders the top-level menu names as a thin row docked at the
top, passive (no active highlighting) when nothing is open. F9 pushes
:class:`MenuScreen`, which renders a visually-matching row at its own
top plus the open menu's dropdown beneath.

Why a separate passive widget plus a modal screen, rather than one
widget that grows a dropdown? Modal-screen-on-F9 keeps key handling
single-source: while a menu is open, the modal owns every keystroke
(arrows, Enter, Esc, letter accelerators). Trying to share state
between a permanent widget and a transient dropdown means coordinating
focus + key routing across two surfaces. The passive MenuBar is just
for visual continuity - it never owns focus and has no logic of its
own.

The two share the menu definitions module-globally (``MENUS``) so the
labels and accelerators stay in sync between the passive bar and the
active modal.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.widget import Widget


@dataclass(frozen=True, slots=True)
class MenuItem:
    """One entry in a dropdown.

    ``label`` is the displayed text (e.g. ``"Copy"``). ``accelerator``
    is the lowercase letter that activates this item from within an
    open menu - usually the first letter of the label, lowercased.
    ``shortcut`` is the user-facing keybinding string for display
    only (e.g. ``"C / F5"``, ``"Ctrl+U"``). ``action`` is the
    ``action_<name>`` method on :class:`WTreeApp` that the menu
    dispatches when the item is activated. ``separator`` items render
    as a divider line; their other fields are ignored.
    """

    label: str
    accelerator: str
    shortcut: str
    action: str
    separator: bool = False


@dataclass(frozen=True, slots=True)
class Menu:
    """One top-level menu (e.g. "File").

    ``name`` is the displayed label. ``accelerator`` is the lowercase
    letter that opens this menu from the top-level (the bar's
    accelerator layer) - usually the first letter of ``name``.
    ``items`` is the dropdown contents.
    """

    name: str
    accelerator: str
    items: tuple[MenuItem, ...]


# Module-level menu definitions. Shared by ``MenuBar`` (passive
# render) and ``MenuScreen`` (active navigation). Editing these is
# the only place needed to add or remove menu items - both surfaces
# pick up the change automatically.
#
# Per the 2026-05-22 design call: show only implemented items.
# Unimplemented operations (Toggle hidden, Sort, Find-across-tree,
# Properties, etc.) are deliberately absent until they land.
#
# Help menu added 2026-05-23 alongside the F1 binding. v0 ships one
# combined "About" entry that opens :class:`HelpScreen` (which serves
# both the version/attribution and the keymap reference); a separate
# "Keymap" item can layer on top later without changing the screen.
MENUS: tuple[Menu, ...] = (
    Menu(
        name="File",
        accelerator="f",
        items=(
            MenuItem("New", "n", "N / F7", "make_new"),
            MenuItem("View", "v", "V / F3", "view"),
            MenuItem("Edit", "e", "E / F4", "edit"),
            MenuItem("Copy", "c", "C / F5", "copy"),
            MenuItem("Move", "m", "M / F6", "move"),
            MenuItem("Rename", "r", "R / F2", "rename"),
            MenuItem("Delete", "d", "D / F8", "delete"),
            MenuItem("", "", "", "", separator=True),
            MenuItem("Quit", "q", "Q / F10", "quit"),
        ),
    ),
    Menu(
        name="Commands",
        accelerator="c",
        items=(
            MenuItem("Search", "s", "/", "search"),
            MenuItem("Find tree", "f", "Ctrl+F", "find_tree"),
            MenuItem("Next match", "n", "Ctrl+G", "next_match"),
            MenuItem("Log new source", "l", "L", "log_new_source"),
            MenuItem("Refresh source", "r", "Ctrl+R", "refresh_source"),
            MenuItem("Untag all", "u", "Ctrl+U", "untag_all"),
        ),
    ),
    Menu(
        name="Help",
        accelerator="h",
        items=(
            MenuItem("About", "a", "F1", "help"),
        ),
    ),
)


def render_menu_row(active_idx: int | None = None) -> Text:
    """Render the top menu row as Rich Text.

    ``active_idx`` highlights the indexed top-level menu (used by
    :class:`MenuScreen` when a menu is open). ``None`` renders the
    row passively - all menus dim, accelerator letters still
    highlighted for the discoverability cue.

    Centralised so :class:`MenuBar` and :class:`MenuScreen` produce
    visually-identical top rows. If they drift, the eye sees the
    flicker the moment F9 is pressed.
    """
    text = Text()
    text.append("  ")
    for idx, menu in enumerate(MENUS):
        is_active = idx == active_idx
        if is_active:
            # Active menu: reverse-video the whole thing including
            # accelerator. Same as how dropdown items render their
            # selected row.
            text.append(menu.name, style="reverse bold")
        else:
            # Passive menu: dim base, accelerator bolded. ``style``
            # interacts with Rich's stacking - the first letter gets
            # both 'bold' and the underlying dim color.
            text.append(menu.name[0], style="bold underline")
            text.append(menu.name[1:], style="dim")
        text.append("  ")
    return text


class MenuBar(Widget):
    """Always-visible MC-style menu row docked at the screen top.

    Renders the top-level menu names with their accelerator letters
    underlined. Does not own focus or accept input - F9 pushes the
    interactive :class:`MenuScreen` instead. Re-rendered only when
    the menu definitions change (which is module-level state, so
    practically never at runtime).
    """

    DEFAULT_CSS = """
    MenuBar {
        dock: top;
        height: 1;
        background: $boost;
        color: $text;
    }
    """

    # ``can_focus = False`` is the default; we re-state it for clarity
    # because the focus model is unusual: the passive bar never holds
    # focus, but the visually-matching MenuScreen top row does when
    # it's pushed.
    can_focus = False

    def render(self) -> Text:
        """Render the passive bar - no menu highlighted as active."""
        return render_menu_row(active_idx=None)
