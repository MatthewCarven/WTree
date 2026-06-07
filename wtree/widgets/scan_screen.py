"""``ScanScreen`` - live modal for slow ``EntrySource.scan`` enumerations.

Pushed by ``WTreeApp._run_scan_with_dialog`` when a directory scan
crosses :data:`SCAN_MODAL_DELAY_SECONDS`. The user sees a centred
dialog naming the path, the source's scan method label
(e.g. ``"os.scandir"``), a running entry count, and an Esc-to-cancel
hint. Fast scans never see this dialog; the delayed-show timer cancels
itself before pushing.

Architecturally distinct from ``ProgressScreen`` (which serves
copies / moves / deletes via the ``OperationQueue``): scans don't go
through ``apply_plan``, have no byte semantics, and need no per-item
progress callback. The two surfaces share an idiom (centred modal +
delayed-show + Esc-cancel) but no code path. See design.md User
interface -> Scan dialog.

Concurrency: the screen polls :class:`ScanContext` state on the event
loop via ``set_interval`` at :data:`wtree.ops.queue.PROGRESS_REDRAW_HZ`
Hz. The consumer (``ContentsPane.show_path``, ``TreePane._populate``)
writes ``entries_seen`` and polls ``cancelled``; the screen reads
``entries_seen`` and (on Esc) calls ``ctx.cancelled.set()``. Both
sides run on the asyncio loop, so the access pattern is
single-threaded by construction. If a future parallel-scan story
lands, the same footnote that applies to the progress dialog applies
here.
"""

from __future__ import annotations

import asyncio
import os

from rich.text import Text
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Label, Static

from wtree.sources.base import Entry, Kind, ScanError

if TYPE_CHECKING:
    from textual.widgets.tree import TreeNode

    from wtree.sources.base import EntrySource

from wtree.ops.queue import PROGRESS_REDRAW_HZ


# Delay before the dialog is pushed. Tighter than the progress dialog's
# 0.4 s because directory-entry freezes feel jankier than copy freezes
# (the user expects copies to take time; directory entries should feel
# snappy). 0.25 s is short enough to surface the dialog before the user
# starts wondering whether the app is wedged, long enough that scans of
# a few hundred entries never see a flash.
SCAN_MODAL_DELAY_SECONDS = 0.25


# Number of entries the consumer iterates before yielding to the event
# loop with ``await asyncio.sleep(0)``. 500 is a sweet spot at typical
# entry sizes: small enough that Textual gets paint frames during big
# scans (the dialog actually appears, Esc actually responds), large
# enough that the yield overhead is negligible (~0.2 % of the scan
# cost on a 100 k-entry directory). Tune higher if profiling shows
# the yields themselves are the bottleneck, lower if responsiveness
# during the scan is the bottleneck.
SCAN_CHUNK_SIZE = 500


@dataclass
class ScanContext:
    """Shared state between a scan consumer and :class:`ScanScreen`.

    The consumer (``ContentsPane.show_path``, ``TreePane._populate``,
    etc.) writes ``entries_seen`` as it iterates the source and polls
    ``cancelled`` between chunks. The screen reads ``entries_seen``
    on its redraw timer and (on Esc) calls ``cancelled.set()``.

    ``completed`` is set by the gate helper
    (:meth:`WTreeApp._run_scan_with_dialog`) when the underlying work
    finishes - the dialog uses it to know it's safe to dismiss without
    racing the cancel path.

    Both events are :class:`asyncio.Event` for event-loop-affine
    signalling; setting from the consumer + polling from the screen
    is single-threaded by construction in v0.
    """

    path: str
    method_label: str
    header: str = "Scanning"
    entries_seen: int = 0
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    completed: asyncio.Event = field(default_factory=asyncio.Event)


