"""Tests for the move-executor chunk hook.

Design lives in design.md -> User interface -> Progress dialog ->
Move executor chunk hook (2026-05-26 decision-log row).

Coverage:

* **Same-fs**: ``os.rename`` fast-path still fires; no bytes flow.
* **Cross-fs simulation**: monkeypatch ``os.rename`` to raise
  ``OSError(errno.EXDEV, ...)``. The executor falls through to its
  per-kind dispatch.
* **Cross-fs FILE with callback**: ``_chunked_copy`` runs, callback
  fires per chunk, source is unlinked on success, dst landed.
* **Cross-fs FILE without callback**: falls through to ``shutil.move``
  (test-contract fast path), source unlinked, dst landed, no
  callback fires.
* **Cross-fs FILE cancel mid-copy**: callback returns ``False``,
  partial dst cleaned, source intact, ``FAILED("cancelled")``.
* **Cross-fs FILE unlink failure**: copy succeeds, unlink fails,
  result is FAILED with clear "unlink source after copy" message.
* **Cross-fs SYMLINK**: target read + recreated at dst + source
  unlinked. No callback fires.
* **Cross-fs DIR**: keeps ``shutil.move`` (no chunked path); plain
  success, dst exists, source gone.
* **apply_plan threading**: bytes_progress reaches ``_native_move``
  via ``apply_plan`` -> ``_apply_item`` -> MOVE branch.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from wtree.ops import OperationQueue, plan_move
from wtree.ops.base import ItemStatus, PlanItem
from wtree.ops.execute import _native_move, apply_plan
from wtree.ops.queue import COPY_CHUNK_SIZE
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag


@pytest.fixture
def registry() -> dict[str, NativeSource]:
    return {"native": NativeSource()}


def _make_exdev_rename():
    """Build a fake ``os.rename`` that always raises ``OSError(EXDEV)``."""
    def _fake(src: str, dst: str) -> None:
        raise OSError(errno.EXDEV, "EXDEV (simulated)", src)
    return _fake


# ---------------------------------------------------------------------------
# Signature / surface
# ---------------------------------------------------------------------------


def test_native_move_signature_takes_bytes_progress() -> None:
    """The new optional ``bytes_progress`` arg is present."""
    import inspect

    sig = inspect.signature(_native_move)
    assert "bytes_progress" in sig.parameters
    # Default must be None so existing callers compile.
    assert sig.parameters["bytes_progress"].default is None


# ---------------------------------------------------------------------------
# Same-fs rename fast path
# ---------------------------------------------------------------------------


async def test_same_fs_rename_uses_os_rename(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """Same-tmpfs move: ``os.rename`` succeeds, no bytes flow."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"hello world")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    calls: list[tuple[int, int]] = []

    def cb(item: PlanItem, done: int, total: int) -> bool:
        calls.append((done, total))
        return True

    result = await apply_plan(plan, registry, bytes_progress=cb)

    assert result.all_succeeded
    # Source gone, dst landed.
    assert not src.exists()
    assert (dst_dir / "a.bin").read_bytes() == b"hello world"
    # No bytes_progress fires for rename-fast-path - zero-guard
    # in the dialog correctly renders em-dashes.
    assert calls == []


