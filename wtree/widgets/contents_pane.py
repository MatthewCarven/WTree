"""``ContentsPane`` — table of entries for the directory the tree's cursor is
sitting on.

Per ``design.md`` § Layout: the panes are coupled. When the tree's cursor
moves, ``WTreeApp`` calls :meth:`show_path` to refresh this pane. Unlike
``TreePane`` (directories only), this pane shows entries of every ``Kind``.

Tagged entries display a ``*`` in the leading ``T`` column **and** the
whole row renders in bold yellow (2026-05-22, Matthew's pick). Space and
``T`` toggle the tag on the row under the cursor; the state lives in the
:class:`~wtree.tagged_set.TaggedSet` owned by ``WTreeApp`` so it persists
across pane refreshes. Error rows are non-taggable.

Navigation (``design.md`` § Modality — pane focus determines arrow-key
behaviour):

* ``←`` and Backspace → go to parent dir (delegates to
  ``TreePane.action_focus_parent``)
* ``→`` and Enter → enter the highlighted dir (calls
  ``TreePane.focus_dir_under_cursor``); no-op on file rows in v0.

DataTable normally binds ←/→ for column navigation; here we override them
because column-nav has no v0 utility (only the marker column at index 0
ever changes), and the design's pane-modal arrow semantics take priority.

Sort order: directories first, then symlinks, then files, then other; each
group case-insensitive alphabetical. Display formatting (sizes, dates) is
deliberately simple in v0 — a richer presentation layer with humanised sizes
and relative timestamps is parking-lot material.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING

from rich.text import Text
from textual.coordinate import Coordinate
from textual.message import Message
from textual.widgets import DataTable

from wtree.sources.base import Entry, EntrySource, Kind, ScanError
from wtree.tagged_set import TaggedSet
from wtree.widgets.scan_screen import SCAN_CHUNK_SIZE, ScanContext

if TYPE_CHECKING:
    # Imported only for type hints — avoids a runtime circular dependency
    # via ``wtree.app`` → ``ContentsPane`` → ``TreePane``.
    from wtree.widgets.tree_pane import TreePane


# Smaller value sorts first.
_KIND_SORT_ORDER: dict[Kind, int] = {
    Kind.DIR: 0,
    Kind.SYMLINK: 1,
    Kind.FILE: 2,
    Kind.OTHER: 3,
}

# Column index of the tag marker — stays consistent across show_path and
# refresh_tag_markers, so we only have to remember one number.
_TAG_COL = 0

# Rich style applied to every cell of a tagged row. Bold yellow is the
# WTree convention (Matthew, 2026-05-22) — visually unmistakable without
# clashing with the DataTable cursor highlight, which Textual paints over
# the top via its own row styling.
_TAGGED_STYLE = "bold yellow"


def _cell(value: str, tagged: bool) -> Text | str:
    """Render one raw cell value as plain str or styled Rich ``Text``.

    Untagged rows stay as plain strings — this keeps the cell's ``str`` /
    equality semantics simple for everything that doesn't care about
    style. Tagged cells become ``Text(value, style=_TAGGED_STYLE)``;
    tests that need to read the underlying string should use
    ``str(cell)`` or ``cell.plain``.
    """
    if tagged:
        return Text(value, style=_TAGGED_STYLE)
    return value


class ContentsPane(DataTable):
    """A table of one directory's entries with per-row tagging.

    Public API:
      - :meth:`show_path` — repopulate from a new directory.
      - :meth:`refresh_tag_markers` — refresh markers + row styling
        without re-scanning (used by ``Ctrl+U``, ``Ctrl+A``, and other
        bulk mutations).
      - :attr:`current_path` — read-only, the path currently displayed.
    """

    BORDER_TITLE = "Contents"

    # Local bindings — only active when this pane is focused. ``T`` is the
    # XTree primary; ``space`` is a universally-comfortable alias from the
    # design's canonical keymap. Arrow / Enter / Backspace bindings carry
    # the pane-modal navigation defined in design.md § Modality.
    BINDINGS = [
        ("space", "toggle_tag", "Tag"),
        ("t", "toggle_tag", "Tag"),
        ("left", "go_parent", "Parent"),
        ("backspace", "go_parent", "Parent"),
        ("right", "enter_dir", "Enter dir"),
        ("enter", "enter_dir", "Enter dir"),
    ]

    class TagsChanged(Message):
        """Posted after :meth:`action_toggle_tag` mutates the tagged set.

        ``WTreeApp`` listens for this to refresh its subtitle (tag count).
        Bubbles naturally up the DOM — handler name is
        ``on_contents_pane_tags_changed``.
        """

    def __init__(
        self,
        source: EntrySource,
        tagged_set: TaggedSet,
        *,
        id: str | None = None,  # noqa: A002 — Textual API uses ``id``
    ) -> None:
        super().__init__(id=id, cursor_type="row", zebra_stripes=True)
        self._source = source
        # The pane borrows a reference; ownership is on the app so the set
        # outlives any single pane mount/unmount cycle.
        self._tagged = tagged_set
        self._current_path: str | None = None
        # Parallel to the DataTable's rows: the full absolute path of each
        # row, or empty string for non-taggable error rows.
        self._row_paths: list[str] = []
        # Parallel kind tracking lets ``action_enter_dir`` distinguish a
        # directory row from a file row without re-scanning the source.
        # ``None`` for error rows (they have neither a path nor a kind).
        self._row_kinds: list[Kind | None] = []
        # Raw (un-styled) cell strings per row, parallel to ``_row_paths``.
        # Lets ``refresh_tag_markers`` restyle a row without re-scanning
        # the source: it walks ``_row_cells[row]`` and pushes each value
        # back through ``_cell()`` with the current tagged state.
        self._row_cells: list[list[str]] = []

    def on_mount(self) -> None:
        # Order must match _TAG_COL above.
        self.add_columns("T", "Name", "Size", "Modified", "Perms")

    @property
    def current_path(self) -> str | None:
        """The path most recently passed to :meth:`show_path`."""
        return self._current_path

    def cursor_entry(self) -> tuple[str, Kind] | None:
        """Return ``(path, kind)`` for the row under the cursor, or ``None``.

        Used by ops bindings (e.g. ``C`` copy) to resolve the "entry under
        cursor" fallback of the Selection rule (``design.md`` § Selection
        rule). Returns ``None`` for empty tables, out-of-range cursors, and
        error rows — callers should fall back gracefully in those cases.
        """
        row = self.cursor_row
        if row < 0 or row >= len(self._row_paths):
            return None
        path = self._row_paths[row]
        kind = self._row_kinds[row]
        if not path or kind is None:
            return None
        return path, kind

    def row_paths(self) -> list[str]:
        """Return absolute paths for every taggable row, in display order.

        Skips error rows (empty path strings). Used by ``Ctrl+A``
        tag-all-in-current-dir and ``+`` / ``-`` glob tagging to enumerate
        what's visible without exposing the internal list.
        """
        return [p for p in self._row_paths if p]

    async def show_path(
        self,
        path: str | None,
        *,
        ctx: ScanContext | None = None,
    ) -> None:
        """Replace the table contents with the entries at ``path``.

        ``None`` clears the table (used when the tree cursor lands on
        an error-leaf with no backing path).

        ``ctx`` is an optional :class:`ScanContext` shared with a
        :class:`ScanScreen`. When supplied, the scan loop:

        * yields control to the event loop every
          :data:`SCAN_CHUNK_SIZE` entries via ``await
          asyncio.sleep(0)`` so Textual gets paint frames during a
          big scan (the dialog actually appears, Esc actually
          responds);
        * writes the running entry count to ``ctx.entries_seen`` so
          the dialog can display it;
        * polls ``ctx.cancelled`` between chunks and returns early
          **without** touching the table if Esc was pressed.

        Cancellation is **non-destructive**: the table is only
        cleared and repopulated after the scan completes
        successfully. A cancelled scan leaves the pane on its
        previous listing, exactly equivalent to "user never pressed
        the key". This is why the original ``self.clear()`` /
        ``_row_*.clear()`` calls have moved from the prologue to the
        commit block below.

        Without ``ctx``, callers get the legacy "drain the iterator
        in one shot" behaviour - useful for tests and any caller
        that knows the scan is small (e.g. ``MockSource`` in
        production code paths).
        """
        if path is None:
            self.clear()
            self._row_paths.clear()
            self._row_kinds.clear()
            self._row_cells.clear()
            self._current_path = None
            return

        # Cursor preservation (design.md 2026-06-07): re-showing the SAME
        # path is a refresh (auto-refresh after ops, Ctrl+R, editor
        # return) - the cursor should stay put. Showing a DIFFERENT path
        # is navigation - the cursor belongs at row 0. Snapshot before
        # the scan; the commit block below decides.
        preserving = path == self._current_path
        old_cursor_row = max(self.cursor_row, 0)
        old_cursor_path: str | None = None
        if preserving and 0 <= self.cursor_row < len(self._row_paths):
            old_cursor_path = self._row_paths[self.cursor_row] or None

        entries: list[Entry] = []
        errors: list[ScanError] = []
        i = 0
        async for item in self._source.scan(path):
            if isinstance(item, Entry):
                entries.append(item)
            elif isinstance(item, ScanError):
                errors.append(item)
            i += 1
            if ctx is not None:
                ctx.entries_seen = i
                if i % SCAN_CHUNK_SIZE == 0:
                    # Yield to the loop so Textual gets paint frames and
                    # the cancel signal lands promptly. The check happens
                    # AFTER the yield so any cancel that lands during the
                    # yield is honoured before the next chunk starts.
                    await asyncio.sleep(0)
                    if ctx.cancelled.is_set():
                        # Bail without touching the table - pane keeps
                        # its previous listing.
                        return

        # Final cancel check before commit, in case Esc landed during
        # the last partial chunk (entries < SCAN_CHUNK_SIZE).
        if ctx is not None and ctx.cancelled.is_set():
            return

        entries.sort(key=lambda e: (_KIND_SORT_ORDER[e.kind], e.name.lower()))

        # Commit: only now do we wipe the previous listing.
        self.clear()
        self._row_paths.clear()
        self._row_kinds.clear()
        self._row_cells.clear()
        self._current_path = path

        sid = self._source.source_id

        for err in errors:
            cells = ["", f"⚠ {err.message}", "", "", ""]
            # Error rows are never tagged — render plain.
            self.add_row(*cells)
            self._row_cells.append(cells)
            # Empty string = "this row is not taggable" — the only sentinel
            # value used in ``_row_paths`` (real paths are never empty).
            self._row_paths.append("")
            self._row_kinds.append(None)
        for entry in entries:
            full_path = os.path.join(path, entry.name)
            tagged = self._tagged.contains(sid, full_path)
            marker = "*" if tagged else ""
            size = "<DIR>" if entry.kind is Kind.DIR else str(entry.size)
            mtime = entry.mtime_iso or ""
            perms = entry.permissions or ""
            # Trailing slash on directory names is XTree-style and reads
            # cleanly without needing a separate "kind" column.
            name = f"{entry.name}/" if entry.kind is Kind.DIR else entry.name
            cells = [marker, name, size, mtime, perms]
            self.add_row(*(_cell(v, tagged) for v in cells))
            self._row_cells.append(cells)
            self._row_paths.append(full_path)
            self._row_kinds.append(entry.kind)

        # Cursor placement. Navigation pins row 0 explicitly (DataTable
        # defaults to (0, 0), but pin it so ``action_toggle_tag`` has a
        # valid ``cursor_row`` right after a pane refresh). A same-path
        # refresh restores the cursor: to the same entry if it still
        # exists (survives reorderings/insertions above it), else to the
        # same row index clamped to the new length - delete row 5 and
        # the cursor lands on what is now row 5, i.e. the next entry
        # (the Explorer/MC convention; design.md 2026-06-07).
        if self.row_count > 0:
            target = 0
            if preserving:
                if (
                    old_cursor_path is not None
                    and old_cursor_path in self._row_paths
                ):
                    target = self._row_paths.index(old_cursor_path)
                else:
                    target = min(old_cursor_row, self.row_count - 1)
            self.move_cursor(row=target, column=0)

    def refresh_tag_markers(self) -> None:
        """Refresh the leading "T" column **and** the row's style from
        the tagged set.

        Cheaper than ``show_path``: doesn't re-scan from the source.
        Walks the stored raw cell values in ``_row_cells`` and pushes
        each through ``_cell()`` so a tagged row gets bold-yellow style
        on every column, untagged stays plain. Use after any bulk
        tagged-set mutation (``Ctrl+U`` clear, ``Ctrl+A`` tag-all, ``+``
        / ``-`` glob, recursive tree-pane Space) where the underlying
        entries haven't changed.
        """
        sid = self._source.source_id
        for row, full_path in enumerate(self._row_paths):
            if not full_path:
                continue
            tagged = self._tagged.contains(sid, full_path)
            cells = self._row_cells[row]
            # Marker tracks tagged state directly; the raw value lives in
            # cells[0] so subsequent refreshes don't compound stale "*"s.
            cells[0] = "*" if tagged else ""
            for col, value in enumerate(cells):
                self.update_cell_at(
                    Coordinate(row, col), _cell(value, tagged)
                )

    def action_toggle_tag(self) -> None:
        """Toggle the tagged state of the entry under the cursor.

        Posts :class:`TagsChanged` so the app can update its subtitle. A
        no-op when the cursor is on an error row or the table is empty.
        Restyles every cell of the row, not just the marker column, so
        the bold-yellow tagged-row style appears or disappears in one
        gesture.
        """
        row = self.cursor_row
        if row < 0 or row >= len(self._row_paths):
            return
        full_path = self._row_paths[row]
        if not full_path:
            return  # Error row — non-taggable by design.
        sid = self._source.source_id
        is_tagged = self._tagged.toggle(sid, full_path)
        cells = self._row_cells[row]
        cells[0] = "*" if is_tagged else ""
        for col, value in enumerate(cells):
            self.update_cell_at(
                Coordinate(row, col), _cell(value, is_tagged)
            )
        self.post_message(self.TagsChanged())

    # ------------------------------------------------------------------
    # Navigation actions — delegate to the tree pane, which holds the
    # authoritative cursor. design.md § Layout: "two views of one
    # selection".
    # ------------------------------------------------------------------

    def _tree(self) -> "TreePane":
        # Local import keeps the module import order TreePane → ContentsPane
        # → app, with ContentsPane only reaching for TreePane at call time.
        from wtree.widgets.tree_pane import TreePane

        return self.app.query_one(TreePane)

    def action_go_parent(self) -> None:
        """``←`` / Backspace — move the tree cursor up one level.

        The tree's ``NodeHighlighted`` event will then call back into
        ``show_path`` and refresh this pane. No-op at the tree root.
        """
        self._tree().action_focus_parent()

    async def action_enter_dir(self) -> None:
        """``→`` / Enter — drill into the directory under the cursor.

        File and error rows are no-ops in v0 (View/V on files is a later
        binding). The tree pane handles expansion + cursor move; the
        cascading ``NodeHighlighted`` refreshes this pane.
        """
        row = self.cursor_row
        if row < 0 or row >= len(self._row_paths):
            return
        full_path = self._row_paths[row]
        if not full_path:
            return  # Error row.
        if self._row_kinds[row] is not Kind.DIR:
            return  # File / symlink — design says only dirs drill in.
        await self._tree().focus_dir_under_cursor(full_path)

    # ------------------------------------------------------------------
    # SearchTarget protocol (used by incremental search ``/``)
    # ------------------------------------------------------------------
    #
    # The app's search machinery treats the pane as an opaque "thing
    # with searchable rows" through these three methods. No abstract
    # base class - duck typing is enough since the protocol is tiny and
    # the only two implementers (this pane and TreePane) are sibling
    # modules. If a third pane joins, formalise via typing.Protocol.

    def iter_searchable(self) -> Iterator[tuple[int, str]]:
        """Yield ``(row_index, label)`` for each searchable row.

        Error rows (empty ``_row_paths`` entry) are skipped - they have
        no useful label and can't be navigated to. The label is the
        entry's basename without the trailing-slash dirs use for
        display, so a user typing ``rep`` matches both ``report.txt``
        and ``reports/`` cleanly.
        """
        for row, path in enumerate(self._row_paths):
            if path:
                yield row, os.path.basename(path)

    def set_search_cursor(self, row: int) -> None:
        """Move the cursor to ``row``. Out-of-range rows are ignored
        rather than raising - the app may pass a stale index if the
        pane refreshed mid-search.
        """
        if 0 <= row < self.row_count:
            self.move_cursor(row=row, column=0)

    def get_search_cursor(self) -> int:
        """Current cursor row. Pair with :meth:`set_search_cursor` to
        record a pre-search position the app can restore on Esc.
        """
        return self.cursor_row
