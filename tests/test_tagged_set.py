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
