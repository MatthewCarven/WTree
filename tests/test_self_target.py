"""Tests for same-directory / self-target (``src == dst``) handling.

Covers the pieces that land together (see ``design.md`` -> Conflict
resolution dialog -> Same-location (self-target) handling):

* ``_same_location`` path/source equality;
* ``resolve_self_targets`` - Copy marks the topmost self-target SELF and
  leaves descendants NONE; Move/Rename drop the no-op item;
* ``annotate_conflicts`` skipping self-targeted items;
* the planners (``plan_copy`` / ``plan_move``) end-to-end on a mock;
* ``resolve_conflicts`` turning a SELF root into a ``(1)`` duplicate (with
  cascade) or dropping it on Skip;
* the ``ConflictDialog`` defaulting SELF rows to Rename;
* the executor's self-destruct guard (``_would_destroy_source`` and a real
  OVERWRITE-onto-self refusal);
* app wiring (Move nudges "already there"; Copy offers the duplicate).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from wtree.ops import (
    ConflictKind,
    OperationKind,
    Resolution,
    apply_plan,
    plan_copy,
    plan_move,
    resolve_conflicts,
    resolve_self_targets,
)
from wtree.ops.base import ItemStatus, Plan, PlanItem
from wtree.ops.conflicts import _same_location
from wtree.ops.execute import _would_destroy_source
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag
from wtree.widgets.conflict import ConflictDialog
from wtree.widgets.prompt import PromptDialog


def _now() -> datetime:
    return datetime(2026, 6, 3, 12, 0, 0)


def _item(
    src: str,
    dst: str,
    kind: Kind = Kind.FILE,
    *,
    src_id: str = "mock",
    dst_id: str = "mock",
    conflict: ConflictKind = ConflictKind.NONE,
) -> PlanItem:
    return PlanItem(
        src_source_id=src_id,
        src_path=src,
        dst_source_id=dst_id,
        dst_path=dst,
        kind=kind,
        size=1,
        conflict=conflict,
    )


@pytest.fixture
def own_dir_mock() -> MockSource:
    """A tree where /d holds a file and a subdir, so copying/moving either
    into /d is a self-target.

    /
    + d/        foo.txt, proj/(a.txt, sub/(deep.txt))
    + other/    (free landing zone)
    """
    now = _now()
    return MockSource(
        contents={
            "/": [Entry("d", Kind.DIR, 4096, now), Entry("other", Kind.DIR, 4096, now)],
            "/d": [
                Entry("foo.txt", Kind.FILE, 10, now),
                Entry("proj", Kind.DIR, 4096, now),
            ],
            "/d/proj": [
                Entry("a.txt", Kind.FILE, 5, now),
                Entry("sub", Kind.DIR, 4096, now),
            ],
            "/d/proj/sub": [Entry("deep.txt", Kind.FILE, 7, now)],
            "/other": [],
        }
    )


def _reg(m: MockSource) -> dict[str, MockSource]:
    return {"mock": m}


# ---------------------------------------------------------------------------
# _same_location
# ---------------------------------------------------------------------------


def test_same_location_true_for_identical():
    assert _same_location(_item("/d/foo.txt", "/d/foo.txt")) is True


def test_same_location_normalises_dots_and_slashes():
    assert _same_location(_item("/d/proj", "/d/./proj/")) is True


def test_same_location_false_for_different_path():
    assert _same_location(_item("/d/foo.txt", "/other/foo.txt")) is False


def test_same_location_false_across_sources():
    it = _item("/d/foo.txt", "/d/foo.txt", src_id="mock", dst_id="zip")
    assert _same_location(it) is False


# ---------------------------------------------------------------------------
# resolve_self_targets (pure transform)
# ---------------------------------------------------------------------------


def test_resolve_self_targets_copy_file_marks_self():
    plan = Plan(kind=OperationKind.COPY, items=[_item("/d/foo.txt", "/d/foo.txt")])
    out = resolve_self_targets(plan)
    assert out.items[0].conflict is ConflictKind.SELF


def test_resolve_self_targets_copy_dir_marks_root_only():
    plan = Plan(
        kind=OperationKind.COPY,
        items=[
            _item("/d/proj", "/d/proj", Kind.DIR),
            _item("/d/proj/a.txt", "/d/proj/a.txt"),
            _item("/d/proj/sub", "/d/proj/sub", Kind.DIR),
        ],
    )
    out = resolve_self_targets(plan)
    assert out.items[0].conflict is ConflictKind.SELF
    # Descendants left NONE - they cascade off the root's rename/skip.
    assert out.items[1].conflict is ConflictKind.NONE
    assert out.items[2].conflict is ConflictKind.NONE


def test_resolve_self_targets_move_drops_noop():
    plan = Plan(
        kind=OperationKind.MOVE,
        items=[
            _item("/d/proj", "/d/proj", Kind.DIR),       # self -> dropped
            _item("/d/proj", "/other/proj", Kind.DIR),   # real -> kept
        ],
    )
    out = resolve_self_targets(plan)
    assert len(out.items) == 1
    assert out.items[0].dst_path == "/other/proj"


def test_resolve_self_targets_empty_plan_unchanged():
    plan = Plan(kind=OperationKind.COPY, items=[])
    assert resolve_self_targets(plan).items == []


# ---------------------------------------------------------------------------
# Planner integration
# ---------------------------------------------------------------------------


async def test_plan_copy_file_into_own_dir_is_self(own_dir_mock):
    plan = await plan_copy(
        [Tag("mock", "/d/foo.txt")], Tag("mock", "/d"), _reg(own_dir_mock)
    )
    assert len(plan.items) == 1
    assert plan.items[0].conflict is ConflictKind.SELF


async def test_plan_copy_dir_into_own_parent_root_self_descendants_none(own_dir_mock):
    plan = await plan_copy(
        [Tag("mock", "/d/proj")], Tag("mock", "/d"), _reg(own_dir_mock)
    )
    by_dst = {i.dst_path: i for i in plan.items}
    assert by_dst["/d/proj"].conflict is ConflictKind.SELF
    # The benign-merge rule would have suppressed the dir; the self skip in
    # annotate keeps every descendant NONE (no spurious file conflicts).
    assert by_dst["/d/proj/a.txt"].conflict is ConflictKind.NONE
    assert by_dst["/d/proj/sub/deep.txt"].conflict is ConflictKind.NONE
    selfs = [i for i in plan.items if i.conflict is ConflictKind.SELF]
    assert len(selfs) == 1


async def test_plan_move_into_own_parent_drops_all(own_dir_mock):
    plan = await plan_move(
        [Tag("mock", "/d/proj")], Tag("mock", "/d"), _reg(own_dir_mock)
    )
    assert plan.items == []
    assert plan.errors == []


# ---------------------------------------------------------------------------
# resolve_conflicts on a SELF root
# ---------------------------------------------------------------------------


async def test_self_rename_duplicates_with_cascade(own_dir_mock):
    plan = await plan_copy(
        [Tag("mock", "/d/proj")], Tag("mock", "/d"), _reg(own_dir_mock)
    )
    resolved = await resolve_conflicts(plan, [Resolution.RENAME], _reg(own_dir_mock))
    by_src = {i.src_path: i.dst_path for i in resolved.items}
    # Root duplicated to "proj (1)"; descendants cascade under the new name.
    assert by_src["/d/proj"] == "/d/proj (1)"
    assert by_src["/d/proj/a.txt"] == "/d/proj (1)/a.txt"
    assert by_src["/d/proj/sub/deep.txt"] == "/d/proj (1)/sub/deep.txt"
    # No item is still pointed at its own source.
    assert all(not _same_location(i) for i in resolved.items)


async def test_self_skip_drops_whole_subtree(own_dir_mock):
    plan = await plan_copy(
        [Tag("mock", "/d/proj")], Tag("mock", "/d"), _reg(own_dir_mock)
    )
    resolved = await resolve_conflicts(plan, [Resolution.SKIP], _reg(own_dir_mock))
    assert resolved.items == []


# ---------------------------------------------------------------------------
# ConflictDialog defaults
# ---------------------------------------------------------------------------


def test_dialog_self_row_defaults_to_rename():
    d = ConflictDialog([_item("/d/foo.txt", "/d/foo.txt", conflict=ConflictKind.SELF)])
    assert d._res == [Resolution.RENAME]


def test_dialog_mixed_rows_default_per_kind():
    d = ConflictDialog(
        [
            _item("/d/foo.txt", "/d/foo.txt", conflict=ConflictKind.SELF),
            _item("/x/a", "/x/a", conflict=ConflictKind.FILE),
        ]
    )
    assert d._res == [Resolution.RENAME, Resolution.SKIP]


def test_dialog_self_row_label_reads_same_location():
    d = ConflictDialog([_item("/d/foo.txt", "/d/foo.txt", conflict=ConflictKind.SELF)])
    assert "same location" in d._row_text(0)


# ---------------------------------------------------------------------------
# Executor self-destruct guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dst,src,expected",
    [
        ("/d/proj", "/d/proj", True),          # identical
        ("/d", "/d/proj", True),               # dst is ancestor of src
        ("/d/proj", "/d/proj2", False),        # sibling prefix, not ancestor
        ("/d/proj", "/other/proj", False),     # unrelated
    ],
)
def test_would_destroy_source(dst, src, expected):
    assert _would_destroy_source(dst, src) is expected


@pytest.fixture
def native_registry() -> dict[str, NativeSource]:
    return {"native": NativeSource()}


async def test_exec_overwrite_move_onto_self_refused(tmp_path, native_registry):
    """A move item with dst == src + OVERWRITE must FAIL, not rmtree the
    source (the catastrophic case the guard exists for)."""
    f = tmp_path / "keep.txt"
    f.write_text("precious")
    item = PlanItem(
        src_source_id="native",
        src_path=str(f),
        dst_source_id="native",
        dst_path=str(f),
        kind=Kind.FILE,
        size=8,
        conflict=ConflictKind.FILE,
        resolution=Resolution.OVERWRITE,
    )
    result = await apply_plan(Plan(kind=OperationKind.MOVE, items=[item]), native_registry)
    assert result.items[0].status is ItemStatus.FAILED
    assert "refusing to overwrite" in result.items[0].message
    assert f.exists() and f.read_text() == "precious"


async def test_exec_overwrite_copy_dir_onto_ancestor_refused(tmp_path, native_registry):
    """OVERWRITE copy whose dst is an ancestor dir of src must refuse rather
    than rmtree the tree containing the source."""
    d = tmp_path / "d"
    (d / "proj").mkdir(parents=True)
    (d / "proj" / "a.txt").write_text("hi")
    item = PlanItem(
        src_source_id="native",
        src_path=str(d / "proj"),
        dst_source_id="native",
        dst_path=str(d),
        kind=Kind.DIR,
        size=0,
        conflict=ConflictKind.DIR,
        resolution=Resolution.OVERWRITE,
    )
    result = await apply_plan(Plan(kind=OperationKind.COPY, items=[item]), native_registry)
    assert result.items[0].status is ItemStatus.FAILED
    assert (d / "proj" / "a.txt").read_text() == "hi"


# ---------------------------------------------------------------------------
# App wiring (real native source on tmp_path)
# ---------------------------------------------------------------------------


async def test_e2e_move_into_own_dir_nudges(tmp_path):
    from wtree.app import WTreeApp

    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("data")

    app = WTreeApp(root_path=str(work))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        # Accept the default destination (the entry's own directory).
        await pilot.press("enter")
        await pilot.pause()
        # No conflict dialog - the no-op was dropped; nothing enqueued.
        assert not isinstance(app.screen, ConflictDialog)
    assert app.last_plan is None
    assert (work / "a.txt").read_text() == "data"


async def test_e2e_copy_into_own_dir_offers_duplicate(tmp_path):
    from textual.widgets import Input

    from wtree.app import WTreeApp

    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("data")

    app = WTreeApp(root_path=str(work))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        app.screen.query_one(Input).value = str(work)  # copy into own dir
        await pilot.press("enter")
        await pilot.pause()
        # Surfaces as a SELF conflict defaulting to Rename.
        assert isinstance(app.screen, ConflictDialog)
        assert app.screen._res == [Resolution.RENAME]
        await pilot.press("enter")  # commit the duplicate
        await pilot.pause()
        assert app.op_queue is not None
        await app.op_queue.wait_until_idle()

    assert (work / "a.txt").read_text() == "data"        # original intact
    assert (work / "a (1).txt").read_text() == "data"    # duplicate made
