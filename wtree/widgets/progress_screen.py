"""``ProgressScreen`` - live progress modal for big copies/moves/deletes.

Pushed by ``WTreeApp`` when an enqueued op trips any of three
thresholds (see :data:`wtree.ops.queue.PROGRESS_MODAL_BYTES` /
``PROGRESS_MODAL_ITEMS`` / ``PROGRESS_MODAL_DELAY_SECONDS``). Tiny ops
fall through to the StatusLine's existing ``Copy N/M`` indicator.

Six-field readout grid (design.md 2026-05-25 Progress dialog):

* **Percent**  - byte-percent of the whole plan (NOT items-done %).
* **Elapsed**  - MM:SS / H:MM:SS since plan start.
* **Data**     - bytes_done scaled (KB / MB / GB).
* **Rate**     - bytes_done / elapsed_seconds, scaled.
* **Drag**     - ``(1 - bytes_done/bytes_total) * elapsed_seconds``,
                 normalised fraction-seconds. A "patience tax" vibe
                 number; peaks mid-op and returns to zero at end.
* **Files**    - items_done / items_total (preserves the existing
                 N/M signal for sanity-checking against Percent).

Zero guard (design.md): readouts whose formula touches elapsed OR
bytes_done render an em-dash while either is zero. Catches:

* divide-by-zero on Rate;
* all-zero opening paint on Drag;
* the wildly-lying Rate a single sub-second chunk would produce;
* the big-file-just-opened case where elapsed has ticked but no
  bytes have flowed yet (Rate would read ``0.0 MB/s`` and Drag
  would spike to its theoretical max).

Cancellation: Esc asks the queue to cancel via
:meth:`~wtree.ops.queue.OperationQueue.request_cancel`. The chunk
loop bails at the next chunk boundary, deletes the partial dest
file, and the item ends as FAILED. The dialog's header switches to
``Cancelling...`` until the queue clears.

Concurrency: the screen never subscribes to the per-chunk byte
callback directly (that would fire from a worker thread). It polls
queue properties on the event loop via ``set_interval`` at
:data:`wtree.ops.queue.PROGRESS_REDRAW_HZ` Hz instead. See
design.md -> Progress dialog -> Concurrency assumptions.
"""

from __future__ import annotations

from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Label, Static

from wtree.ops.queue import OperationQueue, PROGRESS_REDRAW_HZ


# Bar width in cells (the entire ASCII bar fits inside a thick-border
# modal at typical terminal widths). Tuned to ~30 cells, which gives
# every percentage point a distinct visual step at 100% resolution.
_BAR_WIDTH = 30
_BAR_FILLED = "█"
_BAR_HALF = "▌"
_BAR_EMPTY = "░"
_EM_DASH = "—"