class ScanScreen(ModalScreen[None]):
    """Live modal naming the in-flight directory scan.

    Construction takes a :class:`ScanContext`; the dialog reads
    ``entries_seen`` on each repaint and re-derives the body from
    that primitive. No state is duplicated.

    The dialog auto-dismisses when ``ctx.completed`` is set (the
    gate's ``finally`` block) - the timer notices on its next tick
    and calls :meth:`dismiss`.

    Esc-cancel is **immediate**: there's no wind-down phase like the
    progress dialog has, because the consumer checks ``ctx.cancelled``
    once per ``SCAN_CHUNK_SIZE`` entries and returns from the scan
    loop without committing. The pane keeps its previous listing.
    """

    DEFAULT_CSS = """
    ScanScreen {
        align: center middle;
    }

    ScanScreen > Vertical {
        background: $surface;
        border: thick $primary;
        width: 60;
        height: 11;
        padding: 0 1;
    }

    ScanScreen Label.header {
        background: $primary;
        color: $text;
        padding: 0 1;
        text-style: bold;
        dock: top;
    }

    ScanScreen Label.hint {
        background: $panel;
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        dock: bottom;
        text-align: center;
    }

    ScanScreen Static.body {
        padding: 1 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, ctx: ScanContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._timer: Optional[Timer] = None
        self._dismissing = False

    # --- safe dismissal --------------------------------------------------

    def safe_dismiss(self) -> None:
        """Pop this modal at most once, only while it's still on the stack.

        Three callers race to close this dialog: the redraw timer (on
        ``completed`` / ``cancelled``), the Esc handler, and the gate's
        ``finally`` block (:meth:`WTreeApp._run_scan_with_dialog`).
        Textual's :meth:`dismiss` pops the screen stack unconditionally,
        so a second call pops the base ``_default`` screen and raises
        ``ScreenStackError``. Gate on an idempotency flag *and* actual
        stack membership so whichever caller wins, the rest are no-ops.
        """
        if self._dismissing:
            return
        self._dismissing = True
        try:
            if self in self.app.screen_stack:
                self.dismiss(None)
        except Exception:  # noqa: BLE001 - torn down between timer and call
            pass

    # --- compose / mount --------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._header_text(), classes="header", id="scan-header")
            yield Static(self._body_text(), classes="body", id="scan-body")
            yield Label("Esc = Cancel", classes="hint")

    def on_mount(self) -> None:
        interval = 1.0 / PROGRESS_REDRAW_HZ
        self._timer = self.set_interval(interval, self._refresh)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # --- key handlers -----------------------------------------------------

    def action_cancel(self) -> None:
        """Esc: signal cancel and dismiss immediately.

        Unlike :class:`ProgressScreen`, scan cancellation has no
        wind-down phase. The consumer checks ``ctx.cancelled`` once
        per chunk and bails before committing; the pane keeps its
        previous listing. So the dialog can dismiss as soon as it
        fires the cancel signal.
        """
        self._ctx.cancelled.set()
        self.safe_dismiss()

    # --- repaint ---------------------------------------------------------

    def _refresh(self) -> None:
        """Timer callback: redraw body with the live entry count.

        Also auto-dismisses if ``ctx.completed`` is set - the gate
        sets that in its ``finally`` block, so the dialog disappears
        as soon as the scan finishes (cancelled or otherwise).
        """
        if self._ctx.completed.is_set() or self._ctx.cancelled.is_set():
            self.safe_dismiss()
            return
        try:
            body = self.query_one("#scan-body", Static)
        except Exception:  # noqa: BLE001 - torn down between timer and call
            return
        body.update(self._body_text())

    # --- text builders ---------------------------------------------------

    def _header_text(self) -> str:
        return self._ctx.header

    def _body_text(self) -> str:
        """Three lines: path, "via <method_label>", live entry count.

        Path is truncated mid-string with an ellipsis if it would
        overflow the dialog width, so deep paths don't break layout.
        """
        path = _truncate_path(self._ctx.path, max_width=54)
        n = self._ctx.entries_seen
        plural = "entry" if n == 1 else "entries"
        return f"{path}\nvia {self._ctx.method_label}\n\n{n:,} {plural}..."


def _truncate_path(path: str, *, max_width: int) -> str:
    """Shorten ``path`` to fit within ``max_width`` characters.

    Mid-string ellipsis preserves the visually-meaningful prefix (root
    indicator) and the trailing basename (which is what the user just
    typed / navigated into). Falls back to "no change" when the path
    already fits.
    """
    if len(path) <= max_width:
        return path
    if max_width <= 3:
        return "..."
    # Allocate roughly equal halves around the ellipsis.
    half = (max_width - 3) // 2
    return f"{path[:half]}...{path[-half:]}"



async def populate_dir_node(
    node: TreeNode[str],
    source: EntrySource,
    loaded: set[int],
    *,
    ctx: ScanContext | None = None,
    include_files: bool = False,
) -> None:
    """Scan ``node.data`` and add directory children + error leaves (dir-only).

    The shared body of :meth:`wtree.widgets.tree_pane.TreePane._populate` and
    the destination browser's ``_PickerTree._populate`` - factored here, next
    to :class:`ScanContext` and :data:`SCAN_CHUNK_SIZE`, so the two dir-tree
    widgets can't drift. Files and non-directory kinds are excluded (a dir
    tree); ``node.data`` is the directory path, or ``None`` for an error-
    placeholder leaf (skipped). ``loaded`` is the caller's set of already-
    scanned node ids - mutated in place for idempotency.

    Marks ``node.id`` loaded *before* scanning so re-entry during the async
    scan is a no-op. With a ``ctx`` (the scan-dialog gate) the loop writes
    ``ctx.entries_seen``, yields every :data:`SCAN_CHUNK_SIZE` entries, and
    polls ``ctx.cancelled`` - on cancel it drops the ``loaded`` marker and
    returns **before** adding any children (atomic: the node stays empty and
    re-expandable, as if the scan never happened). Without a ``ctx`` it is the
    legacy one-shot drain. Errors are added first (``⚠`` prefix) so they're
    noticed; directories follow, case-insensitively sorted (XTree / most file
    managers).

    ``include_files=True`` (the picker's files-greyed toggle, design.md
    2026-06-07) appends the directory's non-dir entries after the dirs as
    dim, non-selectable, non-expandable leaves with ``data=None`` - the
    same data convention as error placeholders, so selection handlers and
    search walks skip them with no extra checks. TreePane never passes it.
    """
    if node.id in loaded:
        return
    loaded.add(node.id)
    path = node.data
    if path is None:
        return
    directories: list[Entry] = []
    files: list[Entry] = []
    errors: list[ScanError] = []
    i = 0
    async for item in source.scan(path):
        if isinstance(item, Entry):
            if item.kind is Kind.DIR:
                directories.append(item)
            elif include_files:
                files.append(item)
        elif isinstance(item, ScanError):
            errors.append(item)
        i += 1
        if ctx is not None:
            ctx.entries_seen = i
            if i % SCAN_CHUNK_SIZE == 0:
                await asyncio.sleep(0)
                if ctx.cancelled.is_set():
                    loaded.discard(node.id)
                    return
    if ctx is not None and ctx.cancelled.is_set():
        loaded.discard(node.id)
        return
    directories.sort(key=lambda e: e.name.lower())
    for err in errors:
        node.add_leaf(f"⚠ {err.message}", data=None)
    for entry in directories:
        node.add(
            entry.name,
            data=os.path.join(path, entry.name),
            allow_expand=True,
        )
    if include_files:
        files.sort(key=lambda e: e.name.lower())
        for entry in files:
            node.add_leaf(Text(entry.name, style="dim"), data=None)
