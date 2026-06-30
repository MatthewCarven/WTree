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
* A live **selection summary** sits above the hint and always reflects the
  current committed state - "Selected: all N -> OVERWRITE" when every row
  shares one method (the ``S``/``O``/``R`` case), or a per-method breakdown
  ("Selected: 5 skip, 2 overwrite") when mixed - so the user can see exactly
  what Enter will do, and that their last keypress registered.

Default per-row resolution is **Skip**: the safe, non-destructive
choice, so a user who just presses Enter loses nothing.
"""

from __future__ import annotations

import posixpath
from collections.abc import Awaitable, Callable, Sequence

from textual import work
from textual.app import ComposeResult
from rich.text import Text
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from wtree.ops.base import (
    to_native,
    ConflictKind,
    PlanItem,
    Resolution,
    resolve_relative_leaf,
)
from wtree.widgets.prompt import PromptDialog

# Viewport height for the windowed body. The dialog never mounts one widget
# per conflict (a 356k-conflict copy did exactly that = ~7 GB of Textual
# Labels); a single Static shows this many rows around the cursor and is
# rebuilt as the cursor moves, so memory is O(visible) at any conflict count.
_VISIBLE_ROWS = 18
_CURSOR_STYLE = "reverse"


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

    ConflictDialog Static.rows {
        height: auto;
        max-height: 18;
        width: 100%;
    }

    ConflictDialog Label.position {
        color: $text-muted;
        text-style: italic;
    }

    ConflictDialog Label.summary {
        margin-top: 1;
        text-style: bold;
        color: $warning;
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
        ("pageup", "page_up", "Page up"),
        ("pagedown", "page_down", "Page down"),
        ("home", "cursor_home", "First"),
        ("end", "cursor_end", "Last"),
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
        self._top = 0  # index of the first row in the current viewport
        self._body: Static | None = None
        self._position_label: Label | None = None
        self._summary_label: Label | None = None

    # -- composition --------------------------------------------------

    def compose(self) -> ComposeResult:
        n = len(self._items)
        with Vertical():
            yield Label(
                f"{n} conflict(s) - choose what to do", classes="title"
            )
            body = Static(self._window_text(), classes="rows", id="conflict-body")
            self._body = body
            yield body
            position = Label(
                self._position_text(), classes="position", id="conflict-position"
            )
            self._position_label = position
            yield position
            summary = Label(
                self._summary_text(), classes="summary", id="conflict-summary"
            )
            self._summary_label = summary
            yield summary
            yield Label(self._hint_text(), classes="hint")

    def on_mount(self) -> None:
        self._refresh_body()

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
        line = f"{marker} [{res:<9}]  {to_native(item.dst_path)}  (existing: {existing})"
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

    def _summary_text(self) -> str:
        """Live one-line summary of what Enter will commit.

        When every row shares one resolution (the common ``S``/``O``/``R``
        set-all case) it collapses to "Selected: all N -> OVERWRITE"; when
        mixed it lists each non-empty method with its count, in display
        order. Recomputed from ``self._res`` on every change, so it doubles
        as the keypress-landed confirmation.
        """
        n = len(self._items)
        if n == 0:
            return ""
        order = (Resolution.SKIP, Resolution.OVERWRITE, Resolution.RENAME)
        counts = {r: 0 for r in order}
        for r in self._res:
            counts[r] = counts.get(r, 0) + 1
        nonzero = [r for r in order if counts[r] > 0]
        if len(nonzero) == 1:
            only = _RES_LABEL[nonzero[0]].upper()
            if n == 1:
                return f"Selected: {only}"
            return f"Selected: all {n} -> {only}"
        parts = [f"{counts[r]} {_RES_LABEL[r]}" for r in nonzero]
        return "Selected: " + ", ".join(parts)

    def _refresh_summary(self) -> None:
        if self._summary_label is not None:
            self._summary_label.update(self._summary_text())

    def _position_text(self) -> str:
        n = len(self._items)
        return f"row {self._cursor + 1} of {n}" if n else ""

    def _window_text(self) -> Text:
        """The viewport: ``_VISIBLE_ROWS`` rows around the cursor as one Text.

        The cursor row is styled (and already carries a ``>`` marker from
        :meth:`_row_text`). O(visible) - never touches the other rows, so a
        356k-conflict set costs the same as a handful.
        """
        t = Text()
        n = len(self._items)
        if n == 0:
            return t
        top = self._top
        bottom = min(n, top + _VISIBLE_ROWS)
        for i in range(top, bottom):
            line = self._row_text(i)
            t.append(line, style=_CURSOR_STYLE if i == self._cursor else "")
            if i < bottom - 1:
                t.append("\n")
        return t

    def _ensure_cursor_visible(self) -> None:
        """Slide the viewport so the cursor row is inside it."""
        if self._cursor < self._top:
            self._top = self._cursor
        elif self._cursor >= self._top + _VISIBLE_ROWS:
            self._top = self._cursor - _VISIBLE_ROWS + 1
        if self._top < 0:
            self._top = 0

    def _refresh_body(self) -> None:
        if self._body is not None:
            self._body.update(self._window_text())
        if self._position_label is not None:
            self._position_label.update(self._position_text())

    # -- actions ------------------------------------------------------

    def _move_cursor(self, target: int) -> None:
        if not self._items:
            return
        self._cursor = target % len(self._items)
        self._ensure_cursor_visible()
        self._refresh_body()

    def action_cursor_up(self) -> None:
        self._move_cursor(self._cursor - 1)

    def action_cursor_down(self) -> None:
        self._move_cursor(self._cursor + 1)

    def action_page_up(self) -> None:
        if self._items:
            self._move_cursor(max(0, self._cursor - _VISIBLE_ROWS))

    def action_page_down(self) -> None:
        if self._items:
            self._move_cursor(min(len(self._items) - 1, self._cursor + _VISIBLE_ROWS))

    def action_cursor_home(self) -> None:
        if self._items:
            self._move_cursor(0)

    def action_cursor_end(self) -> None:
        if self._items:
            self._move_cursor(len(self._items) - 1)

    def action_set_current(self, which: str) -> None:
        if not self._items:
            return
        self._res[self._cursor] = Resolution(which)
        self._refresh_body()
        self._refresh_summary()

    def action_set_all(self, which: str) -> None:
        self._res = [Resolution(which)] * len(self._res)
        self._refresh_body()
        self._refresh_summary()

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
        self._refresh_body()
        self._refresh_summary()

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

