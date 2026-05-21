"""Tests for the delete planner (``wtree.ops.delete``) - plan-only.

Like Move, Delete emits one PlanItem per top-level tag (no flatten,
because the executor uses ``shutil.rmtree`` for dirs). Unlike Move
there is no destination, so the planner signature is
``plan_delete(tags, registry)`` rather than ``plan_*(tags, dest, registry)``.

Pilot tests exercise the full ``action_delete`` chain through the new
``ConfirmDialog`` - Selection rule, confirm, enqueue.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from wtree.app import WTreeApp
from wtree.ops import OperationKind, plan_delete
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag
from wtree.widgets.confirm import ConfirmDialog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 21, 12, 0, 0)


@pytest.fixture
def small_mock() -> MockSource:
    """Same shape as test_ops_copy / test_ops_move - cross-reference."""
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
# plan_delete shape
# ---------------------------------------------------------------------------


async def test_plan_delete_single_file(small_mock: MockSource) -> None:
    plan = await plan_delete(
        [Tag("mock", "/readme.txt")], {"mock": small_mock}
    )
    assert plan.kind is OperationKind.DELETE
    assert len(plan.items) == 1
    only = plan.items[0]
    assert only.src_path == "/readme.txt"
    assert only.kind is Kind.FILE
    # dst_* sentinels - mirror src_source_id, empty dst_path.
    assert only.dst_source_id == "mock"
    assert only.dst_path == ""


async def test_plan_delete_dir_emits_only_top_level_item(
    small_mock: MockSource,
) -> None:
    """Headline contract: one PlanItem per top-level tag (no flatten).

    Copy would emit 4 items for /proj. Delete emits exactly 1 because
    rmtree handles the whole subtree.
    """
    plan = await plan_delete([Tag("mock", "/proj")], {"mock": small_mock})
    assert len(plan.items) == 1
    only = plan.items[0]
    assert only.src_path == "/proj"
    assert only.kind is Kind.DIR


async def test_plan_delete_mixed_tags(small_mock: MockSource) -> None:
    plan = await plan_delete(
        [Tag("mock", "/proj"), Tag("mock", "/readme.txt")],
        {"mock": small_mock},
    )
    assert len(plan.items) == 2
    src_paths = sorted(i.src_path for i in plan.items)
    assert src_paths == ["/proj", "/readme.txt"]


async def test_plan_delete_summary_text(small_mock: MockSource) -> None:
    plan = await plan_delete(
        [Tag("mock", "/proj"), Tag("mock", "/readme.txt")],
        {"mock": small_mock},
    )
    s = plan.summary()
    assert "delete:" in s
    assert "1 file(s)" in s
    assert "1 dir(s)" in s


async def test_plan_delete_unknown_source_errors() -> None:
    plan = await plan_delete(
        [Tag("does-not-exist", "/whatever")], {"native": NativeSource()}
    )
    assert plan.items == []
    assert len(plan.errors) == 1
    assert plan.errors[0].cause == "UnknownSource"


async def test_plan_delete_missing_path_errors(
    small_mock: MockSource,
) -> None:
    plan = await plan_delete(
        [Tag("mock", "/no/such/thing")], {"mock": small_mock}
    )
    assert plan.items == []
    assert len(plan.errors) == 1


async def test_plan_delete_empty_tag_list(small_mock: MockSource) -> None:
    plan = await plan_delete([], {"mock": small_mock})
    assert plan.is_empty


async def test_plan_delete_refuses_source_root(
    small_mock: MockSource,
) -> None:
    """Tagging the source root produces an error rather than a plan
    that would wipe the whole tree. MockSource's default ``entry_at``
    rejects roots first (``UnsupportedPath``); NativeSource overrides
    ``entry_at`` for roots and would instead hit ``UnrootedTag``.
    Either cause is fine - what matters is no item gets emitted."""
    plan = await plan_delete([Tag("mock", "/")], {"mock": small_mock})
    assert plan.items == []
    assert len(plan.errors) == 1
    assert plan.errors[0].cause in {"UnrootedTag", "UnsupportedPath"}


# ---------------------------------------------------------------------------
# action_delete + ConfirmDialog integration via pilot
# ---------------------------------------------------------------------------


async def test_action_delete_uses_cursor_when_no_tags(
    small_mock: MockSource,
) -> None:
    """D -> confirm dialog -> Y builds a plan from the cursor entry."""
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
        assert cursor[0].endswith("readme.txt")
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is not None
    assert app.last_plan.kind is OperationKind.DELETE
    assert app.last_plan.file_count == 1
    assert app.last_plan.items[0].src_path == "/readme.txt"


async def test_action_delete_enter_also_confirms(
    small_mock: MockSource,
) -> None:
    """Enter on the confirm dialog accepts (alias for Y)."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is not None
    assert app.last_plan.kind is OperationKind.DELETE


async def test_action_delete_n_cancels(small_mock: MockSource) -> None:
    """N on the confirm dialog cancels - no plan."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is None


async def test_action_delete_esc_cancels(small_mock: MockSource) -> None:
    """Esc on the confirm dialog cancels - no plan."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is None


async def test_action_delete_uses_tagged_set_when_present(
    small_mock: MockSource,
) -> None:
    """D with tags + confirm -> plan reflects all tagged entries, top-level."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("space")  # tag row 0 = proj (dir)
        await pilot.press("down")
        await pilot.press("space")  # tag row 1 = dest (dir)
        assert len(app.tagged_set) == 2
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()
    plan = app.last_plan
    assert plan is not None
    assert plan.kind is OperationKind.DELETE
    assert len(plan.items) == 2
    assert plan.dir_count == 2
    assert plan.file_count == 0


async def test_action_delete_warns_when_nothing_selectable() -> None:
    """Empty contents pane + empty tagged set -> no plan, no dialog."""
    src = MockSource(contents={"/": []})
    app = WTreeApp(source=src, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("d")
        await pilot.pause()
        # No dialog should have appeared.
        assert not any(
            isinstance(s, ConfirmDialog) for s in app.screen_stack
        )
    assert app.last_plan is None