class ProgressScreen(ModalScreen[None]):
    """Live progress dialog for one in-flight Plan.

    Construction takes the OperationQueue; the dialog reads
    ``bytes_progress`` / ``running_progress`` / ``elapsed_seconds`` /
    ``running`` / ``cancel_requested`` on each repaint and re-derives
    every readout from those primitives. No state is duplicated.

    The dialog auto-dismisses when the queue stops running the plan
    that was active at push time. If a later plan starts before this
    dialog dismisses (queue chained another op), the dialog will see
    a different ``queue.running`` and dismiss anyway - the threshold
    gate in ``WTreeApp`` will push a fresh dialog for that plan.
    """

    DEFAULT_CSS = """
    ProgressScreen {
        align: center middle;
    }

    ProgressScreen > Vertical {
        background: $surface;
        border: thick $primary;
        width: 60;
        height: 13;
        padding: 0 1;
    }

    ProgressScreen Label.header {
        background: $primary;
        color: $text;
        padding: 0 1;
        text-style: bold;
        dock: top;
    }

    ProgressScreen Label.hint {
        background: $panel;
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        dock: bottom;
        text-align: center;
    }

    ProgressScreen Static.body {
        padding: 1 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel_or_dismiss", "Cancel / Close", show=False),
        Binding("m", "minimize", "Minimize", show=False),
    ]

    def __init__(self, queue: OperationQueue) -> None:
        super().__init__()
        self._queue = queue
        # Plan identity at push time. If the queue moves to a different
        # plan, we auto-dismiss; the gate will push a fresh dialog.
        self._plan = queue.running
        self._timer: Optional[Timer] = None
        self._dismissing = False

    # --- safe dismissal --------------------------------------------------

    def safe_dismiss(self) -> None:
        """Pop this modal at most once, only while it's still on the stack.

        Three callers race to close this dialog: the redraw timer's
        plan-moved-on auto-dismiss (:meth:`_refresh`), the Esc path in
        :meth:`action_cancel_or_dismiss`, and minimize
        (:meth:`action_minimize`). Textual's :meth:`dismiss` pops the
        screen stack unconditionally, so a second call pops the base
        ``_default`` screen and raises ``ScreenStackError``. Gate on an
        idempotency flag *and* actual stack membership so whichever
        caller wins, the rest are no-ops.

        Same shape as :meth:`ScanScreen.safe_dismiss` (the 2026-06-05
        launch-crash fix); ``Ctrl+P`` resume is unaffected because the
        gate always constructs a fresh instance.
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
            yield Label(self._header_text(), classes="header", id="progress-header")
            yield Static(self._body_text(), classes="body", id="progress-body")
            yield Label("Esc = Cancel    m = Minimize", classes="hint")

    def on_mount(self) -> None:
        """Start the polling timer at PROGRESS_REDRAW_HZ Hz."""
        interval = 1.0 / PROGRESS_REDRAW_HZ
        self._timer = self.set_interval(interval, self._refresh)

    def on_unmount(self) -> None:
        """Stop the timer so it doesn't fire after dismiss."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # --- key handlers -----------------------------------------------------

    def action_cancel_or_dismiss(self) -> None:
        """First Esc asks for cancellation; later Esc dismisses.

        While the plan is still running and cancel hasn't been requested
        yet, the first Esc fires :meth:`OperationQueue.request_cancel`
        and the header flips to ``Cancelling...``. The dialog stays open
        until the queue actually clears (the chunk loop and the in-flight
        item need a moment to wind down). A second Esc dismisses
        immediately so a stuck cancellation doesn't trap the user.
        """
        if self._queue.running is not None and not self._queue.cancel_requested:
            self._queue.request_cancel()
            self._refresh_header()
            return
        self.safe_dismiss()

    def action_minimize(self) -> None:
        """Dismiss the dialog without cancelling the queue.

        The queue keeps running in the background; ``Ctrl+P`` on the app
        re-pushes a fresh ``ProgressScreen`` bound to the same queue,
        which polls the live state on first paint. No state is duplicated
        - the queue is the single source of truth, this screen is a view.

        After dismiss, schedule a status-line refresh so the
        ``[Ctrl+P]`` discovery hint appears immediately. The check in
        ``StatusLine._build_text`` only fires the hint when no
        ``ProgressScreen`` is on the stack, so the refresh must run
        *after* the dismiss is processed - hence ``call_after_refresh``.
        """
        self.safe_dismiss()
        try:
            self.app.call_after_refresh(self.app._refresh_status)
        except Exception:  # noqa: BLE001 - defensive; not all hosts are WTreeApp
            pass

    # --- repaint ---------------------------------------------------------

    def _refresh(self) -> None:
        """Timer callback: re-derive readouts from queue state."""
        # If the queue has moved on (our plan is done, or a different
        # one started), auto-dismiss. The gate handles the next plan.
        if self._queue.running is not self._plan:
            self.safe_dismiss()
            return
        try:
            body = self.query_one("#progress-body", Static)
        except Exception:  # noqa: BLE001 - torn down between timer and call
            return
        body.update(self._body_text())
        # Header may flip to Cancelling... once request_cancel fires.
        self._refresh_header()

    def _refresh_header(self) -> None:
        try:
            header = self.query_one("#progress-header", Label)
        except Exception:  # noqa: BLE001
            return
        header.update(self._header_text())

    # --- text builders ---------------------------------------------------

    def _header_text(self) -> str:
        if self._plan is None:
            return "Progress"
        verb = self._plan.kind.value.capitalize()
        if self._queue.cancel_requested:
            return f"{verb}  -  Cancelling..."
        return verb

    def _body_text(self) -> Text:
        """The six-field readout + percent bar.

        All formulas read from the queue's live properties. The zero
        guard (elapsed == 0 OR bytes_done == 0) renders Rate and Drag
        as em-dashes; bar and Percent show 0 in that case rather than
        spuriously dividing.
        """
        bytes_prog = self._queue.bytes_progress
        items_prog = self._queue.running_progress
        elapsed = self._queue.elapsed_seconds

        bytes_done = bytes_prog[0] if bytes_prog else 0
        bytes_total = bytes_prog[1] if bytes_prog else 0
        items_done = items_prog[0] if items_prog else 0
        items_total = items_prog[1] if items_prog else 0

        # Percent: byte-weighted. Falls back to 0 when bytes_total is 0
        # (e.g. a plan of empty files - the items count carries the
        # signal). No divide-by-zero risk.
        if bytes_total > 0:
            fraction = min(1.0, bytes_done / bytes_total)
        else:
            fraction = 0.0
        percent = int(fraction * 100)

        # Zero guard for time-derived readouts.
        zero_guard = (elapsed <= 0.0) or (bytes_done <= 0)

        if zero_guard:
            rate_text = _EM_DASH
            drag_text = _EM_DASH
        else:
            rate_text = _format_rate(bytes_done / elapsed)
            # Drag: (1 - bytes_done/bytes_total) * elapsed, normalised
            # to fraction-seconds. Matthew's quirky readout - a
            # "patience tax" vibe number, peaks mid-op, returns to 0
            # at completion. NOT a buffer / queue depth.
            remaining_frac = 1.0 - fraction
            drag_text = f"{remaining_frac * elapsed:.2f}"

        elapsed_text = _format_elapsed(elapsed)
        data_text = _format_bytes(bytes_done)
        files_text = f"{items_done} / {items_total}"

        bar = _render_bar(fraction)

        t = Text()
        # Source line: show the current item's source path so the user
        # knows what's actively transferring. Best-effort - the queue
        # doesn't track a "current item" pointer directly, so we infer
        # from the plan + items_done. Empty when the plan is empty.
        current_item = _current_item(self._plan, items_done)
        if current_item is not None:
            t.append(current_item, style="cyan")
            t.append("\n\n")
        else:
            t.append("\n")

        t.append(bar)
        t.append(f"  {percent}%\n\n")

        _row(t, "Elapsed", elapsed_text, "Data", data_text)
        _row(t, "Rate", rate_text, "Drag", drag_text)
        t.append(f"  Files     {files_text}\n")

        return t


# ---------------------------------------------------------------------------
# Pure helpers (tested directly without instantiating the screen)
# ---------------------------------------------------------------------------


def _render_bar(fraction: float) -> str:
    """Draw an ASCII progress bar of width :data:`_BAR_WIDTH`.

    ``fraction`` is clamped to [0, 1]. Whole cells are filled with
    ``_BAR_FILLED``; one optional half-cell marks the partial frontier;
    the remainder is ``_BAR_EMPTY``. Matches the design.md layout's
    visual look.
    """
    f = max(0.0, min(1.0, fraction))
    total_halves = int(round(f * _BAR_WIDTH * 2))
    full = total_halves // 2
    half = total_halves % 2
    empty = _BAR_WIDTH - full - half
    return (
        "[" + _BAR_FILLED * full + _BAR_HALF * half + _BAR_EMPTY * empty + "]"
    )


def _format_elapsed(seconds: float) -> str:
    """``MM:SS`` under an hour, ``H:MM:SS`` past one hour."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _format_bytes(n: int) -> str:
    """Compact size string (matches the ops convention)."""
    if n < 1024:
        return f"{n} B"
    size: float = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        size = size / 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} PB"


