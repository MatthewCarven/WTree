"""Unit tests for ``TaggedSet`` — pure data structure, no Textual, no disk.

Exercises set semantics, ``toggle`` return value, cross-source coexistence,
and the iteration contract.
"""

from __future__ import annotations

from wtree.tagged_set import Tag, TaggedSet


def test_empty_set_has_no_members() -> None:
    ts = TaggedSet()
    assert len(ts) == 0
    assert list(ts) == []
    assert not ts  # __bool__ — useful in guards


def test_add_then_contains_returns_true() -> None:
    ts = TaggedSet()
    ts.add("native", "/foo")
    assert len(ts) == 1
    assert ts.contains("native", "/foo")
    assert not ts.contains("native", "/bar")
    assert bool(ts)


def test_add_is_idempotent() -> None:
    ts = TaggedSet()
    ts.add("native", "/foo")
    ts.add("native", "/foo")
    assert len(ts) == 1


def test_toggle_adds_when_absent_and_returns_true() -> None:
    ts = TaggedSet()
    new_state = ts.toggle("native", "/foo")
    assert new_state is True
    assert ts.contains("native", "/foo")


def test_toggle_removes_when_present_and_returns_false() -> None:
    ts = TaggedSet()
    ts.add("native", "/foo")
    new_state = ts.toggle("native", "/foo")
    assert new_state is False
    assert not ts.contains("native", "/foo")


def test_remove_is_silent_when_absent() -> None:
    ts = TaggedSet()
    ts.remove("native", "/never-was")  # no exception
    assert len(ts) == 0


def test_clear_empties_the_set() -> None:
    ts = TaggedSet()
    ts.add("native", "/foo")
    ts.add("native", "/bar")
    ts.clear()
    assert len(ts) == 0


def test_different_source_ids_coexist_for_same_path() -> None:
    """The whole point of (source_id, path) tuples: same path, different
    backing source, must be two distinct tags. See design.md § Tagged set
    scope: a tag can hold ``C:\\foo`` from NativeSource and a future
    ``zip:archive.zip!/foo`` simultaneously.
    """
    ts = TaggedSet()
    ts.add("native", "/foo")
    ts.add("mock", "/foo")
    assert len(ts) == 2
    assert ts.contains("native", "/foo")
    assert ts.contains("mock", "/foo")


def test_iteration_yields_tag_dataclasses() -> None:
    ts = TaggedSet()
    ts.add("native", "/foo")
    items = list(ts)
    assert len(items) == 1
    assert isinstance(items[0], Tag)
    assert items[0].source_id == "native"
    assert items[0].path == "/foo"


# ---------------------------------------------------------------------------
# Bulk API — add_many / remove_many — added 2026-05-22 for the tagging polish
# pass. Used by Ctrl+A, +/- glob, and the recursive tree-pane Space toggle.
# ---------------------------------------------------------------------------


def test_add_many_returns_delta_count() -> None:
    """The delta is the number of *new* tags, not the iterable length."""
    ts = TaggedSet()
    ts.add("native", "/already")
    delta = ts.add_many(
        [("native", "/already"), ("native", "/new1"), ("native", "/new2")]
    )
    assert delta == 2
    assert len(ts) == 3


def test_add_many_on_empty_iterable_is_zero() -> None:
    ts = TaggedSet()
    assert ts.add_many([]) == 0
    assert len(ts) == 0


def test_add_many_is_idempotent_on_duplicate_pairs_in_input() -> None:
    """A pair appearing twice in the input adds once."""
    ts = TaggedSet()
    delta = ts.add_many([("native", "/foo"), ("native", "/foo")])
    assert delta == 1
    assert len(ts) == 1


def test_remove_many_returns_delta_count() -> None:
    ts = TaggedSet()
    ts.add("native", "/a")
    ts.add("native", "/b")
    ts.add("native", "/c")
    delta = ts.remove_many(
        [("native", "/a"), ("native", "/missing"), ("native", "/c")]
    )
    assert delta == 2
    assert len(ts) == 1
    assert ts.contains("native", "/b")


def test_remove_many_on_empty_set_is_zero() -> None:
    ts = TaggedSet()
    assert ts.remove_many([("native", "/never-was")]) == 0


def test_add_many_and_remove_many_round_trip() -> None:
    """add_many then remove_many on the same pairs returns to empty."""
    pairs = [("native", "/x"), ("native", "/y"), ("native", "/z")]
    ts = TaggedSet()
    ts.add_many(pairs)
    assert len(ts) == 3
    delta = ts.remove_many(pairs)
    assert delta == 3
    assert len(ts) == 0
