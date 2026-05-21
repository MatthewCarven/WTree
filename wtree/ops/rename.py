"""Rename planner.

The black sheep of the v0 op family. Where Copy / Move / Delete all
take a ``Sequence[Tag]`` and operate on the design's Selection rule
(tagged set if non-empty, else cursor), Rename is **single-entry only**
per ``design.md`` Selection rule:

    Rename is the exception: it is a single-entry operation in v0.
    If R is pressed while the tagged set is non-empty, the operation
    is rejected with a status-line nudge ("rename works on one entry;
    clear tags first"). Batch rename is parking-lot for post-v0.

That rejection happens at the action layer (in :class:`WTreeApp`); by
the time ``plan_rename`` is called there's exactly one tag.

Semantics: rename changes only the **basename**. The new name is joined
under the same parent directory. Typing a name with a path separator
in it would be a move-disguised-as-rename, which violates user
expectations - so the planner rejects names containing ``/`` or
``os.sep`` with an ``InvalidName`` error.

PlanItem shape: ``src_path`` = current path; ``dst_path`` = parent + new
name; both source ids are the same (renames never cross sources). The
executor's ``RENAME`` branch in ``apply_plan`` routes to ``os.rename``,
which is atomic on every supported filesystem.
"""

from __future__ import annotations

import os
import posixpath
from collections.abc import Mapping

from wtree.ops.base import (
    OperationKind,
    Plan,
    PlanError,
    PlanItem,
)
from wtree.sources.base import EntrySource, ScanError
from wtree.tagged_set import Tag


async def plan_rename(
    tag: Tag,
    new_name: str,
    registry: Mapping[str, EntrySource],
) -> Plan:
    """Build a :class:`Plan` of :attr:`OperationKind.RENAME`.

    ``tag`` is the single entry being renamed. ``new_name`` is the new
    basename - no path separators allowed. ``registry`` is the standard
    ``{source_id: EntrySource}`` map.

    Rejects (returns a Plan with one ``PlanError`` and no items) when:

    * ``tag.source_id`` is unknown in the registry (``UnknownSource``);
    * ``entry_at(tag.path)`` fails (source-level error, e.g. file
      missing);
    * ``new_name`` is empty or whitespace (``InvalidName``);
    * ``new_name`` contains a path separator (``InvalidName``);
    * ``new_name`` is exactly the current basename (``NoChange``) - no
      point queuing a no-op.
    """
    errors: list[PlanError] = []

    src = registry.get(tag.source_id)
    if src is None:
        return Plan(
            kind=OperationKind.RENAME,
            errors=[
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message=f"no source registered for id {tag.source_id!r}",
                    cause="UnknownSource",
                )
            ],
        )

    entry = await src.entry_at(tag.path)
    if isinstance(entry, ScanError):
        return Plan(
            kind=OperationKind.RENAME,
            errors=[
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message=entry.message,
                    cause=entry.cause,
                )
            ],
        )

    # Validate the new name. Strip incidental whitespace so a trailing
    # space (common typo) doesn't propagate to the filesystem.
    name = new_name.strip()
    if not name:
        return Plan(
            kind=OperationKind.RENAME,
            errors=[
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message="new name is empty",
                    cause="InvalidName",
                )
            ],
        )

    # Reject any path separator - rename is basename-only. ``os.sep`` and
    # ``/`` are both checked so the rejection works whether the source
    # uses native or POSIX-style separators in tag paths.
    if "/" in name or os.sep in name or "\\" in name:
        return Plan(
            kind=OperationKind.RENAME,
            errors=[
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message=(
                        f"new name {name!r} contains a path separator; "
                        "rename is basename-only - use Move (M / F6) "
                        "for cross-directory operations"
                    ),
                    cause="InvalidName",
                )
            ],
        )

    current_basename = _basename(tag.path)
    if name == current_basename:
        return Plan(
            kind=OperationKind.RENAME,
            errors=[
                PlanError(
                    source_id=tag.source_id,
                    path=tag.path,
                    message=f"new name is identical to current ({name!r})",
                    cause="NoChange",
                )
            ],
        )

    parent = _parent(tag.path)
    # POSIX-style join in the planner - executor normalises on Windows.
    dst_path = posixpath.join(parent, name) if parent else name

    item = PlanItem(
        src_source_id=tag.source_id,
        src_path=tag.path,
        dst_source_id=tag.source_id,  # rename never crosses sources
        dst_path=dst_path,
        kind=entry.kind,
        size=entry.size,
    )
    return Plan(kind=OperationKind.RENAME, items=[item], errors=errors)


def _basename(path: str) -> str:
    """POSIX-style basename, trailing-slash-aware (matches plan_move)."""
    stripped = path.rstrip("/")
    return posixpath.basename(stripped)


def _parent(path: str) -> str:
    """Parent dir of ``path``, trailing-slash-aware.

    ``_parent("/foo/bar")`` -> ``"/foo"``;
    ``_parent("/foo/bar/")`` -> ``"/foo"``;
    ``_parent("/foo")`` -> ``"/"``;
    ``_parent("foo")`` -> ``""``.
    """
    stripped = path.rstrip("/")
    return posixpath.dirname(stripped)
