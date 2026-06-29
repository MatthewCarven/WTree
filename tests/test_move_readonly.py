"""Read-only entries must not break cross-filesystem directory Move.

Session 1 follow-up to the 2026-06-11 Delete read-only fix. ``_native_move``'s
cross-fs DIR branch used to delegate to ``shutil.move`` (``copytree`` +
``rmtree``); ``shutil.move``'s internal ``rmtree`` has NO read-only retry, so a
read-only entry anywhere in the moved tree (git ``.git/objects/...`` pack/object
files, marked read-only by design) aborts the *source cleanup* mid-walk with
``WinError 5`` on Windows - leaving the source half-removed while the
destination copy is already complete. POSIX puts deletion rights on the parent
DIRECTORY, so these tests stage the POSIX analogue - a write-protected directory
- to drive the same ``PermissionError`` -> chmod-and-retry path the Windows case
takes.

The cross-fs branch only runs when ``os.rename`` raises (``EXDEV``); on one
tmpfs the rename fast-path always wins, so the tests monkeypatch ``os.rename``
to raise ``OSError(EXDEV)`` (the same simulation ``test_move_chunk_hook`` uses).

Root can unlink anything regardless of mode bits, so the permission-dependent
tests skip under euid 0.
"""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

import pytest

from wtree.ops import plan_move
from wtree.ops.base import ItemStatus, Kind, PlanItem
from wtree.ops.execute import _is_within, _native_move, apply_plan
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag

needs_perms = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores permission bits",
)


@pytest.fixture
def registry() -> dict[str, NativeSource]:
    return {"native": NativeSource()}


def _make_exdev_rename():
    """A fake ``os.rename`` that always raises ``OSError(EXDEV)``."""
    def _fake(src: str, dst: str) -> None:
        raise OSError(errno.EXDEV, "EXDEV (simulated)", src)
    return _fake


def _stage_protected_repo(parent: Path, name: str = "victim") -> Path:
    """A dir whose '.git/objects'-alike subdir is write-protected."""
    victim = parent / name
    objects = victim / ".git" / "objects" / "03"
    objects.mkdir(parents=True)
    (objects / "0f270bfc").write_text("packed")
    (victim / "readme.txt").write_text("hi")
    # r-x: listable + statable, but children can't be unlinked (POSIX
    # analogue of Windows' read-only-file refusal).
    os.chmod(objects, 0o500)
    return victim


def _force_writable(root: Path) -> None:
    """Teardown net so a failed test never leaves tmp_path undeletable."""
    for dirpath, _dirnames, _files in os.walk(root):
        try:
            os.chmod(dirpath, 0o700)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# The bug, pinned: bare shutil.move refuses a read-only tree mid-cleanup
# ---------------------------------------------------------------------------


@needs_perms
def test_bare_shutil_move_refuses_readonly_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves the fixture stages the bug: ``shutil.move``'s internal rmtree
    refuses the read-only subdir, so the source can't be cleaned up."""
    victim = _stage_protected_repo(tmp_path)
    dest = tmp_path / "dest"
    monkeypatch.setattr(os, "rename", _make_exdev_rename())
    try:
        with pytest.raises(PermissionError):
            shutil.move(str(victim), str(dest))
        # The copy half completed before the cleanup blew up.
        assert (dest / "readme.txt").exists()
        # ...and the source is still (partly) there - the half-removed state.
        assert victim.exists()
    finally:
        _force_writable(tmp_path)


# ---------------------------------------------------------------------------
# The fix: cross-fs DIR move survives a read-only entry
# ---------------------------------------------------------------------------


