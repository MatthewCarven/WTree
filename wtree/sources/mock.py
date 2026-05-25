"""``MockSource`` — scripted contents and scripted errors for tests.

Lets a test scenario stand up a virtual filesystem in a few lines, including
broken nodes, without touching disk. Used by the UI test suite and by anyone
exercising traversal-strategy logic in isolation from real I/O.
"""

from __future__ import annotations

from typing import AsyncIterator, Mapping

from wtree.sources.base import (
    EntrySource,
    ScanError,
    ScanResult,
    SourceCapability,
)


class MockSource(EntrySource):
    """An in-memory ``EntrySource`` whose scans are fully scripted.

    Example::

        from datetime import datetime
        from wtree.sources import Entry, Kind, MockSource, ScanError

        src = MockSource(contents={
            "/": [
                Entry("home", Kind.DIR, 4096, datetime.now()),
                Entry("readme.txt", Kind.FILE, 128, datetime.now()),
            ],
            "/home": [
                Entry("matthew", Kind.DIR, 4096, datetime.now()),
            ],
        }, errors={
            "/proc": ScanError(path="/proc", message="Permission denied",
                               cause="PermissionError"),
        })
    """

    _CAPABILITY = SourceCapability(
        permissions=False,
        owner=False,
        link_target=False,
        supports_log_all=True,
    )

    def __init__(
        self,
        contents: Mapping[str, list[ScanResult]] | None = None,
        errors: Mapping[str, ScanError] | None = None,
        source_id: str = "mock",
    ) -> None:
        # Copy on the way in — tests will mutate the dicts they pass in
        # otherwise and surprise the next call.
        self._contents: dict[str, list[ScanResult]] = {
            k: list(v) for k, v in (contents or {}).items()
        }
        self._errors: dict[str, ScanError] = dict(errors or {})
        self._source_id = source_id

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def capability(self) -> SourceCapability:
        return self._CAPABILITY

    @property
    def scan_method_label(self) -> str:
        # Mock scans are too fast to hit the dialog threshold in
        # practice; this label is mostly here to satisfy the contract
        # and for tests that script slow yields.
        return "mock source"

    async def scan(self, path: str) -> AsyncIterator[ScanResult]:
        # A directory-level scripted error wins over scripted contents.
        if path in self._errors:
            yield self._errors[path]
            return

        if path not in self._contents:
            yield ScanError(
                path=path,
                message=f"Mock has no scripted contents for {path!r}",
                cause="FileNotFoundError",
            )
            return

        for item in self._contents[path]:
            yield item
