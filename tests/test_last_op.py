"""Tests for the Ctrl+O "last operation" viewer (2026-06-28, Session 2).

Pure-render coverage of :func:`render_last_op` (default = non-success only,
``a`` reveals all, all-succeeded message, empty plan, Delete arrow collapse)
plus pilot coverage of the app wiring (flash when nothing has run; Ctrl+O
opens the viewer; ``a`` toggles; Esc closes).
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.ops.base import (
    ItemResult,
    ItemStatus,
    Kind,
    OperationKind,
    OperationResult,
    Plan,
    PlanItem,
)
from wtree.widgets.last_op import OperationResultScreen, render_last_op


def _item(src: str, dst: str, kind: Kind = Kind.FILE) -> PlanItem:
    return PlanItem(
        src_source_id="native",
        src_path=src,
        dst_source_id="native",
        dst_path=dst,
        kind=kind,
        size=0,
    )


def _result(*items: ItemResult, kind: OperationKind = OperationKind.COPY) -> OperationResult:
    plan = Plan(kind=kind, items=[r.item for r in items])
    return OperationResult(plan=plan, items=list(items))


# ---------------------------------------------------------------------------
# render_last_op (pure)
# ---------------------------------------------------------------------------


def test_render_default_shows_failures_not_successes() -> None:
    res = _result(
        ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS),
        ItemResult(item=_item("/s/b", "/d/b"), status=ItemStatus.FAILED, message="nope"),
        ItemResult(item=_item("/s/c", "/d/c"), status=ItemStatus.SKIPPED, message="cancelled"),
    )
    text = render_last_op(res).plain
    assert "FAILED" in text and "/s/b -> /d/b: nope" in text
    assert "SKIPPED" in text and "/s/c -> /d/c: cancelled" in text
    # Success hidden by default; a note says how many.
    assert "/s/a -> /d/a" not in text
    assert "1 succeeded item(s) hidden" in text


def test_render_show_all_includes_successes() -> None:
    res = _result(
        ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS),
        ItemResult(item=_item("/s/b", "/d/b"), status=ItemStatus.FAILED, message="nope"),
    )
    text = render_last_op(res, show_all=True).plain
    assert "SUCCESS" in text and "/s/a -> /d/a" in text
    assert "FAILED" in text and "/s/b -> /d/b: nope" in text
    assert "hidden" not in text


def test_render_all_succeeded_default_view() -> None:
    res = _result(
        ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS),
        ItemResult(item=_item("/s/b", "/d/b"), status=ItemStatus.SUCCESS),
    )
    text = render_last_op(res).plain
    assert "All 2 item(s) succeeded." in text
    assert "Press  a" in text


def test_render_empty_plan() -> None:
    assert "empty plan" in render_last_op(_result()).plain


def test_render_delete_arrow_collapses() -> None:
    res = _result(
        ItemResult(
            item=_item("/s/gone", "/s/gone"),
            status=ItemStatus.FAILED,
            message="boom",
        ),
        kind=OperationKind.DELETE,
    )
    text = render_last_op(res).plain
    assert "/s/gone: boom" in text
    assert "->" not in text


# ---------------------------------------------------------------------------
# App wiring (pilot)
# ---------------------------------------------------------------------------


async def test_ctrl_o_flashes_when_no_result(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.last_result is None
        await pilot.press("ctrl+o")
        await pilot.pause()
        # No viewer pushed - still on the main screen.
        assert not isinstance(app.screen, OperationResultScreen)


async def test_ctrl_o_opens_viewer_toggle_and_close(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.last_result = _result(
            ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS),
            ItemResult(item=_item("/s/b", "/d/b"), status=ItemStatus.FAILED, message="x"),
        )
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert isinstance(app.screen, OperationResultScreen)
        screen = app.screen
        assert screen._show_all is False
        await pilot.press("a")
        await pilot.pause()
        assert screen._show_all is True
        await pilot.press("a")
        await pilot.pause()
        assert screen._show_all is False
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, OperationResultScreen)


async def test_ctrl_o_no_double_stack(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.last_result = _result(
            ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.FAILED, message="x"),
        )
        await pilot.press("ctrl+o")
        await pilot.pause()
        await pilot.press("ctrl+o")
        await pilot.pause()
        viewers = [s for s in app.screen_stack if isinstance(s, OperationResultScreen)]
        assert len(viewers) == 1
