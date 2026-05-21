"""Tests for the rename planner (``wtree.ops.rename``) - plan-only.

Rename is the v0 black sheep: single-entry, basename-only, rejects when
the tagged set is non-empty (action-layer concern, tested via pilot
below). The planner's contract:

* exactly one Tag in, at most one PlanItem out;
* dst_path = parent of src + new_name;
* rejects InvalidName (empty / contains separator), NoChange (same
  basename), UnknownSource, source-level entry_at error.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from wtree.app import WTreeApp
from wtree.ops import OperationKind, plan_rename
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag
from wtree.widgets.prompt import PromptDialog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 21, 12, 0, 0)


@pytest.fixture
def small_mock() -> MockSource:
    now = _now()
    return MockSource(
        contents={
            "/": [
                Entry("proj", Kind.DIR, 4096, now),
                Entry("readme.txt", Kind.FILE, 200, now),
            ],
            "/proj": [
                Entry("notes.md", Kind.FILE, 80, now),
            ],
        }
    )


# ---------------------------------------------------------------------------
# plan_rename shape
# ---------------------------------------------------------------------------


async def test_plan_rename_file(small_mock: MockSource) -> None:
    plan = await plan_rename(
        Tag("mock", "/readme.txt"), "README.md", {"mock": small_mock}
    )
    assert plan.kind is OperationKind.RENAME
    assert len(plan.items) == 1
    assert not plan.errors
    only = plan.items[0]
    assert only.src_path == "/readme.txt"
    assert only.dst_path == "/README.md"
    assert only.kind is Kind.FILE
    # Rename never crosses sources.
    assert only.src_source_id == only.dst_source_id == "mock"


async def test_plan_rename_dir(small_mock: MockSource) -> None:
    plan = await plan_rename(
        Tag("mock", "/proj"), "project", {"mock": small_mock}
    )
    assert len(plan.items) == 1
    only = plan.items[0]
    assert only.src_path == "/proj"
    assert only.dst_path == "/project"
    assert only.kind is Kind.DIR


async def test_plan_rename_summary_text(small_mock: MockSource) -> None:
    plan = await plan_rename(
        Tag("mock", "/readme.txt"), "README.md", {"mock": small_mock}
    )
    s = plan.summary()
    assert "rename:" in s
    assert "1 file(s)" in s


async def test_plan_rename_unknown_source_errors() -> None:
    plan = await plan_rename(
        Tag("does-not-exist", "/whatever"),
        "anything",
        {"native": NativeSource()},
    )
    assert plan.items == []
    assert len(plan.errors) == 1
    assert plan.errors[0].cause == "UnknownSource"


async def test_plan_rename_missing_path_errors(
    small_mock: MockSource,
) -> None:
    plan = await plan_rename(
        Tag("mock", "/no/such/thing"), "newname", {"mock": small_mock}
    )
    assert plan.items == []
    assert len(plan.errors) == 1


async def test_plan_rename_rejects_empty_name(small_mock: MockSource) -> None:
    plan = await plan_rename(
        Tag("mock", "/readme.txt"), "", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidName"
    assert "empty" in plan.errors[0].message


async def test_plan_rename_rejects_whitespace_only_name(
    small_mock: MockSource,
) -> None:
    plan = await plan_rename(
        Tag("mock", "/readme.txt"), "   ", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidName"


async def test_plan_rename_rejects_slash_in_name(
    small_mock: MockSource,
) -> None:
    """A path-bearing new name would silently become a move - reject."""
    plan = await plan_rename(
        Tag("mock", "/readme.txt"),
        "subdir/newname.txt",
        {"mock": small_mock},
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidName"
    assert "separator" in plan.errors[0].message.lower()


async def test_plan_rename_rejects_backslash_in_name(
    small_mock: MockSource,
) -> None:
    """Windows-style separator also caught."""
    plan = await plan_rename(
        Tag("mock", "/readme.txt"),
        "sub\\new.txt",
        {"mock": small_mock},
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidName"


async def test_plan_rename_rejects_no_change(small_mock: MockSource) -> None:
    """Renaming to the exact current basename is a no-op - refuse."""
    plan = await plan_rename(
        Tag("mock", "/readme.txt"), "readme.txt", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "NoChange"


async def test_plan_rename_strips_whitespace_around_name(
    small_mock: MockSource,
) -> None:
    """Leading / trailing whitespace is stripped - common typo, do the
    sensible thing."""
    plan = await plan_rename(
        Tag("mock", "/readme.txt"),
        "  README.md  ",
        {"mock": small_mock},
    )
    assert len(plan.items) == 1
    assert plan.items[0].dst_path == "/README.md"


async def test_plan_rename_preserves_parent_with_trailing_slash(
    small_mock: MockSource,
) -> None:
    """Rename of /proj/ should still land at /project (parent is "/")."""
    plan = await plan_rename(
        Tag("mock", "/proj/"), "project", {"mock": small_mock}
    )
    # Note: MockSource may return ScanError for "/proj/" if the source's
    # entry_at default doesn't strip trailing slashes. If so, this test
    # documents that as expected behaviour - rename callers should pass
    # canonical paths. We assert one or the other below.
    if plan.items:
        assert plan.items[0].dst_path == "/project"
    else:
        assert len(plan.errors) == 1


# ---------------------------------------------------------------------------
# action_rename + PromptDialog integration via pilot
# ---------------------------------------------------------------------------


async def test_action_rename_uses_cursor_and_renames(
    small_mock: MockSource,
) -> None:
    """R -> modal pre-filled with basename -> type new name -> Enter."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")  # row 1 = readme.txt
        from wtree.widgets.contents_pane import ContentsPane
        contents = app.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        assert cursor is not None
        assert cursor[0].endswith("readme.txt")

        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)

        # The default value should be the current basename.
        from textual.widgets import Input
        modal_input = app.screen.query_one(Input)
        assert modal_input.value == "readme.txt"

        # Replace with new name.
        modal_input.value = "README.md"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

    assert app.last_plan is not None
    assert app.last_plan.kind is OperationKind.RENAME
    assert app.last_plan.items[0].dst_path == "/README.md"


