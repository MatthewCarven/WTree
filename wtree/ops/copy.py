"""Copy planner.

Plan-only in v0 — :func:`plan_copy` walks the source-side tags (recursing
into directories via ``EntrySource.scan``) and pairs each leaf with a
destination path under the destination tag. No files are actually copied;
the execute phase lands together with the destination modal dialog and the
progress reporter.

Cross-source planning is supported on the *plan* side already: the planner
treats every tag as ``(source_id, path)`` and asks the source registry for
the matching ``EntrySource``. Whether a specific source pairing can be
*applied* is a future concern of the execute dispatcher.
"""

from __future__ import annotations

import asyncio
import posixpath
from collections.abc import Callable, Mapping, Sequence
from typing import AsyncIterator

from wtree.ops.base import (
    PLAN_CHUNK_SIZE,
    OperationKind,
    Plan,
    PlanError,
    PlanItem,
    ScanCancelled,
    WalkedEntry,
    WalkSummary,
)
from wtree.ops.conflicts import annotate_conflicts, resolve_self_targets
from wtree.sources.base import Entry, EntrySource, Kind, ScanError
from wtree.tagged_set import Tag


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def walk_tags(
    tags: Sequence[Tag],
    registry: Mapping[str, EntrySource],
    *,
    on_progress: Callable[[int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> WalkSummary:
    """Expand every tag into a flat list of :class:`WalkedEntry`.

    Each tag in ``tags`` becomes:

    * one :class:`WalkedEntry` if it's a file / symlink / other;
    * one :class:`WalkedEntry` plus one per descendant if it's a directory;
    * one :class:`PlanError` if its ``source_id`` isn't in ``registry`` or
      the source can't classify it.

    The walk is depth-first to keep dst-path construction simple — a dir's
    own ``WalkedEntry`` lands in the list before any of its contents. This
    matters for the execute side, which will need to mkdir before copying
    the files into it.
    """
    summary = WalkSummary()
    seen = 0
    for tag in tags:
        # One group per tag, appended up front so ``entries_by_tag`` stays
        # index-parallel to ``tags`` even when a tag errors out below.
        group: list[WalkedEntry] = []
        summary.entries_by_tag.append(group)
        src = registry.get(tag.source_id)
        if src is None:
            summary.errors.append(
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message=f"no source registered for id {tag.source_id!r}",
                    cause="UnknownSource",
                )
            )
            continue

        # Classify the tag itself before deciding whether to recurse.
        top = await src.entry_at(tag.path)
        if isinstance(top, ScanError):
            summary.errors.append(
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message=top.message,
                    cause=top.cause,
                )
            )
            continue

        async for walked in _walk_from(src, tag.path, top):
            if isinstance(walked, PlanError):
                summary.errors.append(walked)
            else:
                summary.entries.append(walked)
                group.append(walked)
            seen += 1
            if seen % PLAN_CHUNK_SIZE == 0:
                if on_progress is not None:
                    on_progress(seen)
                await asyncio.sleep(0)
                if should_cancel is not None and should_cancel():
                    raise ScanCancelled()
    return summary


async def plan_copy(
    tags: Sequence[Tag],
    destination: Tag,
    registry: Mapping[str, EntrySource],
    *,
    on_progress: Callable[[int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Plan:
    """Build a :class:`Plan` of ``OperationKind.COPY`` from ``tags`` into
    ``destination``.

    Destination mapping rule (matches every shell ``cp`` you've ever used):

    * a tagged file ``/src/foo.txt`` lands at ``{dest}/foo.txt``
    * a tagged dir ``/src/proj`` lands at ``{dest}/proj`` (and its contents
      land under that, preserving the relative subtree)

    Paths under the destination are joined with POSIX semantics regardless
    of the source platform. This keeps ``dst_path`` predictable across
    cross-source plans (e.g. native → archive). The execute dispatcher
    will translate to the destination source's native separator when it
    actually applies the plan.
    """
    walk = await walk_tags(
        tags, registry, on_progress=on_progress, should_cancel=should_cancel
    )
    items: list[PlanItem] = []
    seen = 0

    # ``walk.entries_by_tag`` is index-parallel to ``tags`` - each sublist is
    # exactly the entries that tag's walk produced. Zipping is O(total
    # entries); the old per-tag prefix scan of the flat list was O(tags x
    # entries) and froze the UI on large tagged sets.
    for tag, group in zip(tags, walk.entries_by_tag):
        base = _basename(tag.path)
        if not base:
            # Unrooted destination - skip; the walk_tags error path already
            # caught unknown-source cases, so this is a true edge.
            continue
        for walked in group:
            rel = _relative_under(walked.path, tag.path)
            # Destination is dest + base + rel. ``base`` keeps the top-level
            # name; ``rel`` is "" for the top tag itself, deeper for descendants.
            dst_path = posixpath.join(destination.path, base, rel) if rel else posixpath.join(destination.path, base)
            items.append(
                PlanItem(
                    src_source_id=walked.source_id,
                    src_path=walked.path,
                    dst_source_id=destination.source_id,
                    dst_path=dst_path,
                    kind=walked.kind,
                    size=walked.size,
                )
            )
            seen += 1
            if seen % PLAN_CHUNK_SIZE == 0:
                if on_progress is not None:
                    on_progress(seen)
                await asyncio.sleep(0)
                if should_cancel is not None and should_cancel():
                    raise ScanCancelled()

    plan = Plan(kind=OperationKind.COPY, items=items, errors=walk.errors)
    # Self-target pass first: mark the topmost copy-into-own-dir item SELF
    # so it surfaces in the dialog (duplicate-in-place). annotate_conflicts
    # then skips every self-targeted item, so the SELF flag survives and
    # descendants stay NONE for the rename cascade.
    plan = resolve_self_targets(plan)
    return await annotate_conflicts(
        plan, registry, on_progress=on_progress, should_cancel=should_cancel
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _walk_from(
    src: EntrySource, top_path: str, top_entry: Entry
) -> AsyncIterator[WalkedEntry | PlanError]:
    """Yield ``top_entry`` and (if it's a directory) all descendants.

    Errors encountered mid-walk become :class:`PlanError` items; they do
    not abort the walk. This mirrors ``EntrySource.scan``'s errors-as-data
    contract one layer up.
    """
    yield WalkedEntry(
        source_id=src.source_id,
        path=top_path,
        kind=top_entry.kind,
        size=top_entry.size,
    )

    if top_entry.kind is not Kind.DIR:
        return

    # Iterative DFS to avoid recursion-depth limits on real trees. Stack of
    # (parent_path, parent_entry) — only directories ever land here.
    stack: list[str] = [top_path]
    while stack:
        parent = stack.pop()
        async for child in src.scan(parent):
            if isinstance(child, ScanError):
                yield PlanError(
                    source_id=src.source_id,
                    path=child.path,
                    message=child.message,
                    cause=child.cause,
                )
                continue
            child_path = posixpath.join(parent, child.name)
            yield WalkedEntry(
                source_id=src.source_id,
                path=child_path,
                kind=child.kind,
                size=child.size,
            )
            if child.kind is Kind.DIR:
                stack.append(child_path)


def _basename(path: str) -> str:
    """POSIX-style basename, with one twist: a trailing slash is stripped
    first so ``"/foo/bar/"`` → ``"bar"`` (and not ``""``).
    """
    stripped = path.rstrip("/")
    return posixpath.basename(stripped)


def _relative_under(path: str, root: str) -> str:
    """Return ``path`` expressed relative to ``root``.

    ``_relative_under("/a/b/c", "/a")`` → ``"b/c"``;
    ``_relative_under("/a", "/a")`` → ``""``.
    """
    if path == root:
        return ""
    root_with_sep = root if root.endswith("/") else root + "/"
    if path.startswith(root_with_sep):
        return path[len(root_with_sep):]
    # Caller asked for a path that isn't actually under root — return the
    # path unchanged. This shouldn't happen given the walk's structure.
    return path
