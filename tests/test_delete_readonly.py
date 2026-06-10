"""Read-only entries must not break Delete / Overwrite (the Windows
git-objects problem).

Field bug (2026-06-11, op log catch #2): deleting a copied git repo on
Windows died with ``WinError 5`` on ``.git/objects/...`` - git marks
object files read-only, Windows refuses to unlink read-only files, and
bare ``shutil.rmtree`` stops at the first refusal. POSIX puts deletion
rights on the parent DIRECTORY instead, so these tests stage the POSIX
analogue - a write-protected directory - to drive the same
``PermissionError`` -> chmod-and-retry path the Windows case takes.

Root can unlink anything regardless of mode bits, so the
permission-dependent tests skip under euid 0.
"""

from __future__ import annotations

import os
import stat as stat_mod
from pathlib import Path

import pytest

from wtree.ops.base import (
    ItemStatus,
    Kind,
    OperationKind,
    PlanItem,
    Resolution,
)
from wtree.ops.execute import (
    _clear_readonly_and_retry,
    _native_delete,
    _remove_existing_blocking,
    _rmtree_force,
    _unlink_force,
)

needs_perms = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores permission bits",
)


def _stage_protected_repo(tmp_path: Path) -> Path:
    """A dir whose '.git/objects'-alike subdir is write-protected."""
    victim = tmp_path / "victim"
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


@needs_perms
async def test_native_delete_survives_readonly_subdir(tmp_path: Path) -> None:
    victim = _stage_protected_repo(tmp_path)
    try:
        item = PlanItem(
            src_source_id="native",
            src_path=str(victim),
            dst_source_id="native",
            dst_path=str(victim),
            kind=Kind.DIR,
            size=0,
        )
        result = await _native_delete(item)
        assert result.status is ItemStatus.SUCCESS, result.message
        assert not victim.exists()
    finally:
        if victim.exists():
            _force_writable(victim)


@needs_perms
def test_rmtree_force_clears_protection(tmp_path: Path) -> None:
    victim = _stage_protected_repo(tmp_path)
    try:
        # Sanity: bare rmtree refuses, proving the fixture stages the bug.
        import shutil

        with pytest.raises(PermissionError):
            shutil.rmtree(str(victim))
        _rmtree_force(str(victim))
        assert not victim.exists()
    finally:
        if victim.exists():
            _force_writable(victim)


@needs_perms
def test_overwrite_prestep_survives_readonly_dir(tmp_path: Path) -> None:
    victim = _stage_protected_repo(tmp_path)
    try:
        _remove_existing_blocking(str(victim))
        assert not victim.exists()
    finally:
        if victim.exists():
            _force_writable(victim)


@needs_perms
def test_unlink_force_readonly_parent(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    target = sub / "locked.txt"
    target.write_text("x")
    os.chmod(sub, 0o500)
    try:
        _unlink_force(str(target))
        assert not target.exists()
    finally:
        os.chmod(sub, 0o700)


def test_handler_reraises_non_permission_errors(tmp_path: Path) -> None:
    """Only PermissionError gets the chmod-retry; the rest re-raise."""
    boom = FileNotFoundError("gone")
    with pytest.raises(FileNotFoundError):
        _clear_readonly_and_retry(os.unlink, str(tmp_path / "x"), boom)


def test_handler_reraises_original_when_retry_fails(tmp_path: Path) -> None:
    """A retry that fails again surfaces the ORIGINAL error message."""
    target = tmp_path / "still-locked"
    target.write_text("x")
    original = PermissionError("the real reason")

    def _always_fails(path: str) -> None:
        raise PermissionError("retry also failed")

    with pytest.raises(PermissionError) as info:
        _clear_readonly_and_retry(_always_fails, str(target), original)
    assert "the real reason" in str(info.value)


def test_readonly_file_unlink_force(tmp_path: Path) -> None:
    """Windows-shaped case: the FILE itself is read-only. On POSIX this
    unlinks without the retry path (parent is writable), so this is a
    plain everywhere-pass that becomes the real regression on Windows."""
    target = tmp_path / "ro.txt"
    target.write_text("x")
    os.chmod(target, stat_mod.S_IREAD)
    _unlink_force(str(target))
    assert not target.exists()
