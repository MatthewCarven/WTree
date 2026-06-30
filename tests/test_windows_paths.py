"""Windows-native (backslash) tag paths through the planners.

Born from a real field failure (2026-06-11, the op log's first catch):
copying a tagged folder produced ``0 ok 105 failed`` — every dst was
``dest/`` + the ENTIRE backslashed source path, because the planners'
``posixpath``-only path math treats ``"C:\\a\\b"`` as one giant
basename. Windows then refused the mid-path ``C:`` with WinError 123
(and ``os.makedirs`` had already created the destination folder itself
before failing — the "it makes the folder but copies nothing" symptom).

These tests feed native-backslash tag paths (exactly what the Windows
tree pane produces) through plan_copy / plan_move / plan_rename and pin
that the destinations come out clean POSIX. Pure string logic, so the
pins run fine on a POSIX host.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from wtree.ops.copy import plan_copy, _basename as _copy_basename
from wtree.ops.move import plan_move, _basename as _move_basename
from wtree.ops.rename import plan_rename
from wtree.sources.base import Entry, Kind, ScanError
from wtree.sources.mock import MockSource
from wtree.tagged_set import Tag

_NOW = datetime(2026, 6, 11, 12, 0, 0)

WIN_DIR = "C:\\Users\\m\\Desktop\\-=proj=-"
WIN_FILE = WIN_DIR + "\\a.txt"
DEST = "C:/Users/m/Desktop/2"


class _WinMock(MockSource):
    """MockSource + scripted ``entry_at`` (mirrors NativeSource's direct
    lstat — the base-class parent-scan default can't classify a
    backslash path, which is itself Windows-real behaviour we don't
    want colouring these planner pins)."""

    def __init__(self, contents, entries):
        super().__init__(contents=contents)
        self._entries = dict(entries)

    async def entry_at(self, path):
        if path in self._entries:
            return self._entries[path]
        return ScanError(path=path, message="not scripted", cause="Missing")


def _win_mock() -> _WinMock:
    return _WinMock(
        contents={
            WIN_DIR: [
                Entry("a.txt", Kind.FILE, 5, _NOW),
                Entry("sub", Kind.DIR, 0, _NOW),
            ],
            # _walk_from joins children with posixpath -> mixed-separator
            # scan keys, exactly what the real walk produces on Windows.
            WIN_DIR + "/sub": [Entry("b.txt", Kind.FILE, 7, _NOW)],
            "C:/": [],
        },
        entries={
            WIN_DIR: Entry("-=proj=-", Kind.DIR, 0, _NOW),
            WIN_FILE: Entry("a.txt", Kind.FILE, 5, _NOW),
            "C:/": Entry("C:/", Kind.DIR, 0, _NOW),
        },
    )


def _no_midpath_colon(path: str) -> bool:
    """A drive colon is only legal at index 1 (``C:``)."""
    return ":" not in path[2:]


# ---------------------------------------------------------------------------
# plan_copy
# ---------------------------------------------------------------------------


async def test_plan_copy_backslash_tag_builds_clean_posix_dsts() -> None:
    mock = _win_mock()
    plan = await plan_copy(
        [Tag(source_id="mock", path=WIN_DIR)],
        Tag(source_id="mock", path=DEST),
        {"mock": mock},
    )
    assert not plan.errors
    dsts = {i.dst_path for i in plan.items}
    assert dsts == {
        f"{DEST}/-=proj=-",
        f"{DEST}/-=proj=-/a.txt",
        f"{DEST}/-=proj=-/sub",
        f"{DEST}/-=proj=-/sub/b.txt",
    }
    assert all(_no_midpath_colon(d) for d in dsts)
    # Sources stay exactly as tagged - the fix touches dst math only.
    assert plan.items[0].src_path == WIN_DIR


async def test_plan_copy_drive_root_tag_is_skipped_not_garbage() -> None:
    """Tagging a bare drive ("C:/") must not build ``dest/C:``."""
    mock = _win_mock()
    plan = await plan_copy(
        [Tag(source_id="mock", path="C:/")],
        Tag(source_id="mock", path=DEST),
        {"mock": mock},
    )
    assert plan.items == []


# ---------------------------------------------------------------------------
# plan_move
# ---------------------------------------------------------------------------


async def test_plan_move_backslash_tag_builds_clean_posix_dst() -> None:
    mock = _win_mock()
    plan = await plan_move(
        [Tag(source_id="mock", path=WIN_DIR)],
        Tag(source_id="mock", path=DEST),
        {"mock": mock},
    )
    assert not plan.errors
    assert [i.dst_path for i in plan.items] == [f"{DEST}/-=proj=-"]
    assert plan.items[0].src_path == WIN_DIR


async def test_plan_move_drive_root_tag_errors_unrooted() -> None:
    mock = _win_mock()
    plan = await plan_move(
        [Tag(source_id="mock", path="C:/")],
        Tag(source_id="mock", path=DEST),
        {"mock": mock},
    )
    assert plan.items == []
    assert plan.errors and plan.errors[0].cause == "UnrootedTag"


# ---------------------------------------------------------------------------
# plan_rename
# ---------------------------------------------------------------------------


async def test_plan_rename_backslash_tag_keeps_parent_dir() -> None:
    """Pre-fix, posixpath.dirname("C:\\...\\a.txt") was "" and the rename
    target degraded to a bare CWD-relative name."""
    mock = _win_mock()
    plan = await plan_rename(
        Tag(source_id="mock", path=WIN_FILE), "c.txt", {"mock": mock}
    )
    assert not plan.errors
    assert [i.dst_path for i in plan.items] == [
        "C:/Users/m/Desktop/-=proj=-/c.txt"
    ]


async def test_plan_rename_backslash_tag_detects_no_change() -> None:
    """The NoChange guard needs the real basename, not the whole path."""
    mock = _win_mock()
    plan = await plan_rename(
        Tag(source_id="mock", path=WIN_FILE), "a.txt", {"mock": mock}
    )
    assert plan.items == []
    assert plan.errors and plan.errors[0].cause == "NoChange"


# ---------------------------------------------------------------------------
# _basename drive-anchor guard units
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("helper", [_copy_basename, _move_basename])
def test_basename_drive_anchor_maps_to_empty(helper) -> None:
    assert helper("C:") == ""
    assert helper("C:/") == ""
    assert helper("C:/x") == "x"
    assert helper("/foo/bar/") == "bar"


# ---------------------------------------------------------------------------
# Boundary-layer normalisation (2026-06-30, Session 3): paths born POSIX,
# displayed native. POSIX-runnable via the explicit-sep helpers.
# ---------------------------------------------------------------------------

from wtree.ops.base import (  # noqa: E402
    ItemResult,
    ItemStatus,
    OperationKind,
    OperationResult,
    Plan,
    PlanItem,
    to_native,
    to_posix,
)


def test_to_posix_flips_backslashes() -> None:
    assert to_posix("C:\\Users\\m\\proj") == "C:/Users/m/proj"
    assert to_posix("C:/already/posix") == "C:/already/posix"


def test_to_native_flips_on_windows_sep() -> None:
    assert to_native("C:/Users/m/proj", sep="\\") == "C:\\Users\\m\\proj"


def test_to_native_noop_on_posix_sep() -> None:
    assert to_native("C:/Users/m/proj", sep="/") == "C:/Users/m/proj"


def test_to_posix_to_native_round_trip_windows() -> None:
    native = "C:\\Users\\m\\proj\\a.txt"
    assert to_native(to_posix(native), sep="\\") == native


def _pi(src: str, dst: str, kind: Kind = Kind.FILE) -> PlanItem:
    return PlanItem(
        src_source_id="native",
        src_path=src,
        dst_source_id="native",
        dst_path=dst,
        kind=kind,
        size=0,
    )


def _result_for(kind: OperationKind, items: list[ItemResult]) -> OperationResult:
    plan = Plan(kind=kind, items=[r.item for r in items])
    return OperationResult(plan=plan, items=items)


def test_touched_paths_stay_posix_so_they_match_tree_nodes() -> None:
    """The bug this closes: tree node ``data`` is POSIX-flavoured (born
    POSIX), but ``touched_paths`` used native ``os.path.dirname`` -> on
    Windows the COPY destination parent came back backslashed and never
    matched its tree node, so the destination folder didn't auto-refresh
    after the copy. ``posixpath.dirname`` keeps the key POSIX, matching."""
    res = _result_for(
        OperationKind.COPY,
        [ItemResult(item=_pi("C:/s/a.txt", "C:/d/sub/a.txt"), status=ItemStatus.SUCCESS)],
    )
    touched = res.touched_paths
    assert "C:/d/sub" in touched
    assert all("\\" not in p for p in touched)


def test_touched_paths_move_reports_both_parents_posix() -> None:
    res = _result_for(
        OperationKind.MOVE,
        [ItemResult(item=_pi("C:/s/x", "C:/d/x", kind=Kind.DIR), status=ItemStatus.SUCCESS)],
    )
    assert res.touched_paths == {"C:/s", "C:/d"}
