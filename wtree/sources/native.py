"""``NativeSource`` — direct OS calls via :func:`os.scandir`.

The straightforward path. When the disk is healthy and the OS readdir works,
this is the source WTree uses. See ``ShellSource`` (post-v0) for the
unreliable-disk fallback.
"""

from __future__ import annotations

import os
import stat as stat_module
from datetime import datetime
from typing import AsyncIterator

from wtree.sources.base import (
    Entry,
    EntrySource,
    Kind,
    ScanError,
    ScanResult,
    SourceCapability,
)


class NativeSource(EntrySource):
    """Enumerate the local filesystem via :func:`os.scandir`.

    v0 scope: scan one directory, yield its immediate children. No traversal
    strategies wrapped here yet — those live one layer up.
    """

    _CAPABILITY = SourceCapability(
        permissions=True,
        owner=False,  # cross-platform owner lookup deferred (pwd/grp are Unix-only)
        link_target=True,
        supports_log_all=True,
    )

    @property
    def source_id(self) -> str:
        return "native"

    @property
    def capability(self) -> SourceCapability:
        return self._CAPABILITY

    @property
    def scan_method_label(self) -> str:
        # Surfaced verbatim in the scan dialog. ``os.scandir`` is the
        # Python-API name our ``scan`` method actually calls; under the
        # hood it's ``opendir``/``readdir`` on POSIX and
        # ``FindFirstFileW``/``FindNextFileW`` on Windows, but the
        # Python name is the honest one at our layer.
        return "os.scandir"

    async def scan(self, path: str) -> AsyncIterator[ScanResult]:
        try:
            scanner = os.scandir(path)
        except OSError as e:
            # The directory itself cannot be opened. One error, then done.
            yield ScanError(path=path, message=str(e), cause=type(e).__name__)
            return

        try:
            for raw in scanner:
                yield self._entry_from(raw)
        finally:
            scanner.close()

    async def entry_at(self, path: str) -> Entry | ScanError:
        """Direct ``os.lstat`` — O(1), no parent scan.

        Overrides the base default (which would scan the parent dir) for
        the obvious speed and correctness wins. Notably handles filesystem
        roots ("/", "C:\\") that the default can't classify.
        """
        try:
            st = os.lstat(path)
        except OSError as e:
            return ScanError(path=path, message=str(e), cause=type(e).__name__)

        mode = st.st_mode
        if stat_module.S_ISLNK(mode):
            kind = Kind.SYMLINK
        elif stat_module.S_ISDIR(mode):
            kind = Kind.DIR
        elif stat_module.S_ISREG(mode):
            kind = Kind.FILE
        else:
            kind = Kind.OTHER

        link_target: str | None = None
        if kind is Kind.SYMLINK:
            try:
                link_target = os.readlink(path)
            except OSError:
                link_target = None

        return Entry(
            name=os.path.basename(path.rstrip(os.sep)) or path,
            kind=kind,
            size=st.st_size,
            mtime=datetime.fromtimestamp(st.st_mtime),
            permissions=stat_module.filemode(mode),
            owner=None,  # see capability — cross-platform owner lookup deferred
            link_target=link_target,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _entry_from(raw: os.DirEntry[str]) -> ScanResult:
        """Convert a :class:`os.DirEntry` into an :class:`Entry` or :class:`ScanError`."""
        try:
            st = raw.stat(follow_symlinks=False)
        except OSError as e:
            return ScanError(path=raw.path, message=str(e), cause=type(e).__name__)

        # Classify. Order matters — a symlink to a directory should still
        # report as SYMLINK, not DIR.
        try:
            if raw.is_symlink():
                kind = Kind.SYMLINK
            elif raw.is_dir(follow_symlinks=False):
                kind = Kind.DIR
            elif raw.is_file(follow_symlinks=False):
                kind = Kind.FILE
            else:
                kind = Kind.OTHER
        except OSError as e:
            return ScanError(path=raw.path, message=str(e), cause=type(e).__name__)

        link_target: str | None = None
        if kind is Kind.SYMLINK:
            try:
                link_target = os.readlink(raw.path)
            except OSError:
                # Readlink may legitimately fail on broken/odd links; not
                # fatal, the entry is still listable.
                link_target = None

        return Entry(
            name=raw.name,
            kind=kind,
            size=st.st_size,
            mtime=datetime.fromtimestamp(st.st_mtime),
            permissions=stat_module.filemode(st.st_mode),
            owner=None,  # see capability — deferred for cross-platform reasons
            link_target=link_target,
        )
