"""Regression pins for the 2026-11-12 code-review fixes.

Each test reproduces a bug found by reading the code rather than by a
field report; see the worklog entry of the same date for the narrative.

* self-copy + Overwrite truncated every file in the copied folder
* ``_chunked_copy`` truncated a source reached through an alias path
* two tagged entries with one basename silently clobbered each other
* the delete-root guard only recognised ``/`` (not ``C:\\`` / ``C:/``)
* tree child paths were native-joined (``C:/root\\child`` on Windows)
* an out-of-range mtime raised out of ``NativeSource``
* the read-only retry chmod'ed *through* symlinks / choked on dangling
  ones / masked the real error with a ``TypeError``
* Make-new cleared the tagged set
* quoted ``$EDITOR`` paths kept their quotes on Windows
"""

from __future__ import annotations

import ntpath
import os
import shutil
import stat as stat_mod
from pathlib import Path
from types import SimpleNamespace

import pytest

from wtree.app import WTreeApp
from wtree.ops import plan_copy, plan_move, resolve_conflicts
from wtree.ops.base import (
    ConflictKind,
    ItemStatus,
    Kind,
    PlanItem,
    Resolution,
    canonical_path,
)
from wtree.ops.conflicts import preview_renamed_dst
from wtree.ops.delete import _basename as _delete_basename
from wtree.ops.execute import _chunked_copy, _rmtree_force, apply_plan
from wtree.sources.native import NativeSource, _safe_mtime
from wtree.tagged_set import Tag

needs_perms = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores permission bits",
)


def _registry() -> dict[str, NativeSource]:
    return {"native": NativeSource()}


def _always(_item: PlanItem, _done: int, _size: int) -> bool:
    return True


# ---------------------------------------------------------------------------
# Self-copy + Overwrite must not truncate the source
# ---------------------------------------------------------------------------


async def test_self_copy_overwrite_dir_leaves_files_intact(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "a.txt").write_text("hello")
    (proj / "sub" / "b.txt").write_text("world")
    reg = _registry()

    plan = await plan_copy(
        [Tag("native", str(proj))], Tag("native", str(tmp_path)), reg
    )
    conflicts = [i for i in plan.items if i.conflict is not ConflictKind.NONE]
    assert [c.conflict for c in conflicts] == [ConflictKind.SELF]

    resolved = await resolve_conflicts(plan, [Resolution.OVERWRITE], reg)
    # Overwrite-with-itself is a no-op: root AND descendants are dropped.
    assert resolved.items == []

    # Belt and braces: even if such a plan reached the executor through
    # the progress-aware (chunked) path, nothing may be truncated.
    await apply_plan(resolved, reg, bytes_progress=_always)
    assert (proj / "a.txt").read_text() == "hello"
    assert (proj / "sub" / "b.txt").read_text() == "world"


