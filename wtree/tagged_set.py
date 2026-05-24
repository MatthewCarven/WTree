"""Per-session, source-agnostic tagged set.

The tagged set is the central object of WTree's selection model — see
``design.md`` § Tagged set. Tags are ``(source_id, path)`` pairs (modelled
here as :class:`Tag` frozen dataclasses) so the set can hold entries from
different drives, mounts, UNC paths, or future sources simultaneously.

Lives in memory for the duration of a session. Persisting across sessions
is parking-lot material.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator


@dataclass(frozen=True, slots=True)
class Tag:
    """One tag — an identity for a single entry across all WTree's sources.

    ``(source_id, path)`` is the canonical pairing from ``design.md``
    § Tagged set scope. ``path`` is whatever absolute form the source uses
    (``C:\\foo`` on Windows-NativeSource, ``/foo`` on POSIX-NativeSource,
    ``zip:/archive.zip!/inner`` for the future ArchiveSource, etc.).

    Frozen + slots → hashable + memory-light, so a session-long ``set[Tag]``
    is cheap even when the user tags thousands of entries.
    """

    source_id: str
    path: str


class TaggedSet:
    """In-memory set of :class:`Tag`.

    Mutable; not thread-safe — only one writer (the UI event loop). Set
    semantics: :meth:`add` is idempotent, :meth:`remove` is silent when
    absent (so the UI doesn't have to look-before-leap), :meth:`toggle`
    flips and returns the new state for the caller to render.

    Iteration yields :class:`Tag` objects in arbitrary order (Python set
    iteration order is not specified).
    """

    def __init__(self) -> None:
        self._tags: set[Tag] = set()

    def add(self, source_id: str, path: str) -> None:
        """Add a tag. Idempotent — adding an already-tagged pair is a no-op."""
        self._tags.add(Tag(source_id, path))

    def add_many(self, pairs: Iterable[tuple[str, str]]) -> int:
        """Add many ``(source_id, path)`` tags in one call.

        Returns the **delta** — the number of tags that weren't already
        present and so actually got added. Callers use this to flash
        accurate counts ("Tagged 12 entries") without doing their own
        before/after arithmetic. Pairs already in the set are silent
        no-ops, mirroring single-add idempotency.
        """
        before = len(self._tags)
        for source_id, path in pairs:
            self._tags.add(Tag(source_id, path))
        return len(self._tags) - before

    def remove(self, source_id: str, path: str) -> None:
        """Remove a tag. Silent when the pair is not tagged."""
        self._tags.discard(Tag(source_id, path))

    def remove_many(self, pairs: Iterable[tuple[str, str]]) -> int:
        """Remove many ``(source_id, path)`` tags in one call.

        Returns the **delta** — the number of tags that were present and
        so actually got removed. Pairs not in the set are silent no-ops,
        mirroring single-remove. Used by ``-`` glob untag and the
        recursive untag path from the tree pane.
        """
        before = len(self._tags)
        for source_id, path in pairs:
            self._tags.discard(Tag(source_id, path))
        return before - len(self._tags)

    def toggle(self, source_id: str, path: str) -> bool:
        """Flip the tagged state. Returns the **new** state — ``True`` if
        the entry is now tagged, ``False`` if it was just untagged.
        """
        tag = Tag(source_id, path)
        if tag in self._tags:
            self._tags.discard(tag)
            return False
        self._tags.add(tag)
        return True

    def contains(self, source_id: str, path: str) -> bool:
        return Tag(source_id, path) in self._tags

    def clear(self) -> None:
        """Drop every tag. Used by ``Ctrl+U`` (untag-all)."""
        self._tags.clear()

    def __len__(self) -> int:
        return len(self._tags)

    def __iter__(self) -> Iterator[Tag]:
        return iter(self._tags)

    def __bool__(self) -> bool:
        # Lets callers write ``if app.tagged_set:`` instead of
        # ``if len(app.tagged_set) > 0:``. Used by ``action_untag_all`` to
        # no-op when there's nothing to clear.
        return bool(self._tags)
