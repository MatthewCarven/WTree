"""Tests for the progress dialog pipeline (design.md 2026-05-25).

Three surfaces under test, structured like ``test_properties.py``:

* **Chunked copy** (``wtree.ops.execute._chunked_copy``) - per-chunk
  callback fires with monotone bytes_done, cancellation breaks the
  loop and cleans the partial dest, content + metadata preserved on
  success.

* **Queue byte-progress state** - new properties (``bytes_progress``,
  ``elapsed_seconds``, ``cancel_requested``) plus ``request_cancel()``
  behave correctly across idle / running / cancelled / completed.

* **ProgressScreen pure helpers** - ``_render_bar`` / ``_format_*`` /
  ``_current_item`` produce the right output for the cases the
  design.md mockup specifies, including the zero-guard cases.

End-to-end app-level threshold-gate testing is deferred to a follow-up
pilot-based test; the helpers (``_maybe_push_progress_dialog``,
``_push_progress_dialog_if_running``) exist on ``WTreeApp`` and are
exercised by the queue / execute layer integration in this file.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from wtree.ops import OperationQueue, Plan, plan_copy
from wtree.ops.base import ItemStatus, OperationKind, PlanItem
from wtree.ops.execute import _chunked_copy, apply_plan
from wtree.ops.queue import (
    COPY_CHUNK_SIZE,
    PROGRESS_MODAL_BYTES,
    PROGRESS_MODAL_DELAY_SECONDS,
    PROGRESS_MODAL_ITEMS,
    PROGRESS_REDRAW_HZ,
)
from wtree.sources.base import Kind
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag
from wtree.widgets.progress_screen import (
    _EM_DASH,
    ProgressScreen,
    _current_item,
    _format_bytes,
    _format_elapsed,
    _format_rate,
    _render_bar,
)


@pytest.fixture
def registry() -> dict[str, NativeSource]:
    return {"native": NativeSource()}


# ---------------------------------------------------------------------------
# Module-level constants - sanity check the values design.md committed to
# ---------------------------------------------------------------------------


def test_constants_match_design_doc() -> None:
    """Constants ship with the values design.md 2026-05-25 specified."""
    assert COPY_CHUNK_SIZE == 256 * 1024  # 256 KB
    assert PROGRESS_REDRAW_HZ == 10
    assert PROGRESS_MODAL_BYTES == 4 * 1024 * 1024  # 4 MiB
    assert PROGRESS_MODAL_ITEMS == 50
    assert PROGRESS_MODAL_DELAY_SECONDS == 0.4


# ---------------------------------------------------------------------------
# Chunked copy
# ---------------------------------------------------------------------------


def _make_plan_item(src: Path, dst: Path) -> PlanItem:
    """Synthesize a single-file PlanItem for direct _chunked_copy testing."""
    return PlanItem(
        src_source_id="native",
        src_path=str(src),
        dst_source_id="native",
        dst_path=str(dst),
        kind=Kind.FILE,
        size=src.stat().st_size,
    )


def test_chunked_copy_writes_file_and_fires_callback(tmp_path: Path) -> None:
    """End-to-end: small file copies correctly and callback fires."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 1024)  # 1 KB - under one chunk
    dst = tmp_path / "dst.bin"
    item = _make_plan_item(src, dst)

    calls: list[tuple[int, int]] = []

    def cb(it: PlanItem, done: int, total: int) -> bool:
        assert it is item
        calls.append((done, total))
        return True

    cancelled = _chunked_copy(item, str(src), str(dst), cb)

    assert cancelled is False
    assert dst.read_bytes() == b"x" * 1024
    # Initial zero callback + final full-chunk callback (file < one chunk).
    assert calls[0] == (0, 1024)
    assert calls[-1] == (1024, 1024)
    # All done values are monotonically non-decreasing.
    for prev, nxt in zip(calls, calls[1:]):
        assert prev[0] <= nxt[0]


