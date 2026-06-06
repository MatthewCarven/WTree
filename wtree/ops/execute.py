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
    Resolution,
    canonical_path,
)
from wtree.sources.base import EntrySource, Kind

# Callback signature for per-item progress. Sync; the queue/UI bridges
# any async-ness on its side. Kept tiny on purpose - the progress
# dialog (post-v0) will subscribe to higher-level OperationQueue events
# rather than this raw stream.
ProgressCb = Callable[[ItemResult], None]

# Callback signature for per-chunk byte progress (2026-05-25 progress
# dialog). Called from inside :func:`_chunked_copy` (which runs in a
# worker thread via :func:`asyncio.to_thread`) once per chunk written.
# Receives ``(item, bytes_done_in_item, item_size)``; returns ``True``
# to continue, ``False`` to cancel the copy (the chunk loop deletes
# the partial destination file and the item ends as FAILED).
#
# THREADING: the callback may fire from a worker thread, NOT the
# event loop. Subscribers must not touch event-loop-affine state
# directly; see design.md -> Progress dialog -> Concurrency
# assumptions.
BytesProgressCb = Callable[[PlanItem, int, int], bool]


async def apply_plan(
    plan: Plan,
    registry: Mapping[str, EntrySource],
    progress: ProgressCb | None = None,
    bytes_progress: BytesProgressCb | None = None,
    is_cancelled: Callable[[], bool] | None = None,
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

    ``is_cancelled`` (optional) is polled at the top of the per-item
    loop. Once it returns True, every remaining item is marked
    ``ItemStatus.SKIPPED`` with message ``"cancelled"`` and the
    per-item ``progress`` callback still fires for each so the dialog's
    items counter stays consistent. The in-flight item that was
    cancelled mid-copy (via ``bytes_progress`` returning False) keeps
    its existing ``FAILED("cancelled")`` status - distinct from the
    not-yet-started SKIPPED items so ``OperationResult.summary()``
    surfaces the difference. See design.md -> Progress dialog ->
    Mid-plan cancellation.
    """
    results: list[ItemResult] = []
    cancelled = False
    for item in plan.items:
        if not cancelled and is_cancelled is not None and is_cancelled():
            cancelled = True
        if cancelled:
            # Don't even try; mark and continue so the per-item
            # progress callback still fires (keeps the dialog's
            # items counter consistent with len(plan.items)).
            result = ItemResult(
                item=item,
                status=ItemStatus.SKIPPED,
                message="cancelled",
            )
        else:
            try:
                result = await _apply_item(
                    plan.kind, item, registry, bytes_progress
                )
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
    bytes_progress: BytesProgressCb | None = None,
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
            return await _native_copy(item, bytes_progress)
        if kind is OperationKind.MOVE:
            return await _native_move(item, bytes_progress)
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


def _would_destroy_source(dst: str, src: str) -> bool:
    """True if removing ``dst`` would also remove the operation's ``src``.

    Two cases: ``dst`` *is* ``src`` (move/copy/rename of an entry onto
    itself), or ``dst`` is an ancestor *directory* of ``src`` (so an
    ``rmtree(dst)`` would take ``src`` down with it). Either way the
    OVERWRITE pre-step must refuse. Comparison goes through
    :func:`~wtree.ops.base.canonical_path` (separator + dot collapse, case
    fold on Windows) - the same judgement plan-time self-target detection
    uses - so a typed-``\\`` or different-case ``dst`` is still caught. No
    filesystem I/O (no ``realpath``): we compare the paths the plan carries,
    the cheap belt-and-suspenders we want, not a symlink-resolving audit. The
    canonical form is ``/``-separated, so the ancestor test uses ``/``.
    """
    ndst, nsrc = canonical_path(dst), canonical_path(src)
    if ndst == nsrc:
        return True
    return nsrc.startswith(ndst + "/")


def _remove_existing_blocking(dst: str, src: str | None = None) -> None:
    """Remove whatever currently occupies ``dst`` (the OVERWRITE pre-step).

    Synchronous - call via :func:`asyncio.to_thread`. Symlinks are unlinked
    (never followed - we remove the link, not its target); real directories
    are recursively removed; everything else is unlinked. A ``dst`` that no
    longer exists is a silent no-op (a benign TOCTOU race - the obstacle
    cleared itself).

    Used only when a :class:`PlanItem` carries ``Resolution.OVERWRITE``;
    the conflict was detected at plan time and the user explicitly chose to
    replace. "Replace, not merge" - see ``design.md`` -> Conflict resolution
    dialog.

    Defence in depth: when ``src`` is supplied and removing ``dst`` would
    also destroy it (``dst == src`` or ``dst`` is an ancestor of ``src``),
    raise ``ValueError`` rather than ``rmtree`` the source. The plan-time
    self-target pass (:func:`wtree.ops.conflicts.resolve_self_targets`)
    already rewrites Copy and drops Move/Rename self-targets, so this should
    be unreachable in normal flow - but a planner bug or a post-plan race
    must never silently eat the user's data. The raise is caught by
    :func:`apply_plan` and surfaces as a ``FAILED`` item.
    """
    if src is not None and _would_destroy_source(dst, src):
        raise ValueError(
            f"refusing to overwrite {dst!r}: it is (or contains) the "
            f"operation source {src!r}"
        )
    if os.path.islink(dst):
        os.unlink(dst)
    elif os.path.isdir(dst):
        shutil.rmtree(dst)
    elif os.path.lexists(dst):
        os.unlink(dst)


# ---------------------------------------------------------------------------
# Native -> native copy
# ---------------------------------------------------------------------------


async def _native_copy(
    item: PlanItem,
    bytes_progress: BytesProgressCb | None = None,
) -> ItemResult:
    """``shutil.copy2`` for files, ``os.makedirs`` for dirs, recreate
    symlinks. Each kind handled explicitly so the failure modes have
    clean per-kind error messages.

    Big files are dispatched via :func:`asyncio.to_thread` so the
    Textual event loop stays responsive during large copies. The OS
    handles the actual byte movement; we just don't block the main
    thread waiting on it.

    When ``bytes_progress`` is supplied (progress-dialog era) the FILE
    branch uses :func:`_chunked_copy` so per-chunk progress can be
    reported and cancellation can break the copy mid-file. When it's
    None (tests, headless usage) we fall through to ``shutil.copy2``
    which is faster for small files and preserves the long-standing
    contract.
    """
    src = item.src_path
    dst = _normalise_dst(item.dst_path)

    # OVERWRITE pre-step: clear whatever's in the way so the copy lands on a
    # clean destination. Needed for type-mismatch collisions (dir onto an
    # existing file, file onto an existing dir); a file-onto-file overwrite
    # would clobber anyway, but removing first keeps the behaviour uniform.
    if item.resolution is Resolution.OVERWRITE:
        await asyncio.to_thread(_remove_existing_blocking, dst, src)

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
        if bytes_progress is None:
            # Fast path: no progress callback, use shutil.copy2 which
            # is faster than our pure-Python chunk loop for small files
            # and preserves the metadata in one call.
            await asyncio.to_thread(shutil.copy2, src, dst)
            return ItemResult(item=item, status=ItemStatus.SUCCESS)
        # Progress-aware path: chunked copy with per-chunk callback,
        # then copystat to restore mtime/permissions (matching copy2).
        cancelled = await asyncio.to_thread(
            _chunked_copy, item, src, dst, bytes_progress
        )
        if cancelled:
            return ItemResult(
                item=item,
                status=ItemStatus.FAILED,
                message="cancelled",
            )
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
# Chunked file copy (with per-chunk callback + cancellation)
# ---------------------------------------------------------------------------


def _chunked_copy(
    item: PlanItem,
    src: str,
    dst: str,
    bytes_progress: BytesProgressCb,
) -> bool:
    """Pure-Python chunked file copy with per-chunk progress callback.

    Runs inside :func:`asyncio.to_thread` - everything here is
    synchronous I/O. Returns ``True`` if the callback asked to cancel
    (in which case the partial destination file has already been
    deleted), ``False`` on successful completion.

    Chunk size is :data:`wtree.ops.queue.COPY_CHUNK_SIZE`. After the
    data is copied :func:`shutil.copystat` restores mtime / permissions
    so the result matches what ``shutil.copy2`` would have produced.

    THREADING: ``bytes_progress`` is invoked from this worker thread,
    NOT the event loop. Subscribers must not touch event-loop-affine
    state directly. The default queue-side callback only mutates
    GIL-atomic single-int attributes, which is safe.
    """
    # Import here to avoid a circular import (queue.py imports from us).
    from wtree.ops.queue import COPY_CHUNK_SIZE

    item_size = item.size
    bytes_done = 0
    cancelled = False
    try:
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            # Fire one initial callback at zero so subscribers learn the
            # item's size up front (useful for per-item progress UI).
            if not bytes_progress(item, 0, item_size):
                cancelled = True
            while not cancelled:
                chunk = fsrc.read(COPY_CHUNK_SIZE)
                if not chunk:
                    break
                fdst.write(chunk)
                bytes_done += len(chunk)
                if not bytes_progress(item, bytes_done, item_size):
                    cancelled = True
                    break
    except Exception:
        # Clean up the partial destination before propagating, so a
        # failed copy doesn't leave a truncated file behind.
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise

    if cancelled:
        try:
            os.unlink(dst)
        except OSError:
            pass
        return True

    # Mirror shutil.copy2's metadata-preservation: mtime + permissions.
    try:
        shutil.copystat(src, dst)
    except OSError:
        # copystat failure is non-fatal - the data is on disk; metadata
        # restore is best-effort (matches shutil's own forgiveness on
        # filesystems that don't support all the bits).
        pass
    return False


# ---------------------------------------------------------------------------
# Native -> native move
# ---------------------------------------------------------------------------


async def _native_move(
    item: PlanItem,
    bytes_progress: BytesProgressCb | None = None,
) -> ItemResult:
    """Move ``item`` from src to dst, threading bytes progress for
    cross-filesystem file moves so the progress dialog can render
    ``Rate`` / ``Drag`` instead of em-dashes.

    Dispatch (matches design.md -> Progress dialog -> Move executor
    chunk hook):

    1. **Always try** ``os.rename(src, dst)`` first. Atomic, instant
       same-fs for any kind. No bytes flow; the progress dialog's
       zero-guard correctly renders em-dashes for ``Rate`` / ``Drag``.
    2. On ``OSError`` (cross-fs ``EXDEV`` on POSIX,
       ``ERROR_NOT_SAME_DEVICE`` on Windows; caught generically so
       the same code works on both platforms), dispatch by kind:

       * **FILE**: ``_chunked_copy(item, src, dst, bytes_progress)``
         then ``os.unlink(src)``. Cancel mid-copy returns
         ``FAILED("cancelled")`` with the partial dst cleaned and
         the source intact. If ``bytes_progress`` is None (test
         contract / headless), fall through to ``shutil.move`` for
         the file too - mirrors ``_native_copy``'s
         callback-present-or-fast-path pattern.
       * **SYMLINK**: ``os.readlink`` + ``os.symlink`` at dst +
         ``os.unlink`` src. No bytes flow.
       * **DIR**: ``shutil.move`` (which does ``copytree + rmtree``
         internally). Recursive walked-progress for cross-fs dir
         moves is parked - rare case, real code, mid-walk cancel
         and mid-dir errors deserve their own pass.
       * **OTHER**: SKIPPED (matches the copy executor).

    Pre-check that ``dst`` does not already exist. ``shutil.move``'s
    behaviour when ``dst`` is an existing directory is "move src INTO
    dst" (i.e. dst becomes ``dst/basename(src)``); failing fast is
    safer for v0. A future overwrite/merge prompt can layer on top.
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

    # Guard against shutil.move's "move INTO existing directory" mode. When
    # the user resolved the plan-time conflict with OVERWRITE, clear the
    # destination first (replace semantics); otherwise an existing dst is a
    # TOCTOU race that arrived after planning - fail it.
    if await asyncio.to_thread(os.path.lexists, dst):
        if item.resolution is Resolution.OVERWRITE:
            await asyncio.to_thread(_remove_existing_blocking, dst, src)
        else:
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

    # Always try the rename fast-path first. Atomic same-fs for any
    # kind; instant. shutil.move does this internally too, but we
    # need to split it apart so the cross-fs fallback can call our
    # chunked path with the byte-progress callback.
    try:
        await asyncio.to_thread(os.rename, src, dst)
        return ItemResult(item=item, status=ItemStatus.SUCCESS)
    except OSError:
        # Cross-filesystem (EXDEV) or some other rename
        # incompatibility. Fall through to per-kind dispatch.
        pass

    if item.kind is Kind.FILE:
        if bytes_progress is None:
            # Fast path: no progress callback, use shutil.move which
            # is well-tested for cross-fs file moves. Preserves the
            # pre-progress-dialog test contract.
            await asyncio.to_thread(shutil.move, src, dst)
            return ItemResult(item=item, status=ItemStatus.SUCCESS)
        # Progress-aware path: chunked copy then unlink source.
        cancelled = await asyncio.to_thread(
            _chunked_copy, item, src, dst, bytes_progress
        )
        if cancelled:
            # _chunked_copy already cleaned the partial dst.
            # Source is intact.
            return ItemResult(
                item=item,
                status=ItemStatus.FAILED,
                message="cancelled",
            )
        try:
            await asyncio.to_thread(os.unlink, src)
        except OSError as exc:
            # Copy succeeded but the source unlink failed; the user
            # now has the file in both places. Surface a clearer
            # error than shutil.move would have given.
            return ItemResult(
                item=item,
                status=ItemStatus.FAILED,
                message=(
                    f"unlink source after copy: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
        return ItemResult(item=item, status=ItemStatus.SUCCESS)

    if item.kind is Kind.SYMLINK:
        # Recreate the link at dst, then unlink src. No bytes flow.
        try:
            target = await asyncio.to_thread(os.readlink, src)
        except OSError as exc:
            return ItemResult(
                item=item,
                status=ItemStatus.FAILED,
                message=f"readlink: {type(exc).__name__}: {exc}",
            )
        try:
            await asyncio.to_thread(os.symlink, target, dst)
        except OSError as exc:
            return ItemResult(
                item=item,
                status=ItemStatus.FAILED,
                message=f"symlink: {type(exc).__name__}: {exc}",
            )
        try:
            await asyncio.to_thread(os.unlink, src)
        except OSError as exc:
            return ItemResult(
                item=item,
                status=ItemStatus.FAILED,
                message=(
                    f"unlink source after symlink: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
        return ItemResult(item=item, status=ItemStatus.SUCCESS)

    if item.kind is Kind.DIR:
        # Cross-fs DIR moves keep shutil.move (copytree + rmtree).
        # Recursive walked-progress is parked - see design.md.
        await asyncio.to_thread(shutil.move, src, dst)
        return ItemResult(item=item, status=ItemStatus.SUCCESS)

    # Unreachable - OTHER handled above. Defensive.
    return ItemResult(
        item=item,
        status=ItemStatus.SKIPPED,
        message=f"unhandled kind: {item.kind.value}",
    )


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
    # src_path - we don't need to mkdir anything. But pre-check collision so
    # we don't silently clobber. OVERWRITE (chosen at plan time) clears the
    # destination first; otherwise an existing dst is a post-plan TOCTOU
    # race and we fail rather than destroy.
    if await asyncio.to_thread(os.path.lexists, dst):
        if item.resolution is Resolution.OVERWRITE:
            await asyncio.to_thread(_remove_existing_blocking, dst, src)
        else:
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

    # OVERWRITE pre-step: the user resolved a plan-time leaf collision by
    # choosing to replace whatever occupies it. Clear it before the
    # exclusive create. ``src`` is deliberately *not* passed to the
    # self-destruct guard: Make-new mirrors ``src_path`` onto ``dst_path``,
    # so ``_would_destroy_source`` would always fire - but the mirror is
    # structural, there is no real source to protect, and the existing entry
    # at the leaf is exactly what the user asked to replace.
    if item.resolution is Resolution.OVERWRITE:
        await asyncio.to_thread(_remove_existing_blocking, dst)

    try:
        await asyncio.to_thread(_make_new_blocking, dst, item.kind)
    except FileExistsError:
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
