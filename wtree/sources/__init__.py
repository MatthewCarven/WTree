"""Pluggable directory enumeration backends (``EntrySource`` and friends).

See ``design.md`` § Core architecture / EntrySource abstraction.
"""

from wtree.sources.base import (
    Entry,
    EntrySource,
    Kind,
    ScanError,
    ScanResult,
    SourceCapability,
)

__all__ = [
    "Entry",
    "EntrySource",
    "Kind",
    "ScanError",
    "ScanResult",
    "SourceCapability",
]