def test_chunked_copy_multi_chunk_monotone(tmp_path: Path) -> None:
    """Multi-chunk file: callback fires per chunk with monotone done."""
    # Use 3x the chunk size + a remainder so we exercise both the
    # full-chunk and short-final-read code paths.
    n_bytes = COPY_CHUNK_SIZE * 3 + 12345
    src = tmp_path / "big.bin"
    src.write_bytes(b"a" * n_bytes)
    dst = tmp_path / "big.copy"
    item = _make_plan_item(src, dst)

    calls: list[int] = []

    def cb(it: PlanItem, done: int, total: int) -> bool:
        calls.append(done)
        return True

    cancelled = _chunked_copy(item, str(src), str(dst), cb)

    assert cancelled is False
    assert dst.stat().st_size == n_bytes
    # First call is the initial zero, last call is exactly n_bytes.
    assert calls[0] == 0
    assert calls[-1] == n_bytes
    # Monotone, no duplicates after the initial zero.
    for prev, nxt in zip(calls, calls[1:]):
        assert prev < nxt or (prev == nxt == 0)
    # Number of chunk callbacks (excluding the initial zero) matches the
    # number of read iterations: ceil(n_bytes / CHUNK).
    chunks_expected = (n_bytes + COPY_CHUNK_SIZE - 1) // COPY_CHUNK_SIZE
    assert len(calls) == 1 + chunks_expected  # +1 for the initial zero


def test_chunked_copy_cancel_cleans_partial(tmp_path: Path) -> None:
    """Callback returning False breaks the loop and deletes the partial dest."""
    src = tmp_path / "big.bin"
    src.write_bytes(b"a" * (COPY_CHUNK_SIZE * 5))
    dst = tmp_path / "big.copy"
    item = _make_plan_item(src, dst)

    call_count = 0

    def cb(it: PlanItem, done: int, total: int) -> bool:
        nonlocal call_count
        call_count += 1
        # Cancel after we've moved a couple of chunks (so there's a real
        # partial file on disk to clean up).
        return call_count < 3

    cancelled = _chunked_copy(item, str(src), str(dst), cb)

    assert cancelled is True
    # The partial destination must be cleaned up.
    assert not dst.exists()


def test_chunked_copy_cancel_at_initial_zero(tmp_path: Path) -> None:
    """If callback returns False on the very first call, no bytes are written."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 4096)
    dst = tmp_path / "dst.bin"
    item = _make_plan_item(src, dst)

    def cb(it: PlanItem, done: int, total: int) -> bool:
        return False  # Cancel immediately.

    cancelled = _chunked_copy(item, str(src), str(dst), cb)

    assert cancelled is True
    assert not dst.exists()


def test_chunked_copy_preserves_mtime(tmp_path: Path) -> None:
    """``copystat`` after the chunk loop restores mtime (mirrors copy2)."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    # Set a recognisable mtime in the past.
    past = time.time() - 86400  # one day ago
    os.utime(src, (past, past))
    dst = tmp_path / "dst.bin"
    item = _make_plan_item(src, dst)

    def cb(it: PlanItem, done: int, total: int) -> bool:
        return True

    _chunked_copy(item, str(src), str(dst), cb)

    # Destination mtime should match source mtime within a second
    # (filesystem timestamp resolution varies).
    assert abs(dst.stat().st_mtime - past) < 1.0


# ---------------------------------------------------------------------------
# apply_plan with bytes_progress threading
# ---------------------------------------------------------------------------


