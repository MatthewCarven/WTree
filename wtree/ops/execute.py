"""Execute a :class:`~wtree.ops.base.Plan`.

The planner half (``wtree/ops/copy.py``, ``wtree/ops/move.py``,
``wtree/ops/delete.py``, ``wtree/ops/rename.py``) produces a Plan.
This module consumes one - dispatching each :class:`PlanItem` by
``(plan.kind, src_source_id, dst_source_id)`` to the right transfer
adapter.

v0 implements only the ``("native", "native")`` pair via stdlib
``shutil`` + ``os``. Every other pair raises :class:`NotImplementedError`,
caught per-item by :func:`apply_plan` so one unsupported entry doesn't
break the rest. When archive / SFTP sources land they grow their own
adapters and slot into the same dispatch table.

The executor never raises - per-item exceptions are converted to
:class:`ItemResult` with ``status=FAILED``. Callers (the operation
queue, tests) can trust they always get a complete
:class:`OperationResult` back.

Per-operation dispatch:

* **Copy items** are leaf-by-leaf - the copy planner flattens directory
  subtrees. The executor handles them per-kind (``mkdir`` for dirs,
  ``shutil.copy2`` for files, ``os.symlink`` for links).
* **Move items** are top-level - one ``PlanItem`` per user-tagged
  entry. The executor delegates to ``shutil.move`` which transparently
  handles rename-fast-path and copy-then-delete fallback for cross-fs.
* **Delete items** are top-level. ``dst_*`` is sentinel and ignored.
  Executor uses ``os.unlink`` for files/symlinks, ``shutil.rmtree`` for
  dirs.
* **Rename items** are always exactly one (single-entry op per design).
  ``dst_path`` is the parent dir joined with the new basename; executor
  uses ``os.rename`` which is atomic on every supported filesystem.
* **Make-new items** are always exactly one. ``dst_path`` is the
  intended leaf; ``src_path`` mirrors it (Make-new has no source).
  Executor uses ``os.makedirs(leaf, exist_ok=False)`` for DIR and
  ``open(leaf, "x").close()`` (after ensuring the parent exists via
  ``os.makedirs(..., exist_ok=True)``) for FILE. Lenient mode -
  intermediate dirs are created as needed; the leaf must not pre-exist.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable, Mapping

from wtree.ops.base import (
    ItemResult,
    ItemStatus,
    OperationKind,
    OperationResult,
    Plan,
    PlanItem,
)
from wtree.sources.base import EntrySource, Kind

# Callback signature for per-item progress. Sync; the queue/UI bridges
# any async-ness on its side. Kept tiny on purpose - the progress
# dialog (post-v0) will subscribe to higher-level OperationQueue events
# rather than this raw stream.
ProgressCb = Callable[[ItemResult], None]


async def apply_plan(
    plan: Plan,
    registry: Mapping[str, EntrySource],
    progress: ProgressCb | None = None,
) -> OperationResult:
    """Apply every :class:`PlanItem` in ``plan`` in order.

    Order follows the planner's emit order:

    * Copy plans: depth-first parent-first, so a directory's own
      ``PlanItem`` is processed (``mkdir``) before any item that lands
      inside it.
    * Move / Delete plans: one item per top-level tag.
    * Rename plans: always exactly one item.

    ``apply_plan`` does NOT re-sort - it trusts the planner.

    ``registry`` is the same ``{source_id: EntrySource}`` map the planner
    used. v0 only consults it to detect cross-source pairs (which become
    failed items); future adapters will use it to ask sources for
    transfer-relevant capabilities (e.g. ``can_stream_chunks``).

    Errors mid-item never propagate. Each ``PlanItem`` produces an
    :class:`ItemResult`; ``progress`` (if provided) is called once per
    item in order.
    """
    results: list[ItemResult] = []
    for item in plan.items:
        try:
            result = await _apply_item(plan.kind, item, registry)
        except Exception as exc:  # noqa: BLE001 - intentional: per-item isolation
            result = ItemResult(
                item=item,
                status=ItemStatus.FAILED,
                message=f"{type(exc).__name__}: {exc}",
            )
        results.append(result)
        if progress is not None:
            progress(result)
    return OperationResult(plan=plan, items=results)


# ---------------------------------------------------------------------------
# Per-item dispatch
# ---------------------------------------------------------------------------


async def _apply_item(
    kind: OperationKind,
    item: PlanItem,
    registry: Mapping[str, EntrySource],
) -> ItemResult:
    """Pick the right adapter for ``(kind, src, dst)`` and run it.

    The dispatch table is intentionally explicit (a chain of ``if`` rather
    than a dict-of-callables) so unsupported pairs fail with a clear
    ``NotImplementedError`` message instead of a generic ``KeyError``.

    DELETE is checked first because its ``dst_*`` is a sentinel mirror;
    falling into the pair check would incorrectly match the
    ``("native","native")`` adapter even though there's no destination
    in the user-facing sense. RENAME shares the same single-source
    invariant - the pair is always ``(src, src)`` since renames never
    cross sources - but the pair check still works for it (the executor
    just needs to dispatch on kind).
    """
    if kind is OperationKind.DELETE:
        if item.src_source_id == "native":
            return await _native_delete(item)
        raise NotImplementedError(
            f"delete on source {item.src_source_id!r} not supported in v0"
        )

    if kind is OperationKind.RENAME:
        # Rename never crosses sources; planner enforces dst_source_id
        # == src_source_id. The pair check below would also catch this
        # but the explicit branch makes the v0 native-only constraint
        # obvious.
        if item.src_source_id == "native":
            return await _native_rename(item)
        raise NotImplementedError(
            f"rename on source {item.src_source_id!r} not supported in v0"
        )

    if kind is OperationKind.MAKE_NEW:
        # Make-new never crosses sources either; planner sets src and
        # dst to the same id. Mirror the explicit RENAME branch above
        # so the v0 native-only constraint stays loud.
        if item.dst_source_id == "native":
            return await _native_make_new(item)
        raise NotImplementedError(
            f"make_new on source {item.dst_source_id!r} not supported in v0"
        )

    pair = (item.src_source_id, item.dst_source_id)
    if pair == ("native", "native"):
        if kind is OperationKind.COPY:
            return await _native_copy(item)
        if kind is OperationKind.MOVE:
            return await _native_move(item)
        raise NotImplementedError(
            f"{kind.value!r} on (native, native) not supported in v0"
        )
    raise NotImplementedError(
        f"{kind.value} from {item.src_source_id!r} to "
        f"{item.dst_source_id!r} not supported in v0"
    )


def _normalise_dst(dst_path: str) -> str:
    """Translate planner's POSIX dst_path to native separators on Windows.

    Both forms work for OS calls on Windows, but normalising keeps the
    visible path string consistent with how the user typed it.
    """
    return os.path.normpath(dst_path) if os.sep == "\\" else dst_path


# ---------------------------------------------------------------------------
# Native -> native copy
# ---------------------------------------------------------------------------


async def _native_copy(item: PlanItem) -> ItemResult:
    """``shutil.copy2`` for files, ``os.makedirs`` for dirs, recreate
    symlinks. Each kind handled explicitly so the failure modes have
    clean per-kind error messages.

    Big files are dispatched via :func:`asyncio.to_thread` so the
    Textual event loop stays responsive during large copies. The OS
    handles the actual byte movement; we just don't block the main
    thread waiting on it.
    """
    src = item.src_path
    dst = _normalise_dst(item.dst_path)

    if item.kind is Kind.DIR:
        await asyncio.to_thread(os.makedirs, dst, exist_ok=True)
        return ItemResult(item=item, status=ItemStatus.SUCCESS)

    if item.kind is Kind.FILE:
        # Ensure the immediate parent exists - the planner emits parents
        # before their contents but a typed destination may itself be a
        # path that doesn't exist yet (e.g. user typed "/tmp/new-name").
        parent = os.path.dirname(dst)
        if parent:
            await asyncio.to_thread(os.makedirs, parent, exist_ok=True)
        # copy2 preserves mtime/permissions - matches what XTree/MC do.
        await asyncio.to_thread(shutil.copy2, src, dst)
        return ItemResult(item=item, status=ItemStatus.SUCCESS)

    if item.kind is Kind.SYMLINK:
        # Read the link target from the source and recreate. Don't follow
        # the symlink - that's what shutil.copy2 would do.
        try:
            target = os.readlink(src)
        except OSError as exc:
            return ItemResult(
                item=item,
                status=ItemStatus.FAILED,
                message=f"readlink: {type(exc).__name__}: {exc}",
            )
        parent = os.path.dirname(dst)
        if parent:
            await asyncio.to_thread(os.makedirs, parent, exist_ok=True)
        try:
            await asyncio.to_thread(os.symlink, target, dst)
        except OSError as exc:
            return ItemResult(
                item=item,
                status=ItemStatus.FAILED,
                message=f"symlink: {type(exc).__name__}: {exc}",
            )
        return ItemResult(item=item, status=ItemStatus.SUCCESS)

    # Kind.OTHER - sockets, devices, fifos, etc. v0 punts.
    return ItemResult(
        item=item,
        status=ItemStatus.SKIPPED,
        message=f"unhandled kind: {item.kind.value}",
    )


# ---------------------------------------------------------------------------
# Native -> native move
# ---------------------------------------------------------------------------


async def _native_move(item: PlanItem) -> ItemResult:
    """``shutil.move`` for files, dirs, and symlinks.

    ``shutil.move(src, dst)`` tries ``os.rename`` first; if that fails
    (cross-fs ``EXDEV``, dst is on a different device, etc.) it falls
    back to ``copy + delete``. This single call handles every case the
    Move design called out in the worklog:

    * **Same filesystem:** ``os.rename`` - O(1), instant for any size.
    * **Cross filesystem:** ``shutil.copy2`` (preserving mtime) + remove.

    Pre-check that ``dst`` does not already exist. ``shutil.move``'s
    behaviour when ``dst`` is an existing directory is "move src INTO
    dst" (i.e. dst becomes ``dst/basename(src)``) - that would silently
    nest user data deeper than intended. Failing fast is safer for v0;
    a future overwrite/merge prompt can layer on top.
    """
    src = item.src_path
    dst = _normalise_dst(item.dst_path)

    # Ensure the destination parent exists - mirrors the copy executor's
    # behaviour and lets typed destinations like "/tmp/new-name" work.
    parent = os.path.dirname(dst)
    if parent:
        try:
            await asyncio.to_thread(os.makedirs, parent, exist_ok=True)
        except OSError as exc:
            return ItemResult(
                item=item,
                status=ItemStatus.FAILED,
                message=(
                    f"mkdir parent: {type(exc).__name__}: {exc}"
                ),
            )

    # Guard against shutil.move's "move INTO existing directory" mode.
    if await asyncio.to_thread(os.path.lexists, dst):
        return ItemResult(
            item=item,
            status=ItemStatus.FAILED,
            message=f"destination already exists: {dst}",
        )

    if item.kind is Kind.OTHER:
        return ItemResult(
            item=item,
            status=ItemStatus.SKIPPED,
            message=f"unhandled kind: {item.kind.value}",
        )

    await asyncio.to_thread(shutil.move, src, dst)
    return ItemResult(item=item, status=ItemStatus.SUCCESS)


# ---------------------------------------------------------------------------
# Native delete
# ---------------------------------------------------------------------------


async def _native_delete(item: PlanItem) -> ItemResult:
    """``os.unlink`` for files / symlinks, ``shutil.rmtree`` for dirs.

    Big rmtree calls are dispatched via :func:`asyncio.to_thread` so the
    Textual event loop stays responsive - a recursive delete of a
    multi-GB tree shouldn't freeze the UI any more than the equivalent
    copy would.

    Symlinks are unlinked, NOT followed. Deleting a symlink should
    remove the link itself, never the target.
    """
    src = item.src_path

    if item.kind is Kind.DIR:
        await asyncio.to_thread(shutil.rmtree, src)
        return ItemResult(item=item, status=ItemStatus.SUCCESS)

    if item.kind in (Kind.FILE, Kind.SYMLINK):
        await asyncio.to_thread(os.unlink, src)
        return ItemResult(item=item, status=ItemStatus.SUCCESS)

    return ItemResult(
        item=item,
        status=ItemStatus.SKIPPED,
        message=f"unhandled kind: {item.kind.value}",
    )


# ---------------------------------------------------------------------------
# Native rename
# ---------------------------------------------------------------------------


async def _native_rename(item: PlanItem) -> ItemResult:
    """``os.rename(src, dst)`` for the single-entry rename case.

    ``os.rename`` is atomic on POSIX (``rename(2)``) and on Windows when
    both paths are on the same volume (``MoveFileEx`` with
    ``MOVEFILE_REPLACE_EXISTING`` is not used - we pre-check ``lexists``
    to refuse silent clobbers, consistent with Move's behaviour).

    Pre-check ``dst`` existence and refuse with a FAILED ItemResult
    rather than overwriting. Filesystem behaviour for ``rename`` over
    an existing dst differs across platforms (POSIX unlinks-and-renames
    atomically; Windows ``rename`` errors with FileExistsError) - the
    pre-check produces consistent UX. Future overwrite-prompt work
    would relax this.

    For Kind.OTHER (sockets, devices, fifos) we still attempt the
    rename - ``os.rename`` on POSIX works on any inode and the user
    asked for it. No special-case skip here.
    """
    src = item.src_path
    dst = _normalise_dst(item.dst_path)

    # The planner already verified dst_path stays in the same parent as
    # src_path - we don't need to mkdir anything. But pre-check
    # collision so we don't silently clobber.
    if await asyncio.to_thread(os.path.lexists, dst):
        return ItemResult(
            item=item,
            status=ItemStatus.FAILED,
            message=f"destination already exists: {dst}",
        )

    await asyncio.to_thread(os.rename, src, dst)
    return ItemResult(item=item, status=ItemStatus.SUCCESS)


# ---------------------------------------------------------------------------
# Native make-new
# ---------------------------------------------------------------------------


def _make_new_blocking(dst: str, kind: Kind) -> None:
    """Synchronous body of :func:`_native_make_new`.

    Factored out so the dispatch in ``_native_make_new`` can dispatch
    one ``asyncio.to_thread`` call rather than two. Keeps the per-kind
    branching readable and centralises the "ensure parent + create
    leaf" sequence in one place.

    DIR case: ``os.makedirs(dst, exist_ok=False)`` - the planner has
    already verified the leaf doesn't exist, but ``exist_ok=False``
    keeps the per-leaf safety belt fastened so a race between plan and
    apply surfaces as a clear ``FileExistsError`` rather than silent
    success.

    FILE case: ensure the parent chain via ``os.makedirs(parent,
    exist_ok=True)``, then ``open(dst, "x").close()``. ``"x"`` is the
    exclusive-create mode - identical safety belt to the DIR branch.
    The empty file lands with the current umask, matching ``touch``.
    """
    if kind is Kind.DIR:
        os.makedirs(dst, exist_ok=False)
        return
    if kind is Kind.FILE:
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # "x" mode is open-for-exclusive-create; raises FileExistsError
        # if the leaf already exists. Close immediately - the new file
        # is empty by design.
        with open(dst, "x"):
            pass
        return
    # Should not reach here - planner enforces _MAKEABLE kinds. Defensive
    # guard so a future kind doesn't silently fall through to success.
    raise ValueError(f"_make_new_blocking: unsupported kind {kind!r}")


async def _native_make_new(item: PlanItem) -> ItemResult:
    """Create a new directory or file at ``item.dst_path``.

    Lenient mode (per the 2026-05-22 design conversation):
    intermediate directories on the path to the leaf are created as
    needed. The leaf itself must not pre-exist - the planner already
    checked, but we use ``exist_ok=False`` / ``"x"`` mode at apply
    time too so a race between plan and apply surfaces as
    ``FileExistsError`` rather than a silent overwrite.

    No-op on ``Kind.OTHER`` and ``Kind.SYMLINK``: the planner rejects
    these with ``InvalidKind`` before they reach the executor, but we
    keep the defensive skip here so a future planner change doesn't
    accidentally produce an unhandled item.
    """
    dst = _normalise_dst(item.dst_path)

    if item.kind not in (Kind.DIR, Kind.FILE):
        return ItemResult(
            item=item,
            status=ItemStatus.SKIPPED,
            message=f"unhandled kind: {item.kind.value}",
        )

    try:
        await asyncio.to_thread(_make_new_blocking, dst, item.kind)
    except FileExistsError as exc:
        # Surface the racy-clobber as FAILED rather than letting the
        # generic Exception branch in apply_plan turn it into a less
        # specific message. Same shape as Rename's lexists pre-check
        # failure.
        return ItemResult(
            item=item,
            status=ItemStatus.FAILED,
            message=f"path already exists: {dst}",
        )
    return ItemResult(item=item, status=ItemStatus.SUCCESS)
