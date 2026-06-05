"""Tests for cancellable, O(n) plan-building (the big-tagged-set freeze fix).

Covers:

* ``walk_tags`` populates ``entries_by_tag`` index-parallel to the input
  tags (the structure that lets ``plan_copy`` zip in O(n) instead of
  re-scanning the flat list once per tag), and the flat ``entries`` stays
  the union.
* The planners (``walk_tags`` / ``plan_copy`` / ``plan_move`` /
  ``annotate_conflicts``) call ``on_progress`` and raise
  :class:`ScanCancelled` when ``should_cancel`` fires at a chunk boundary.
* Without the callbacks the planners behave exactly as before (back-compat).
"""

from __future__ import annotations

from datetime import datetime

import pytest

import wtree.ops.conflicts as conflicts_mod
import wtree.ops.copy as copy_mod
import wtree.ops.move as move_mod
from wtree.ops import (
    OperationKind,
    Plan,
    PlanItem,
    ScanCancelled,
)
from wtree.ops.conflicts import annotate_conflicts
from wtree.ops.copy import plan_copy, walk_tags
from wtree.ops.move import plan_move
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.tagged_set import Tag


def _now() -> datetime:
    return datetime(2026, 6, 5, 12, 0, 0)


@pytest.fixture
def mock() -> MockSource:
    now = _now()
    return MockSource(
        contents={
            "/": [
                Entry("proj", Kind.DIR, 4096, now),
                Entry("readme.txt", Kind.FILE, 200, now),
                Entry("dest", Kind.DIR, 4096, now),
            ],
            "/proj": [
                Entry("notes.md", Kind.FILE, 80, now),
                Entry("src", Kind.DIR, 4096, now),
            ],
            "/proj/src": [Entry("main.py", Kind.FILE, 1500, now)],
            "/dest": [],
        }
    )


# --- grouping -------------------------------------------------------------

@pytest.mark.asyncio
async def test_walk_tags_groups_entries_by_tag(mock: MockSource):
    tags = [Tag("mock", "/proj"), Tag("mock", "/readme.txt")]
    walk = await walk_tags(tags, {"mock": mock})

    # One group per tag, in tag order.
    assert len(walk.entries_by_tag) == len(tags)
    # /proj group = proj + its whole subtree; readme group = just the file.
    proj_paths = {e.path for e in walk.entries_by_tag[0]}
    assert proj_paths == {"/proj", "/proj/notes.md", "/proj/src", "/proj/src/main.py"}
    assert [e.path for e in walk.entries_by_tag[1]] == ["/readme.txt"]
    # Flat entries is the union of the groups (same objects, tag order).
    flat = [e for group in walk.entries_by_tag for e in group]
    assert flat == walk.entries


@pytest.mark.asyncio
async def test_walk_tags_keeps_group_slot_on_error(mock: MockSource):
    tags = [Tag("nope", "/x"), Tag("mock", "/readme.txt")]
    walk = await walk_tags(tags, {"mock": mock})
    # Slot for the bad tag is present (empty) so groups stay parallel to tags.
    assert len(walk.entries_by_tag) == 2
    assert walk.entries_by_tag[0] == []
    assert [e.path for e in walk.entries_by_tag[1]] == ["/readme.txt"]
    assert len(walk.errors) == 1


@pytest.mark.asyncio
async def test_plan_copy_unchanged_without_callbacks(mock: MockSource):
    plan = await plan_copy(
        [Tag("mock", "/proj")], Tag("mock", "/dest"), {"mock": mock}
    )
    dsts = sorted(i.dst_path for i in plan.items)
    assert dsts == [
        "/dest/proj",
        "/dest/proj/notes.md",
        "/dest/proj/src",
        "/dest/proj/src/main.py",
    ]


# --- progress + cancellation ---------------------------------------------

@pytest.mark.asyncio
async def test_walk_tags_reports_progress(mock: MockSource, monkeypatch):
    monkeypatch.setattr(copy_mod, "PLAN_CHUNK_SIZE", 1)
    seen: list[int] = []
    await walk_tags(
        [Tag("mock", "/proj")], {"mock": mock}, on_progress=seen.append
    )
    assert seen == sorted(seen) and seen  # monotonic, non-empty


@pytest.mark.asyncio
async def test_walk_tags_cancel_raises(mock: MockSource, monkeypatch):
    monkeypatch.setattr(copy_mod, "PLAN_CHUNK_SIZE", 1)
    with pytest.raises(ScanCancelled):
        await walk_tags(
            [Tag("mock", "/proj")], {"mock": mock}, should_cancel=lambda: True
        )


@pytest.mark.asyncio
async def test_plan_copy_cancel_raises(mock: MockSource, monkeypatch):
    monkeypatch.setattr(copy_mod, "PLAN_CHUNK_SIZE", 1)
    with pytest.raises(ScanCancelled):
        await plan_copy(
            [Tag("mock", "/proj")],
            Tag("mock", "/dest"),
            {"mock": mock},
            should_cancel=lambda: True,
        )


@pytest.mark.asyncio
async def test_plan_move_cancel_raises(mock: MockSource, monkeypatch):
    monkeypatch.setattr(move_mod, "PLAN_CHUNK_SIZE", 1)
    with pytest.raises(ScanCancelled):
        await plan_move(
            [Tag("mock", "/proj"), Tag("mock", "/readme.txt")],
            Tag("mock", "/dest"),
            {"mock": mock},
            should_cancel=lambda: True,
        )


@pytest.mark.asyncio
async def test_annotate_conflicts_cancel_raises(mock: MockSource, monkeypatch):
    monkeypatch.setattr(conflicts_mod, "PLAN_CHUNK_SIZE", 1)
    plan = Plan(
        kind=OperationKind.COPY,
        items=[
            PlanItem("mock", "/readme.txt", "mock", "/dest/readme.txt", Kind.FILE, 200),
            PlanItem("mock", "/proj", "mock", "/dest/proj", Kind.DIR, 4096),
        ],
        errors=[],
    )
    with pytest.raises(ScanCancelled):
        await annotate_conflicts(plan, {"mock": mock}, should_cancel=lambda: True)
