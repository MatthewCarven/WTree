"""Plan-time conflict detection and resolution.

Two halves, both pure-data-in/pure-data-out apart from the destination
stats they need:

* :func:`annotate_conflicts` runs at the tail of each destination-bearing
  planner (``plan_copy`` / ``plan_move`` / ``plan_rename``). It stats every
  ``PlanItem``'s ``dst_path`` and tags the item with a
  :class:`~wtree.ops.base.ConflictKind`. The *benign-merge rule* lives here:
  for ``OperationKind.COPY`` a directory landing on an existing directory is
  deliberately left ``NONE`` (directory merge is correct existing behaviour,
  not a conflict). Everything else that already exists is flagged.

* :func:`resolve_conflicts` runs after the user has chosen per-conflict
  resolutions in :class:`~wtree.widgets.conflict.ConflictDialog`. It rebuilds
  the plan: ``SKIP`` items (and the descendants of skipped directories) are
  dropped, ``RENAME`` items get a collision-free ``name (n)`` destination
  (with the new prefix cascaded onto any descendants), and ``OVERWRITE``
  items are tagged so the executor replaces the existing destination.

See ``design.md`` -> User interface -> Conflict resolution dialog.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import replace

from wtree.ops.base import (
    ConflictKind,
    OperationKind,
    Plan,
    PlanItem,
    Resolution,
    canonical_path,
)
from wtree.sources.base import EntrySource, Kind, ScanError


# Safety belt: never spin forever hunting a free " (n)" suffix.
_MAX_SUFFIX = 9999


# ---------------------------------------------------------------------------
# Self-target detection (same-directory / src == dst)
# ---------------------------------------------------------------------------


def _same_location(item: PlanItem) -> bool:
    """True when ``item``'s destination is its own source.

    Same source id *and* the canonical paths are equal - i.e. the user aimed
    an entry at the directory it already lives in, so the planner built
    ``dst_path == src_path``. :func:`~wtree.ops.base.canonical_path` collapses
    incidental dot / double-slash / trailing-slash differences a typed
    destination can carry (``/d/./proj/`` == ``/d/proj``), unifies separators
    (a typed Windows ``\\`` destination vs a POSIX ``/`` source), and folds
    case on case-insensitive platforms (Windows/NTFS) - the same judgement the
    executor's :func:`~wtree.ops.execute._would_destroy_source` guard uses.
    """
    if item.src_source_id != item.dst_source_id:
        return False
    return canonical_path(item.src_path) == canonical_path(item.dst_path)


def resolve_self_targets(plan: Plan) -> Plan:
    """Handle items whose destination is their own source, per operation.

    Runs **before** :func:`annotate_conflicts` in each destination-bearing
    planner. Pure data in / pure data out - no I/O (the Copy suffix is
    computed later, only if the user picks Rename, by
    :func:`resolve_conflicts`).

    * **COPY** - duplicate-in-place. The *topmost* self-targeted item gets
      ``conflict = SELF`` so it surfaces in
      :class:`~wtree.widgets.conflict.ConflictDialog` (defaulting to
      Rename). Descendants of that root are left ``NONE``: if the user
      renames the root, the resolution transform cascades the new prefix
      onto them; if they skip it, the skip-prefix logic drops the whole
      subtree. Marking only the root keeps the dialog to one row per
      user-tagged entry instead of one per walked leaf.
    * **MOVE / RENAME** - genuine no-op (the entry is already where it was
      asked to go). The item is **dropped**. Dropping rather than offering
      Overwrite is the whole point: an Overwrite on a move-onto-self would
      ``rmtree`` the destination, which *is* the source. Move emits one
      item per top-level tag (no descendants in the plan) and Rename is
      single-entry, so a flat drop suffices - no cascade needed.

    See ``design.md`` -> Conflict resolution dialog -> Same-location
    (self-target) handling.
    """
    if not plan.items:
        return plan

    if plan.kind is OperationKind.COPY:
        claimed_roots: list[str] = []
        new_items: list[PlanItem] = []
        for it in plan.items:
            if _same_location(it) and not any(
                _is_under(it.dst_path, root) for root in claimed_roots
            ):
                claimed_roots.append(it.dst_path)
                new_items.append(replace(it, conflict=ConflictKind.SELF))
            else:
                new_items.append(it)
        return replace(plan, items=new_items)

    # MOVE / RENAME (and any other destination-bearing kind): drop no-ops.
    new_items = [it for it in plan.items if not _same_location(it)]
    return replace(plan, items=new_items)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _conflict_for(existing_kind: Kind) -> ConflictKind:
    """Map a destination entry's :class:`Kind` to a :class:`ConflictKind`.

    ``SYMLINK`` and ``OTHER`` both collapse to ``OTHER`` - the dialog only
    needs the file/dir/other distinction, and a symlink in the way behaves
    like an opaque "other" for replace purposes.
    """
    if existing_kind is Kind.FILE:
        return ConflictKind.FILE
    if existing_kind is Kind.DIR:
        return ConflictKind.DIR
    return ConflictKind.OTHER


async def annotate_conflicts(
    plan: Plan,
    registry: Mapping[str, EntrySource],
) -> Plan:
    """Return a copy of ``plan`` with each item's ``conflict`` field set.

    Stats every item's ``dst_path`` via ``dst_source.entry_at`` (errors-as-
    data: a :class:`ScanError` means "nothing there", so no conflict). The
    benign-merge rule suppresses the flag for COPY directory-on-directory.

    A missing destination source leaves the item ``NONE`` - the executor
    will fail that pair as an unsupported transfer anyway, so there's no
    conflict to resolve.
    """
    if not plan.items:
        return plan

    new_items: list[PlanItem] = []
    for item in plan.items:
        new_items.append(await _annotate_item(plan.kind, item, registry))
    return replace(plan, items=new_items)


async def _annotate_item(
    kind: OperationKind,
    item: PlanItem,
    registry: Mapping[str, EntrySource],
) -> PlanItem:
    # Self-target guard: when ``dst_path`` *is* ``src_path`` the entry that
    # "already exists" at the destination is the operation's own source, not
    # a real collision. Stating it and flagging FILE/DIR would route the
    # item into the conflict dialog with replace semantics that could
    # overwrite (i.e. destroy) the source. Leave it untouched here -
    # :func:`resolve_self_targets` (run *before* this pass) has already
    # marked the topmost Copy self-target ``SELF`` and dropped Move/Rename
    # self-targets. Anything still matching here is a Copy descendant whose
    # prefix the resolution transform will rewrite, so it must stay NONE.
    #
    # Make-new is the exception: it mirrors ``src_path`` onto ``dst_path``
    # for executor symmetry, so *every* Make-new item is a structural
    # self-target - but its "self-target" is the leaf the user asked to
    # create, and that leaf genuinely already existing is a real collision.
    # Exempt MAKE_NEW so annotate stats the leaf and flags FILE/DIR/OTHER.
    # ``resolve_self_targets`` is never run for Make-new, so the mirror is
    # never mistaken for a duplicate-in-place.
    if kind is not OperationKind.MAKE_NEW and _same_location(item):
        return item
    dst_src = registry.get(item.dst_source_id)
    if dst_src is None:
        return item
    existing = await dst_src.entry_at(item.dst_path)
    if isinstance(existing, ScanError):
        # Not found (or parent unreadable) - either way nothing to clobber
        # that we can detect. Real I/O errors surface at execute time.
        return item
    # Benign-merge rule: a COPY of a directory onto an existing directory is
    # a merge, not a conflict. Only flag leaf collisions and type
    # mismatches.
    if (
        kind is OperationKind.COPY
        and item.kind is Kind.DIR
        and existing.kind is Kind.DIR
    ):
        return item
    return replace(item, conflict=_conflict_for(existing.kind))


# ---------------------------------------------------------------------------
# Collision-free naming
# ---------------------------------------------------------------------------


def suffixed_name(name: str, n: int, is_dir: bool) -> str:
    """Insert a `` (n)`` suffix into ``name``.

    Directories (and extension-less / dotfile / trailing-dot files) get the
    suffix appended whole: ``proj`` -> ``proj (1)``. Files with a real
    extension get it inserted before the last ``.X`` (the same last-``.X``
    rule the Rename smart cursor uses): ``report.txt`` -> ``report (1).txt``,
    ``foo.tar.gz`` -> ``foo.tar (1).gz``.
    """
    if is_dir:
        return f"{name} ({n})"
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        # No dot, leading-dot-only dotfile, or trailing dot - append whole.
        return f"{name} ({n})"
    return f"{name[:dot]} ({n}){name[dot:]}"


def _split(path: str) -> tuple[str, str]:
    """``(parent, basename)`` for a POSIX-style ``dst_path``, trailing-slash
    aware so ``"/a/proj/"`` -> ``("/a", "proj")``."""
    stripped = path.rstrip("/")
    return posixpath.dirname(stripped), posixpath.basename(stripped)


async def _free_dst(
    item: PlanItem,
    registry: Mapping[str, EntrySource],
) -> str:
    """Find the first `` (n)``-suffixed destination that doesn't exist."""
    dst_src = registry.get(item.dst_source_id)
    parent, base = _split(item.dst_path)
    is_dir = item.kind is Kind.DIR
    n = 1
    while n <= _MAX_SUFFIX:
        cand_name = suffixed_name(base, n, is_dir)
        cand = posixpath.join(parent, cand_name) if parent else cand_name
        if dst_src is None:
            return cand  # can't stat; best-effort first candidate
        existing = await dst_src.entry_at(cand)
        if isinstance(existing, ScanError):
            return cand
        n += 1
    # Exhausted - return the last candidate; the executor's exclusive
    # checks will surface any remaining collision as a failed item.
    return cand


