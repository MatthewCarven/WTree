"""Generic plan/result types shared across copy, move, delete, rename.

Operations follow a two-phase shape: a synchronous-feeling **plan** step
that walks the inputs and produces a flat list of intended actions, then
an **execute** step that applies them with progress reporting and an
optional undo log. The plan is pure data - no filesystem writes, no
network - so it's safe to render in a confirmation dialog, persist for
an undo entry, or smoke-test in a unit test.

See ``design.md`` Operation semantics vary by source pairing for the
rationale on why operations live above ``EntrySource`` rather than as
methods on it.
"""

from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass, field
from enum import Enum

from wtree.sources.base import Kind


# Path identity is case-insensitive on Windows (NTFS default) and case-
# sensitive on POSIX. macOS (HFS+/APFS default-insensitive) is treated as
# case-sensitive for now - a known soft spot, same as the case-only-rename
# note in todo.md. The ``canonical_path`` flag lets callers (and tests) pin
# the behaviour explicitly regardless of host OS.
_PATHS_CASE_INSENSITIVE = os.name == "nt"


def to_posix(path: str) -> str:
    """Flip native backslashes to forward slashes.

    The internal path convention is POSIX-flavoured: typed destinations and
    the segment-walk planners funnel through here so every downstream
    ``posixpath`` op (join / basename / normpath) and string comparison sees
    one separator style. :func:`wtree.ops.execute._normalise_dst` flips them
    back to the native separator on Windows just before the OS call.
    """
    return path.replace("\\", "/")


def canonical_path(
    path: str, *, case_insensitive: bool = _PATHS_CASE_INSENSITIVE
) -> str:
    """Canonical form for comparing two paths for *identity*.

    Flips separators to ``/`` (:func:`to_posix`), collapses ``.`` / ``..`` /
    redundant slashes (``posixpath.normpath``), and lowercases when
    ``case_insensitive`` (the Windows / NTFS default). Deterministic given
    the flag regardless of host OS, so the cross-platform behaviour is unit-
    testable anywhere. Used by self-target detection
    (:func:`wtree.ops.conflicts._same_location`) and the executor's overwrite
    self-destruct guard (:func:`wtree.ops.execute._would_destroy_source`) so
    both judge "same location" identically.
    """
    p = posixpath.normpath(to_posix(path))
    return p.lower() if case_insensitive else p


def resolve_relative_leaf(
    parent_path: str, typed: str
) -> tuple[str | None, str | None]:
    """Resolve a user-typed *relative* name to a leaf path under ``parent_path``.

    Returns ``(leaf, None)`` on success or ``(None, error)`` on rejection.
    Lenient on separators - ``sub/leaf`` implies intermediate directories the
    caller's executor will create. Rejects absolute paths, ``..`` escapes, and
    names that collapse to nothing. Shared by the Make-new planner and the
    :class:`~wtree.widgets.conflict.ConflictDialog` custom-rename editor so
    both validate a typed relative target identically. POSIX-flavoured
    throughout (see :func:`to_posix`).

    Three parent shapes: ``""`` -> leaf is the bare segments; ``"/"`` -> leaf
    is ``"/" + segments``; otherwise ``parent (trailing-slash-trimmed) + "/" +
    segments``.
    """
    cleaned = to_posix(typed.strip()).rstrip("/")
    if not cleaned:
        return None, "name is empty"
    # Absolute is rejected - the target lands under ``parent_path``, not at a
    # typed root. Catches POSIX-absolute ("/x"), Windows-drive ("C:/x" after
    # the backslash flip) and UNC ("//srv/x", caught by the leading "/").
    if cleaned.startswith("/") or (len(cleaned) >= 2 and cleaned[1] == ":"):
        return None, f"name {cleaned!r} is absolute; use a relative name"
    segments = [s for s in cleaned.split("/") if s and s != "."]
    if not segments:
        return None, "name resolves to no path components"
    if any(seg == ".." for seg in segments):
        return None, f"name {cleaned!r} contains a '..' segment"
    name_rel = "/".join(segments)
    parent_posix = to_posix(parent_path)
    if parent_posix == "":
        leaf = name_rel
    elif parent_posix == "/":
        leaf = "/" + name_rel
    else:
        leaf = parent_posix.rstrip("/") + "/" + name_rel
    return leaf, None


