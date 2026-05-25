"""The ``EntrySource`` contract.

Every backend that can enumerate directory entries — local disk, a shell-out
fallback, an in-memory mock, a future archive or SFTP source — implements this
interface. The rest of WTree never knows which kind of source it is looking at.

See ``design.md`` § Core architecture for the design rationale.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import AsyncIterator, Union


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


class Kind(str, Enum):
    """Coarse classification of an entry.

    String values are stable wire format — they are what gets serialised when
    a source persists its log to disk (``LogPersist`` strategy, post-v0).
    """

    FILE = "file"
    DIR = "dir"
    SYMLINK = "symlink"
    OTHER = "other"


# Canonical date storage format. See ``design.md`` § Entry shape.
ISO_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True, slots=True)
class Entry:
    """A single directory entry as exposed by an ``EntrySource``.

    Required fields are always populated. Optional fields are ``None`` when the
    source cannot supply them — consult :attr:`EntrySource.capability` to know
    in advance which optional fields a particular source can fill in.
    """

    name: str
    kind: Kind
    size: int
    mtime: datetime | None
    # Optional fields — populated only if the source's capability advertises them.
    permissions: str | None = None  # e.g. "rwxr-xr-x" or "drwx------"
    owner: str | None = None
    link_target: str | None = None  # for symlinks; absolute or relative as the source reports

    @property
    def mtime_iso(self) -> str | None:
        """``mtime`` rendered in WTree's canonical format, or ``None``."""
        return self.mtime.strftime(ISO_DATE_FORMAT) if self.mtime is not None else None


@dataclass(frozen=True, slots=True)
class ScanError:
    """A scan failure attached to a specific path.

    Errors are yielded inline with successful entries — see
    :data:`ScanResult` — so a permission-denied or I/O failure on one node
    never aborts the whole scan. The UI renders these as damaged entries
    visually distinct from real files.
    """

    path: str
    message: str
    # The underlying exception's class name, e.g. "PermissionError". Useful for
    # triage in logs without dragging the live exception object around.
    cause: str | None = None


ScanResult = Union[Entry, ScanError]
"""A scan yields one of these per item. Errors-as-data, by design."""


@dataclass(frozen=True, slots=True)
class SourceCapability:
    """What optional fields a source can populate.

    Required fields (name, kind, size, mtime) are assumed; only the optional
    fields are declared here. A source that cannot supply ``mtime`` should
    set it to ``None`` on its ``Entry`` instances rather than lying.
    """

    permissions: bool = False
    owner: bool = False
    link_target: bool = False
    # Whether the source supports an eager full-tree scan. Unreliable-disk
    # sources may refuse ``LogAll`` even if logically possible.
    supports_log_all: bool = True


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class EntrySource(ABC):
    """Abstract pluggable directory enumerator.

    Implementations must be lazy *per directory*: ``scan(path)`` yields the
    immediate children of ``path``. Traversal across the tree (eager log,
    on-demand expansion, depth-limited log, persistent log) is a composable
    strategy that sits above this interface — not the source's concern.

    All ``scan`` implementations are async generators so that long enumeration
    yields to the Textual event loop naturally without threads.
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Stable identifier used in tagged-set tuples ``(source_id, path)``.

        Must be unique within a session. Examples: ``"native"`` for the local
        filesystem; ``"shell:C:"`` for a Windows shell-backed drive; future
        sources will mint their own (``"zip:/path/to/archive.zip"``).
        """

    @property
    @abstractmethod
    def capability(self) -> SourceCapability:
        """Describe which optional fields this source can populate."""

    @property
    def scan_method_label(self) -> str:
        """Short, user-readable label describing what this source
        subcontracts the scan to.

        Surfaced in the centered scan dialog (``ScanScreen``) when a
        directory enumeration crosses the delayed-show threshold. The
        UI renders the label verbatim - the source self-documents the
        layer it delegates to.

        Defaults to ``"scan"`` (generic) so existing third-party
        sources don't need to opt in. v0 sources override:

        * :class:`NativeSource` -> ``"os.scandir"`` (POSIX
          ``opendir``/``readdir``, Windows
          ``FindFirstFileW``/``FindNextFileW`` under the hood, but the
          Python-API name is what our code actually calls)
        * :class:`MockSource` -> ``"mock source"`` (rarely seen by
          users - mock scans never hit the threshold in practice)

        Post-v0: ``ArchiveSource`` -> ``"zipfile"``, ``SFTPSource``
        -> ``"paramiko SFTP listdir"``, etc.
        """
        return "scan"

    @abstractmethod
    def scan(self, path: str) -> AsyncIterator[ScanResult]:
        """Yield the immediate children of ``path``.

        Implementations must:

        - never raise; instead yield a :class:`ScanError` for any item that
          cannot be read
        - yield items in whatever order is cheapest (the UI sorts)
        - convert all dates into ``datetime`` objects with naive local time

        ``path`` is opaque to WTree — it has whatever shape this source uses
        (a Windows absolute path, a POSIX path, a virtual archive path, …).
        Pair it with :attr:`source_id` in the tagged set.
        """
        # Defined as a regular method (not ``async def``) so subclasses can
        # implement it as an async generator. Python's ABC doesn't have a
        # clean way to abstract-decorate an async generator yet.
        raise NotImplementedError

    async def entry_at(self, path: str) -> "Entry | ScanError":
        """Return the :class:`Entry` describing ``path`` itself.

        Used by the operations layer (``wtree.ops``) when planning copy /
        move / delete — it needs to know whether ``path`` is a file (single
        item) or a directory (recurse). Distinct from :meth:`scan`, which
        returns ``path``'s *children*.

        Default implementation scans the parent directory and finds the
        entry by basename — O(parent fan-out). Sources backed by random-
        access metadata (NativeSource via ``os.lstat``, archive sources
        via a central directory) should override for efficiency.

        Returns :class:`ScanError` if the path cannot be classified — the
        parent itself is unreadable, the basename isn't there, or the path
        has no separable parent. Errors are returned in-band so the
        operations layer can surface them in plan errors.
        """
        # Default uses POSIX path semantics; sources that index Windows
        # paths or virtual-FS paths should override. Importing posixpath
        # locally keeps the base module dependency-light.
        # locally keeps the base module dependency-light.
        import posixpath

        # Strip a trailing slash so "/foo/bar/" classifies as "bar", not "".
        normalized = path.rstrip("/") if path != "/" else path
        parent = posixpath.dirname(normalized)
        name = posixpath.basename(normalized)
        if not name:
            return ScanError(
                path=path,
                message="cannot determine entry for a root-like path; "
                "source must override entry_at()",
                cause="UnsupportedPath",
            )
        async for child in self.scan(parent):
            if isinstance(child, ScanError):
                # Parent itself is unreadable — surface that, not "not found".
                return ScanError(
                    path=path, message=child.message, cause=child.cause
                )
            if child.name == name:
                return child
        return ScanError(
            path=path,
            message=f"no entry named {name!r} under {parent!r}",
            cause="FileNotFoundError",
        )
