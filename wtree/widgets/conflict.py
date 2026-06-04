"""``ConflictDialog`` - per-conflict resolution modal.

Shown by the Copy / Move / Rename / Make-new action layer when plan-time
conflict detection (:func:`wtree.ops.conflicts.annotate_conflicts`) flags
one or more ``PlanItem``s whose destination already exists. The user picks
a resolution per row; the dialog dismisses with a parallel
``(list[Resolution], list[str | None])`` that feeds
:func:`wtree.ops.conflicts.resolve_conflicts` - the second list carries a
custom rename target per row (or ``None`` = use the auto `` (n)`` suffix).

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
* ``e`` edits the *current* row's Rename target - pops a
  :class:`~wtree.widgets.prompt.PromptDialog` pre-filled with the auto
  `` (n)`` name; the typed value (a relative subpath is allowed) is
  validated and re-stat'd, re-prompting on a collision so the chosen name
  is guaranteed free. Editing forces the row to Rename.
* ``Enter`` commits - dismisses with ``(resolutions, custom_dsts)``.
* ``Esc`` cancels the *entire* operation - dismisses with ``None``
  (distinct from skipping every row).

Default per-row resolution is **Skip**: the safe, non-destructive
choice, so a user who just presses Enter loses nothing.
"""

from __future__ import annotations

import posixpath
from collections.abc import Awaitable, Callable, Sequence

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label

from wtree.ops.base import (
    ConflictKind,
    PlanItem,
    Resolution,
    resolve_relative_leaf,
)
from wtree.widgets.prompt import PromptDialog


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

# Async existence check the editor uses to verify a typed custom target is
# free: ``(item, candidate_dst_path) -> bool``. Supplied by the action layer
# (it knows the sources); ``None`` disables the check (items-only tests).
NameExists = Callable[[PlanItem, str], Awaitable[bool]]


class ConflictDialog(
    ModalScreen["tuple[list[Resolution], list[str | None]] | None"]
):
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
        ("e", "edit_name", "Edit name"),
        ("enter", "commit", "Commit"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        items: Sequence[PlanItem],
        previews: Sequence[str] | None = None,
        name_exists: NameExists | None = None,
    ) -> None:
        """``items`` is the list of blocking conflict items, in plan order
        (i.e. ``[i for i in plan.items if i.conflict is not NONE]``). The
        returned lists are parallel to it.

        ``previews`` (optional, parallel to ``items``) is the collision-free
        `` (n)``-suffixed destination each item would land on if Renamed -
        precomputed at dialog-open time via
        :func:`wtree.ops.conflicts.preview_renamed_dst`. A Rename row shows the
        concrete target inline; absent or short, rows render without it.

        ``name_exists`` (optional) is the async checker the ``e`` editor uses
        to verify a typed custom target is free before accepting it. ``None``
        disables the check - used by items-only unit tests.
        """
        super().__init__()
        self._items = list(items)
        self._previews = list(previews) if previews is not None else []
        self._name_exists = name_exists
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
        # Custom RENAME target per row (fully-resolved, collision-verified
        # dst_path), or None = use the auto `` (n)`` suffix.
        self._custom: list[str | None] = [None] * len(self._items)
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

    def _parent_of(self, i: int) -> str:
        """POSIX parent directory of the conflict item's destination."""
        return posixpath.dirname(self._items[i].dst_path.rstrip("/"))

    def _rel_under_parent(self, i: int, full: str) -> str:
        """``full`` expressed relative to row ``i``'s parent dir, so a
        subpath custom target stays legible; falls back to the basename."""
        parent = self._parent_of(i)
        sep = parent if parent.endswith("/") else parent + "/"
        if full.startswith(sep):
            return full[len(sep):]
        return posixpath.basename(full)

    def _rename_target_display(self, i: int) -> str | None:
        """The name shown after ``->`` on a Rename row, or None if unknown.

        A user-edited custom target wins (relative to the parent, tagged
        ``(edited)``); otherwise the precomputed auto-suffix basename.
        """
        custom = self._custom[i]
        if custom is not None:
            return f"{self._rel_under_parent(i, custom)} (edited)"
        preview = self._previews[i] if i < len(self._previews) else None
        return posixpath.basename(preview) if preview else None

    def _row_text(self, i: int) -> str:
        item = self._items[i]
        marker = ">" if i == self._cursor else " "
        res = _RES_LABEL[self._res[i]]
        existing = _EXISTING_LABEL.get(item.conflict, "?")
        line = f"{marker} [{res:<9}]  {item.dst_path}  (existing: {existing})"
        # Live preview: when this row will Rename, append the concrete target
        # (custom if edited, else the auto-suffix basename). Only RENAME rows
        # show it - Skip / Overwrite keep the bare line.
        if self._res[i] is Resolution.RENAME:
            target = self._rename_target_display(i)
            if target:
                line += f"  -> {target}"
        return line

    def _hint_text(self) -> str:
        return (
            "Up/Down move  -  s/o/r set row  -  S/O/R set all  -  "
            "e edit name  -  Enter confirm  -  Esc cancel op"
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

    @work
    async def action_edit_name(self) -> None:
        """Edit the current row's Rename target via a PromptDialog.

        A worker (``push_screen_wait`` must run off the message pump). Editing
        forces the row to Rename. The prompt is pre-filled with the current
        effective target (prior custom, else the auto-suffix). The typed value
        may be a relative subpath; it's validated by
        :func:`wtree.ops.base.resolve_relative_leaf` and re-stat'd via
        ``name_exists``, re-prompting (with the reason on the hint line) on an
        invalid or already-existing target. Esc on the prompt keeps whatever
        was there. No checker -> no existence test.
        """
        if not self._items:
            return
        i = self._cursor
        item = self._items[i]
        self._res[i] = Resolution.RENAME  # editing implies rename
        parent = self._parent_of(i)
        prefill = self._edit_prefill(i)
        hint = "Enter to confirm  -  Esc to keep the auto name"
        title = f"Rename target under {parent or '/'}:"
        while True:
            typed = await self.app.push_screen_wait(
                PromptDialog(
                    title=title,
                    initial=prefill,
                    placeholder="new name (relative subpath allowed)",
                    hint=hint,
                )
            )
            if typed is None:
                break  # keep current (auto suffix or a prior custom)
            leaf, err = resolve_relative_leaf(parent, typed)
            if err is not None:
                prefill, hint = typed, f"! {err}"
                continue
            if self._name_exists is not None and await self._name_exists(
                item, leaf
            ):
                prefill, hint = typed, f"! already exists: {leaf}"
                continue
            self._custom[i] = leaf
            break
        self._refresh_row(i)

    def _edit_prefill(self, i: int) -> str:
        """The string to pre-fill the edit prompt with."""
        custom = self._custom[i]
        if custom is not None:
            return self._rel_under_parent(i, custom)
        preview = self._previews[i] if i < len(self._previews) else None
        if preview:
            return posixpath.basename(preview)
        return posixpath.basename(self._items[i].dst_path.rstrip("/"))

    def action_commit(self) -> None:
        self.dismiss((list(self._res), list(self._custom)))

    def action_cancel(self) -> None:
        self.dismiss(None)

    # -- helpers ------------------------------------------------------

    def _scroll_to_cursor(self) -> None:
        if 0 <= self._cursor < len(self._row_labels):
            self._row_labels[self._cursor].scroll_visible()
