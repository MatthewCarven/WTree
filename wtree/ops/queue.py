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
from collections.abc import Callable, Mapping
from typing import Optional

from wtree.ops.base import ItemResult, OperationResult, Plan
from wtree.ops.execute import apply_plan
from wtree.sources.base import EntrySource

_log = logging.getLogger(__name__)


PlanStartCb = Callable[[Plan, "OperationQueue"], None]
PlanCompleteCb = Callable[[OperationResult, "OperationQueue"], None]
# Item progress: (item_result, queue). The queue carries running_progress.
ItemProgressCb = Callable[[ItemResult, "OperationQueue"], None]


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
    ) -> None:
        self._registry = registry
        self._on_plan_start = on_plan_start
        self._on_plan_complete = on_plan_complete
        self._on_item_progress = on_item_progress
        self._pending: asyncio.Queue[Plan] = asyncio.Queue()
        self._running: Optional[Plan] = None
        # (items_done, items_total) while a plan is running, else None.
        self._running_progress: Optional[tuple[int, int]] = None
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
            try:
                if self._on_plan_start is not None:
                    try:
                        self._on_plan_start(plan, self)
                    except Exception:  # noqa: BLE001 - UI cb isolation
                        _log.exception("on_plan_start callback raised")

                def _progress(item_result: ItemResult) -> None:
                    # Closure runs inside apply_plan, on the same task.
                    # Increment the counter, then fan out to the UI.
                    done = (self._running_progress or (0, total))[0] + 1
                    self._running_progress = (done, total)
                    if self._on_item_progress is not None:
                        try:
                            self._on_item_progress(item_result, self)
                        except Exception:  # noqa: BLE001 - UI cb isolation
                            _log.exception(
                                "on_item_progress callback raised"
                            )

                try:
                    result = await apply_plan(
                        plan, self._registry, progress=_progress
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
                self._pending.task_done()
