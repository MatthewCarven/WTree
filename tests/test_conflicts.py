"""Tests for plan-time conflict detection and resolution.

Covers the four pieces that landed together (see ``design.md`` -> User
interface -> Conflict resolution dialog):

* detection in the planners (``annotate_conflicts`` via ``plan_copy`` /
  ``plan_move`` / ``plan_rename``), including the benign-merge rule;
* the ``suffixed_name`` collision-free naming helper;
* the ``resolve_conflicts`` plan transform (skip / overwrite / rename,
  with directory cascade);
* the executor honouring ``Resolution.OVERWRITE`` (real filesystem);
* the ``ConflictDialog`` widget state machine;
* end-to-end app wiring (Copy/Rename surface the dialog and act on it).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from wtree.app import WTreeApp
from wtree.ops import (
    ConflictKind,
    OperationKind,
    Resolution,
    apply_plan,
    plan_copy,
    plan_move,
    plan_rename,
    resolve_conflicts,
    suffixed_name,
)
from wtree.ops.base import Plan, PlanItem
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag
from wtree.widgets.conflict import ConflictDialog
from wtree.widgets.prompt import PromptDialog


def _now() -> datetime:
    return datetime(2026, 6, 3, 12, 0, 0)


@pytest.fixture
def collide_mock() -> MockSource:
    """A mock with destinations that already hold colliding entries.

    /
    + src/        a.txt, b.txt, proj/(inner.txt)
    + dest/       a.txt (collides), proj/(inner.txt collides)
    + dest2/      proj  (a FILE - type mismatch target for a dir copy)
    + empty/      (free destination)
    """
    now = _now()
    return MockSource(
        contents={
            "/": [
                Entry("src", Kind.DIR, 4096, now),
                Entry("dest", Kind.DIR, 4096, now),
                Entry("dest2", Kind.DIR, 4096, now),
                Entry("empty", Kind.DIR, 4096, now),
            ],
            "/src": [
                Entry("a.txt", Kind.FILE, 10, now),
                Entry("b.txt", Kind.FILE, 20, now),
                Entry("proj", Kind.DIR, 4096, now),
            ],
            "/src/proj": [Entry("inner.txt", Kind.FILE, 5, now)],
            "/dest": [
                Entry("a.txt", Kind.FILE, 999, now),
                Entry("proj", Kind.DIR, 4096, now),
            ],
            "/dest/proj": [Entry("inner.txt", Kind.FILE, 999, now)],
            "/dest2": [Entry("proj", Kind.FILE, 30, now)],
            "/empty": [],
        }
    )


def _reg(m: MockSource) -> dict[str, MockSource]:
    return {"mock": m}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


async def test_copy_file_onto_existing_file_flags_conflict(collide_mock):
    plan = await plan_copy(
        [Tag("mock", "/src/a.txt")], Tag("mock", "/dest"), _reg(collide_mock)
    )
    assert len(plan.items) == 1
    assert plan.items[0].conflict is ConflictKind.FILE


async def test_copy_file_onto_free_dest_no_conflict(collide_mock):
    plan = await plan_copy(
        [Tag("mock", "/src/a.txt")], Tag("mock", "/empty"), _reg(collide_mock)
    )
    assert len(plan.items) == 1
    assert plan.items[0].conflict is ConflictKind.NONE


async def test_copy_dir_onto_existing_dir_is_benign_merge(collide_mock):
    """The dir item merges (NONE); only the colliding inner file is flagged."""
    plan = await plan_copy(
        [Tag("mock", "/src/proj")], Tag("mock", "/dest"), _reg(collide_mock)
    )
    by_dst = {i.dst_path: i for i in plan.items}
    assert by_dst["/dest/proj"].conflict is ConflictKind.NONE
    assert by_dst["/dest/proj/inner.txt"].conflict is ConflictKind.FILE
    conflicts = [i for i in plan.items if i.conflict is not ConflictKind.NONE]
    assert len(conflicts) == 1


async def test_copy_dir_onto_existing_file_is_type_mismatch(collide_mock):
    """Copying a dir where a *file* sits flags the dir item (existing=file)."""
    plan = await plan_copy(
        [Tag("mock", "/src/proj")], Tag("mock", "/dest2"), _reg(collide_mock)
    )
    by_dst = {i.dst_path: i for i in plan.items}
    assert by_dst["/dest2/proj"].conflict is ConflictKind.FILE


async def test_move_file_onto_existing_flags_conflict(collide_mock):
    plan = await plan_move(
        [Tag("mock", "/src/a.txt")], Tag("mock", "/dest"), _reg(collide_mock)
    )
    assert plan.items[0].conflict is ConflictKind.FILE


async def test_move_dir_onto_existing_dir_flags_conflict(collide_mock):
    """Move has no benign-merge rule: dir-on-dir is a real conflict."""
    plan = await plan_move(
        [Tag("mock", "/src/proj")], Tag("mock", "/dest"), _reg(collide_mock)
    )
    assert len(plan.items) == 1
    assert plan.items[0].conflict is ConflictKind.DIR


async def test_rename_onto_existing_flags_conflict(collide_mock):
    plan = await plan_rename(
        Tag("mock", "/src/a.txt"), "b.txt", _reg(collide_mock)
    )
    assert len(plan.items) == 1
    assert plan.items[0].conflict is ConflictKind.FILE


# ---------------------------------------------------------------------------
# suffixed_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,n,is_dir,expected",
    [
        ("report.txt", 1, False, "report (1).txt"),
        ("foo.tar.gz", 2, False, "foo.tar (2).gz"),
        ("Makefile", 1, False, "Makefile (1)"),
        (".bashrc", 1, False, ".bashrc (1)"),
        ("trailing.", 1, False, "trailing. (1)"),
        ("proj", 3, True, "proj (3)"),
        ("archive.zip", 1, True, "archive.zip (1)"),
    ],
)
def test_suffixed_name(name, n, is_dir, expected):
    assert suffixed_name(name, n, is_dir) == expected


# ---------------------------------------------------------------------------
# resolve_conflicts transform
# ---------------------------------------------------------------------------


async def test_resolve_skip_drops_item(collide_mock):
    plan = await plan_copy(
        [Tag("mock", "/src/a.txt")], Tag("mock", "/dest"), _reg(collide_mock)
    )
    resolved = await resolve_conflicts(plan, [Resolution.SKIP], _reg(collide_mock))
    assert resolved.items == []


async def test_resolve_overwrite_tags_item(collide_mock):
    plan = await plan_copy(
        [Tag("mock", "/src/a.txt")], Tag("mock", "/dest"), _reg(collide_mock)
    )
    resolved = await resolve_conflicts(
        plan, [Resolution.OVERWRITE], _reg(collide_mock)
    )
    assert len(resolved.items) == 1
    assert resolved.items[0].resolution is Resolution.OVERWRITE


async def test_resolve_rename_file_picks_free_name(collide_mock):
    plan = await plan_copy(
        [Tag("mock", "/src/a.txt")], Tag("mock", "/dest"), _reg(collide_mock)
    )
    resolved = await resolve_conflicts(
        plan, [Resolution.RENAME], _reg(collide_mock)
    )
    assert resolved.items[0].dst_path == "/dest/a (1).txt"
    assert resolved.items[0].conflict is ConflictKind.NONE
    assert resolved.items[0].resolution is Resolution.PROCEED


async def test_resolve_skip_dir_cascades_to_descendants(collide_mock):
    """Skipping a type-mismatch dir drops its descendant items too."""
    plan = await plan_copy(
        [Tag("mock", "/src/proj")], Tag("mock", "/dest2"), _reg(collide_mock)
    )
    # One conflict: the /dest2/proj dir item (existing is a file).
    conflicts = [i for i in plan.items if i.conflict is not ConflictKind.NONE]
    assert len(conflicts) == 1
    resolved = await resolve_conflicts(plan, [Resolution.SKIP], _reg(collide_mock))
    # Both the dir item and its inner.txt descendant are gone.
    assert resolved.items == []


async def test_resolve_rename_dir_cascades_prefix(collide_mock):
    plan = await plan_copy(
        [Tag("mock", "/src/proj")], Tag("mock", "/dest2"), _reg(collide_mock)
    )
    resolved = await resolve_conflicts(
        plan, [Resolution.RENAME], _reg(collide_mock)
    )
    dsts = sorted(i.dst_path for i in resolved.items)
    assert dsts == ["/dest2/proj (1)", "/dest2/proj (1)/inner.txt"]


async def test_resolve_length_mismatch_raises(collide_mock):
    plan = await plan_copy(
        [Tag("mock", "/src/a.txt")], Tag("mock", "/dest"), _reg(collide_mock)
    )
    with pytest.raises(ValueError):
        await resolve_conflicts(
            plan, [Resolution.SKIP, Resolution.SKIP], _reg(collide_mock)
        )


# ---------------------------------------------------------------------------
# Executor honours OVERWRITE (real filesystem)
# ---------------------------------------------------------------------------


@pytest.fixture
def native_registry() -> dict[str, NativeSource]:
    return {"native": NativeSource()}


async def test_exec_move_overwrite_replaces_file(tmp_path, native_registry):
    src = tmp_path / "a.txt"
    src.write_text("new")
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("old")

    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(d)), native_registry
    )
    assert plan.items[0].conflict is ConflictKind.FILE
    resolved = await resolve_conflicts(plan, [Resolution.OVERWRITE], native_registry)
    result = await apply_plan(resolved, native_registry)
    assert result.all_succeeded
    assert (d / "a.txt").read_text() == "new"
    assert not src.exists()


async def test_exec_move_overwrite_replaces_dir(tmp_path, native_registry):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "keep.txt").write_text("keep")
    d = tmp_path / "d"
    (d / "proj").mkdir(parents=True)
    (d / "proj" / "stale.txt").write_text("stale")

    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(d)), native_registry
    )
    assert plan.items[0].conflict is ConflictKind.DIR
    resolved = await resolve_conflicts(plan, [Resolution.OVERWRITE], native_registry)
    result = await apply_plan(resolved, native_registry)
    assert result.all_succeeded
    # Replace, not merge: the stale file is gone, the new one is present.
    assert (d / "proj" / "keep.txt").read_text() == "keep"
    assert not (d / "proj" / "stale.txt").exists()


async def test_exec_move_without_overwrite_fails_on_collision(
    tmp_path, native_registry
):
    src = tmp_path / "a.txt"
    src.write_text("new")
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("old")

    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(d)), native_registry
    )
    # Apply WITHOUT resolving - the race-net guard must refuse to clobber.
    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1
    assert (d / "a.txt").read_text() == "old"
    assert src.exists()


async def test_exec_rename_overwrite_replaces(tmp_path, native_registry):
    f = tmp_path / "a.txt"
    f.write_text("new")
    (tmp_path / "b.txt").write_text("old")

    plan = await plan_rename(Tag("native", str(f)), "b.txt", native_registry)
    assert plan.items[0].conflict is ConflictKind.FILE
    resolved = await resolve_conflicts(plan, [Resolution.OVERWRITE], native_registry)
    result = await apply_plan(resolved, native_registry)
    assert result.all_succeeded
    assert (tmp_path / "b.txt").read_text() == "new"
    assert not f.exists()


async def test_exec_copy_overwrite_file_onto_file(tmp_path, native_registry):
    src = tmp_path / "a.txt"
    src.write_text("new")
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("old")

    plan = await plan_copy(
        [Tag("native", str(src))], Tag("native", str(d)), native_registry
    )
    resolved = await resolve_conflicts(plan, [Resolution.OVERWRITE], native_registry)
    result = await apply_plan(resolved, native_registry)
    assert result.all_succeeded
    assert (d / "a.txt").read_text() == "new"
    assert src.read_text() == "new"  # copy leaves source intact


async def test_exec_copy_overwrite_dir_onto_file_type_mismatch(
    tmp_path, native_registry
):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "inner.txt").write_text("data")
    d = tmp_path / "d"
    d.mkdir()
    (d / "proj").write_text("i am a file")  # blocks the dir copy

    plan = await plan_copy(
        [Tag("native", str(src))], Tag("native", str(d)), native_registry
    )
    conflicts = [i for i in plan.items if i.conflict is not ConflictKind.NONE]
    assert len(conflicts) == 1  # the dir item, existing=file
    n = len(conflicts)
    resolved = await resolve_conflicts(
        plan, [Resolution.OVERWRITE] * n, native_registry
    )
    result = await apply_plan(resolved, native_registry)
    assert result.all_succeeded
    assert (d / "proj").is_dir()
    assert (d / "proj" / "inner.txt").read_text() == "data"


# ---------------------------------------------------------------------------
# ConflictDialog widget state machine (no app needed for pure state)
# ---------------------------------------------------------------------------


def _item(dst: str, kind: Kind, conflict: ConflictKind) -> PlanItem:
    return PlanItem(
        src_source_id="mock",
        src_path="/src" + dst,
        dst_source_id="mock",
        dst_path=dst,
        kind=kind,
        size=1,
        conflict=conflict,
    )


def test_dialog_defaults_to_skip():
    d = ConflictDialog([_item("/d/a", Kind.FILE, ConflictKind.FILE)])
    assert d._res == [Resolution.SKIP]


def test_dialog_set_all():
    items = [
        _item("/d/a", Kind.FILE, ConflictKind.FILE),
        _item("/d/b", Kind.FILE, ConflictKind.FILE),
    ]
    d = ConflictDialog(items)
    d.action_set_all("overwrite")
    assert d._res == [Resolution.OVERWRITE, Resolution.OVERWRITE]


def test_dialog_set_current_and_cursor():
    items = [
        _item("/d/a", Kind.FILE, ConflictKind.FILE),
        _item("/d/b", Kind.FILE, ConflictKind.FILE),
    ]
    d = ConflictDialog(items)
    d.action_set_current("overwrite")  # row 0
    d.action_cursor_down()
    d.action_set_current("rename")  # row 1
    assert d._res == [Resolution.OVERWRITE, Resolution.RENAME]
    assert d._cursor == 1


def test_dialog_cursor_wraps():
    d = ConflictDialog([_item("/d/a", Kind.FILE, ConflictKind.FILE)])
    d.action_cursor_up()
    assert d._cursor == 0  # single row, wraps to itself


def test_dialog_row_text_shows_path_and_kind():
    d = ConflictDialog([_item("/d/a.txt", Kind.FILE, ConflictKind.DIR)])
    text = d._row_text(0)
    assert "/d/a.txt" in text
    assert "dir" in text  # existing kind label


# ---------------------------------------------------------------------------
# End-to-end app wiring
# ---------------------------------------------------------------------------


async def _press_copy_to(pilot, app, dest: str) -> None:
    from textual.widgets import Input

    await pilot.press("c")
    await pilot.pause()
    assert isinstance(app.screen, PromptDialog)
    app.screen.query_one(Input).value = dest
    await pilot.press("enter")
    await pilot.pause()


async def test_e2e_copy_collision_shows_dialog_then_overwrite(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("new")
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("old")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await _press_copy_to(pilot, app, str(d))
        assert isinstance(app.screen, ConflictDialog)
        await pilot.press("O")
        await pilot.press("enter")
        await pilot.pause()
        assert app.op_queue is not None
        await app.op_queue.wait_until_idle()

    assert app.last_plan is not None
    assert app.last_plan.items[0].resolution is Resolution.OVERWRITE
    assert (d / "a.txt").read_text() == "new"


async def test_e2e_copy_collision_skip_all_does_nothing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("new")
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("old")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await _press_copy_to(pilot, app, str(d))
        assert isinstance(app.screen, ConflictDialog)
        await pilot.press("S")  # skip all
        await pilot.press("enter")
        await pilot.pause()

    # Nothing enqueued; destination untouched.
    assert app.last_plan is None
    assert (d / "a.txt").read_text() == "old"


async def test_e2e_copy_collision_escape_cancels(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("new")
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("old")

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await _press_copy_to(pilot, app, str(d))
        assert isinstance(app.screen, ConflictDialog)
        await pilot.press("escape")
        await pilot.pause()

    assert app.last_plan is None
    assert (d / "a.txt").read_text() == "old"