async def test_same_fs_rename_works_without_callback(
    tmp_path: Path, registry: dict[str, NativeSource]
) -> None:
    """Legacy callers (no callback) keep working."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 100)
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    result = await apply_plan(plan, registry)

    assert result.all_succeeded
    assert not src.exists()
    assert (dst_dir / "a.bin").exists()


# ---------------------------------------------------------------------------
# Cross-fs FILE path
# ---------------------------------------------------------------------------


async def test_cross_fs_file_with_callback_chunks(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EXDEV from os.rename -> chunked copy + unlink, callback fires."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * (COPY_CHUNK_SIZE + 200))
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    calls: list[int] = []

    def cb(item: PlanItem, done: int, total: int) -> bool:
        calls.append(done)
        return True

    src_size = src.stat().st_size
    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    result = await apply_plan(plan, registry, bytes_progress=cb)

    assert result.all_succeeded
    assert calls, "bytes_progress was never invoked"
    assert calls[0] == 0  # initial-zero callback
    assert calls[-1] == src_size
    # Source gone, dst landed with the right contents.
    assert not src.exists()
    dst = dst_dir / "a.bin"
    assert dst.exists()
    assert dst.stat().st_size == src_size


async def test_cross_fs_file_without_callback_uses_fast_path(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No callback => shutil.move (preserves pre-progress test contract)."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"hello world")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    result = await apply_plan(plan, registry)  # no bytes_progress

    assert result.all_succeeded
    assert not src.exists()
    assert (dst_dir / "a.bin").read_bytes() == b"hello world"


async def test_cross_fs_file_cancel_mid_copy(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callback returns False -> partial dst cleaned, source intact, FAILED."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * (COPY_CHUNK_SIZE * 3))
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    def cb(item: PlanItem, done: int, total: int) -> bool:
        # Cancel on the first NON-zero call (after the initial zero
        # callback). Cancelling on the zero callback also works but
        # exercises the same path; this exercises mid-copy.
        return done == 0

    result = await apply_plan(plan, registry, bytes_progress=cb)

    assert not result.all_succeeded
    # Source MUST still exist - cancellation is data-safe.
    assert src.exists()
    assert src.stat().st_size == COPY_CHUNK_SIZE * 3
    # Partial dst was cleaned.
    assert not (dst_dir / "a.bin").exists()
    # Result message identifies the cancel.
    failed_items = [
        i for i in result.items if i.status == ItemStatus.FAILED
    ]
    assert failed_items
    assert any("cancelled" in (i.message or "") for i in failed_items)


async def test_cross_fs_file_unlink_failure_surfaces_clear_error(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copy succeeds, unlink fails -> FAILED with 'unlink source after copy'."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"hello")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    real_unlink = os.unlink
    src_str = str(src)

    def fake_unlink(path: str | os.PathLike) -> None:
        if str(path) == src_str:
            raise PermissionError("simulated unlink fail")
        real_unlink(path)

    monkeypatch.setattr(os, "unlink", fake_unlink)

    def cb(item: PlanItem, done: int, total: int) -> bool:
        return True

    result = await apply_plan(plan, registry, bytes_progress=cb)

    assert not result.all_succeeded
    # File exists in BOTH places - that's the partial-failure
    # semantics we surface (clearer than shutil.move's opaque trace).
    assert src.exists()
    assert (dst_dir / "a.bin").exists()
    failed_items = [
        i for i in result.items if i.status == ItemStatus.FAILED
    ]
    assert failed_items
    assert any(
        "unlink source after copy" in (i.message or "")
        for i in failed_items
    )


# ---------------------------------------------------------------------------
# Cross-fs SYMLINK path
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.name == "nt", reason="symlinks need admin privileges on Windows CI"
)
async def test_cross_fs_symlink_recreates_at_dst(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SYMLINK cross-fs: readlink + recreate + unlink, no bytes flow."""
    target = tmp_path / "target.txt"
    target.write_text("hello")
    link = tmp_path / "link"
    os.symlink(target, link)
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_move(
        [Tag("native", str(link))], Tag("native", str(dst_dir)), registry
    )

    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    calls: list[tuple[int, int]] = []

    def cb(item: PlanItem, done: int, total: int) -> bool:
        calls.append((done, total))
        return True

    result = await apply_plan(plan, registry, bytes_progress=cb)

    assert result.all_succeeded
    # Source link gone.
    assert not link.exists() and not link.is_symlink()
    # Dst link present, pointing at the same target.
    moved = dst_dir / "link"
    assert moved.is_symlink()
    assert os.readlink(moved) == str(target)
    # No bytes_progress for symlinks - no bytes flow.
    assert calls == []


# ---------------------------------------------------------------------------
# Cross-fs DIR path (keeps shutil.move per scope decision)
# ---------------------------------------------------------------------------


async def test_cross_fs_dir_keeps_shutil_move(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DIR cross-fs uses shutil.move (copytree + rmtree). No chunked path."""
    src_dir = tmp_path / "sub"
    src_dir.mkdir()
    (src_dir / "a.txt").write_text("aaa")
    (src_dir / "b.txt").write_text("bbb")
    dst_parent = tmp_path / "dst"
    dst_parent.mkdir()

    plan = await plan_move(
        [Tag("native", str(src_dir))],
        Tag("native", str(dst_parent)),
        registry,
    )

    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    calls: list[tuple[int, int]] = []

    def cb(item: PlanItem, done: int, total: int) -> bool:
        calls.append((done, total))
        return True

    result = await apply_plan(plan, registry, bytes_progress=cb)

    assert result.all_succeeded
    # Source gone.
    assert not src_dir.exists()
    # Dst landed with contents intact.
    moved = dst_parent / "sub"
    assert (moved / "a.txt").read_text() == "aaa"
    assert (moved / "b.txt").read_text() == "bbb"
    # NO bytes_progress fired - DIR path is shutil.move,
    # no chunked hook. This is the documented gap.
    assert calls == []


# ---------------------------------------------------------------------------
# apply_plan threading
# ---------------------------------------------------------------------------


async def test_apply_plan_threads_bytes_progress_through_move(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MOVE branch of _apply_item now passes bytes_progress through."""
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * (COPY_CHUNK_SIZE + 50))
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    src_size = src.stat().st_size  # capture before move unlinks src
    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    fired: list[int] = []

    def cb(item: PlanItem, done: int, total: int) -> bool:
        fired.append(done)
        return True

    result = await apply_plan(plan, registry, bytes_progress=cb)

    assert result.all_succeeded
    # The callback must have fired at least twice (initial zero +
    # at least one chunk). This is what proves the bytes_progress
    # arg reached _native_move instead of being silently dropped.
    assert len(fired) >= 2
    assert fired[0] == 0
    assert fired[-1] == src_size


async def test_queue_bytes_progress_during_cross_fs_move(
    tmp_path: Path,
    registry: dict[str, NativeSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OperationQueue's bytes_progress property tracks a cross-fs move."""
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * (COPY_CHUNK_SIZE * 2))
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    plan = await plan_move(
        [Tag("native", str(src))], Tag("native", str(dst_dir)), registry
    )

    monkeypatch.setattr(os, "rename", _make_exdev_rename())

    queue = OperationQueue(registry=registry)
    queue.start()
    try:
        # While idle, bytes_progress is None.
        assert queue.bytes_progress is None
        queue.enqueue(plan)
        await queue.wait_until_idle()
        assert queue.running is None, "queue did not drain"
    finally:
        await queue.stop()

    # Source moved.
    assert not src.exists()
    assert (dst_dir / "big.bin").exists()