def test_chunked_copy_refuses_same_file_via_alias(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    src = real / "data.bin"
    src.write_bytes(b"x" * 1000)
    alias = tmp_path / "alias"
    os.symlink(real, alias)
    item = PlanItem("native", str(src), "native", str(alias / "data.bin"),
                    Kind.FILE, 1000)

    with pytest.raises(shutil.SameFileError):
        _chunked_copy(item, str(src), str(alias / "data.bin"), _always)
    assert src.read_bytes() == b"x" * 1000


def test_chunked_copy_failure_keeps_preexisting_destination(
    tmp_path: Path,
) -> None:
    """An unreadable/missing source must not delete a dst we never wrote."""
    dst = tmp_path / "keep.txt"
    dst.write_text("precious")
    missing = tmp_path / "missing.txt"
    item = PlanItem("native", str(missing), "native", str(dst), Kind.FILE, 0)

    with pytest.raises(FileNotFoundError):
        _chunked_copy(item, str(missing), str(dst), _always)
    assert dst.read_text() == "precious"


# ---------------------------------------------------------------------------
# Intra-plan duplicate destinations
# ---------------------------------------------------------------------------


def _two_same_named(tmp_path: Path) -> tuple[list[Tag], Tag]:
    for d, body in (("x", "from x"), ("y", "from y")):
        (tmp_path / d).mkdir()
        (tmp_path / d / "n.txt").write_text(body)
    (tmp_path / "dest").mkdir()
    tags = [
        Tag("native", str(tmp_path / "x" / "n.txt")),
        Tag("native", str(tmp_path / "y" / "n.txt")),
    ]
    return tags, Tag("native", str(tmp_path / "dest"))


async def test_copy_same_basename_twice_is_flagged(tmp_path: Path) -> None:
    tags, dest = _two_same_named(tmp_path)
    plan = await plan_copy(tags, dest, _registry())
    assert [i.conflict for i in plan.items] == [
        ConflictKind.NONE,
        ConflictKind.FILE,
    ]


async def test_copy_same_basename_rename_keeps_both(tmp_path: Path) -> None:
    tags, dest = _two_same_named(tmp_path)
    reg = _registry()
    plan = await plan_copy(tags, dest, reg)
    resolved = await resolve_conflicts(plan, [Resolution.RENAME], reg)
    result = await apply_plan(resolved, reg, bytes_progress=_always)

    assert all(r.status is ItemStatus.SUCCESS for r in result.items)
    # The un-flagged first item keeps its name; only the duplicate moves.
    assert (tmp_path / "dest" / "n.txt").read_text() == "from x"
    assert (tmp_path / "dest" / "n (1).txt").read_text() == "from y"


async def test_copy_same_basename_skip_drops_only_the_duplicate(
    tmp_path: Path,
) -> None:
    tags, dest = _two_same_named(tmp_path)
    reg = _registry()
    plan = await plan_copy(tags, dest, reg)
    resolved = await resolve_conflicts(plan, [Resolution.SKIP], reg)
    assert [i.src_path for i in resolved.items] == [tags[0].path]


async def test_three_same_named_renames_get_distinct_suffixes(
    tmp_path: Path,
) -> None:
    for d in ("x", "y", "z"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "n.txt").write_text(d)
    (tmp_path / "dest").mkdir()
    tags = [Tag("native", str(tmp_path / d / "n.txt")) for d in "xyz"]
    reg = _registry()
    plan = await plan_copy(tags, Tag("native", str(tmp_path / "dest")), reg)
    resolved = await resolve_conflicts(
        plan, [Resolution.RENAME, Resolution.RENAME], reg
    )
    names = sorted(os.path.basename(i.dst_path) for i in resolved.items)
    assert names == ["n (1).txt", "n (2).txt", "n.txt"]

    # The dialog previews, given the same shared ``taken`` set, agree.
    taken = {canonical_path(i.dst_path) for i in plan.items}
    conflicts = [i for i in plan.items if i.conflict is not ConflictKind.NONE]
    previews = [await preview_renamed_dst(i, reg, taken) for i in conflicts]
    assert sorted(os.path.basename(p) for p in previews) == [
        "n (1).txt",
        "n (2).txt",
    ]


async def test_copy_same_named_dirs_still_merge(tmp_path: Path) -> None:
    """Benign-merge rule holds inside a plan: dirs merge, leaves collide."""
    for d in ("x", "y"):
        (tmp_path / d / "proj").mkdir(parents=True)
        (tmp_path / d / "proj" / "same.txt").write_text(d)
        (tmp_path / d / "proj" / f"only_{d}.txt").write_text(d)
    (tmp_path / "dest").mkdir()
    tags = [Tag("native", str(tmp_path / d / "proj")) for d in "xy"]
    plan = await plan_copy(
        tags, Tag("native", str(tmp_path / "dest")), _registry()
    )
    flagged = [
        os.path.basename(i.dst_path)
        for i in plan.items
        if i.conflict is not ConflictKind.NONE
    ]
    assert flagged == ["same.txt"]


async def test_move_same_basename_twice_is_flagged(tmp_path: Path) -> None:
    tags, dest = _two_same_named(tmp_path)
    plan = await plan_move(tags, dest, _registry())
    assert [i.conflict for i in plan.items] == [
        ConflictKind.NONE,
        ConflictKind.FILE,
    ]


# ---------------------------------------------------------------------------
# Delete-root guard on Windows-shaped roots
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("root", ["/", "C:\\", "C:/", "C:", "d:\\"])
def test_delete_basename_treats_roots_as_unrooted(root: str) -> None:
    assert _delete_basename(root) == ""


@pytest.mark.parametrize(
    ("path", "expected"),
    [("C:\\Users\\me", "me"), ("C:/Users/me/", "me"), ("/home/me", "me")],
)
def test_delete_basename_normal_paths(path: str, expected: str) -> None:
    assert _delete_basename(path) == expected


# ---------------------------------------------------------------------------
# Tree child paths are born-POSIX even under a Windows os.path
# ---------------------------------------------------------------------------


class _OneDirSource:
    source_id = "fake"

    async def scan(self, path: str):
        from wtree.sources.base import Entry

        yield Entry(name="child", kind=Kind.DIR, size=0, mtime=None)


async def test_populate_dir_node_posix_paths_under_windows_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from textual.widgets import Tree

    from wtree.widgets import scan_screen

    # Stand in for Windows: os.path.join -> ntpath.join (backslash).
    monkeypatch.setattr(scan_screen, "os", SimpleNamespace(path=ntpath))

    tree: Tree[str] = Tree("root", data="C:/root")
    await scan_screen.populate_dir_node(
        tree.root, _OneDirSource(), set(), posix_paths=True
    )
    assert [c.data for c in tree.root.children] == ["C:/root/child"]

    # The picker's native convention is untouched.
    native: Tree[str] = Tree("root", data="C:\\root")
    await scan_screen.populate_dir_node(native.root, _OneDirSource(), set())
    assert [c.data for c in native.root.children] == ["C:\\root\\child"]


# ---------------------------------------------------------------------------
# Out-of-range mtimes
# ---------------------------------------------------------------------------


def test_safe_mtime_degrades_to_none() -> None:
    assert _safe_mtime(0.0) is not None
    assert _safe_mtime(1e30) is None
    assert _safe_mtime(float("inf")) is None


# ---------------------------------------------------------------------------
# Read-only retry vs symlinks / non-removal callbacks
# ---------------------------------------------------------------------------


@needs_perms
def test_rmtree_force_readonly_dir_with_dangling_symlink(tmp_path: Path) -> None:
    ro = tmp_path / "victim" / "ro"
    ro.mkdir(parents=True)
    os.symlink("/nonexistent-target", ro / "dangling")
    os.chmod(ro, 0o555)
    _rmtree_force(str(tmp_path / "victim"))
    assert not (tmp_path / "victim").exists()


@needs_perms
def test_rmtree_force_does_not_chmod_symlink_targets(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("mine")
    os.chmod(outside, 0o444)
    ro = tmp_path / "victim" / "ro"
    ro.mkdir(parents=True)
    os.symlink(outside, ro / "link")
    os.chmod(ro, 0o555)

    _rmtree_force(str(tmp_path / "victim"))

    assert not (tmp_path / "victim").exists()
    assert stat_mod.S_IMODE(outside.stat().st_mode) == 0o444


@needs_perms
def test_rmtree_force_unreadable_dir_reports_permission_error(
    tmp_path: Path,
) -> None:
    locked = tmp_path / "victim" / "locked"
    locked.mkdir(parents=True)
    (locked / "f").write_text("x")
    os.chmod(locked, 0o000)
    try:
        with pytest.raises(PermissionError):  # not a masking TypeError
            _rmtree_force(str(tmp_path / "victim"))
    finally:
        os.chmod(locked, 0o700)


# ---------------------------------------------------------------------------
# Make-new leaves the tagged set alone
# ---------------------------------------------------------------------------


async def test_make_new_does_not_clear_tagged_set(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("x")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("space")
        assert len(app.tagged_set) == 1
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press(*"newdir")
        await pilot.press("enter")
        await pilot.pause()
        assert app.op_queue is not None
        await app.op_queue.wait_until_idle()
        await pilot.pause()
        assert (tmp_path / "newdir").is_dir()
        assert len(app.tagged_set) == 1


# ---------------------------------------------------------------------------
# Quoted editor path on Windows
# ---------------------------------------------------------------------------


def test_resolve_editor_strips_quotes_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wtree import editor

    # Swap the module's ``os`` for a stand-in rather than mutating the
    # real ``os.name`` for the duration of the test.
    monkeypatch.setattr(
        editor, "os", SimpleNamespace(name="nt", environ=os.environ)
    )
    monkeypatch.setenv("VISUAL", '"C:\\Program Files\\np\\np.exe" -multiInst')
    assert editor.resolve_editor() == [
        "C:\\Program Files\\np\\np.exe",
        "-multiInst",
    ]
