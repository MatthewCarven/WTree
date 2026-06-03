"""Tests for the copy planner (``wtree.ops.copy``) - plan-only, no FS writes.

The planner is the heart of every operation; tests focus on:

* the **shape** of the produced :class:`Plan` (right kind, right paths,
  right counts, right errors)
* the **Selection-rule integration** via the live ``WTreeApp.action_copy``
  binding + the destination modal (pilot tests).
* **walk_tags** independently, since it's the part future planners (move,
  delete) will reuse.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from wtree.app import WTreeApp
from wtree.ops import OperationKind, plan_copy, walk_tags
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
    """A two-level mock filesystem:

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
# walk_tags
# ---------------------------------------------------------------------------


async def test_walk_tags_single_file(small_mock: MockSource) -> None:
    walk = await walk_tags([Tag("mock", "/readme.txt")], {"mock": small_mock})
    assert walk.file_count == 1
    assert walk.dir_count == 0
    assert walk.total_bytes == 200
    assert not walk.errors


async def test_walk_tags_single_dir_recurses(small_mock: MockSource) -> None:
    walk = await walk_tags([Tag("mock", "/proj")], {"mock": small_mock})
    assert walk.dir_count == 2
    assert walk.file_count == 2
    assert walk.total_bytes == 80 + 1500
    paths = sorted(e.path for e in walk.entries)
    assert paths == ["/proj", "/proj/notes.md", "/proj/src", "/proj/src/main.py"]


async def test_walk_tags_unknown_source_id() -> None:
    walk = await walk_tags(
        [Tag("does-not-exist", "/whatever")], {"native": NativeSource()}
    )
    assert len(walk.errors) == 1
    assert walk.errors[0].cause == "UnknownSource"
    assert not walk.entries


async def test_walk_tags_missing_path_in_mock(small_mock: MockSource) -> None:
    walk = await walk_tags(
        [Tag("mock", "/no/such/thing")], {"mock": small_mock}
    )
    assert len(walk.errors) == 1
    assert not walk.entries


# ---------------------------------------------------------------------------
# plan_copy
# ---------------------------------------------------------------------------


async def test_plan_copy_file_into_dir(small_mock: MockSource) -> None:
    plan = await plan_copy(
        [Tag("mock", "/readme.txt")],
        Tag("mock", "/dest"),
        {"mock": small_mock},
    )
    assert plan.kind is OperationKind.COPY
    assert len(plan.items) == 1
    only = plan.items[0]
    assert only.src_path == "/readme.txt"
    assert only.dst_path == "/dest/readme.txt"
    assert only.kind is Kind.FILE


async def test_plan_copy_dir_preserves_subtree(small_mock: MockSource) -> None:
    plan = await plan_copy(
        [Tag("mock", "/proj")], Tag("mock", "/dest"), {"mock": small_mock}
    )
    dst_paths = sorted(i.dst_path for i in plan.items)
    assert dst_paths == [
        "/dest/proj",
        "/dest/proj/notes.md",
        "/dest/proj/src",
        "/dest/proj/src/main.py",
    ]


async def test_plan_copy_summary_text(small_mock: MockSource) -> None:
    plan = await plan_copy(
        [Tag("mock", "/proj"), Tag("mock", "/readme.txt")],
        Tag("mock", "/dest"),
        {"mock": small_mock},
    )
    s = plan.summary()
    assert "copy:" in s
    assert "3 file(s)" in s
    assert "2 dir(s)" in s


async def test_plan_copy_errors_propagated_from_walk(small_mock: MockSource) -> None:
    plan = await plan_copy(
        [Tag("does-not-exist", "/x")],
        Tag("mock", "/dest"),
        {"mock": small_mock},
    )
    assert plan.items == []
    assert len(plan.errors) == 1


async def test_plan_copy_empty_tag_list(small_mock: MockSource) -> None:
    plan = await plan_copy([], Tag("mock", "/dest"), {"mock": small_mock})
    assert plan.is_empty


async def test_plan_copy_cross_source_dst_paths(small_mock: MockSource) -> None:
    other = MockSource(contents={"/zip-root": []}, source_id="zip:demo")
    plan = await plan_copy(
        [Tag("mock", "/readme.txt")],
        Tag("zip:demo", "/zip-root"),
        {"mock": small_mock, "zip:demo": other},
    )
    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.src_source_id == "mock"
    assert item.dst_source_id == "zip:demo"
    assert item.dst_path == "/zip-root/readme.txt"


# ---------------------------------------------------------------------------
# action_copy + PromptDialog integration via pilot
# ---------------------------------------------------------------------------


async def test_action_copy_uses_cursor_when_no_tags(small_mock: MockSource) -> None:
    """C -> modal -> Enter (accept default) builds a plan from the cursor."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("down")
        from wtree.widgets.contents_pane import ContentsPane
        contents = app.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        assert cursor is not None
        cursor_path, cursor_kind = cursor
        assert cursor_path.endswith("readme.txt")
        assert cursor_kind is Kind.FILE
        await pilot.press("c")
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
    assert app.last_plan.kind is OperationKind.COPY
    assert app.last_plan.file_count == 1
    assert app.last_plan.dir_count == 0
    assert app.last_plan.items[0].dst_path == "/readme.txt"


async def test_action_copy_uses_tagged_set_when_present(small_mock: MockSource) -> None:
    """C with a non-empty tagged set + modal -> plan reflects all tags."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("space")
        await pilot.press("down")
        await pilot.press("space")
        assert len(app.tagged_set) == 2
        await pilot.press("c")
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
    assert plan.dir_count == 3
    assert plan.file_count == 2


async def test_action_copy_typed_destination_overrides_default(
    small_mock: MockSource,
) -> None:
    """Typing into the modal sets the destination on the resulting plan."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("end")
        await pilot.press("d", "e", "s", "t")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is not None
    assert app.last_plan.items[0].dst_path == "/dest/readme.txt"


async def test_action_copy_cancelled_by_esc_leaves_last_plan_alone(
    small_mock: MockSource,
) -> None:
    """Esc on the modal dismisses without clobbering last_plan."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is None


async def test_action_copy_empty_destination_is_cancellation(
    small_mock: MockSource,
) -> None:
    """Clearing the modal and pressing Enter is treated as a cancel."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("end")
        for _ in range(5):
            await pilot.press("backspace")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is None


async def test_action_copy_warns_when_nothing_selectable() -> None:
    """Empty contents pane + empty tagged set -> no plan, no modal opens."""
    src = MockSource(contents={"/": []})
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("c")
        await pilot.pause()
        from wtree.widgets.prompt import PromptDialog
        assert not any(
            isinstance(s, PromptDialog) for s in app.screen_stack
        )
    assert app.last_plan is None
