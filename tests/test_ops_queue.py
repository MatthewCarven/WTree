"""Tests for ``wtree.ops.queue.OperationQueue``.

These run the queue with real ``NativeSource`` + real ``tmp_path`` so
"completed" actually means "bytes moved". Mock-only would test the
plumbing but not the FIFO+blocking promise the queue makes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wtree.ops import (
    OperationQueue,
    OperationResult,
    Plan,
    plan_copy,
)
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag


@pytest.fixture
def registry() -> dict[str, NativeSource]:
    return {"native": NativeSource()}


async def _make_file_plan(
    tmp_path: Path,
    name: str,
    body: str,
    registry: dict[str, NativeSource],
    dst_dir_name: str = "dst",
) -> Plan:
    """Helper: write tmp_path/{name} containing body, plan copy into
    tmp_path/{dst_dir_name}/, return the Plan."""
    src = tmp_path / name
    src.write_text(body)
    dst = tmp_path / dst_dir_name
    dst.mkdir(exist_ok=True)
    return await plan_copy(
        [Tag("native", str(src))], Tag("native", str(dst)), registry
    )


# ---------------------------------------------------------------------------
# Single plan
# ---------------------------------------------------------------------------


async def test_queue_runs_a_single_plan(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    plan = await _make_file_plan(tmp_path, "a.txt", "hello", registry)
    queue = OperationQueue(registry=registry)
    queue.start()
    try:
        queue.enqueue(plan)
        await queue.wait_until_idle()
        assert (tmp_path / "dst" / "a.txt").read_text() == "hello"
        assert len(queue.completed) == 1
        assert queue.completed[0].all_succeeded
        assert queue.depth == 0
        assert queue.running is None
    finally:
        await queue.stop()


# ---------------------------------------------------------------------------
# FIFO ordering + blocking
# ---------------------------------------------------------------------------


async def test_queue_runs_two_plans_in_fifo_order(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    plan1 = await _make_file_plan(tmp_path, "a.txt", "first", registry)
    plan2 = await _make_file_plan(tmp_path, "b.txt", "second", registry)
    queue = OperationQueue(registry=registry)
    queue.start()
    try:
        queue.enqueue(plan1)
        queue.enqueue(plan2)
        # At this instant the worker might already be partway through
        # plan1 - it's a race; only the *final* state is deterministic.
        await queue.wait_until_idle()
        assert (tmp_path / "dst" / "a.txt").read_text() == "first"
        assert (tmp_path / "dst" / "b.txt").read_text() == "second"
        # Completion log preserves order.
        assert queue.completed[0].plan is plan1
        assert queue.completed[1].plan is plan2
    finally:
        await queue.stop()


async def test_queue_second_plan_waits_for_first(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """Whitebox-ish: the queue must never run two plans concurrently.

    We hook the start callback to record the order in which plans
    started, then assert plan2 only starts after plan1's complete cb
    has fired.
    """
    plan1 = await _make_file_plan(tmp_path, "a.txt", "1", registry)
    plan2 = await _make_file_plan(tmp_path, "b.txt", "2", registry, dst_dir_name="dst2")

    events: list[str] = []

    def on_start(plan: Plan, q: OperationQueue) -> None:
        events.append(f"start:{plan.items[0].src_path.split('/')[-1]}")

    def on_complete(result: OperationResult, q: OperationQueue) -> None:
        events.append(f"done:{result.plan.items[0].src_path.split('/')[-1]}")

    queue = OperationQueue(
        registry=registry,
        on_plan_start=on_start,
        on_plan_complete=on_complete,
    )
    queue.start()
    try:
        queue.enqueue(plan1)
        queue.enqueue(plan2)
        await queue.wait_until_idle()
    finally:
        await queue.stop()

    # Each plan: start before done; plan1 fully bracketed before plan2.
    assert events == [
        "start:a.txt",
        "done:a.txt",
        "start:b.txt",
        "done:b.txt",
    ]


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


async def test_queue_failing_plan_does_not_block_next(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """If plan1 has failing items, plan2 still runs to completion."""
    # plan1: source doesn't exist (deleted between plan and apply).
    src1 = tmp_path / "ghost.txt"
    src1.write_text("x")
    dst1 = tmp_path / "dst1"
    dst1.mkdir()
    plan1 = await plan_copy(
        [Tag("native", str(src1))], Tag("native", str(dst1)), registry
    )
    src1.unlink()  # vanish

    plan2 = await _make_file_plan(
        tmp_path, "b.txt", "still works", registry, dst_dir_name="dst2"
    )
    queue = OperationQueue(registry=registry)
    queue.start()
    try:
        queue.enqueue(plan1)
        queue.enqueue(plan2)
        await queue.wait_until_idle()
    finally:
        await queue.stop()

    assert (tmp_path / "dst2" / "b.txt").read_text() == "still works"
    assert queue.completed[0].failed_count >= 1
    assert queue.completed[1].all_succeeded


async def test_queue_start_is_idempotent(
    registry: dict[str, NativeSource],
) -> None:
    """Double-starting the queue doesn't spawn two workers."""
    queue = OperationQueue(registry=registry)
    queue.start()
    first_worker = queue._worker  # noqa: SLF001 - whitebox check
    queue.start()
    assert queue._worker is first_worker  # noqa: SLF001
    await queue.stop()


async def test_queue_stop_is_safe_when_idle(
    registry: dict[str, NativeSource],
) -> None:
    """Stopping before any start is a no-op."""
    queue = OperationQueue(registry=registry)
    await queue.stop()  # should not raise


# ---------------------------------------------------------------------------
# Callback exceptions don't kill the worker
# ---------------------------------------------------------------------------


async def test_queue_callback_exceptions_are_isolated(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    plan = await _make_file_plan(tmp_path, "a.txt", "z", registry)

    def on_complete(result: OperationResult, q: OperationQueue) -> None:
        raise RuntimeError("UI bug!")

    queue = OperationQueue(registry=registry, on_plan_complete=on_complete)
    queue.start()
    try:
        queue.enqueue(plan)
        await queue.wait_until_idle()
        # Plan still completed despite the callback explosion.
        assert (tmp_path / "dst" / "a.txt").read_text() == "z"
        assert len(queue.completed) == 1
        # And the worker is still alive for the next plan.
        plan2 = await _make_file_plan(tmp_path, "b.txt", "y", registry, dst_dir_name="dst2")
        queue.enqueue(plan2)
        await queue.wait_until_idle()
        assert (tmp_path / "dst2" / "b.txt").read_text() == "y"
    finally:
        await queue.stop()
