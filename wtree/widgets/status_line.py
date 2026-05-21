"""``StatusLine`` - one-line transient status between the panes and the
F-key bar.

Per ``design.md`` Modality: "The status line shows the active mode."
Per ``design.md`` Layout (implicit): an MC-style status surface above
the F-key cheat sheet.

Priority order of what we show (highest first):

1. **Queue running** - "Copying: 3/5 items, 1 queued" - because that's
   the most volatile state and the thing the user wants to know NOW.
2. **Cursor entry** - "/path/to/file  1.4 KB  2026-05-21 12:00" - when
   idle, surface what's selected (per design "the active mode" is
   nothing-special, so we surface what the user is pointing at).
3. **Fallback** - blank.

Transient messages (errors, cancellations) go through Textual's
notify() rather than this line - keeping the status line consistent
means the user can rely on it always reflecting current state, not
something stale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

from wtree.ops.base import _human_bytes
from wtree.sources.base import Kind

if TYPE_CHECKING:
    from wtree.app import WTreeApp


class StatusLine(Static):
    """Reactive single-line status display. Dock at screen bottom."""

    DEFAULT_CSS = """
    StatusLine {
        dock: bottom;
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("", markup=True)

    def refresh_from(self, app: "WTreeApp") -> None:
        """Re-render from the app's current state.

        Called from any handler that mutates queue state, cursor
        position, or the tagged set. Cheap enough to call generously
        - the work is a few attribute reads and an f-string.
        """
        self.update(self._build_text(app))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _build_text(app: "WTreeApp") -> str:
        # Queue-active beats everything - copying is the most important
        # transient state.
        queue = app.op_queue
        if queue is not None and queue.depth > 0:
            running = queue.running
            progress = queue.running_progress
            if running is not None and progress is not None:
                done, total = progress
                ahead = queue.depth - 1
                ahead_note = (
                    "" if ahead <= 0 else f"  [+{ahead} queued]"
                )
                return (
                    f"[b]{running.kind.value.capitalize()}[/b]: "
                    f"{done}/{total} items{ahead_note}"
                )
            # Pending-only state (worker hasn't picked it up yet).
            return f"[b]Queued[/b]: {queue.depth} op(s) pending"

        # Idle - show what's under the cursor.
        from wtree.widgets.contents_pane import ContentsPane

        contents = app.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        if cursor is None:
            current = contents.current_path or ""
            return f"[dim]{current}[/dim]" if current else ""
        path, kind = cursor
        # Best-effort size + mtime from the source. We could cache
        # this on _row_paths but the on-demand stat is fine while idle.
        try:
            import os
            st = os.stat(path)
            size = "<DIR>" if kind is Kind.DIR else _human_bytes(st.st_size)
            from datetime import datetime
            mtime = datetime.fromtimestamp(st.st_mtime).strftime(
                "%Y-%m-%d %H:%M"
            )
        except OSError:
            size = ""
            mtime = ""
        kind_marker = ""
        if kind is Kind.DIR:
            kind_marker = "[dim]/[/dim]"
        elif kind is Kind.SYMLINK:
            kind_marker = "[dim]@[/dim]"
        parts = [f"{path}{kind_marker}"]
        if size:
            parts.append(size)
        if mtime:
            parts.append(mtime)
        return "  ".join(parts)
