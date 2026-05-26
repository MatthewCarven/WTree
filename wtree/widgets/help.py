"""``HelpScreen`` - the F1 / Help menu modal (About + keymap reference).

Per ``design.md`` Keymap: ``F1`` / ``?`` is Help. v0 ships a single
combined modal that serves both roles - "About" (name, version,
attribution) and "Help" (categorised keymap reference) - because the
two are small enough to share one surface and a future "Keymap-only"
sub-item can layer on top by adding a second :class:`MenuItem` that
opens the same screen scrolled to a specific section.

Modal contract mirrors :class:`~wtree.widgets.viewer.ViewerScreen`:

* ``Esc`` or ``Q`` dismisses with ``None``.
* Scrolling is owned by ``VerticalScroll`` - arrow keys, PgUp/PgDn,
  Home/End all just work.
* The screen never mutates anything.

The keymap section is hand-written rather than derived from
:attr:`WTreeApp.BINDINGS` - the design.md table groups bindings by
concept (Navigation, Tagging, Operations, Search, Application) and
the binding table is flat. The cost of keeping these in sync (when
a new binding lands, this file and design.md both get touched) is
small and the readability gain for the user is large. If the keymap
ever grows past ~50 entries we'd revisit.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from wtree import __version__


class HelpScreen(ModalScreen[None]):
    """Read-only About + keymap reference. F1 and the Help menu push this."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }

    HelpScreen > VerticalScroll {
        background: $surface;
        border: thick $primary;
        width: 80%;
        height: 90%;
    }

    HelpScreen Label.header {
        background: $primary;
        color: $text;
        padding: 0 1;
        text-style: bold;
        dock: top;
    }

    HelpScreen Label.hint {
        background: $panel;
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        dock: bottom;
    }

    HelpScreen Static.body {
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("q", "dismiss_screen", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-scroll"):
            yield Label(
                f"WTree v{__version__}  -  Help & About", classes="header"
            )
            yield Static(_help_content(), classes="body", id="help-body")
            yield Label(
                "Esc / Q to close  -  arrow keys / PgUp PgDn / Home End to scroll",
                classes="hint",
            )

    def action_dismiss_screen(self) -> None:
        """Esc or Q - close the help modal."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Body content
# ---------------------------------------------------------------------------


def _help_content() -> Text:
    """Build the About + keymap reference body as Rich Text.

    Pure function so tests can assert on the rendered content
    without instantiating the screen.
    """
    t = Text()

    # ---- About block --------------------------------------------------
    t.append("WTree", style="bold cyan")
    t.append(f"  v{__version__}\n", style="dim")
    t.append(
        "A keyboard-driven TUI file manager in the XTree / Midnight Commander\n"
        "lineage. Hierarchy view on the left, contents on the right, a\n"
        "persistent tagged set spanning every directory you visit.\n\n"
    )
    t.append("Built with Python and Textual. ", style="")
    t.append("See ", style="")
    t.append("design.md", style="italic")
    t.append(" for architecture and the full decision log.\n\n")

    def section(title: str) -> None:
        t.append(title + "\n", style="bold underline")

    def row(keys: str, action: str) -> None:
        t.append(f"  {keys:<18}", style="cyan")
        t.append(f"  {action}\n")

    # ---- Keymap -------------------------------------------------------
    section("Navigation")
    row("Up / Down", "Move cursor")
    row("PgUp / PgDn", "Page through list")
    row("Home / End", "Top / bottom of list")
    row("Tab", "Switch pane focus (tree <-> contents)")
    row("Enter", "Open / enter directory")
    row("Backspace", "Go to parent directory (contents pane)")
    row("Left (on root)", "Ascend - re-root tree at parent")
    row("Left / Right", "Collapse / expand (tree pane)")
    row("L", "Log new source (prompt for path; re-roots)")
    t.append("\n")

    section("Tagging")
    row("Space / T", "Tag or untag the cursor entry")
    row("Space (tree pane)", "Recursive tag / untag of the subtree")
    row("Ctrl+A", "Tag all in the current directory")
    row("Ctrl+U", "Untag all (clear the tagged set)")
    row("+", "Tag by glob (basename match)")
    row("-", "Untag by glob")
    t.append("\n")

    section("File operations")
    row("C   /  F5", "Copy")
    row("M   /  F6", "Move")
    row("R   /  F2", "Rename (single entry)")
    row("D   /  Del / F8", "Delete")
    row("V   /  F3", "View (built-in pager)")
    row("E   /  F4", "Edit ($VISUAL / $EDITOR)")
    row("N   /  F7", "Make new (directory or file)")
    t.append("\n")

    section("Search")
    row("/", "Incremental search in the focused pane")
    row("Up / Down  (in /)", "Previous / next match")
    row("Enter  (in /)", "Commit - cursor stays at the match")
    row("Esc  (in /)", "Cancel - cursor restores to pre-search position")
    row("Ctrl+F", "Find across the full tree (prompt for query)")
    row("Ctrl+G", "Jump to next Ctrl+F match (wraps)")
    t.append("\n")

    section("Application")
    row("F9", "Open the menu bar")
    row("F1  /  ?", "This help screen")
    row("Ctrl+R", "Refresh source (re-scan both panes)")
    row("Ctrl+I", "Properties (cursor entry or tagged-set summary)")
    row("Ctrl+P", "Show progress dialog (re-open after minimize)")
    row("Q   /  F10", "Quit")
    row("Esc", "Cancel the current dialog or modal")
    t.append("\n")

    section("Selection rule")
    t.append(
        "  File operations act on the tagged set if non-empty, otherwise on\n"
        "  the entry under the cursor. Rename and Make-new are exceptions -\n"
        "  Rename is single-entry only (clear tags with Ctrl+U first), and\n"
        "  Make-new always creates in the contents pane's current directory.\n"
    )
    return t
