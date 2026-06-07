"""Delete planner.

Sibling of :mod:`wtree.ops.copy` and :mod:`wtree.ops.move`. Unlike both,
Delete has no destination - the operation is "remove this thing" - so
the :class:`PlanItem`'s ``dst_*`` fields are sentinel: ``dst_source_id``
equals ``src_source_id`` and ``dst_path`` is the empty string. The
executor's dispatch table ignores ``dst_*`` for delete items.

Like Move, the planner emits **one ``PlanItem`` per top-level tag** -
``shutil.rmtree`` handles whole subtrees in one syscall and there's no
benefit to flattening.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence

from wtree.ops.base import (
    collapse_nested_tags,
    OperationKind,
    Plan,
    PlanError,
    PlanItem,
)
from wtree.sources.base import EntrySource, ScanError
from wtree.tagged_set import Tag


async def plan_delete(
    tags: Sequence[Tag],
    registry: Mapping[str, EntrySource],
) -> Plan:
    """Build a :class:`Plan` of :attr:`OperationKind.DELETE` from ``tags``.

    One ``PlanItem`` per top-level tag. ``dst_source_id`` mirrors
    ``src_source_id`` and ``dst_path`` is ``""`` - delete items have no
    destination semantics.

    Tagging the source root (``"/"``) is rejected with an
    ``UnrootedTag`` error (matching :func:`plan_move`'s guard): wiping
    a whole source root is almost certainly not what the user meant,
    and refusing here keeps the executor honest.
    """
    tags, collapsed = collapse_nested_tags(tags)

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

        if not _basename(tag.path):
            # Refuse to delete the source root - same conservative stance
            # plan_move takes for Unrooted tags. A user who really wants
            # this can navigate into the root and tag the children.
            errors.append(
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message="cannot delete source root",
                    cause="UnrootedTag",
                )
            )
            continue

        items.append(
            PlanItem(
                src_source_id=tag.source_id,
                src_path=tag.path,
                dst_source_id=tag.source_id,  # sentinel - unused for DELETE
                dst_path="",                  # sentinel - unused for DELETE
                kind=entry.kind,
                size=entry.size,
            )
        )

    return Plan(
        kind=OperationKind.DELETE,
        collapsed_tags=collapsed,
        items=items,
        errors=errors,
    )


def _basename(path: str) -> str:
    """POSIX-style basename, trailing-slash-aware (matches plan_move)."""
    stripped = path.rstrip("/")
    return posixpath.basename(stripped)