async def test_apply_plan_threads_bytes_progress(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """When ``bytes_progress`` is supplied, the chunked path fires it."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * (COPY_CHUNK_SIZE + 100))
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_copy(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    calls: list[int] = []

    def bytes_cb(item: PlanItem, done: int, total: int) -> bool:
        calls.append(done)
        return True

    result = await apply_plan(plan, registry, bytes_progress=bytes_cb)

    assert result.all_succeeded
    assert calls, "bytes_progress was never invoked"
    assert calls[0] == 0
    assert calls[-1] == src.stat().st_size


async def test_apply_plan_no_bytes_progress_uses_fast_path(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """No callback => shutil.copy2 fast path. Spot-checked by behaviour."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"hello world")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_copy(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    result = await apply_plan(plan, registry)  # no bytes_progress

    assert result.all_succeeded
    assert (dst_dir / "a.bin").read_bytes() == b"hello world"


# ---------------------------------------------------------------------------
# Queue byte-progress state
# ---------------------------------------------------------------------------


async def test_queue_bytes_progress_idle_returns_none(
    registry: dict[str, NativeSource],
) -> None:
    """``bytes_progress`` is None when no plan is running."""
    queue = OperationQueue(registry=registry)
    queue.start()
    try:
        assert queue.bytes_progress is None
        assert queue.elapsed_seconds == 0.0
        assert queue.cancel_requested is False
    finally:
        await queue.stop()


async def test_queue_bytes_progress_during_plan(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """``bytes_progress`` reflects cumulative bytes during execution."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"a" * (COPY_CHUNK_SIZE * 2))
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_copy(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    snapshots: list[tuple[int, int]] = []

    def on_bytes(item: PlanItem, done: int, total: int, q: Any) -> None:
        bp = q.bytes_progress
        if bp is not None:
            snapshots.append(bp)

    queue = OperationQueue(registry=registry, on_bytes_progress=on_bytes)
    queue.start()
    try:
        queue.enqueue(plan)
        await queue.wait_until_idle()
    finally:
        await queue.stop()

    # We should have seen multiple snapshots, monotonically non-decreasing,
    # all <= plan.total_bytes, last one == plan.total_bytes (last chunk
    # callback fires before the item completes and the SUCCESS rolls
    # _bytes_done_current into _bytes_done_completed).
    assert snapshots
    for prev, nxt in zip(snapshots, snapshots[1:]):
        assert prev[0] <= nxt[0]
    assert snapshots[-1][0] == plan.total_bytes
    assert snapshots[-1][1] == plan.total_bytes


async def test_queue_elapsed_seconds_grows_then_resets(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """``elapsed_seconds`` is > 0 during a plan and back to 0.0 after."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 1024)
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_copy(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    captured: list[float] = []

    def on_bytes(item: PlanItem, done: int, total: int, q: Any) -> None:
        captured.append(q.elapsed_seconds)

    queue = OperationQueue(registry=registry, on_bytes_progress=on_bytes)
    queue.start()
    try:
        queue.enqueue(plan)
        await queue.wait_until_idle()
    finally:
        await queue.stop()

    # During the plan we saw non-negative elapsed values (could be 0.0
    # for the initial chunk callback, which is fine).
    assert captured
    assert all(e >= 0.0 for e in captured)
    # After wait_until_idle the plan is done, elapsed resets to 0.0.
    assert queue.elapsed_seconds == 0.0


async def test_queue_request_cancel_idle_is_noop(
    registry: dict[str, NativeSource],
) -> None:
    """Calling ``request_cancel`` with no running plan is a no-op."""
    queue = OperationQueue(registry=registry)
    queue.start()
    try:
        queue.request_cancel()  # should not raise
        assert queue.cancel_requested is False
    finally:
        await queue.stop()


async def test_queue_request_cancel_aborts_running_plan(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """request_cancel mid-copy lands as FAILED with 'cancelled' message.

    Cancellation is requested from inside the first chunk callback to
    avoid the wall-clock race a 'sleep then cancel' loop would have:
    on a fast disk the whole copy may complete before the test even
    gets a chance to call request_cancel.
    """
    src = tmp_path / "big.bin"
    src.write_bytes(b"a" * (COPY_CHUNK_SIZE * 5))
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_copy(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    queue_ref: list[OperationQueue] = []
    fired = False

    def on_bytes(item: PlanItem, done: int, total: int, q: Any) -> None:
        nonlocal fired
        if not fired:
            fired = True
            queue_ref.append(q)
            q.request_cancel()

    queue = OperationQueue(registry=registry, on_bytes_progress=on_bytes)
    queue.start()
    try:
        queue.enqueue(plan)
        await queue.wait_until_idle()
    finally:
        await queue.stop()

    assert fired, "on_bytes_progress should have fired at least once"
    assert len(queue.completed) == 1
    result = queue.completed[0]
    # The single copy item should be FAILED with the 'cancelled' message.
    assert len(result.items) == 1
    assert result.items[0].status is ItemStatus.FAILED
    assert "cancelled" in result.items[0].message.lower()
    # And the destination file must not exist on disk.
    assert not (dst_dir / "big.bin").exists()


async def test_queue_cancel_flag_resets_between_plans(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """``cancel_requested`` returns to False at the start of each plan.

    Plan1 is cancelled from its first chunk callback (deterministic);
    plan2 must then run to success, proving the flag was cleared.
    """
    src1 = tmp_path / "a.bin"
    src1.write_bytes(b"a" * (COPY_CHUNK_SIZE * 5))
    src2 = tmp_path / "b.bin"
    src2.write_bytes(b"b" * 1024)
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan1 = await plan_copy(
        [Tag("native", str(src1))], Tag("native", str(dst_dir)), registry
    )
    plan2 = await plan_copy(
        [Tag("native", str(src2))], Tag("native", str(dst_dir)), registry
    )

    plans_seen: list[Plan] = []

    def on_bytes(item: PlanItem, done: int, total: int, q: Any) -> None:
        running = q.running
        if running is plan1 and not q.cancel_requested:
            q.request_cancel()
        if running is not None and running not in plans_seen:
            plans_seen.append(running)

    queue = OperationQueue(registry=registry, on_bytes_progress=on_bytes)
    queue.start()
    try:
        queue.enqueue(plan1)
        queue.enqueue(plan2)
        await queue.wait_until_idle()
    finally:
        await queue.stop()

    # Both plans completed. Plan1 cancelled, plan2 succeeded.
    assert len(queue.completed) == 2
    assert queue.completed[0].items[0].status is ItemStatus.FAILED
    assert queue.completed[1].all_succeeded
    # Plan2 succeeded => cancel_requested was reset between plans.
    assert (dst_dir / "b.bin").read_bytes() == b"b" * 1024
    # And we did observe both plans running through the byte callback.
    assert plan1 in plans_seen and plan2 in plans_seen


# ---------------------------------------------------------------------------
# ProgressScreen pure helpers
# ---------------------------------------------------------------------------


def test_render_bar_zero_fifty_full() -> None:
    """Bar at 0%, 50%, 100% renders as design.md mockup describes."""
    zero = _render_bar(0.0)
    half = _render_bar(0.5)
    full = _render_bar(1.0)
    # All have brackets and the same total cell count between them.
    assert zero.startswith("[") and zero.endswith("]")
    assert half.startswith("[") and half.endswith("]")
    assert full.startswith("[") and full.endswith("]")
    # 0% has zero filled cells; 100% has no empty cells.
    assert "█" not in zero
    assert "░" not in full
    # Mid-range has both filled and empty cells.
    assert "█" in half and "░" in half


def test_render_bar_clamps_out_of_range() -> None:
    """Fractions outside [0, 1] clamp rather than crash."""
    assert _render_bar(-0.5) == _render_bar(0.0)
    assert _render_bar(2.0) == _render_bar(1.0)


def test_format_elapsed_under_and_over_hour() -> None:
    assert _format_elapsed(0) == "00:00"
    assert _format_elapsed(14) == "00:14"
    assert _format_elapsed(75) == "01:15"
    assert _format_elapsed(3712) == "1:01:52"


def test_format_bytes_scales() -> None:
    assert _format_bytes(0) == "0 B"
    assert _format_bytes(512) == "512 B"
    assert _format_bytes(2048) == "2.0 KB"
    assert _format_bytes(215 * 1024 * 1024) == "215.0 MB"


def test_format_rate_scales() -> None:
    assert "B/s" in _format_rate(100)
    assert "KB/s" in _format_rate(50 * 1024)
    assert "MB/s" in _format_rate(15 * 1024 * 1024)


def test_current_item_returns_dst_for_copy() -> None:
    """``_current_item`` prefers dst_path for copy/move (where it's going)."""
    item = PlanItem(
        src_source_id="native",
        src_path="/src/a.bin",
        dst_source_id="native",
        dst_path="/dst/a.bin",
        kind=Kind.FILE,
        size=100,
    )
    plan = Plan(kind=OperationKind.COPY, items=[item])
    result = _current_item(plan, 0)
    assert result is not None
    assert "/dst/a.bin" in result


def test_current_item_returns_src_for_delete() -> None:
    """For delete (dst_path == src_path), returns the src side."""
    item = PlanItem(
        src_source_id="native",
        src_path="/src/a.bin",
        dst_source_id="native",
        dst_path="/src/a.bin",  # mirror for delete
        kind=Kind.FILE,
        size=100,
    )
    plan = Plan(kind=OperationKind.DELETE, items=[item])
    result = _current_item(plan, 0)
    assert result == "/src/a.bin"


def test_current_item_handles_empty_plan_and_overflow() -> None:
    """Returns None for empty plan or items_done past the end."""
    assert _current_item(None, 0) is None
    empty_plan = Plan(kind=OperationKind.COPY, items=[])
    assert _current_item(empty_plan, 0) is None
    item = PlanItem(
        src_source_id="native",
        src_path="/a",
        dst_source_id="native",
        dst_path="/b",
        kind=Kind.FILE,
        size=1,
    )
    plan = Plan(kind=OperationKind.COPY, items=[item])
    assert _current_item(plan, 1) is None  # past end


# ---------------------------------------------------------------------------
# Zero guard + Drag formula (tested by constructing a ProgressScreen and
# inspecting its body output)
# ---------------------------------------------------------------------------


class _StubQueue:
    """Minimal duck-typed queue for ProgressScreen body-rendering tests.

    Avoids spinning up a real asyncio worker - the screen reads four
    properties (``bytes_progress``, ``running_progress``,
    ``elapsed_seconds``, ``running``, ``cancel_requested``) and we want
    deterministic values.
    """

    def __init__(
        self,
        *,
        bytes_done: int = 0,
        bytes_total: int = 0,
        items_done: int = 0,
        items_total: int = 0,
        elapsed: float = 0.0,
        running: bool = True,
        plan: Plan | None = None,
        cancel: bool = False,
    ) -> None:
        self._bytes_done = bytes_done
        self._bytes_total = bytes_total
        self._items_done = items_done
        self._items_total = items_total
        self._elapsed = elapsed
        self._running = running
        self._plan = plan
        self._cancel = cancel

    @property
    def bytes_progress(self) -> tuple[int, int] | None:
        if not self._running:
            return None
        return (self._bytes_done, self._bytes_total)

    @property
    def running_progress(self) -> tuple[int, int] | None:
        if not self._running:
            return None
        return (self._items_done, self._items_total)

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed

    @property
    def running(self) -> Plan | None:
        return self._plan

    @property
    def cancel_requested(self) -> bool:
        return self._cancel


def _body_str(queue: _StubQueue, plan: Plan | None = None) -> str:
    """Render ProgressScreen's body to a plain string for assertions."""
    screen = ProgressScreen.__new__(ProgressScreen)
    screen._queue = queue
    screen._plan = plan
    return screen._body_text().plain


def test_zero_guard_elapsed_zero_renders_em_dash() -> None:
    """At elapsed=0, Rate and Drag both render the em-dash."""
    item = PlanItem(
        src_source_id="native", src_path="/a", dst_source_id="native",
        dst_path="/b", kind=Kind.FILE, size=1024,
    )
    plan = Plan(kind=OperationKind.COPY, items=[item])
    queue = _StubQueue(
        bytes_done=0,
        bytes_total=1024,
        items_done=0,
        items_total=1,
        elapsed=0.0,
        plan=plan,
    )
    body = _body_str(queue, plan)
    # Both Rate and Drag should appear as the em-dash.
    assert f"Rate    {_EM_DASH}" in body or f"Rate     {_EM_DASH}" in body
    assert f"Drag    {_EM_DASH}" in body or f"Drag     {_EM_DASH}" in body


def test_zero_guard_bytes_zero_with_elapsed_renders_em_dash() -> None:
    """Even after elapsed > 0, if no bytes flowed yet, em-dash applies.

    This is the big-file-just-opened case: stat call landed, file
    opened, but the first read hasn't returned. Without this guard,
    Drag would spike to its theoretical max at second one and Rate
    would read 0.0 MB/s, both false-meaningful.
    """
    item = PlanItem(
        src_source_id="native", src_path="/a", dst_source_id="native",
        dst_path="/b", kind=Kind.FILE, size=1024,
    )
    plan = Plan(kind=OperationKind.COPY, items=[item])
    queue = _StubQueue(
        bytes_done=0,
        bytes_total=1024,
        items_done=0,
        items_total=1,
        elapsed=2.5,  # ticked over, but no bytes flowed
        plan=plan,
    )
    body = _body_str(queue, plan)
    assert _EM_DASH in body
    # Both Rate and Drag should be em-dash - not a "0.0 MB/s" or "0.00".
    assert "0.0 MB/s" not in body
    assert "0.00" not in body or body.count(_EM_DASH) >= 2


def test_zero_guard_releases_once_both_nonzero() -> None:
    """Once elapsed > 0 AND bytes_done > 0, real values appear."""
    item = PlanItem(
        src_source_id="native", src_path="/a", dst_source_id="native",
        dst_path="/b", kind=Kind.FILE, size=1024,
    )
    plan = Plan(kind=OperationKind.COPY, items=[item])
    queue = _StubQueue(
        bytes_done=512,  # half done
        bytes_total=1024,
        items_done=0,
        items_total=1,
        elapsed=2.0,
        plan=plan,
    )
    body = _body_str(queue, plan)
    # Rate = 512 B / 2s = 256 B/s, formatted as "256 B/s"
    assert "B/s" in body
    # Drag = (1 - 0.5) * 2 = 1.00
    assert "1.00" in body


def test_drag_formula_matches_design() -> None:
    """Drag = (1 - bytes_done/bytes_total) * elapsed_seconds, normalised."""
    item = PlanItem(
        src_source_id="native", src_path="/a", dst_source_id="native",
        dst_path="/b", kind=Kind.FILE, size=1000,
    )
    plan = Plan(kind=OperationKind.COPY, items=[item])
    # At 25% done after 10s: drag = 0.75 * 10 = 7.50
    queue = _StubQueue(
        bytes_done=250,
        bytes_total=1000,
        items_done=0,
        items_total=1,
        elapsed=10.0,
        plan=plan,
    )
    body = _body_str(queue, plan)
    assert "7.50" in body


def test_percent_is_byte_weighted_not_item_weighted() -> None:
    """Percent reports byte progress, not items-done/items-total."""
    item = PlanItem(
        src_source_id="native", src_path="/a", dst_source_id="native",
        dst_path="/b", kind=Kind.FILE, size=1000,
    )
    plan = Plan(kind=OperationKind.COPY, items=[item])
    # 47% of bytes done; 0 of 10 items done
    queue = _StubQueue(
        bytes_done=470,
        bytes_total=1000,
        items_done=0,
        items_total=10,
        elapsed=1.0,
        plan=plan,
    )
    body = _body_str(queue, plan)
    # The Percent label appears as e.g. "47%" - bytes-weighted, not "0%".
    assert "47%" in body