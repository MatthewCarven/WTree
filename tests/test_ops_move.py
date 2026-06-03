"""Tests for the move planner (``wtree.ops.move``) - plan-only, no FS writes.

Move shares almost all of Copy's shape but differs in one important
way: it does NOT recurse. One ``PlanItem`` per top-level tag, because
the underlying ``shutil.move`` handles whole subtrees in a single call
(see ``wtree/ops/move.py`` docstring). These tests pin that contract
down, plus the standard planner-shape checks.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from wtree.app import WTreeApp
from wtree.ops import OperationKind, plan_move
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 21, 12, 0, 0)


@pytest.fixture
def small_mock() -> MockSource:
    """Same shape as test_ops_copy's fixture so behaviour can be
    cross-referenced.

    /
    + proj/
    | + notes.md          (80 B)
    | + src/
    |   + main.py         (1500 B)
    + readme.txt          (200 B)
    + dest/               (empty target)
    """
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
            "/proj/src": [
                Entry("main.py", Kind.FILE, 1500, now),
            ],
            "/dest": [],
        }
    )


# ---------------------------------------------------------------------------
# plan_move shape
# ---------------------------------------------------------------------------


async def test_plan_move_file_into_dir(small_mock: MockSource) -> None:
    plan = await plan_move(
        [Tag("mock", "/readme.txt")],
        Tag("mock", "/dest"),
        {"mock": small_mock},
    )
    assert plan.kind is OperationKind.MOVE
    assert len(plan.items) == 1
    only = plan.items[0]
    assert only.src_path == "/readme.txt"
    assert only.dst_path == "/dest/readme.txt"
    assert only.kind is Kind.FILE


async def test_plan_move_dir_emits_only_top_level_item(
    small_mock: MockSource,
) -> None:
    """The headline contract: a directory tag becomes ONE item, not many.

    Copy would flatten /proj into 4 items (proj + notes.md + src +
    main.py). Move emits exactly the top-level /proj item because
    shutil.move handles the subtree in one syscall.
    """
    plan = await plan_move(
        [Tag("mock", "/proj")],
        Tag("mock", "/dest"),
        {"mock": small_mock},
    )
    assert len(plan.items) == 1
    only = plan.items[0]
    assert only.src_path == "/proj"
    assert only.dst_path == "/dest/proj"
    assert only.kind is Kind.DIR


async def test_plan_move_mixed_tags(small_mock: MockSource) -> None:
    plan = await plan_move(
        [Tag("mock", "/proj"), Tag("mock", "/readme.txt")],
        Tag("mock", "/dest"),
        {"mock": small_mock},
    )
    assert len(plan.items) == 2
    dst_paths = sorted(i.dst_path for i in plan.items)
    assert dst_paths == ["/dest/proj", "/dest/readme.txt"]


async def test_plan_move_summary_text(small_mock: MockSource) -> None:
    plan = await plan_move(
        [Tag("mock", "/proj"), Tag("mock", "/readme.txt")],
        Tag("mock", "/dest"),
        {"mock": small_mock},
    )
    s = plan.summary()
    assert "move:" in s
    # Top-level counting: 1 file + 1 dir.
    assert "1 file(s)" in s
    assert "1 dir(s)" in s


async def test_plan_move_unknown_source_errors() -> None:
    plan = await plan_move(
        [Tag("does-not-exist", "/whatever")],
        Tag("native", "/dest"),
        {"native": NativeSource()},
    )
    assert plan.items == []
    assert len(plan.errors) == 1
    assert plan.errors[0].cause == "UnknownSource"


async def test_plan_move_missing_path_errors(small_mock: MockSource) -> None:
    plan = await plan_move(
        [Tag("mock", "/no/such/thing")],
        Tag("mock", "/dest"),
        {"mock": small_mock},
    )
    assert plan.items == []
    assert len(plan.errors) == 1


async def test_plan_move_empty_tag_list(small_mock: MockSource) -> None:
    plan = await plan_move([], Tag("mock", "/dest"), {"mock": small_mock})
    assert plan.is_empty


async def test_plan_move_cross_source_dst_paths(
    small_mock: MockSource,
) -> None:
    """The planner doesn't care about source pairings - only the
    executor decides what's implementable. A native -> archive plan
    is still well-formed."""
    other = MockSource(contents={"/zip-root": []}, source_id="zip:demo")
    plan = await plan_move(
        [Tag("mock", "/readme.txt")],
        Tag("zip:demo", "/zip-root"),
        {"mock": small_mock, "zip:demo": other},
    )
    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.src_source_id == "mock"
    assert item.dst_source_id == "zip:demo"
    assert item.dst_path == "/zip-root/readme.txt"


async def test_plan_move_rejects_unrooted_source(
    small_mock: MockSource,
) -> None:
    """Tagging the source root (e.g. ``"/"``) produces a plan error
    rather than a garbage destination. The error can come from either
    layer: the source-level ``entry_at`` default rejects roots first
    (cause ``"UnsupportedPath"``), so that path wins for MockSource;
    NativeSource overrides ``entry_at`` for roots and would instead hit
    the planner's own ``"UnrootedTag"`` guard. Either cause is fine -
    what matters is that the planner refuses to emit an item."""
    plan = await plan_move(
        [Tag("mock", "/")], Tag("mock", "/dest"), {"mock": small_mock}
    )
    assert plan.items == []
    assert len(plan.errors) == 1
    assert plan.errors[0].cause in {"UnrootedTag", "UnsupportedPath"}


# ---------------------------------------------------------------------------
# action_move + PromptDialog integration via pilot
# ---------------------------------------------------------------------------


async def test_action_move_uses_cursor_when_no_tags(
    small_mock: MockSource,
) -> None:
    """M -> modal -> Enter builds a plan from the cursor entry."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("down")  # row 2 = readme.txt
        from wtree.widgets.contents_pane import ContentsPane
        contents = app.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        assert cursor is not None
        cursor_path, cursor_kind = cursor
        assert cursor_path.endswith("readme.txt")
        assert cursor_kind is Kind.FILE
        await pilot.press("m")
        await pilot.pause()
        from wtree.widgets.prompt import PromptDialog
        assert isinstance(app.screen, PromptDialog)
        await pilot.press("enter")
        await pilot.pause()
        # Destination collides (copying/moving into the item's own dir);
        # the conflict dialog appears - overwrite-all keeps every item.
        from wtree.widgets.conflict import ConflictDialog
        assert isinstance(app.screen, ConflictDialog)
        await pilot.press("O")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is not None
    assert app.last_plan.kind is OperationKind.MOVE
    assert app.last_plan.file_count == 1
    assert app.last_plan.dir_count == 0
    assert app.last_plan.items[0].dst_path == "/readme.txt"