def _format_rate(bytes_per_second: float) -> str:
    """Compact rate string. ``KB/s`` / ``MB/s`` / ``GB/s`` etc."""
    if bytes_per_second < 1024:
        return f"{bytes_per_second:.0f} B/s"
    size: float = float(bytes_per_second)
    for unit in ("KB/s", "MB/s", "GB/s", "TB/s"):
        size = size / 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} PB/s"


def _row(t: Text, label_l: str, value_l: str, label_r: str, value_r: str) -> None:
    """Append a 2-column row to the body. Fixed columns keep the
    field labels lined up regardless of value length, matching the
    design.md mockup.
    """
    t.append(f"  {label_l:<8}{value_l:<14}", style=None)
    t.append(f"{label_r:<8}{value_r}\n", style=None)


def _current_item(plan, items_done: int) -> Optional[str]:
    """Best-effort path of the item currently being processed.

    ``items_done`` is the count of items *finished*; the in-flight
    item is at index ``items_done`` in the plan's item list. Returns
    None if the plan is empty or already past its last item (the
    moment between final-item completion and dialog dismiss).
    """
    if plan is None:
        return None
    items = plan.items
    if not items:
        return None
    if items_done >= len(items):
        return None
    item = items[items_done]
    # Prefer dst_path for copies / moves (the user thinks of "where
    # it's going to"); src_path for deletes (no dst).
    if item.dst_path and item.dst_path != item.src_path:
        return f"-> {item.dst_path}"
    return item.src_path
