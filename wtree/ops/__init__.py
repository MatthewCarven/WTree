"""Generic file-operation scaffold.

The ``EntrySource`` layer (``wtree/sources``) only enumerates - it does
not copy, move, delete, or rename. Operations live here, one layer
above, so they can mix sources (NativeSource + future ArchiveSource +
future SFTPSource) and share planning, capability negotiation, queue
management, and progress reporting.

Layout:

* ``base.py`` - pure data types (Plan, PlanItem, OperationResult, etc.)
* ``copy.py`` - the copy planner (walk_tags + plan_copy)
* ``move.py`` - the move planner (plan_move; top-level items only,
  executor handles subtrees via ``shutil.move``)
* ``delete.py`` - the delete planner (plan_delete; top-level items
  only, no destination, executor handles subtrees via
  ``shutil.rmtree``)
* ``rename.py`` - the rename planner (plan_rename; SINGLE tag,
  basename-only, executor uses ``os.rename``)
* ``make_new.py`` - the make-new planner (plan_make_new; NO tags,
  parent + name + kind, lenient subdir creation, executor uses
  ``os.makedirs`` or ``open(path, "x")``)
* ``conflicts.py`` - plan-time conflict detection (annotate_conflicts)
  and the resolution transform (resolve_conflicts)
* ``execute.py`` - the apply_plan dispatcher (native->native in v0)
* ``queue.py`` - serialised :class:`OperationQueue` (one plan at a time,
  background worker, callbacks for UI updates)

See ``design.md`` "Operation semantics vary by source pairing" for the
rationale on why operations live above ``EntrySource`` rather than as
methods on it. The queue's serial-FIFO design call is recorded in
``worklog.md`` 2026-05-21 entry. The "one item per top-level tag"
shape for Move/Delete (vs Copy's flatten-then-emit) is rationalised in
``move.py`` / ``delete.py`` module docstrings. Rename is single-entry
only - see ``rename.py`` docstring and ``design.md`` Selection rule.
Make-new is no-tags, no-source - see ``make_new.py`` docstring.
"""

from wtree.ops.base import (
    ConflictKind,
    ItemResult,
    ItemStatus,
    OperationKind,
    OperationResult,
    Plan,
    PlanError,
    PlanItem,
    Resolution,
    WalkSummary,
    WalkedEntry,
)
from wtree.ops.conflicts import (
    resolve_conflicts,
    resolve_self_targets,
    suffixed_name,
)
from wtree.ops.copy import plan_copy, walk_tags
from wtree.ops.delete import plan_delete
from wtree.ops.execute import apply_plan
from wtree.ops.make_new import plan_make_new
from wtree.ops.move import plan_move
from wtree.ops.queue import OperationQueue
from wtree.ops.rename import plan_rename, select_range_for_rename

__all__ = [
    "ConflictKind",
    "ItemResult",
    "ItemStatus",
    "OperationKind",
    "OperationQueue",
    "OperationResult",
    "Plan",
    "PlanError",
    "PlanItem",
    "Resolution",
    "WalkSummary",
    "WalkedEntry",
    "apply_plan",
    "plan_copy",
    "plan_delete",
    "plan_make_new",
    "plan_move",
    "plan_rename",
    "resolve_conflicts",
    "resolve_self_targets",
    "select_range_for_rename",
    "suffixed_name",
    "walk_tags",
]