class OperationKind(str, Enum):
    """The kinds of operations a Plan can describe.

    String values are stable wire format for the future undo log.
    """

    COPY = "copy"
    MOVE = "move"
    DELETE = "delete"
    RENAME = "rename"
    MAKE_NEW = "make_new"


class ConflictKind(str, Enum):
    """What already occupies a :class:`PlanItem`'s destination at plan time.

    Set by the planners (``plan_copy`` / ``plan_move`` / ``plan_rename``)
    from a pre-stat of ``dst_path``. ``NONE`` means the destination is free
    (the common case); the others describe the *kind* of the existing entry
    so the conflict dialog can show "(existing: file)" vs "(existing: dir)"
    and the resolution transform can reason about replace semantics.

    String values are stable wire format for the future undo log.

    See ``design.md`` -> User interface -> Conflict resolution dialog. Note
    the *benign-merge rule*: for COPY a directory landing on an existing
    directory is deliberately left ``NONE`` - directory merge is correct
    existing behaviour, not a conflict. Only leaf file/other collisions and
    type-mismatches are flagged.
    """

    NONE = "none"
    FILE = "file"
    DIR = "dir"
    OTHER = "other"
    # The destination *is* the item's own source - the user aimed an entry
    # at the directory it already lives in (``dst_path == src_path``). Not a
    # collision with a *different* entry; the resolution semantics differ
    # (Copy duplicates in place; Move/Rename are no-ops). Surfaced only for
    # Copy - the Move/Rename planners drop self-targeted items before this
    # ever reaches the dialog. See ``design.md`` -> Conflict resolution
    # dialog -> Same-location (self-target) handling.
    SELF = "self"


class Resolution(str, Enum):
    """How a conflicting :class:`PlanItem` should be applied.

    Default ``PROCEED`` covers both "no conflict" and "conflict cleared by
    rename". The conflict dialog produces ``SKIP`` / ``OVERWRITE`` /
    ``RENAME`` choices which :func:`wtree.ops.conflicts.resolve_conflicts`
    bakes into the plan: ``SKIP`` items are dropped before the executor sees
    them, ``RENAME`` rewrites ``dst_path`` and resets to ``PROCEED``, and
    only ``OVERWRITE`` survives into the executor as a live signal to replace
    the existing destination.

    String values are stable wire format for the future undo log.
    """

    PROCEED = "proceed"
    SKIP = "skip"
    OVERWRITE = "overwrite"
    RENAME = "rename"


@dataclass(frozen=True, slots=True)
class WalkedEntry:
    """One leaf of a tag-tree walk - file or directory, source-side only.

    Produced by :func:`wtree.ops.copy.walk_tags`. ``size`` is best-effort:
    for directories it is the directory's own ``st_size`` (which is the
    size of the dir entry itself on most filesystems, not the contents)
    so that callers comparing planned totals don't have to special-case
    dirs.
    """

    source_id: str
    path: str
    kind: Kind
    size: int


@dataclass(frozen=True, slots=True)
class PlanError:
    """Couldn't plan this leaf - e.g. unknown source_id, parent unscannable.

    Errors are returned in-band with successful items (errors-as-data,
    the same pattern ``ScanResult`` uses one layer down).
    """

    source_id: str
    path: str
    message: str
    cause: str | None = None


