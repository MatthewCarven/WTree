"""Tests for ``wtree.ops.execute.apply_plan``.

Uses real temp directories and ``NativeSource`` because the executor's
job is to actually move bytes - mocking that out would test nothing
useful. Each test gets its own ``tmp_path`` via pytest's fixture so the
suite stays parallel-safe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from wtree.ops import (
    ItemStatus,
    OperationKind,
    apply_plan,
    plan_copy,
)
from wtree.ops.base import PlanItem
from wtree.sources.base import Kind
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag


@pytest.fixture
def native_registry() -> dict[str, NativeSource]:
    return {"native": NativeSource()}


# ---------------------------------------------------------------------------
# Native -> native happy paths
# ---------------------------------------------------------------------------


async def test_apply_plan_copies_single_file(
    tmp_path: Path, native_registry: dict[str, NativeSource]
) -> None:
    src_file = tmp_path / "src.txt"
    src_file.write_text("hello world")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    plan = await plan_copy(
        [Tag("native", str(src_file))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    assert result.success_count == 1
    landed = dst_dir / "src.txt"
    assert landed.exists()
    assert landed.read_text() == "hello world"


async def test_apply_plan_copies_dir_with_subtree(
    tmp_path: Path, native_registry: dict[str, NativeSource]
) -> None:
    src_dir = tmp_path / "src"
    (src_dir / "sub").mkdir(parents=True)
    (src_dir / "a.txt").write_text("aaa")
    (src_dir / "sub" / "b.txt").write_text("bbb")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    plan = await plan_copy(
        [Tag("native", str(src_dir))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    # success_count: src dir + sub dir + a.txt + b.txt = 4
    assert result.success_count == 4
    assert (dst_dir / "src" / "a.txt").read_text() == "aaa"
    assert (dst_dir / "src" / "sub" / "b.txt").read_text() == "bbb"


async def test_apply_plan_preserves_mtime(
    tmp_path: Path, native_registry: dict[str, NativeSource]
) -> None:
    """shutil.copy2 is used because XTree/MC both preserve mtime; this
    nails down the choice."""
    src_file = tmp_path / "src.txt"
    src_file.write_text("ts")
    # Set an explicit mtime well in the past.
    os.utime(src_file, (1_000_000_000, 1_000_000_000))
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    plan = await plan_copy(
        [Tag("native", str(src_file))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    await apply_plan(plan, native_registry)
    landed = dst_dir / "src.txt"
    assert int(landed.stat().st_mtime) == 1_000_000_000


async def test_apply_plan_creates_missing_destination_parent(
    tmp_path: Path, native_registry: dict[str, NativeSource]
) -> None:
    """User-typed destinations may not exist yet - the executor mkdirs
    on demand rather than failing."""
    src_file = tmp_path / "src.txt"
    src_file.write_text("x")
    dst_dir = tmp_path / "deep" / "new" / "dir"
    # dst_dir does NOT exist yet.

    plan = await plan_copy(
        [Tag("native", str(src_file))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    assert (dst_dir / "src.txt").read_text() == "x"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows symlink creation requires elevated privileges in CI",
)
async def test_apply_plan_recreates_symlink(
    tmp_path: Path, native_registry: dict[str, NativeSource]
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("via link")
    link = tmp_path / "link"
    link.symlink_to(target)
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    plan = await plan_copy(
        [Tag("native", str(link))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    landed = dst_dir / "link"
    assert landed.is_symlink()
    assert os.readlink(landed) == str(target)


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


async def test_apply_plan_missing_source_marks_failed(
    tmp_path: Path, native_registry: dict[str, NativeSource]
) -> None:
    """Source vanishes between plan and apply - one FAILED item, queue
    keeps going."""
    src_file = tmp_path / "ghost.txt"
    src_file.write_text("present")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    plan = await plan_copy(
        [Tag("native", str(src_file))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    # Delete the source *after* planning but *before* applying.
    src_file.unlink()

    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1
    assert not result.all_succeeded
    assert "FileNotFoundError" in result.items[0].message


async def test_apply_plan_cross_source_pair_fails_per_item(
    tmp_path: Path, native_registry: dict[str, NativeSource]
) -> None:
    """Manually-constructed cross-source PlanItem -> NotImplementedError
    bubbles up as a FAILED ItemResult, not a raised exception."""
    from wtree.ops.base import OperationKind, Plan

    item = PlanItem(
        src_source_id="native",
        src_path=str(tmp_path / "fake"),
        dst_source_id="zip:demo",
        dst_path="/inside.txt",
        kind=Kind.FILE,
        size=10,
    )
    plan = Plan(kind=OperationKind.COPY, items=[item])
    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1
    assert "not supported in v0" in result.items[0].message


async def test_apply_plan_progress_callback_fires_per_item(
    tmp_path: Path, native_registry: dict[str, NativeSource]
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.txt").write_text("a")
    (src_dir / "b.txt").write_text("b")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    plan = await plan_copy(
        [Tag("native", str(src_dir))],
        Tag("native", str(dst_dir)),
        native_registry,
    )

    seen: list[ItemStatus] = []

    def cb(item_result):
        seen.append(item_result.status)

    result = await apply_plan(plan, native_registry, progress=cb)
    # src dir + a.txt + b.txt = 3 items
    assert len(seen) == 3
    assert all(s is ItemStatus.SUCCESS for s in seen)
    assert result.success_count == 3


# ---------------------------------------------------------------------------
# Empty plan
# ---------------------------------------------------------------------------


async def test_apply_plan_empty(native_registry: dict[str, NativeSource]) -> None:
    from wtree.ops.base import OperationKind, Plan

    plan = Plan(kind=OperationKind.COPY)
    result = await apply_plan(plan, native_registry)
    assert result.success_count == 0
    # Empty plan trivially "all succeeded" - nothing could have failed.
    assert result.all_succeeded


# ===========================================================================
# Native -> native MOVE
# ===========================================================================


async def test_apply_move_renames_single_file(
    tmp_path, native_registry
):
    src_file = tmp_path / "src.txt"
    src_file.write_text("byebye")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    from wtree.ops import plan_move
    plan = await plan_move(
        [Tag("native", str(src_file))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    assert plan.kind is OperationKind.MOVE
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    assert result.success_count == 1
    # File now lives at dst, not src.
    assert not src_file.exists()
    landed = dst_dir / "src.txt"
    assert landed.exists()
    assert landed.read_text() == "byebye"


async def test_apply_move_renames_dir_with_subtree(
    tmp_path, native_registry
):
    """One PlanItem for the dir; subtree comes along via shutil.move."""
    src_dir = tmp_path / "src"
    (src_dir / "sub").mkdir(parents=True)
    (src_dir / "a.txt").write_text("aaa")
    (src_dir / "sub" / "b.txt").write_text("bbb")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    from wtree.ops import plan_move
    plan = await plan_move(
        [Tag("native", str(src_dir))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    # Headline contract: ONE item, not 4.
    assert len(plan.items) == 1
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    assert result.success_count == 1
    # Source dir gone, dst dir has the subtree intact.
    assert not src_dir.exists()
    assert (dst_dir / "src" / "a.txt").read_text() == "aaa"
    assert (dst_dir / "src" / "sub" / "b.txt").read_text() == "bbb"


async def test_apply_move_creates_missing_destination_parent(
    tmp_path, native_registry
):
    """Typed destinations may not exist yet - executor mkdirs parent."""
    src_file = tmp_path / "src.txt"
    src_file.write_text("x")
    dst_dir = tmp_path / "deep" / "new" / "dir"
    # dst_dir parents don't exist yet.

    from wtree.ops import plan_move
    plan = await plan_move(
        [Tag("native", str(src_file))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    assert (dst_dir / "src.txt").read_text() == "x"
    assert not src_file.exists()


async def test_apply_move_refuses_when_destination_exists(
    tmp_path, native_registry
):
    """Pre-check: if dst already exists, fail rather than silently nest
    src into it (shutil.move's default behaviour for existing dst dirs).
    """
    src_file = tmp_path / "src.txt"
    src_file.write_text("attempt")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    # Pre-create the destination file.
    (dst_dir / "src.txt").write_text("incumbent")

    from wtree.ops import plan_move
    plan = await plan_move(
        [Tag("native", str(src_file))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1
    assert "already exists" in result.items[0].message
    # Source must still exist - we refused to move it.
    assert src_file.read_text() == "attempt"
    # Destination is the incumbent, untouched.
    assert (dst_dir / "src.txt").read_text() == "incumbent"


async def test_apply_move_cross_fs_fallback_uses_copy_then_delete(
    tmp_path, native_registry, monkeypatch
):
    """When os.rename fails with EXDEV, shutil.move falls back to
    copy+delete. We force the rename failure to exercise the fallback
    deterministically (real cross-fs setup isn't portable in tests)."""
    src_file = tmp_path / "src.txt"
    src_file.write_text("crossing-fs")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    real_rename = os.rename
    seen_rename: list[tuple[str, str]] = []

    def fail_rename(s, d, *a, **k):
        seen_rename.append((str(s), str(d)))
        raise OSError(18, "Invalid cross-device link", str(s), None, str(d))

    monkeypatch.setattr(os, "rename", fail_rename)
    # shutil.move references os.rename via the os module - patching the
    # attribute on the os module is sufficient. Verify shutil sees it.
    import shutil as _shutil
    assert _shutil.os.rename is fail_rename

    from wtree.ops import plan_move
    plan = await plan_move(
        [Tag("native", str(src_file))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    result = await apply_plan(plan, native_registry)

    # Restore for any teardown bookkeeping.
    monkeypatch.setattr(os, "rename", real_rename)

    assert result.all_succeeded, (
        f"expected fallback path to succeed, got {result.items[0].message}"
    )
    assert seen_rename, "shutil.move should have tried os.rename first"
    # File lives at dst now, source is gone.
    assert not src_file.exists()
    assert (dst_dir / "src.txt").read_text() == "crossing-fs"


async def test_apply_move_missing_source_marks_failed(
    tmp_path, native_registry
):
    src_file = tmp_path / "ghost.txt"
    src_file.write_text("here for now")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    from wtree.ops import plan_move
    plan = await plan_move(
        [Tag("native", str(src_file))],
        Tag("native", str(dst_dir)),
        native_registry,
    )
    src_file.unlink()  # source vanishes between plan and apply

    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1
    assert not result.all_succeeded


async def test_apply_move_cross_source_pair_fails_per_item(
    tmp_path, native_registry
):
    """Manually-constructed cross-source MOVE PlanItem -> FAILED, same
    as the COPY case. Tests that the dispatch table treats unsupported
    move pairs identically."""
    from wtree.ops.base import Plan
    item = PlanItem(
        src_source_id="native",
        src_path=str(tmp_path / "fake"),
        dst_source_id="sftp:remote",
        dst_path="/inbox/x",
        kind=Kind.FILE,
        size=10,
    )
    plan = Plan(kind=OperationKind.MOVE, items=[item])
    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1
    assert "not supported in v0" in result.items[0].message


async def test_apply_move_progress_callback_fires_per_item(
    tmp_path, native_registry
):
    src_a = tmp_path / "a.txt"
    src_a.write_text("a")
    src_b = tmp_path / "b.txt"
    src_b.write_text("b")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    from wtree.ops import plan_move
    plan = await plan_move(
        [Tag("native", str(src_a)), Tag("native", str(src_b))],
        Tag("native", str(dst_dir)),
        native_registry,
    )

    seen: list[ItemStatus] = []
    def cb(item_result):
        seen.append(item_result.status)

    result = await apply_plan(plan, native_registry, progress=cb)
    assert len(seen) == 2  # two top-level move items
    assert all(s is ItemStatus.SUCCESS for s in seen)
    assert result.success_count == 2


# ===========================================================================
# Native DELETE
# ===========================================================================


async def test_apply_delete_removes_single_file(
    tmp_path, native_registry
):
    src_file = tmp_path / "doomed.txt"
    src_file.write_text("erase me")

    from wtree.ops import plan_delete
    plan = await plan_delete(
        [Tag("native", str(src_file))], native_registry
    )
    assert plan.kind is OperationKind.DELETE
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    assert result.success_count == 1
    assert not src_file.exists()


async def test_apply_delete_removes_dir_with_subtree(
    tmp_path, native_registry
):
    """One PlanItem deletes the whole subtree via shutil.rmtree."""
    src_dir = tmp_path / "doomed"
    (src_dir / "sub").mkdir(parents=True)
    (src_dir / "a.txt").write_text("a")
    (src_dir / "sub" / "b.txt").write_text("b")

    from wtree.ops import plan_delete
    plan = await plan_delete(
        [Tag("native", str(src_dir))], native_registry
    )
    assert len(plan.items) == 1  # headline contract
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    assert not src_dir.exists()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows symlink creation requires elevated privileges in CI",
)
async def test_apply_delete_unlinks_symlink_not_target(
    tmp_path, native_registry
):
    """Deleting a symlink removes the link; the target stays put."""
    target = tmp_path / "target.txt"
    target.write_text("keep me")
    link = tmp_path / "link"
    link.symlink_to(target)

    from wtree.ops import plan_delete
    plan = await plan_delete([Tag("native", str(link))], native_registry)
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    assert not link.exists()
    # Critical: target was NOT followed-and-removed.
    assert target.read_text() == "keep me"


async def test_apply_delete_missing_source_marks_failed(
    tmp_path, native_registry
):
    """Source vanishes between plan and apply - FAILED, queue keeps going."""
    src_file = tmp_path / "ghost.txt"
    src_file.write_text("here")

    from wtree.ops import plan_delete
    plan = await plan_delete(
        [Tag("native", str(src_file))], native_registry
    )
    src_file.unlink()  # disappear before apply

    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1
    assert not result.all_succeeded


async def test_apply_delete_cross_source_pair_fails_per_item(
    tmp_path, native_registry
):
    """A delete item with a non-native source becomes a FAILED."""
    from wtree.ops.base import Plan
    item = PlanItem(
        src_source_id="sftp:remote",
        src_path="/remote/file.txt",
        dst_source_id="sftp:remote",  # sentinel mirror for DELETE
        dst_path="",
        kind=Kind.FILE,
        size=10,
    )
    plan = Plan(kind=OperationKind.DELETE, items=[item])
    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1
    assert "not supported in v0" in result.items[0].message


async def test_apply_delete_progress_callback_fires_per_item(
    tmp_path, native_registry
):
    a = tmp_path / "a.txt"
    a.write_text("a")
    b = tmp_path / "b.txt"
    b.write_text("b")
    c = tmp_path / "c.txt"
    c.write_text("c")

    from wtree.ops import plan_delete
    plan = await plan_delete(
        [Tag("native", str(p)) for p in (a, b, c)], native_registry
    )

    seen: list[ItemStatus] = []
    def cb(item_result):
        seen.append(item_result.status)

    result = await apply_plan(plan, native_registry, progress=cb)
    assert len(seen) == 3
    assert all(s is ItemStatus.SUCCESS for s in seen)
    assert result.success_count == 3
    # All three sources removed.
    assert not a.exists() and not b.exists() and not c.exists()


# ===========================================================================
# Native RENAME
# ===========================================================================


async def test_apply_rename_renames_single_file(
    tmp_path, native_registry
):
    src_file = tmp_path / "before.txt"
    src_file.write_text("contents stay")

    from wtree.ops import plan_rename
    plan = await plan_rename(
        Tag("native", str(src_file)), "after.txt", native_registry
    )
    assert plan.kind is OperationKind.RENAME
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    assert result.success_count == 1
    assert not src_file.exists()
    landed = tmp_path / "after.txt"
    assert landed.read_text() == "contents stay"


async def test_apply_rename_renames_directory(
    tmp_path, native_registry
):
    """Rename a directory - whole subtree moves with it (os.rename
    is atomic on the inode)."""
    src_dir = tmp_path / "old-name"
    (src_dir / "inside.txt").parent.mkdir(parents=True)
    (src_dir / "inside.txt").write_text("child file")

    from wtree.ops import plan_rename
    plan = await plan_rename(
        Tag("native", str(src_dir)), "new-name", native_registry
    )
    result = await apply_plan(plan, native_registry)
    assert result.all_succeeded
    assert not src_dir.exists()
    assert (tmp_path / "new-name" / "inside.txt").read_text() == "child file"


async def test_apply_rename_refuses_when_destination_exists(
    tmp_path, native_registry
):
    """Pre-check refuses to silently clobber. Matches Move's behaviour."""
    src_file = tmp_path / "src.txt"
    src_file.write_text("source")
    incumbent = tmp_path / "taken.txt"
    incumbent.write_text("incumbent")

    from wtree.ops import plan_rename
    plan = await plan_rename(
        Tag("native", str(src_file)), "taken.txt", native_registry
    )
    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1
    assert "already exists" in result.items[0].message
    # Both files survive untouched.
    assert src_file.read_text() == "source"
    assert incumbent.read_text() == "incumbent"


async def test_apply_rename_missing_source_marks_failed(
    tmp_path, native_registry
):
    """Source vanishes between plan and apply -> FAILED."""
    src_file = tmp_path / "ghost.txt"
    src_file.write_text("here for now")

    from wtree.ops import plan_rename
    plan = await plan_rename(
        Tag("native", str(src_file)), "ghost.bak", native_registry
    )
    src_file.unlink()

    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1


async def test_apply_rename_non_native_source_fails(
    tmp_path, native_registry
):
    """A rename item with a non-native source becomes FAILED via the
    NotImplementedError branch of the dispatcher."""
    from wtree.ops.base import Plan
    item = PlanItem(
        src_source_id="sftp:remote",
        src_path="/remote/file.txt",
        dst_source_id="sftp:remote",
        dst_path="/remote/renamed.txt",
        kind=Kind.FILE,
        size=10,
    )
    plan = Plan(kind=OperationKind.RENAME, items=[item])
    result = await apply_plan(plan, native_registry)
    assert result.failed_count == 1
    assert "not supported in v0" in result.items[0].message


async def test_apply_rename_progress_callback_fires(
    tmp_path, native_registry
):
    src_file = tmp_path / "before.txt"
    src_file.write_text("p")

    from wtree.ops import plan_rename
    plan = await plan_rename(
        Tag("native", str(src_file)), "after.txt", native_registry
    )

    seen: list[ItemStatus] = []
    def cb(item_result):
        seen.append(item_result.status)

    result = await apply_plan(plan, native_registry, progress=cb)
    assert len(seen) == 1
    assert seen[0] is ItemStatus.SUCCESS
    assert result.success_count == 1
