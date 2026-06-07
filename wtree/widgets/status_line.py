"""``StatusLine`` - one-line transient status between the panes and the
F-key bar.

Per ``design.md`` Modality: "The status line shows the active mode."
Per ``design.md`` Layout (implicit): an MC-style status surface above
the F-key cheat sheet.

Priority order of what we show (highest first):

1. **Active flash** - a transient message scheduled via :meth:`flash`
   (e.g. "Rename rejected", "Already at filesystem root"). Holds for
   the configured timeout, even through cursor moves. Auto-clears
   when the timer fires.
2. **Queue running** - "Copying: 3/5 items, 1 queued" - because that's
   the most volatile state and the thing the user wants to know NOW.
3. **Cursor entry** - "/path/to/file  1.4 KB  2026-05-21 12:00" - when
   idle, surface what's selected (per design "the active mode" is
   nothing-special, so we surface what the user is pointing at).
4. **Fallback** - blank.

Two kinds of transient feedback live in the codebase. **Status flashes**
(this widget's :meth:`flash`) are user-immediate nudges: "X rejected",
"X cancelled", "Already at root". They go through here so the status
line is the single place to look. **Toast notifications**
(:meth:`textual.app.App.notify`) are kept for things that may fire
async when the user isn't looking - queue completion most importantly,
where a copy might finish minutes later in the background.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.timer import Timer
from textual.widgets import Static

from rich.markup import escape as _escape_markup

from wtree.ops.base import _human_bytes
from wtree.sources.base import Kind

if TYPE_CHECKING:
    from wtree.app import WTreeApp


# Default flash timeout. Long enough to read a typical "X rejected"
# sentence at a glance, short enough to feel transient and not crowd
# the next status update. 3 seconds matches the typical Vim status
# message duration.
DEFAULT_FLASH_TIMEOUT = 3.0


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

    # Severity -> Rich markup style for the flash line (design.md
    # 2026-06-07, todo "Severity-styled flash"). info renders unstyled.
    _SEVERITY_STYLES = {
        "info": None,
        "warning": "yellow",
        "error": "bold red",
    }

    def __init__(self) -> None:
        super().__init__("", markup=True)
        # Flash state. ``_flash_message`` is the text currently being
        # displayed (None when no flash is active). ``_flash_timer`` is
        # the Textual timer that will clear the flash; we keep the
        # reference so a new flash() call can cancel and replace it.
        self._flash_message: str | None = None
        self._flash_severity: str = "info"
        self._flash_timer: Timer | None = None

    # ------------------------------------------------------------------
    # Flash API - transient status messages
    # ------------------------------------------------------------------

    def flash(
        self,
        message: str,
        *,
        timeout: float = DEFAULT_FLASH_TIMEOUT,
        severity: str = "info",
    ) -> None:
        """Show ``message`` for ``timeout`` seconds, then revert.

        Replaces any currently-active flash (the previous timer is
        cancelled). The flash holds through cursor moves and other
        ``refresh_from`` calls until its timer fires, then the status
        line reverts to its normal app-state render via
        :meth:`refresh_from`.

        ``severity`` styles the line: ``"info"`` (default) renders
        plain, ``"warning"`` yellow, ``"error"`` bold red. The message
        itself is rendered **literally** - it is markup-escaped before
        the severity wrapper is applied, so a path containing ``[i]``
        can't style (or break) the line. Callers wanting decorated
        text use the severity channel, not inline tags. Unknown
        severities render as info rather than raising (a bad flash
        must never crash the caller).
        """
        # Cancel any in-flight timer so the new flash gets its full
        # timeout window, not whatever was left on the old one.
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_message = message
        self._flash_severity = severity
        self._flash_timer = self.set_timer(timeout, self._clear_flash)
        # Show immediately - don't wait for the next refresh_from.
        self.update(self._styled_flash(message, severity))

    @classmethod
    def _styled_flash(cls, message: str, severity: str) -> str:
        """Escape ``message`` and wrap it in the severity style tags."""
        body = _escape_markup(message)
        style = cls._SEVERITY_STYLES.get(severity)
        if style is None:
            return body
        return f"[{style}]{body}[/{style}]"

    def _clear_flash(self) -> None:
        """Timer callback - clear flash state and revert to app render.

        ``refresh_from`` needs the app to rebuild the normal status
        text. ``self.app`` is the mounted Textual app, which is the
        :class:`WTreeApp` instance we want.
        """
        self._flash_message = None
        self._flash_timer = None
        # Best-effort revert. If the app isn't a WTreeApp for some
        # reason (e.g. a stripped-down test harness), fall back to a
        # blank line rather than crashing the timer callback.
        try:
            from wtree.app import WTreeApp

            if isinstance(self.app, WTreeApp):
                self.refresh_from(self.app)
                return
        except Exception:  # noqa: BLE001 - defensive in a timer callback
            pass
        self.update("")

    def refresh_from(self, app: "WTreeApp") -> None:
        """Re-render from the app's current state.

        Called from any handler that mutates queue state, cursor
        position, or the tagged set. Cheap enough to call generously
        - the work is a few attribute reads and an f-string.

        **Flash precedence:** if a flash is currently active, this is
        a no-op. The flash holds the line until its timer fires; the
        timer's ``_clear_flash`` then calls back into this method to
        revert. Without this guard, the cursor move that happens
        immediately after (say) an ascend would overwrite the
        "Logged: NEW (ascended from OLD)" flash before the user could
        read it.
        """
        if self._flash_message is not None:
            return
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
                # Discovery hint for the resume key. Only show when
                # the progress dialog is NOT currently on the screen
                # stack - no point hinting at Ctrl+P while the dialog
                # is up. Import is local to avoid widget->widget
                # import cycles at module load.
                hint = ""
                try:
                    from wtree.widgets.progress_screen import ProgressScreen
                    if not any(
                        isinstance(s, ProgressScreen)
                        for s in app.screen_stack
                    ):
                        hint = "  [dim][Ctrl+P][/dim]"
                except Exception:  # noqa: BLE001 - defensive at render time
                    pass
                return (
                    f"[b]{running.kind.value.capitalize()}[/b]: "
                    f"{done}/{total} items{ahead_note}{hint}"
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