async def preview_renamed_dst(
    item: PlanItem,
    registry: Mapping[str, EntrySource],
) -> str:
    """The collision-free `` (n)``-suffixed destination a RENAME of ``item``
    would produce - identical to what :func:`resolve_conflicts` lands on for a
    RENAME row, exposed so :class:`~wtree.widgets.conflict.ConflictDialog` can
    show a live preview of the rename target without re-deriving the suffix
    logic. Same per-item independence as the resolve pass (no cross-row
    cascade), so the previewed name matches the committed result when the
    filesystem is unchanged between dialog-open and apply.
    """
    return await _free_dst(item, registry)


# ---------------------------------------------------------------------------
# Resolution transform
# ---------------------------------------------------------------------------


def _is_under(path: str, prefix: str) -> bool:
    """True if ``path`` equals ``prefix`` or sits beneath it."""
    if path == prefix:
        return True
    prefix_sep = prefix if prefix.endswith("/") else prefix + "/"
    return path.startswith(prefix_sep)


def _rewrite_prefix(path: str, old: str, new: str) -> str:
    """Replace a leading ``old`` segment of ``path`` with ``new``.

    ``_rewrite_prefix("/d/proj/a.txt", "/d/proj", "/d/proj (1)")`` ->
    ``"/d/proj (1)/a.txt"``. Exact match maps ``old`` -> ``new``.
    """
    if path == old:
        return new
    old_sep = old if old.endswith("/") else old + "/"
    if path.startswith(old_sep):
        return new + path[len(old):]
    return path


