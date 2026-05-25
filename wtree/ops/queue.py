"""Serialised operation queue.

Matthew's design call 2026-05-21: strictly FIFO, one plan at a time, no
device-busy detection. "the 2nd copy waits until the first is finished
... my take is just to wait for the 1st to complete then do the next and
so on for simplicity sake."

Implementation: one background ``asyncio.Task`` consumes
``asyncio.Queue[Plan]``. ``enqueue()`` is sync (a put into the queue);
the worker awaits :func:`~wtree.ops.execute.apply_plan` per plan and
moves on. Catastrophic exceptions in ``apply_plan`` itself are logged
and the worker continues - one bad plan never freezes the queue.

UI integration is by **callbacks**, not events. ``on_plan_start`` fires
just before ``apply_plan``; ``on_plan_complete`` fires after, with the
:class:`OperationResult` and with ``running`` already cleared.
``on_item_progress`` fires once per item inside ``apply_plan`` so a
status line / progress dialog can render fine-grained progress. All
callbacks are sync - the worker calls them inline. A future progress
dialog can subscribe via these callbacks without the queue having to
know what a Textual widget is.

Why an in-class worker rather than ``app.run_worker``: the queue
outlives any individual screen / modal / async task in the UI. It's
owned by the app and lives for the app's lifetime.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from typing import Optional

from wtree.ops.base import ItemResult, OperationResult, Plan, PlanItem
from wtree.ops.execute import apply_plan
from wtree.sources.base import EntrySource

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunable constants (design.md 2026-05-25)
# ---------------------------------------------------------------------------

# Bytes per read/write chunk during copy. Tuning notes:
#   - 4 KB    : physical sector on most drives
#   - 64 KB   : Python's shutil default
#   - 256 KB  : SSD sweet spot, sensible default here
#   - 1 MB+   : NTFS large-volume clusters, SMB/NFS shares
#   - 4 MB+   : 10GbE -> fast NVMe sequential bulk transfers
#
# The per-chunk constant is tunable, but copy time (and the progress
# dialog's update rate) is only as granular as how often the per-chunk
# callback is called - bigger chunks mean faster bulk throughput on
# fast hardware but coarser progress steps.
COPY_CHUNK_SIZE = 256 * 1024

# Maximum progress repaints per second (coalesces per-chunk callbacks).
# Past ~30 Hz this is diminishing returns: the human eye plateaus on
# slow-moving bars and terminal repaint cost starts stealing CPU from
# the copy worker itself.
PROGRESS_REDRAW_HZ = 10


# ---------------------------------------------------------------------------
# Threshold gate (delayed-show modal)
# ---------------------------------------------------------------------------

# Show the progress modal when any of these trip. Below all three, the
# StatusLine `Copy N/M` carries on alone - flashing a modal for three
# text files is worse than no feedback.
PROGRESS_MODAL_BYTES = 4 * 1024 * 1024  # 4 MiB
PROGRESS_MODAL_ITEMS = 50
PROGRESS_MODAL_DELAY_SECONDS = 0.4


PlanStartCb = Callable[[Plan, "OperationQueue"], None]
PlanCompleteCb = Callable[[OperationResult, "OperationQueue"], None]
# Item progress: (item_result, queue). The queue carries running_progress.
ItemProgressCb = Callable[[ItemResult, "OperationQueue"], None]
# Byte progress: (item, bytes_done_in_item, item_size, queue). Fires
# from inside the chunked copy loop, which runs in a worker thread via
# asyncio.to_thread - subscribers should NOT touch event-loop-affine
# state directly. Built-in ProgressScreen sidesteps this by polling
# queue properties on the event loop instead of subscribing here.
BytesProgressCb = Callable[[PlanItem, int, int, "OperationQueue"], None]


class OperationQueue:
    """A serial FIFO of :class:`Plan` to execute.

    Lifecycle:

    1. Construct with a source registry (and optional callbacks).
    2. Call :meth:`start` once an event loop is running (``on_mount`` in
       a Textual app).
    3. Call :meth:`enqueue` any number of times to add plans.
    4. Call :meth:`stop` during shutdown to cancel the worker.

    The queue is thread-safe only insofar as ``asyncio.Queue`` is - that
    is, single-event-loop access only. Textual apps run a single loop,
    so this is fine.
    """

    def __init__(
        self,
        registry: Mapping[str, EntrySource],
        *,
        on_plan_start: PlanStartCb | None = None,
        on_plan_complete: PlanCompleteCb | None = None,
        on_item_progress: ItemProgressCb | None = None,
        on_bytes_progress: BytesProgressCb | None = None,
    ) -> None:
        self._registry = registry
        self._on_plan_start = on_plan_start
        self._on_plan_complete = on_plan_complete
        self._on_item_progress = on_item_progress
        self._on_bytes_progress = on_bytes_progress
        self._pending: asyncio.Queue[Plan] = asyncio.Queue()
        self._running: Optional[Plan] = None
        # (items_done, items_total) while a plan is running, else None.
        self._running_progress: Optional[tuple[int, int]] = None
        # Byte-level progress state for the running plan. All writes are
        # GIL-atomic single-attribute assignments; safe to read from the
        # event loop while the chunked copy runs in a worker thread.
        # See design.md -> Progress dialog -> Concurrency assumptions.
        self._bytes_total: int = 0
        self._bytes_done_completed: int = 0  # sum of finished items' sizes
        self._bytes_done_current: int = 0  # in-flight item progress
        self._started_at: float | None = None  # monotonic seconds at start
        self._cancel_requested: bool = False
        self._completed: list[OperationResult] = []
        self._worker: Optional[asyncio.Task[None]] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background worker task. Idempotent."""
        if self._worker is not None and not self._worker.done():
            return
        self._worker = asyncio.create_task(
            self._run(), name="wtree-op-queue"
        )

    async def stop(self) -> None:
        """Cancel the worker and await its exit. Idempotent."""
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, plan: Plan) -> None:
        """Append ``plan`` to the queue. Returns immediately."""
        self._pending.put_nowait(plan)

    @property
    def depth(self) -> int:
        """How many plans are in flight or waiting.

        ``running + pending``. Goes to 0 when the queue is fully drained.
        """
        return self._pending.qsize() + (1 if self._running is not None else 0)

    @property
    def running(self) -> Plan | None:
        """The plan currently being applied, or ``None`` when idle."""
        return self._running

    @property
    def running_progress(self) -> tuple[int, int] | None:
        """``(items_done, items_total)`` for the running plan, or ``None``
        when no plan is running. Reads atomically - the worker only
        mutates this from the same event loop.
        """
        return self._running_progress

    @property
    def bytes_progress(self) -> tuple[int, int] | None:
        """``(bytes_done, bytes_total)`` for the running plan, or ``None``
        when no plan is running.

        ``bytes_done`` sums the sizes of completed items plus the
        in-flight item's chunk-loop progress. Single-int reads are
        GIL-atomic; the worker thread writes ``_bytes_done_current`` per
        chunk and the event loop reads here for repaint. See design.md
        -> Progress dialog -> Concurrency assumptions.
        """
        if self._running is None:
            return None
        return (
            self._bytes_done_completed + self._bytes_done_current,
            self._bytes_total,
        )

    @property
    def elapsed_seconds(self) -> float:
        """Wall-clock seconds since the current plan started, or 0.0 idle.

        Monotonic clock - immune to wall-clock jumps mid-op. Returns
        exactly 0.0 when no plan is running, which is the value the
        progress dialog's zero-guard checks against to render an em-dash.
        """
        if self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at

    @property
    def cancel_requested(self) -> bool:
        """True once :meth:`request_cancel` has been called for the
        running plan. Cleared at the start of each new plan.
        """
        return self._cancel_requested

    def request_cancel(self) -> None:
        """Ask the running plan to stop at the next chunk boundary.

        The chunked copy loop polls this flag once per chunk and bails
        cleanly, deleting any partial destination file. Items already
        completed stay done; the current item is rolled back; remaining
        items are skipped (FAILED with "cancelled" message).

        No-op if no plan is running.
        """
        if self._running is None:
            return
        self._cancel_requested = True

    @property
    def completed(self) -> list[OperationResult]:
        """Append-only log of finished plans, in completion order."""
        return self._completed

    async def wait_until_idle(self) -> None:
        """Block until every plan put has been matched by a task_done.

        Useful for tests that need a deterministic "all done" point.
        """
        await self._pending.join()

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Loop: pull a plan, apply it, log, repeat. Forever."""
        while True:
            plan = await self._pending.get()
            self._running = plan
            total = len(plan.items)
            self._running_progress = (0, total)
            # Reset byte-progress state for the new plan.
            self._bytes_total = plan.total_bytes
            self._bytes_done_completed = 0
            self._bytes_done_current = 0
            self._cancel_requested = False
            self._started_at = time.monotonic()
            try:
                if self._on_plan_start is not None:
                    try:
                        self._on_plan_start(plan, self)
                    except Exception:  # noqa: BLE001 - UI cb isolation
                        _log.exception("on_plan_start callback raised")

                def _progress(item_result: ItemResult) -> None:
                    # Closure runs inside apply_plan, on the same task.
                    # Increment the item counter, then roll the in-flight
                    # byte counter forward and fan out to the UI.
                    from wtree.ops.base import ItemStatus

                    done = (self._running_progress or (0, total))[0] + 1
                    self._running_progress = (done, total)
                    # Only credit completed bytes on SUCCESS. On FAILED
                    # (including user-cancelled mid-copy) the partial
                    # bytes are discarded with the partial dest file -
                    # the cumulative readout must not jump up by the
                    # full item size for a file that didn't land.
                    if item_result.status is ItemStatus.SUCCESS:
                        self._bytes_done_completed += item_result.item.size
                    self._bytes_done_current = 0
                    if self._on_item_progress is not None:
                        try:
                            self._on_item_progress(item_result, self)
                        except Exception:  # noqa: BLE001 - UI cb isolation
                            _log.exception(
                                "on_item_progress callback raised"
                            )

                def _bytes_progress(
                    item: PlanItem, done_in_item: int, item_size: int
                ) -> bool:
                    """Called from the chunked copy loop, possibly in a
                    worker thread (via asyncio.to_thread).

                    Returns True to continue, False to cancel. Updates
                    the in-flight byte counter and fans out to any
                    external on_bytes_progress subscriber.
                    """
                    self._bytes_done_current = done_in_item
                    if self._on_bytes_progress is not None:
                        try:
                            self._on_bytes_progress(
                                item, done_in_item, item_size, self
                            )
                        except Exception:  # noqa: BLE001 - UI cb isolation
                            _log.exception(
                                "on_bytes_progress callback raised"
                            )
                    return not self._cancel_requested

                try:
                    result = await apply_plan(
                        plan,
                        self._registry,
                        progress=_progress,
                        bytes_progress=_bytes_progress,
                    )
                except Exception as exc:  # noqa: BLE001 - keep queue alive
                    _log.exception(
                        "apply_plan raised unexpectedly: %s", exc
                    )
                    # Synthesise a "everything failed" result so the
                    # completed log stays consistent and downstream code
                    # doesn't see a phantom plan disappear.
                    from wtree.ops.base import ItemStatus
                    result = OperationResult(
                        plan=plan,
                        items=[
                            ItemResult(
                                item=i,
                                status=ItemStatus.FAILED,
                                message=(
                                    f"queue-level: {type(exc).__name__}: {exc}"
                                ),
                            )
                            for i in plan.items
                        ],
                    )
                self._completed.append(result)
                # Clear running BEFORE the callback so listeners see the
                # post-plan state - queue depth has dropped by one and
                # ``running`` is None. Matches the natural reading of
                # "on plan complete": when the cb fires, the plan is no
                # longer running.
                self._running = None
                self._running_progress = None
                self._started_at = None
                self._bytes_total = 0
                self._bytes_done_completed = 0
                self._bytes_done_current = 0
                self._cancel_requested = False
                if self._on_plan_complete is not None:
                    try:
                        self._on_plan_complete(result, self)
                    except Exception:  # noqa: BLE001 - UI cb isolation
                        _log.exception("on_plan_complete callback raised")
            finally:
                # Belt-and-braces: cancellation between plan-get and the
                # clear above leaves ``_running`` pointing at a phantom.
                self._running = None
                self._running_progress = None
                self._started_at = None
                self._bytes_total = 0
                self._bytes_done_completed = 0
                self._bytes_done_current = 0
                self._cancel_requested = False
                self._pending.task_done()
