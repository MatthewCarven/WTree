"""``ConflictDialog`` - per-conflict resolution modal.

Shown by the Copy / Move / Rename action layer when plan-time conflict
detection (:func:`wtree.ops.conflicts.annotate_conflicts`) flags one or
more ``PlanItem``s whose destination already exists. The user picks a
resolution per row; the returned list feeds
:func:`wtree.ops.conflicts.resolve_conflicts`.

Distinct from :class:`~wtree.widgets.confirm.ConfirmDialog` (a pure
yes/no gate) - the per-row state needs its own widget. Mirrors the
centered-modal idiom (``Vertical`` shell, ``$primary`` border) and the
scrollable body of :class:`~wtree.widgets.viewer.ViewerScreen` /
:class:`~wtree.widgets.properties.PropertiesScreen`.

Modal contract (see ``design.md`` -> User interface -> Conflict
resolution dialog):

* Up / Down move the row cursor.
* ``s`` / ``o`` / ``r`` set the *current* row to Skip / Overwrite /
  Rename.
* ``S`` / ``O`` / ``R`` set *all* rows at once (the common case).
* ``Enter`` commits the current selections - dismisses with the
  parallel ``list[Resolution]``.
* ``Esc`` cancels the *entire* operation - dismisses with ``None``
  (distinct from skipping every row).

Default per-row resolution is **Skip**: the safe, non-destructive
choice, so a user who just presses Enter loses nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label

from wtree.ops.base import ConflictKind, PlanItem, Resolution


# Display order / labels for the three user-choosable resolutions.
_RES_LABEL = {
    Resolution.SKIP: "skip",
    Resolution.OVERWRITE: "overwrite",
    Resolution.RENAME: "rename",
}

_EXISTING_LABEL = {
    ConflictKind.FILE: "file",
    ConflictKind.DIR: "dir",
    ConflictKind.OTHER: "other",
    # Self-target: the destination is the item's own location. Labelled
    # distinctly so the user reads "duplicate in place" rather than
    # "something is in the way".
    ConflictKind.SELF: "same location",
}


class ConflictDialog(ModalScreen[list[Resolution] | None]):
    """A modal letting the user resolve each detected conflict."""

    DEFAULT_CSS = """
    ConflictDialog {
        align: center middle;
    }

    ConflictDialog > Vertical {
        background: $panel;
        border: thick $warning;
        padding: 1 2;
        width: 90%;
        max-width: 110;
        height: auto;
        max-height: 90%;
    }

    ConflictDialog Label.title {
        margin-bottom: 1;
        text-style: bold;
    }

    ConflictDialog #conflict-rows {
        height: auto;
        max-height: 18;
    }

    ConflictDialog Label.row {
        width: 100%;
    }

    ConflictDialog Label.row-cursor {
        background: $accent;
        color: $text;
        text-style: bold;
    }

    ConflictDialog Label.hint {
        margin-top: 1;
        color: $text-muted;
        text-style: italic;
    }
    """

    BINDINGS = [
        ("up", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("s", "set_current('skip')", "Skip"),
        ("o", "set_current('overwrite')", "Overwrite"),
        ("r", "set_current('rename')", "Rename"),
        ("S", "set_all('skip')", "Skip all"),
        ("O", "set_all('overwrite')", "Overwrite all"),
        ("R", "set_all('rename')", "Rename all"),
        ("enter", "commit", "Commit"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, items: Sequence[PlanItem]) -> None:
        """``items`` is the list of blocking conflict items, in plan order
        (i.e. ``[i for i in plan.items if i.conflict is not NONE]``). The
        returned ``list[Resolution]`` is parallel to it.
        """
        super().__init__()
        self._items = list(items)
        # Default per row. A real collision defaults to the safe,
        # non-destructive Skip (Enter loses nothing). A SELF row - the user
        # copying an entry into its own directory - defaults to Rename: the
        # duplicate-in-place idiom is what they almost certainly want, and
        # Skip on a self-target would silently do nothing. They can still
        # flip it to Skip, or Overwrite (which the executor's self-destruct
        # guard refuses, failing the item rather than eating the source).
        self._res = [
            Resolution.RENAME
            if it.conflict is ConflictKind.SELF
            else Resolution.SKIP
            for it in self._items
        ]
        self._cursor = 0
        self._row_labels: list[Label] = []

    # -- composition --------------------------------------------------

    def compose(self) -> ComposeResult:
        n = len(self._items)
        with Vertical():
            yield Label(
                f"{n} conflict(s) - choose what to do", classes="title"
            )
            with VerticalScroll(id="conflict-rows"):
                for i in range(n):
                    label = Label(self._row_text(i), classes="row")
                    self._row_labels.append(label)
                    yield label
            yield Label(self._hint_text(), classes="hint")

    def on_mount(self) -> None:
        self._restyle_rows()

    # -- rendering ----------------------------------------------------

    def _row_text(self, i: int) -> str:
        item = self._items[i]
        marker = ">" if i == self._cursor else " "
        res = _RES_LABEL[self._res[i]]
        existing = _EXISTING_LABEL.get(item.conflict, "?")
        return f"{marker} [{res:<9}]  {item.dst_path}  (existing: {existing})"

    def _hint_text(self) -> str:
        return (
            "Up/Down move  -  s/o/r set row  -  S/O/R set all  -  "
            "Enter confirm  -  Esc cancel op"
        )

    def _refresh_row(self, i: int) -> None:
        if 0 <= i < len(self._row_labels):
            self._row_labels[i].update(self._row_text(i))

    def _refresh_all_rows(self) -> None:
        for i in range(len(self._row_labels)):
            self._refresh_row(i)

    def _restyle_rows(self) -> None:
        """Apply the cursor highlight class to the current row only."""
        for i, label in enumerate(self._row_labels):
            label.set_class(i == self._cursor, "row-cursor")

    # -- actions ------------------------------------------------------

    def action_cursor_up(self) -> None:
        if not self._items:
            return
        prev = self._cursor
        self._cursor = (self._cursor - 1) % len(self._items)
        self._refresh_row(prev)
        self._refresh_row(self._cursor)
        self._restyle_rows()
        self._scroll_to_cursor()

    def action_cursor_down(self) -> None:
        if not self._items:
            return
        prev = self._cursor
        self._cursor = (self._cursor + 1) % len(self._items)
        self._refresh_row(prev)
        self._refresh_row(self._cursor)
        self._restyle_rows()
        self._scroll_to_cursor()

    def action_set_current(self, which: str) -> None:
        if not self._items:
            return
        self._res[self._cursor] = Resolution(which)
        self._refresh_row(self._cursor)

    def action_set_all(self, which: str) -> None:
        res = Resolution(which)
        for i in range(len(self._res)):
            self._res[i] = res
        self._refresh_all_rows()

    def action_commit(self) -> None:
        self.dismiss(list(self._res))

    def action_cancel(self) -> None:
        self.dismiss(None)

    # -- helpers ------------------------------------------------------

    def _scroll_to_cursor(self) -> None:
        if 0 <= self._cursor < len(self._row_labels):
            self._row_labels[self._cursor].scroll_visible()
