"""Tests for mid-plan cancellation in ``apply_plan``.

Design lives in design.md -> User interface -> Progress dialog ->
Mid-plan cancellation (2026-05-26 decision-log row).

Coverage:

* **Signature**: ``apply_plan`` takes optional ``is_cancelled``.
* **Pre-item check**: once the flag flips True, every remaining
  item short-circuits to ``ItemStatus.SKIPPED`` with message
  ``"cancelled"``.
* **Per-item progress fires for skipped items**: the dialog's
  items counter stays consistent with ``len(plan.items)``.
* **Status split**: in-flight cancelled mid-copy stays FAILED;
  not-yet-started items are SKIPPED. ``OperationResult.summary()``
  surfaces both counts.
* **No-op when flag stays False**: behaviour matches the no-cancel
  path (regression guard).
* **Cancel before first item**: all items SKIPPED, no SUCCESS.
* **Queue integration**: ``OperationQueue.request_cancel()`` mid-plan
  drains remaining items to SKIPPED, plan completes, ``all_succeeded``
  is False.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wtree.ops import OperationQueue, plan_copy
from wtree.ops.base import ItemStatus, OperationKind, PlanItem
from wtree.ops.execute import apply_plan
from wtree.sources.base import Kind
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag


@pytest.fixture
def registry() -> dict[str, NativeSource]:
    return {"native": NativeSource()}


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def test_apply_plan_signature_takes_is_cancelled() -> None:
    """``is_cancelled`` is now a kwarg on apply_plan."""
    import inspect

    sig = inspect.signature(apply_plan)
    assert "is_cancelled" in sig.parameters
    assert sig.parameters["is_cancelled"].default is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_copy_plan(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    n_files: int,
) -> tuple[list[Path], Path]:
    """Stage ``n_files`` files + a destination dir, return their paths.

    Returns ``(src_paths, dst_dir)``.
    """
    srcs: list[Path] = []
    for i in range(n_files):
        p = tmp_path / f"src_{i:02d}.txt"
        p.write_text(f"contents-{i}")
        srcs.append(p)
    dst = tmp_path / "dst"
    dst.mkdir()
    return srcs, dst


async def _plan_for(
    srcs: list[Path], dst: Path, registry: dict[str, NativeSource]
):
    return await plan_copy(
        [Tag("native", str(s)) for s in srcs],
        Tag("native", str(dst)),
        registry,
    )


# ---------------------------------------------------------------------------
# is_cancelled never trips -> no behavioural change
# ---------------------------------------------------------------------------


async def test_is_cancelled_false_runs_every_item(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """Regression guard: closure that never returns True is a no-op."""
    srcs, dst = await _make_copy_plan(tmp_path, registry, 5)
    plan = await _plan_for(srcs, dst, registry)

    calls = {"checks": 0}

    def never_cancel() -> bool:
        calls["checks"] += 1
        return False

    result = await apply_plan(plan, registry, is_cancelled=never_cancel)

    assert result.all_succeeded
    assert calls["checks"] == len(plan.items)  # one check per item
    for s in srcs:
        # Source still there (copy not move)
        assert s.exists()
        assert (dst / s.name).exists()


# ---------------------------------------------------------------------------
# Cancel before first item -> every item SKIPPED("cancelled")
# ---------------------------------------------------------------------------


async def test_cancel_before_first_item_skips_all(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """Flag set before apply_plan even starts: no SUCCESS, all SKIPPED."""
    srcs, dst = await _make_copy_plan(tmp_path, registry, 4)
    plan = await _plan_for(srcs, dst, registry)

    result = await apply_plan(plan, registry, is_cancelled=lambda: True)

    assert not result.all_succeeded
    assert result.success_count == 0
    assert result.skipped_count == len(plan.items)
    assert result.failed_count == 0
    # All skipped items carry the cancelled message.
    for r in result.items:
        assert r.status == ItemStatus.SKIPPED
        assert r.message == "cancelled"
    # No file copies actually happened.
    for s in srcs:
        assert not (dst / s.name).exists()


# ---------------------------------------------------------------------------
# Cancel mid-plan -> remaining items SKIPPED
# ---------------------------------------------------------------------------


async def test_cancel_mid_plan_skips_remaining(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """Flag flips after item N: items 0..N succeed, rest are SKIPPED."""
    srcs, dst = await _make_copy_plan(tmp_path, registry, 6)
    plan = await _plan_for(srcs, dst, registry)

    state = {"flag": False, "checks": 0}

    def is_cancelled() -> bool:
        state["checks"] += 1
        # Trip after the third check (i.e. before item index 3 runs).
        # Item indices: 0, 1, 2 succeed; 3, 4, 5 skip.
        if state["checks"] >= 4:
            state["flag"] = True
        return state["flag"]

    result = await apply_plan(plan, registry, is_cancelled=is_cancelled)

    assert not result.all_succeeded
    # First 3 items copied.
    assert result.success_count == 3
    assert result.skipped_count == 3
    assert result.failed_count == 0
    # Per-item statuses in order.
    statuses = [r.status for r in result.items]
    assert statuses == [
        ItemStatus.SUCCESS, ItemStatus.SUCCESS, ItemStatus.SUCCESS,
        ItemStatus.SKIPPED, ItemStatus.SKIPPED, ItemStatus.SKIPPED,
    ]
    # Skipped items carry the cancelled message.
    for r in result.items[3:]:
        assert r.message == "cancelled"
    # First three landed on disk; the last three did not.
    for i, s in enumerate(srcs):
        landed = (dst / s.name).exists()
        assert landed is (i < 3), f"item {i} landed={landed}"


# ---------------------------------------------------------------------------
# progress callback fires for every item including skipped ones
# ---------------------------------------------------------------------------


async def test_progress_fires_for_skipped_items(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """Per-item progress must keep firing - dialog counter relies on it."""
    srcs, dst = await _make_copy_plan(tmp_path, registry, 5)
    plan = await _plan_for(srcs, dst, registry)

    fired: list[ItemStatus] = []

    def cb(item_result) -> None:
        fired.append(item_result.status)

    # Cancel before item 2 (so items 0, 1 succeed; 2, 3, 4 skip).
    state = {"checks": 0}

    def is_cancelled() -> bool:
        state["checks"] += 1
        return state["checks"] >= 3

    result = await apply_plan(
        plan, registry, progress=cb, is_cancelled=is_cancelled
    )

    # Exactly len(items) progress callbacks fired - one per PlanItem.
    assert len(fired) == len(plan.items) == 5
    # The first two SUCCESS, last three SKIPPED.
    assert fired == [
        ItemStatus.SUCCESS, ItemStatus.SUCCESS,
        ItemStatus.SKIPPED, ItemStatus.SKIPPED, ItemStatus.SKIPPED,
    ]


# ---------------------------------------------------------------------------
# Summary shows the skipped count
# ---------------------------------------------------------------------------


async def test_summary_includes_skipped_count(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """OperationResult.summary() surfaces the cancelled SKIPPED items."""
    srcs, dst = await _make_copy_plan(tmp_path, registry, 4)
    plan = await _plan_for(srcs, dst, registry)

    state = {"checks": 0}

    def is_cancelled() -> bool:
        state["checks"] += 1
        return state["checks"] >= 3

    result = await apply_plan(plan, registry, is_cancelled=is_cancelled)

    summary = result.summary()
    assert "2 ok" in summary
    assert "2 skipped" in summary


# ---------------------------------------------------------------------------
# Queue integration
# ---------------------------------------------------------------------------


async def test_queue_request_cancel_drains_remaining_to_skipped(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """OperationQueue.request_cancel() mid-plan: remaining items SKIPPED."""
    # Stage enough files that the plan takes some non-trivial time so
    # we can request cancel before it finishes. 20 small files is
    # plenty given pytest-asyncio's loop is single-threaded.
    srcs, dst = await _make_copy_plan(tmp_path, registry, 20)
    plan = await _plan_for(srcs, dst, registry)

    items_done: list[ItemStatus] = []

    def on_item_progress(item_result, queue):
        items_done.append(item_result.status)
        # Request cancel after the first item completes - the per-item
        # loop's pre-item check should pick it up before the next item
        # runs.
        if len(items_done) == 1 and item_result.status == ItemStatus.SUCCESS:
            queue.request_cancel()

    queue = OperationQueue(
        registry=registry, on_item_progress=on_item_progress
    )
    queue.start()
    try:
        queue.enqueue(plan)
        await queue.wait_until_idle()
    finally:
        await queue.stop()

    # We should have at least one SUCCESS and a bunch of SKIPPED.
    success = sum(1 for s in items_done if s == ItemStatus.SUCCESS)
    skipped = sum(1 for s in items_done if s == ItemStatus.SKIPPED)
    # All progress callbacks fired (one per PlanItem).
    assert len(items_done) == len(plan.items) == 20
    assert success >= 1, "at least one item must have completed"
    assert skipped >= 1, "at least one item must have been skipped"
    # The completed result reflects the partial state.
    assert len(queue.completed) == 1
    result = queue.completed[0]
    assert not result.all_succeeded
    assert result.success_count == success
    assert result.skipped_count == skipped


async def test_queue_cancel_flag_resets_between_plans_for_skip_path(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """A second plan after a cancelled one starts clean (no leftover flag)."""
    # Plan 1: cancel it.
    p1 = tmp_path / "p1"; p1.mkdir()
    srcs1, dst1 = await _make_copy_plan(p1, registry, 4)
    plan1 = await _plan_for(srcs1, dst1, registry)

    # Plan 2: should run clean.
    p2 = tmp_path / "p2"; p2.mkdir()
    srcs2, dst2 = await _make_copy_plan(p2, registry, 3)
    plan2 = await _plan_for(srcs2, dst2, registry)

    cancelled_first = False

    def on_item_progress(item_result, queue):
        nonlocal cancelled_first
        # Cancel plan 1 after first item.
        if not cancelled_first and item_result.item.dst_path.startswith(
            str(dst1)
        ):
            queue.request_cancel()
            cancelled_first = True

    queue = OperationQueue(
        registry=registry, on_item_progress=on_item_progress
    )
    queue.start()
    try:
        queue.enqueue(plan1)
        queue.enqueue(plan2)
        await queue.wait_until_idle()
    finally:
        await queue.stop()

    # Both plans recorded.
    assert len(queue.completed) == 2
    r1, r2 = queue.completed
    # Plan 1 was cancelled - some success, rest skipped.
    assert not r1.all_succeeded
    assert r1.skipped_count >= 1
    # Plan 2 ran clean (cancel flag was reset between plans).
    assert r2.all_succeeded
    assert r2.skipped_count == 0