@dataclass(frozen=True, slots=True)
class PlanItem:
    """A single src->dst pairing the planner believes should happen.

    ``src_*`` and ``dst_*`` carry full ``(source_id, path)`` tuples so
    the execute dispatcher can pick the right transfer adapter
    (``native->native`` = ``shutil.copy2``; ``native->archive`` = write
    into a zip; etc.) without re-walking either side.
    """

    src_source_id: str
    src_path: str
    dst_source_id: str
    dst_path: str
    kind: Kind
    size: int
    # Plan-time conflict state. ``conflict`` is set by the planner from a
    # pre-stat of ``dst_path``; ``resolution`` is set by the conflict
    # dialog's resolution transform and read by the executor. Both default
    # so older call sites and the undo-log wire format stay unaffected.
    conflict: ConflictKind = ConflictKind.NONE
    resolution: Resolution = Resolution.PROCEED


@dataclass
class WalkSummary:
    """Result of a source-only walk - what's there plus what couldn't be
    enumerated. Exposed for tests so the source-expansion logic is
    checkable without a destination.
    """

    entries: list[WalkedEntry] = field(default_factory=list)
    errors: list[PlanError] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return sum(1 for e in self.entries if e.kind is Kind.FILE)

    @property
    def dir_count(self) -> int:
        return sum(1 for e in self.entries if e.kind is Kind.DIR)

    @property
    def total_bytes(self) -> int:
        # Files only - dir "sizes" aren't comparable across filesystems
        # and would distort the user-facing total.
        return sum(e.size for e in self.entries if e.kind is Kind.FILE)


@dataclass
class Plan:
    """A fully-resolved operation plan - what would happen if applied.

    Construction is via the planner functions (:func:`plan_copy` etc.),
    not direct instantiation, so the invariant "items + errors describe
    every intended input" is upheld in one place.

    Application: :func:`wtree.ops.execute.apply_plan` consumes a Plan
    and produces an :class:`OperationResult`. The split keeps Plan a
    pure data object - safe to render in a dialog, persist for an undo
    log, or pass around between sessions once that lands.
    """

    kind: OperationKind
    items: list[PlanItem] = field(default_factory=list)
    errors: list[PlanError] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return sum(1 for i in self.items if i.kind is Kind.FILE)

    @property
    def dir_count(self) -> int:
        return sum(1 for i in self.items if i.kind is Kind.DIR)

    @property
    def total_bytes(self) -> int:
        return sum(i.size for i in self.items if i.kind is Kind.FILE)

    @property
    def is_empty(self) -> bool:
        """No items *and* no errors. The Selection rule produced nothing."""
        return not self.items and not self.errors

    def summary(self) -> str:
        """Single-line human summary for status line / notify().

        Format: ``copy: 3 file(s), 2 dir(s), 4.1 KB (1 error)``.
        Designed to fit on one line at typical terminal widths.
        """
        fc, dc, tb = self.file_count, self.dir_count, self.total_bytes
        size = _human_bytes(tb)
        parts = [f"{self.kind.value}: {fc} file(s), {dc} dir(s), {size}"]
        if self.errors:
            parts.append(f"({len(self.errors)} error(s))")
        return " ".join(parts)


class ItemStatus(str, Enum):
    """Outcome of applying a single :class:`PlanItem`.

    String values are stable wire format for the future undo log.
    """

    SUCCESS = "success"
    SKIPPED = "skipped"  # kind not handled (e.g. Kind.OTHER); not a failure
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ItemResult:
    """The outcome of one item in the executed plan.

    ``message`` is human text for the failure / skip reason - empty
    string on success. It's what the future progress dialog and post-op
    notify() will surface.
    """

    item: PlanItem
    status: ItemStatus
    message: str = ""


