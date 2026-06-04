"""Move planner.

Cousin of :mod:`wtree.ops.copy`, but with a different shape: where Copy
recurses through directories to emit one ``PlanItem`` per leaf, Move
emits exactly **one ``PlanItem`` per top-level tag**. The reason is the
underlying syscall: ``os.rename`` (and ``shutil.move`` which wraps it)
moves a directory and its entire subtree in a single operation when src
and dst share a filesystem. Flattening the tree first would force N
calls where 1 suffices.

When src and dst are on *different* filesystems, ``shutil.move`` falls
back to copy + delete - still one logical PlanItem per top-level tag,
just with a slower implementation under the hood. The executor handles
both cases via :func:`shutil.move`.

Cross-source moves (e.g. native -> SFTP) are not supported in v0; the
executor returns FAILED for any pair other than ``("native","native")``.
The planner still emits items for them so the user sees what *would*
have happened.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence

from wtree.ops.base import (
    OperationKind,
    Plan,
    PlanError,
    PlanItem,
)
from wtree.ops.conflicts import annotate_conflicts, resolve_self_targets
from wtree.sources.base import EntrySource, ScanError
from wtree.tagged_set import Tag


async def plan_move(
    tags: Sequence[Tag],
    destination: Tag,
    registry: Mapping[str, EntrySource],
) -> Plan:
    """Build a :class:`Plan` of :attr:`OperationKind.MOVE` from ``tags``
    into ``destination``.

    Destination mapping is the same as :func:`plan_copy`:

    * a tagged file ``/src/foo.txt`` lands at ``{dest}/foo.txt``
    * a tagged dir ``/src/proj`` lands at ``{dest}/proj`` (with subtree)

    Unlike :func:`plan_copy`, the planner does NOT recurse - one
    ``PlanItem`` per top-level tag. The executor uses ``shutil.move``
    which handles whole-subtree moves in one call.

    Paths under the destination are joined with POSIX semantics
    regardless of the source platform; the execute dispatcher
    translates to native separators when applying the plan.
    """
    items: list[PlanItem] = []
    errors: list[PlanError] = []

    for tag in tags:
        src = registry.get(tag.source_id)
        if src is None:
            errors.append(
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message=f"no source registered for id {tag.source_id!r}",
                    cause="UnknownSource",
                )
            )
            continue

        entry = await src.entry_at(tag.path)
        if isinstance(entry, ScanError):
            errors.append(
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message=entry.message,
                    cause=entry.cause,
                )
            )
            continue

        base = _basename(tag.path)
        if not base:
            # Unrooted - tag was the bare source root (e.g. "/"). Skip
            # rather than produce a garbage destination.
            errors.append(
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message="cannot move source root",
                    cause="UnrootedTag",
                )
            )
            continue

        dst_path = posixpath.join(destination.path, base)
        items.append(
            PlanItem(
                src_source_id=tag.source_id,
                src_path=tag.path,
                dst_source_id=destination.source_id,
                dst_path=dst_path,
                kind=entry.kind,
                size=entry.size,
            )
        )

    plan = Plan(kind=OperationKind.MOVE, items=items, errors=errors)
    # Drop self-targeted moves (entry into its own directory) - a genuine
    # no-op. Critically this happens *before* annotate_conflicts so such an
    # item can never be flagged and offered Overwrite, which would rmtree
    # the destination that is also the source.
    plan = resolve_self_targets(plan)
    return await annotate_conflicts(plan, registry)


def _basename(path: str) -> str:
    """POSIX-style basename, with one twist: a trailing slash is stripped
    first so ``"/foo/bar/"`` -> ``"bar"`` (and not ``""``).
    """
    stripped = path.rstrip("/")
    return posixpath.basename(stripped)
