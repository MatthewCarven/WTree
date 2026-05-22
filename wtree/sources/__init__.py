"""Pluggable directory enumeration backends (``EntrySource`` and friends).

See ``design.md`` § Core architecture / EntrySource abstraction.

The base types live in ``wtree.sources.base``; the concrete implementations
in ``wtree.sources.native`` (real filesystem via ``os.scandir``) and
``wtree.sources.mock`` (scripted contents for tests). All are re-exported
here so callers can write::

    from wtree.sources import NativeSource, MockSource, Kind

without having to know the submodule layout.
"""

from wtree.sources.base import (
    Entry,
    EntrySource,
    Kind,
    ScanError,
    ScanResult,
    SourceCapability,
)
from wtree.sources.mock import MockSource
from wtree.sources.native import NativeSource

__all__ = [
    "Entry",
    "EntrySource",
    "Kind",
    "MockSource",
    "NativeSource",
    "ScanError",
    "ScanResult",
    "SourceCapability",
]