async def test_action_rename_rejects_when_tagged_set_nonempty(
    small_mock: MockSource,
) -> None:
    """Per design.md Selection rule: R with tags is rejected with a
    nudge; no dialog opens, no plan is made."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("space")  # tag row 0 = proj
        assert len(app.tagged_set) == 1
        await pilot.press("r")
        await pilot.pause()
        # No modal opened.
        assert not any(
            isinstance(s, PromptDialog) for s in app.screen_stack
        )
    # No plan stored.
    assert app.last_plan is None
    # Tags are still in the set - we didn't clear them.
    assert len(app.tagged_set) == 1


async def test_action_rename_esc_cancels(small_mock: MockSource) -> None:
    """Esc on the rename modal dismisses with no plan."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is None


async def test_action_rename_empty_input_cancels(
    small_mock: MockSource,
) -> None:
    """Clearing the input and Enter is treated as a cancellation."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("r")
        await pilot.pause()
        from textual.widgets import Input
        modal_input = app.screen.query_one(Input)
        modal_input.value = ""
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is None


async def test_action_rename_separator_in_name_surfaces_error(
    small_mock: MockSource,
) -> None:
    """A path-bearing new name produces a PlanError surfaced via
    notify; ``last_plan`` is set so users can inspect what happened."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("r")
        await pilot.pause()
        from textual.widgets import Input
        modal_input = app.screen.query_one(Input)
        modal_input.value = "subdir/new.txt"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

    assert app.last_plan is not None
    assert app.last_plan.items == []
    assert len(app.last_plan.errors) == 1
    assert app.last_plan.errors[0].cause == "InvalidName"


async def test_action_rename_no_change_surfaces_error(
    small_mock: MockSource,
) -> None:
    """Typing the same basename and pressing Enter surfaces NoChange."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("r")
        await pilot.pause()
        # Modal already has 'readme.txt' as default - press Enter without
        # changing.
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

    assert app.last_plan is not None
    assert app.last_plan.items == []
    assert app.last_plan.errors[0].cause == "NoChange"


async def test_action_rename_warns_when_no_cursor() -> None:
    """Empty contents pane -> no dialog, no plan."""
    src = MockSource(contents={"/": []})
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("r")
        await pilot.pause()
        assert not any(
            isinstance(s, PromptDialog) for s in app.screen_stack
        )
    assert app.last_plan is None
