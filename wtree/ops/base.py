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

from dataclasses import dataclass, field
from enum import Enum

from wtree.sources.base import Kind


class OperationKind(str, Enum):
    """The kinds of operations a Plan can describe.

    String values are stable wire format for the future undo log.
    """

    COPY = "copy"
    MOVE = "move"
    DELETE = "delete"
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
