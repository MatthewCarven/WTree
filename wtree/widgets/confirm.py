"""``ConfirmDialog`` - reusable yes/no modal for destructive operations.

The dual of :class:`~wtree.widgets.prompt.PromptDialog`: where Prompt
collects a typed string (or cancellation), Confirm collects a boolean
(``True`` for confirm, ``False`` for cancel). Same async-await ergonomic
- the action body stays linear, the dialog returns the answer.

First user is Delete (``D`` / Del / F8) per ``design.md`` Keymap. Any
future destructive operation (overwrite during a copy, rmtree on an
out-of-tree path) should share this dialog rather than rolling its own.

Modal contract:

* ``Y`` or ``Enter`` dismisses with ``True`` (confirm).
* ``N``, ``Esc``, or the explicit ``[Cancel]`` action dismiss with
  ``False`` (cancel).
* The dialog shows a title (the question) and optional body lines (the
  items being acted on, truncated if many).
* No text input - the dialog is just a yes/no gate.

Launched via ``await app.push_screen_wait(ConfirmDialog(...))``; returns
``bool`` rather than ``bool | None`` because there's no "third option"
- if the user dismissed the dialog at all without confirming, that's a
cancel.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label


class ConfirmDialog(ModalScreen[bool]):
    """A modal asking a yes/no question.

    The generic type parameter ``bool`` ties the dismiss type to the
    caller's ``await``: ``await push_screen_wait(ConfirmDialog(...))``
    statically narrows to ``bool``.
    """

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
    }

    ConfirmDialog > Vertical {
        background: $panel;
        border: thick $error;
        padding: 1 2;
        width: 60%;
        max-width: 80;
        height: auto;
    }

    ConfirmDialog Label.title {
        margin-bottom: 1;
        text-style: bold;
    }

    ConfirmDialog Label.body-line {
        color: $text;
    }

    ConfirmDialog Label.hint {
        margin-top: 1;
        color: $text-muted;
        text-style: italic;
    }
    """

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("enter", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "Cancel"),
    ]

    # How many body lines to show before truncating with a "+N more"
    # tail. Matches the notify summary cap in WTreeApp.
    BODY_PREVIEW = 5

    def __init__(
        self,
        title: str,
        *,
        body: Sequence[str] = (),
        hint: str = "Y / Enter to confirm  -  N / Esc to cancel",
    ) -> None:
        """``title`` is the headline question (e.g. "Delete 3 items?").

        ``body`` is a list of detail lines - typically the paths being
        operated on. Truncated to :attr:`BODY_PREVIEW` lines plus an
        ellipsis count if the list is longer.

        ``hint`` is a quiet bottom line spelling out the key options.
        Override to change phrasing (e.g. "Press Y to continue") but the
        default already mentions all four accepted keys.
        """
        super().__init__()
        self._title = title
        self._body = tuple(body)
        self._hint = hint

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title, classes="title")
            for line in self._body[: self.BODY_PREVIEW]:
                yield Label(line, classes="body-line")
            extra = len(self._body) - self.BODY_PREVIEW
            if extra > 0:
                yield Label(
                    f"... (+{extra} more)", classes="body-line"
                )
            if self._hint:
                yield Label(self._hint, classes="hint")

    def action_confirm(self) -> None:
        """Y / Enter - dismiss True."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """N / Esc - dismiss False."""
        self.dismiss(False)
