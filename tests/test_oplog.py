"""Operation log (``wtree/oplog.py``) — units + app integration.

Born from a real field report (2026-06-11): a Copy ended "done with
errors" and the toast faded before the per-item failures could be read.
The log is the durable record; these tests pin its format, its bounded
growth, its never-raise contract, and the app-side wiring (write on
every plan completion; done-with-errors toast names the path).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.widgets import Input

from wtree import oplog
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
from wtree.widgets.prompt import PromptDialog

_NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def _item(src: str, dst: str, kind: Kind = Kind.FILE) -> PlanItem:
    return PlanItem(
        src_source_id="native",
        src_path=src,
        dst_source_id="native",
        dst_path=dst,
        kind=kind,
        size=5,
    )


def _result(*items: ItemResult, kind: OperationKind = OperationKind.COPY) -> OperationResult:
    plan = Plan(kind=kind, items=[r.item for r in items])
    return OperationResult(plan=plan, items=list(items))


# ---------------------------------------------------------------------------
# format_result units (pure - no filesystem)
# ---------------------------------------------------------------------------


def test_format_header_carries_timestamp_and_summary() -> None:
    res = _result(ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS))
    text = oplog.format_result(res, now=_NOW)
    assert text.startswith("[2026-06-11 12:00:00 UTC] ")
    assert res.summary() in text


def test_format_success_items_get_no_detail_lines() -> None:
    """Quiet on success - a clean copy is exactly one line."""
    res = _result(
        ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS),
        ItemResult(item=_item("/s/b", "/d/b"), status=ItemStatus.SUCCESS),
    )
    text = oplog.format_result(res, now=_NOW)
    assert text.count("\n") == 1  # header + trailing newline only
    assert "/s/a" not in text


def test_format_failed_and_skipped_lines() -> None:
    res = _result(
        ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS),
        ItemResult(
            item=_item("/s/b", "/d/b"),
            status=ItemStatus.FAILED,
            message="PermissionError: nope",
        ),
        ItemResult(
            item=_item("/s/c", "/d/c"),
            status=ItemStatus.SKIPPED,
            message="cancelled",
        ),
    )
    text = oplog.format_result(res, now=_NOW)
    assert "FAILED  /s/b -> /d/b: PermissionError: nope" in text
    assert "SKIPPED /s/c -> /d/c: cancelled" in text


def test_format_delete_sentinel_collapses_arrow() -> None:
    """Delete items mirror src into dst; the arrow would be noise."""
    res = _result(
        ItemResult(
            item=_item("/s/gone", "/s/gone"),
            status=ItemStatus.FAILED,
            message="boom",
        ),
        kind=OperationKind.DELETE,
    )
    text = oplog.format_result(res, now=_NOW)
    assert "FAILED  /s/gone: boom" in text
    assert "->" not in text


def test_format_empty_message_gets_placeholder() -> None:
    res = _result(ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.FAILED))
    assert "(no message)" in oplog.format_result(res, now=_NOW)


# ---------------------------------------------------------------------------
# write_result units (tmp_path filesystem)
# ---------------------------------------------------------------------------


def test_write_appends_chronologically(tmp_path: Path) -> None:
    log = tmp_path / "operations.log"
    first = _result(ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS))
    second = _result(
        ItemResult(item=_item("/s/b", "/d/b"), status=ItemStatus.FAILED, message="x")
    )
    assert oplog.write_result(first, log, now=_NOW) == log
    assert oplog.write_result(second, log, now=_NOW) == log
    body = log.read_text(encoding="utf-8")
    assert body.index("/d/b") > body.index("copy done")  # second after first
    assert body.count("[2026-06-11") == 2


def test_write_creates_parent_directory(tmp_path: Path) -> None:
    log = tmp_path / "deep" / "nested" / "operations.log"
    res = _result(ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS))
    assert oplog.write_result(res, log, now=_NOW) == log
    assert log.exists()


def test_write_rotates_oversize_log(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(oplog, "MAX_LOG_BYTES", 64)
    log = tmp_path / "operations.log"
    log.write_text("x" * 100, encoding="utf-8")
    res = _result(ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS))
    assert oplog.write_result(res, log, now=_NOW) == log
    rotated = tmp_path / "operations.log.1"
    assert rotated.read_text(encoding="utf-8") == "x" * 100
    assert "copy done" in log.read_text(encoding="utf-8")
    # A second rotation overwrites .1 rather than growing generations.
    log.write_text("y" * 100, encoding="utf-8")
    assert oplog.write_result(res, log, now=_NOW) == log
    assert rotated.read_text(encoding="utf-8") == "y" * 100


def test_write_never_raises_returns_none(tmp_path: Path) -> None:
    """Parent 'directory' is actually a file -> mkdir fails -> None."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    log = blocker / "operations.log"
    res = _result(ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.SUCCESS))
    assert oplog.write_result(res, log, now=_NOW) is None