async def resolve_conflicts(
    plan: Plan,
    resolutions: Sequence[Resolution],
    registry: Mapping[str, EntrySource],
    *,
    custom_dsts: Sequence[str | None] | None = None,
) -> Plan:
    """Rebuild ``plan`` according to the user's per-conflict ``resolutions``.

    ``resolutions`` is parallel to ``[i for i in plan.items if i.conflict is
    not ConflictKind.NONE]`` in plan order - exactly what
    :class:`~wtree.widgets.conflict.ConflictDialog` returns. Raises
    ``ValueError`` on a length mismatch (a wiring bug, not a user error).

    Transform rules (see module docstring): ``SKIP`` drops the item and any
    descendants of a skipped directory; ``RENAME`` rewrites ``dst_path`` to a
    collision-free name (cascading the new prefix onto descendants for
    directories) and clears the conflict; ``OVERWRITE`` keeps the item and
    sets ``resolution = OVERWRITE`` for the executor.
    """
    conflict_items = [
        i for i in plan.items if i.conflict is not ConflictKind.NONE
    ]
    if len(conflict_items) != len(resolutions):
        raise ValueError(
            f"resolve_conflicts: got {len(resolutions)} resolution(s) "
            f"for {len(conflict_items)} conflict(s)"
        )
    res_by_id = {id(it): r for it, r in zip(conflict_items, resolutions)}

    # Optional per-conflict custom RENAME destinations (parallel to
    # ``resolutions``): a fully-resolved, collision-verified dst the user typed
    # in the ConflictDialog editor. When present for a RENAME item it is used
    # verbatim instead of the auto `` (n)`` hunt; descendants cascade onto it
    # through the same rename_map rewrite.
    custom_by_id: dict[int, str | None] = {}
    if custom_dsts is not None:
        if len(custom_dsts) != len(conflict_items):
            raise ValueError(
                f"resolve_conflicts: got {len(custom_dsts)} custom dst(s) "
                f"for {len(conflict_items)} conflict(s)"
            )
        custom_by_id = {
            id(it): c for it, c in zip(conflict_items, custom_dsts)
        }

    # First pass: gather skip prefixes (skipped directories) and compute
    # collision-free destinations for renamed items.
    skip_prefixes: list[str] = []
    rename_map: dict[str, str] = {}  # old dst_path -> new dst_path
    for it in conflict_items:
        r = res_by_id[id(it)]
        if r is Resolution.SKIP:
            if it.kind is Kind.DIR:
                skip_prefixes.append(it.dst_path)
        elif r is Resolution.RENAME:
            custom = custom_by_id.get(id(it))
            rename_map[it.dst_path] = (
                custom if custom else await _free_dst(it, registry)
            )

    # Second pass: rebuild the items list.
    new_items: list[PlanItem] = []
    for it in plan.items:
        r = res_by_id.get(id(it), Resolution.PROCEED)
        if r is Resolution.SKIP:
            continue
        if any(_is_under(it.dst_path, p) for p in skip_prefixes):
            # Descendant of a skipped directory.
            continue

        new_dst = it.dst_path
        for old, new in rename_map.items():
            rewritten = _rewrite_prefix(new_dst, old, new)
            if rewritten != new_dst:
                new_dst = rewritten
                break

        if r is Resolution.OVERWRITE:
            new_items.append(
                replace(it, dst_path=new_dst, resolution=Resolution.OVERWRITE)
            )
        elif r is Resolution.RENAME:
            # Conflict cleared by the rename; proceed normally at new dst.
            new_items.append(
                replace(
                    it,
                    dst_path=new_dst,
                    conflict=ConflictKind.NONE,
                    resolution=Resolution.PROCEED,
                )
            )
        else:
            # PROCEED - either a never-conflicting item or a descendant of a
            # renamed directory whose prefix we just rewrote.
            if new_dst != it.dst_path:
                new_items.append(replace(it, dst_path=new_dst))
            else:
                new_items.append(it)

    return replace(plan, items=new_items)
