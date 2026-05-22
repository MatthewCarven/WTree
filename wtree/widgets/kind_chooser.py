"""``KindChooserDialog`` - dir-or-file modal for Make-new.

The first step of the Make-new sub-prompt (per ``design.md`` Keymap
"Make new (dir or file): N / F7. Sub-prompt asks dir or file"). Once
the user picks, the action layer pushes a :class:`PromptDialog` for the
name.

Modal contract:

* ``D`` dismisses with :attr:`Kind.DIR`.
* ``F`` dismisses with :attr:`Kind.FILE`.
* ``Esc`` dismisses with ``None`` - caller treats as cancellation.
* No text input - this is a single-keystroke gate.

The two-step shape (chooser then name) was chosen over a trailing-slash
"mydir/" convention or a combined radio-plus-input modal in the
2026-05-22 design conversation: each step is unambiguous, mirrors
XTree's keystroke-driven feel, and reuses the existing PromptDialog
unchanged.

Launched via ``await app.push_screen_wait(KindChooserDialog())``;
returns ``Kind | None``. Generic type parameter ties the dismiss type
to the caller's ``await``.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label

from wtree.sources.base import Kind


class KindChooserDialog(ModalScreen[Kind | None]):
    """A modal asking "dir or file?" for Make-new."""

    DEFAULT_CSS = """
    KindChooserDialog {
        align: center middle;
    }

    KindChooserDialog > Vertical {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 50%;
        max-width: 60;
        height: auto;
    }

    KindChooserDialog Label.title {
        margin-bottom: 1;
        text-style: bold;
    }

    KindChooserDialog Label.hint {
        margin-top: 1;
        color: $text-muted;
        text-style: italic;
    }
    """

    BINDINGS = [
        ("d", "pick_dir", "Directory"),
        ("f", "pick_file", "File"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        *,
        title: str = "Make new:",
        hint: str = "D for directory  -  F for file  -  Esc to cancel",
    ) -> None:
        """``title`` is the question; default works for the only v0
        caller (Make-new). ``hint`` is the bottom muted line spelling
        out the three keys. Override the title from the action body if
        a future caller wants to reuse the dialog with different
        framing.
        """
        super().__init__()
        self._title = title
        self._hint = hint

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title, classes="title")
            yield Label("[D]irectory", classes="body-line")
            yield Label("[F]ile", classes="body-line")
            yield Label(self._hint, classes="hint")

    def action_pick_dir(self) -> None:
        """D - dismiss with Kind.DIR."""
        self.dismiss(Kind.DIR)

    def action_pick_file(self) -> None:
        """F - dismiss with Kind.FILE."""
        self.dismiss(Kind.FILE)

    def action_cancel(self) -> None:
        """Esc - dismiss with None."""
        self.dismiss(None)
