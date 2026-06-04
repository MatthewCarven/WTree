"""``PromptDialog`` — reusable single-line modal input.

The first user of this is the Copy destination prompt; every operation in
``design.md`` § Keymap that takes a typed argument will share the same
dialog (Move dest, Rename target, Make-new name, Log-new source path,
glob patterns for ``+``/``-``).

Modal contract:

* ``Esc`` dismisses with ``None`` — caller treats as cancellation.
* ``Enter`` (or ``Input.Submitted``) dismisses with the current text.
* ``Tab`` is *not* trapped here; we don't compose anything else focusable
  inside the dialog, so the input stays focused without competition.

The dialog is launched via ``await app.push_screen_wait(PromptDialog(...))``.
The await is the whole point — actions stay async, the modal blocks them at
exactly one ``await`` point, and the typed value (or ``None``) is the
answer. No callback plumbing, no message-passing dance.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


# Sentinel dismiss value: the user asked to switch to the directory browser
# (Ctrl+B) rather than type a path. The Copy/Move caller checks
# ``result is PromptDialog.BROWSE`` and pushes the DirPickerScreen. Only the
# Copy/Move destination prompt enables this (``browse=True``); for every other
# caller Ctrl+B is inert.
class _Browse:
    __slots__ = ()
    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "PromptDialog.BROWSE"


class PromptDialog(ModalScreen[str | None]):
    """A modal asking for one string.

    The generic type parameter ``str | None`` ties the dismiss type to the
    caller's ``await``: ``await push_screen_wait(PromptDialog(...))``
    statically narrows to ``str | None``. In ``browse=True`` mode the dialog
    may instead dismiss with the :data:`BROWSE` sentinel (see above).
    """

    BROWSE = _Browse()

    DEFAULT_CSS = """
    PromptDialog {
        align: center middle;
    }

    PromptDialog > Vertical {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 60%;
        max-width: 80;
        height: auto;
    }

    PromptDialog Label.title {
        margin-bottom: 1;
        text-style: bold;
    }

    PromptDialog Label.hint {
        margin-top: 1;
        color: $text-muted;
        text-style: italic;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+b", "browse", "Browse"),
    ]

    def __init__(
        self,
        title: str,
        *,
        initial: str = "",
        placeholder: str = "",
        hint: str = "",
        select_initial: tuple[int, int] | None = None,
        browse: bool = False,
    ) -> None:
        """``title`` is the question (e.g. "Copy 3 items to:"). ``initial``
        prefills the input. ``hint`` is a quiet line below the input —
        useful for "Esc to cancel" reminders until the status line exists.

        ``select_initial`` is an optional ``(start, end)`` range within
        ``initial`` to pre-select on open — typing then replaces the
        selected portion (Textual's ``Input`` already replaces selected
        text on the next printable key). The Rename action uses this to
        select the basename stem so the user can type a replacement
        while keeping the extension. ``None`` (the default) places the
        cursor at end-of-text, matching every OS-level "Save As" dialog.
        """
        super().__init__()
        self._title = title
        self._initial = initial
        self._placeholder = placeholder
        self._hint = hint
        self._select_initial = select_initial
        # When True, Ctrl+B dismisses with the BROWSE sentinel so the caller
        # can open the directory browser. Ctrl+B is not an Input binding, so
        # it never steals a typed character.
        self._browse = browse

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title, classes="title")
            yield Input(
                value=self._initial,
                placeholder=self._placeholder,
                id="prompt-input",
            )
            if self._hint:
                yield Label(self._hint, classes="hint")

    def on_mount(self) -> None:
        # Focus the input the moment the dialog mounts so the user can
        # start typing without an extra keypress. Then either pre-select
        # a range (smart cursor for Rename) or pre-position the cursor at
        # end-of-text (every OS-level "Save As" dialog).
        from textual.widgets._input import Selection

        inp = self.query_one(Input)
        inp.focus()
        if self._select_initial is not None and self._initial:
            start, end = self._select_initial
            # Clamp to the valid range; out-of-bounds asserts are
            # never the caller's intent here.
            length = len(self._initial)
            start = max(0, min(start, length))
            end = max(0, min(end, length))
            # Setting ``selection`` is sufficient — Textual's ``Input``
            # derives ``cursor_position`` from ``selection.end``. Setting
            # ``cursor_position`` *after* this would call
            # ``selection = Selection.cursor(end)`` and clobber the
            # range we just installed.
            inp.selection = Selection(start, end)
        elif self._initial:
            inp.cursor_position = len(self._initial)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter on the input commits the dialog.
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        # Esc cancels.
        self.dismiss(None)

    def action_browse(self) -> None:
        # Ctrl+B: only the Copy/Move destination prompt opts in; elsewhere
        # the key is inert (no browse affordance offered).
        if self._browse:
            self.dismiss(self.BROWSE)