# ---------------------------------------------------------------------------
# App integration (pilot) - the _on_plan_complete wiring
# ---------------------------------------------------------------------------


async def _copy_via_modal(pilot, app: WTreeApp, destination: str) -> None:
    await pilot.press("tab")  # focus contents pane
    await pilot.pause()
    await pilot.press("c")
    await pilot.pause()
    assert isinstance(app.screen, PromptDialog)
    app.screen.query_one(Input).value = destination
    await pilot.press("enter")
    await pilot.pause()
    assert app.op_queue is not None
    await app.op_queue.wait_until_idle()


async def test_e2e_success_writes_oplog(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "oplog" / "operations.log"
    monkeypatch.setattr(oplog, "OPLOG_PATH", log)
    src = tmp_path / "src"
    src.mkdir()
    (src / "hello.txt").write_text("greetings")
    dst = tmp_path / "dst"
    dst.mkdir()

    app = WTreeApp(root_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _copy_via_modal(pilot, app, str(dst))
        await pilot.pause()

    body = log.read_text(encoding="utf-8")
    assert "copy done: 1 ok" in body
    assert "FAILED" not in body


async def test_e2e_failure_logs_detail_and_toast_names_path(
    tmp_path: Path, monkeypatch
) -> None:
    """Force the executor to fail and check both durable + toast surfaces."""
    log = tmp_path / "oplog" / "operations.log"
    monkeypatch.setattr(oplog, "OPLOG_PATH", log)

    def _explode(item, src, dst, bytes_progress):
        raise OSError("disk exploded")

    # The app path always supplies bytes_progress, so FILE copies route
    # through _chunked_copy - patch it to fail deterministically.
    monkeypatch.setattr("wtree.ops.execute._chunked_copy", _explode)

    src = tmp_path / "src"
    src.mkdir()
    (src / "hello.txt").write_text("greetings")
    dst = tmp_path / "dst"
    dst.mkdir()

    toasts: list[tuple[str, str]] = []

    app = WTreeApp(root_path=str(src))
    real_notify = app.notify

    def _capture(message, **kwargs):
        toasts.append((kwargs.get("title", ""), str(message)))
        return real_notify(message, **kwargs)

    app.notify = _capture  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        await _copy_via_modal(pilot, app, str(dst))
        await pilot.pause()

    body = log.read_text(encoding="utf-8")
    assert "FAILED" in body and "disk exploded" in body
    done_toasts = [t for t in toasts if "done with errors" in t[0]]
    assert done_toasts, f"no error toast seen in {toasts}"
    assert str(log) in done_toasts[0][1]


# ---------------------------------------------------------------------------
# Per-operation detail cap (2026-06-11 follow-up: a mass failure must not
# write tens of MB in one entry - rotation only bounds the file BETWEEN writes)
# ---------------------------------------------------------------------------


def test_format_caps_detail_lines(monkeypatch) -> None:
    monkeypatch.setattr(oplog, "MAX_DETAIL_LINES", 3)
    failures = [
        ItemResult(
            item=_item(f"/s/{n}", f"/d/{n}"),
            status=ItemStatus.FAILED,
            message="boom",
        )
        for n in range(10)
    ]
    text = oplog.format_result(_result(*failures), now=_NOW)
    assert text.count("FAILED") == 3
    assert "and 7 more non-success item(s)" in text


def test_format_no_overflow_line_under_cap() -> None:
    res = _result(
        ItemResult(item=_item("/s/a", "/d/a"), status=ItemStatus.FAILED, message="x")
    )
    assert "more non-success" not in oplog.format_result(res, now=_NOW)
