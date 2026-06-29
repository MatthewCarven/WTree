"""``OperationResultScreen`` - the ``Ctrl+O`` "last operation" viewer.

Closes the loop the op log (2026-06-11) opened: the done/done-with-errors
toast is transient, and ``~/.wtree/operations.log`` requires leaving the
app to read. The app already stashes the most recent
:class:`~wtree.ops.base.OperationResult` on ``WTreeApp.last_result``; this
read-only modal renders it on demand.

Per the 2026-06-28 design call (AskUserQuestion): the viewer opens showing
the **summary + failed / skipped items** (the same set the log file keeps),
and ``a`` toggles to **all items** including successes. Esc / Q close. No
disk I/O - it reads the already-held in-memory result, so it never touches
the flaky project mount or the log file.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from wtree.oplog import item_arrow
from wtree.ops.base import ItemStatus, OperationResult

# Per-status styling for the detail lines. Failures shout, skips are
# muted-but-visible, successes are calm green (only shown in "all" mode).
_STATUS_STYLE = {
    ItemStatus.SUCCESS: "green",
    ItemStatus.SKIPPED: "yellow",
    ItemStatus.FAILED: "bold red",
}


def render_last_op(result: OperationResult, *, show_all: bool = False) -> Text:
    """Render ``result`` for the viewer body (pure - tested directly).

    Header is the plan's ``summary()``. Then, unless ``show_all``, only the
    non-SUCCESS items; with ``show_all`` every item. An all-success result
    in the default view says so and points at ``a``; a non-default view that
    hid successes notes how many.
    """
    t = Text()
    t.append(result.summary(), style="bold")
    t.append("\n\n")

    items = list(result.items)
    if not items:
        t.append("(empty plan - nothing was done)\n", style="dim")
        return t

    nonsuccess = [r for r in items if r.status is not ItemStatus.SUCCESS]
    shown = items if show_all else nonsuccess

    if not shown:
        # Default view, everything succeeded.
        t.append(f"All {len(items)} item(s) succeeded.\n", style="green")
        t.append("Press  a  to list every item.\n", style="dim")
        return t

    t.append("All items:" if show_all else "Failed / skipped items:", style="bold")
    t.append("\n")
    for r in shown:
        style = _STATUS_STYLE.get(r.status, "")
        t.append(f"  {r.status.value.upper():7s} ", style=style)
        t.append(item_arrow(r.item))
        if r.message:
            t.append(f": {r.message}", style="dim")
        t.append("\n")

    hidden = len(items) - len(shown)
    if hidden > 0:
        t.append(
            f"\n({hidden} succeeded item(s) hidden - press  a  to show all)\n",
            style="dim",
        )
    return t


class OperationResultScreen(ModalScreen[None]):
    """Read-only viewer for the most recent ``OperationResult``.

    Ctrl+O and the Commands menu push this. Reads the result handed in at
    construction (``WTreeApp.last_result``); no live polling, no I/O.
    """

    DEFAULT_CSS = """
    OperationResultScreen {
        align: center middle;
    }

    OperationResultScreen > VerticalScroll {
        background: $surface;
        border: thick $primary;
        width: 80%;
        height: 80%;
    }

    OperationResultScreen Label.header {
        background: $primary;
        color: $text;
        padding: 0 1;
        text-style: bold;
        dock: top;
    }

    OperationResultScreen Label.hint {
        background: $panel;
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        dock: bottom;
    }

    OperationResultScreen Static.body {
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close", show=False),
        Binding("q", "dismiss_screen", "Close", show=False),
        Binding("a", "toggle_all", "Show all / failures", show=False),
    ]

    def __init__(self, result: OperationResult) -> None:
        super().__init__()
        self._result = result
        self._show_all = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="lastop-scroll"):
            yield Label(self._header_text(), classes="header")
            yield Static(
                render_last_op(self._result, show_all=self._show_all),
                classes="body",
                id="lastop-body",
            )
            yield Label(self._hint_text(), classes="hint")

    def _header_text(self) -> str:
        verb = self._result.plan.kind.value.capitalize()
        state = "done" if self._result.all_succeeded else "done with errors"
        return f"Last operation  -  {verb} ({state})"

    def _hint_text(self) -> str:
        toggle = "a = failures only" if self._show_all else "a = show all items"
        return f"Esc / Q to close  -  {toggle}  -  arrows / PgUp PgDn to scroll"

    def action_toggle_all(self) -> None:
        """Flip between non-success-only and all-items, re-rendering."""
        self._show_all = not self._show_all
        try:
            body = self.query_one("#lastop-body", Static)
            body.update(render_last_op(self._result, show_all=self._show_all))
            self.query_one(".hint", Label).update(self._hint_text())
        except Exception:  # noqa: BLE001 - torn down mid-toggle
            pass

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