async def test_action_move_uses_tagged_set_when_present(
    small_mock: MockSource,
) -> None:
    """M with tags + modal -> plan reflects all tagged entries, top-level."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("space")  # tag row 0 = proj (dir)
        await pilot.press("down")
        await pilot.press("space")  # tag row 1 = dest (dir)
        assert len(app.tagged_set) == 2
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # Destination collides (copying/moving into the item's own dir);
        # the conflict dialog appears - overwrite-all keeps every item.
        from wtree.widgets.conflict import ConflictDialog
        assert isinstance(app.screen, ConflictDialog)
        await pilot.press("O")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
    plan = app.last_plan
    assert plan is not None
    assert plan.kind is OperationKind.MOVE
    # Top-level only - 2 tagged dirs -> 2 items, NOT flattened.
    assert len(plan.items) == 2
    assert plan.dir_count == 2
    assert plan.file_count == 0


async def test_action_move_cancelled_by_esc_leaves_last_plan_alone(
    small_mock: MockSource,
) -> None:
    """Esc on the move modal dismisses without clobbering last_plan."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is None


async def test_action_move_warns_when_nothing_selectable() -> None:
    """Empty contents pane + empty tagged set -> no plan, no modal opens."""
    src = MockSource(contents={"/": []})
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("m")
        await pilot.pause()
        from wtree.widgets.prompt import PromptDialog
        assert not any(
            isinstance(s, PromptDialog) for s in app.screen_stack
        )
    assert app.last_plan is None