@needs_perms
async def test_cross_fs_dir_move_survives_readonly_subdir(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """copytree + ``_rmtree_force`` clears the read-only bit and completes."""
    src_parent = tmp_path / "from"
    src_parent.mkdir()
    victim = _stage_protected_repo(src_parent)
    dst_parent = tmp_path / "to"
    dst_parent.mkdir()

    plan = await plan_move(
        [Tag("native", str(victim))], Tag("native", str(dst_parent)), registry
    )
    monkeypatch.setattr(os, "rename", _make_exdev_rename())
    try:
        result = await apply_plan(plan, registry)
        assert result.all_succeeded, [i.message for i in result.items]
        # Source fully removed despite the read-only objects dir.
        assert not victim.exists()
        # Destination landed with the read-only entry intact.
        moved = dst_parent / "victim"
        assert (moved / "readme.txt").read_text() == "hi"
        assert (
            moved / ".git" / "objects" / "03" / "0f270bfc"
        ).read_text() == "packed"
    finally:
        _force_writable(tmp_path)


@needs_perms
async def test_cross_fs_dir_move_no_callback_path(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-only-tolerant cleanup is used whether or not a bytes
    callback is supplied (DIR has no chunked hook - same path both ways)."""
    src_parent = tmp_path / "from"
    src_parent.mkdir()
    victim = _stage_protected_repo(src_parent)
    dst_parent = tmp_path / "to"
    dst_parent.mkdir()

    plan = await plan_move(
        [Tag("native", str(victim))], Tag("native", str(dst_parent)), registry
    )
    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    calls: list[tuple[int, int]] = []

    def cb(item: PlanItem, done: int, total: int) -> bool:
        calls.append((done, total))
        return True

    try:
        result = await apply_plan(plan, registry, bytes_progress=cb)
        assert result.all_succeeded, [i.message for i in result.items]
        assert not victim.exists()
        assert (dst_parent / "victim" / "readme.txt").exists()
        # DIR path has no chunked hook - no bytes_progress fires.
        assert calls == []
    finally:
        _force_writable(tmp_path)


# ---------------------------------------------------------------------------
# Post-copy cleanup failure is attributed distinctly
# ---------------------------------------------------------------------------


async def test_remove_source_after_copy_failure_surfaces_clear_error(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copy lands but the source rmtree fails even after the retry -> FAILED
    with 'remove source after copy', dir present in BOTH places."""
    src_dir = tmp_path / "sub"
    src_dir.mkdir()
    (src_dir / "a.txt").write_text("aaa")
    dst_parent = tmp_path / "dst"
    dst_parent.mkdir()

    plan = await plan_move(
        [Tag("native", str(src_dir))], Tag("native", str(dst_parent)), registry
    )
    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    def _boom(path: str) -> None:
        raise OSError("simulated rmtree refusal")

    monkeypatch.setattr("wtree.ops.execute._rmtree_force", _boom)

    result = await apply_plan(plan, registry)

    assert not result.all_succeeded
    failed = [i for i in result.items if i.status is ItemStatus.FAILED]
    assert failed
    assert any(
        "remove source after copy" in (i.message or "") for i in failed
    )
    # Copy half landed; source still present - the partial-failure state.
    assert (dst_parent / "sub" / "a.txt").read_text() == "aaa"
    assert src_dir.exists()


# ---------------------------------------------------------------------------
# Refuse moving a directory into its own subtree (the _destinsrc guard)
# ---------------------------------------------------------------------------


async def test_cross_fs_dir_move_into_itself_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dst nested under src -> FAILED 'into itself', source untouched
    (copytree would recurse forever otherwise)."""
    src_dir = tmp_path / "x"
    src_dir.mkdir()
    (src_dir / "f.txt").write_text("hi")
    dst = src_dir / "sub"  # inside src

    item = PlanItem(
        src_source_id="native",
        src_path=str(src_dir),
        dst_source_id="native",
        dst_path=str(dst),
        kind=Kind.DIR,
        size=0,
    )
    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    result = await _native_move(item)

    assert result.status is ItemStatus.FAILED
    assert "into itself" in (result.message or "")
    # Source is untouched - nothing copied, nothing removed.
    assert (src_dir / "f.txt").read_text() == "hi"
    assert not dst.exists()


# ---------------------------------------------------------------------------
# symlinks=True parity: links inside a moved dir are preserved, not chased
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.name == "nt", reason="symlinks need admin privileges on Windows CI"
)
async def test_cross_fs_dir_move_preserves_inner_symlink(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symlink inside the moved tree survives as a symlink (copytree
    symlinks=True), not dereferenced into a copy of its target."""
    src_dir = tmp_path / "tree"
    src_dir.mkdir()
    (src_dir / "real.txt").write_text("payload")
    os.symlink("real.txt", src_dir / "link")  # relative inner link
    dst_parent = tmp_path / "dst"
    dst_parent.mkdir()

    plan = await plan_move(
        [Tag("native", str(src_dir))], Tag("native", str(dst_parent)), registry
    )
    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    result = await apply_plan(plan, registry)

    assert result.all_succeeded, [i.message for i in result.items]
    moved_link = dst_parent / "tree" / "link"
    assert moved_link.is_symlink()
    assert os.readlink(moved_link) == "real.txt"
    assert not src_dir.exists()


# ---------------------------------------------------------------------------
# _is_within unit pins (the destinsrc guard, POSIX-runnable)
# ---------------------------------------------------------------------------


def test_is_within_exact_match() -> None:
    assert _is_within("/a", "/a")


def test_is_within_nested() -> None:
    assert _is_within("/a/b/c", "/a")


def test_is_within_sibling_not() -> None:
    assert not _is_within("/ab", "/a")


def test_is_within_lexicographic_trap() -> None:
    # '/a-x' sorts between '/a' and '/a/b' but is NOT under '/a'.
    assert not _is_within("/a-x", "/a")


def test_is_within_parent_not_under_child() -> None:
    assert not _is_within("/a", "/a/b")


def test_is_within_mixed_separators() -> None:
    # Backslash child still resolves under a POSIX parent (canonical_path
    # flips separators) - the typed-Windows-path case.
    assert _is_within("/a\\b", "/a")