@dataclass
class OperationResult:
    """Result of running an entire :class:`Plan` end-to-end.

    Carries the plan back along with the per-item results so a UI can
    render "X succeeded, Y skipped, Z failed" alongside the original
    src/dst paths. Aggregate helpers read cheaply at render time.
    """

    plan: Plan
    items: list[ItemResult] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.items if r.status is ItemStatus.SUCCESS)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.items if r.status is ItemStatus.SKIPPED)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.items if r.status is ItemStatus.FAILED)

    @property
    def all_succeeded(self) -> bool:
        """Every item came back ``SUCCESS``. Empty plans count as success
        because there's nothing that *could* have failed.
        """
        return all(r.status is ItemStatus.SUCCESS for r in self.items)

    def summary(self) -> str:
        """Single-line human summary mirroring :meth:`Plan.summary`."""
        parts = [
            f"{self.plan.kind.value} done:",
            f"{self.success_count} ok",
        ]
        if self.skipped_count:
            parts.append(f"{self.skipped_count} skipped")
        if self.failed_count:
            parts.append(f"{self.failed_count} failed")
        return " ".join(parts)

    @property
    def touched_paths(self) -> set[str]:
        """Absolute directory paths whose listings the op changed.

        Computed lazily from the per-item results, restricted to
        ``SUCCESS`` items so partial-failure cases only report what
        actually landed on disk. The UI layer feeds this into
        :meth:`wtree.widgets.tree_pane.TreePane.refresh_paths` to
        invalidate the lazy-load memo for affected subtrees so tree
        nodes whose contents just changed get re-scanned on the next
        paint.

        Per-kind rules:

        * **COPY / MAKE_NEW** - parent of ``dst_path`` (the dir that
          gained a new entry).
        * **DELETE** - parent of ``src_path`` (the dir that lost an
          entry).
        * **MOVE** - both parents (source dir loses, destination dir
          gains).
        * **RENAME** - parent of ``src_path``. Planner guarantees the
          rename stays in the same parent, so ``dirname(src) ==
          dirname(dst)`` and one parent covers both ends.

        Returned as a ``set`` for de-duplication when many items share
        the same parent (typical for batch ops). Empty for empty plans
        or all-failed plans.
        """
        paths: set[str] = set()
        kind = self.plan.kind
        for r in self.items:
            if r.status is not ItemStatus.SUCCESS:
                continue
            item = r.item
            if kind is OperationKind.COPY or kind is OperationKind.MAKE_NEW:
                paths.add(os.path.dirname(item.dst_path))
            elif kind is OperationKind.DELETE:
                paths.add(os.path.dirname(item.src_path))
            elif kind is OperationKind.MOVE:
                paths.add(os.path.dirname(item.src_path))
                paths.add(os.path.dirname(item.dst_path))
            elif kind is OperationKind.RENAME:
                # Planner guarantees same-parent rename.
                paths.add(os.path.dirname(item.src_path))
        # Empty strings can sneak in for paths with no parent component
        # (theoretically only at the filesystem root, which has no
        # children to refresh anyway). Drop them so callers don't have
        # to filter.
        paths.discard("")
        return paths


def _human_bytes(n: int) -> str:
    """Compact size - XTree-ish.

    Kept tiny on purpose: a richer presentation layer (humanised sizes,
    locale-aware separators) is parking-lot material along with the size
    column work in the contents pane.
    """
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n_kb = n / 1024
        if n_kb < 1024:
            return f"{n_kb:.1f} {unit}"
        n = int(n_kb)  # type: ignore[assignment]
    return f"{n:.1f} PB"


def drive_anchor(path: str) -> str:
    """The drive / share root of ``path`` - where the destination browser
    starts so the user can roam the whole current drive.

    ``/`` on POSIX, ``C:\\`` (or ``\\\\server\\share\\``) on Windows, via
    ``os.path.splitdrive``. Used by the Copy/Move browse affordance to root
    the :class:`~wtree.widgets.dir_picker.DirPickerScreen` at the top of the
    drive the current destination lives on. (Switching to *another* drive is
    a parked phase-2 stretch - see design.md.)
    """
    drive, _ = os.path.splitdrive(path)
    return drive + os.sep if drive else os.sep
