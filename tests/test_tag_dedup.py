"""Tests for overlapping-tag collapse (design.md 2026-06-07).

``collapse_nested_tags`` units, the three planners, and the e2e that
proves the old flattened-duplicate-siblings bug is gone: recursively
tag a folder, copy it, and the destination contains ONLY the folder.
"""

from __future__ import annotations

import os
from pathlib import Path

from wtree.app import WTreeApp
from wtree.ops import (
    collapse_nested_tags,
    plan_copy,
    plan_delete,
    plan_move,
)
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.tree_pane import TreePane


# ---------------------------------------------------------------------------
# collapse_nested_tags units
# ---------------------------------------------------------------------------


def _t(*paths: str, sid: str = "n") -> list[Tag]:
    return [Tag(sid, p) for p in paths]


def test_nested_tags_collapse_to_topmost() -> None:
    kept, n = collapse_nested_tags(
        _t("/a", "/a/b", "/a/b/c.txt", "/z")
    )
    assert [t.path for t in kept] == ["/a", "/z"]
    assert n == 2


def test_lexicographic_sibling_trap() -> None:
    """/a-x sorts between /a and /a/b lexicographically - it must be
    kept, and /a/b must still collapse under /a."""
    kept, n = collapse_nested_tags(_t("/a", "/a-x", "/a/b"))
    assert [t.path for t in kept] == ["/a", "/a-x"]
    assert n == 1


def test_disjoint_tags_untouched_in_order() -> None:
    kept, n = collapse_nested_tags(_t("/b", "/a", "/c/d"))
    assert [t.path for t in kept] == ["/b", "/a", "/c/d"]  # original order
    assert n == 0


def test_sources_isolated() -> None:
    tags = [Tag("n", "/a"), Tag("m", "/a/b")]
    kept, n = collapse_nested_tags(tags)
    assert len(kept) == 2
    assert n == 0


def test_mixed_separators_collapse() -> None:
    """A backslash-separator descendant still collapses (canonical_path)."""
    kept, n = collapse_nested_tags(
        _t("/a", "\\a\\b"), case_insensitive=False
    )
    assert [t.path for t in kept] == ["/a"]
    assert n == 1


def test_case_insensitive_windows_rule() -> None:
    kept, n = collapse_nested_tags(
        _t("/A", "/a/b"), case_insensitive=True
    )
    assert [t.path for t in kept] == ["/A"]
    assert n == 1
    # POSIX rule: different identity, no collapse.
    kept, n = collapse_nested_tags(
        _t("/A", "/a/b"), case_insensitive=False
    )
    assert len(kept) == 2


def test_canonical_duplicate_collapses() -> None:
    kept, n = collapse_nested_tags(
        _t("/a/b", "\\a\\b"), case_insensitive=False
    )
    assert len(kept) == 1
    assert n == 1


# ---------------------------------------------------------------------------
# Planner integration
# ---------------------------------------------------------------------------


def _stage_tree(tmp_path: Path) -> tuple[str, str]:
    src = tmp_path / "src"
    (src / "foo" / "bar").mkdir(parents=True)
    (src / "foo" / "bar" / "baz.txt").write_text("payload")
    dest = tmp_path / "dest"
    dest.mkdir()
    return str(src), str(dest)


def _recursive_tags(root: str) -> list[Tag]:
    """foo + every descendant, the recursive-Space shape."""
    foo = os.path.join(root, "foo")
    return [
        Tag("native", foo),
        Tag("native", os.path.join(foo, "bar")),
        Tag("native", os.path.join(foo, "bar", "baz.txt")),
    ]


async def test_plan_copy_collapses(tmp_path: Path) -> None:
    src, dest = _stage_tree(tmp_path)
    source = NativeSource()
    registry = {source.source_id: source}
    tags = [
        Tag(source.source_id, os.path.join(src, "foo")),
        Tag(source.source_id, os.path.join(src, "foo", "bar")),
        Tag(source.source_id, os.path.join(src, "foo", "bar", "baz.txt")),
    ]
    plan = await plan_copy(
        tags, Tag(source.source_id, dest), registry
    )
    assert plan.collapsed_tags == 2
    # Every dst lands under dest/foo - no flattened dest/bar, dest/baz.txt.
    for item in plan.items:
        rel = os.path.relpath(item.dst_path, dest)
        assert rel.split(os.sep)[0].split("/")[0] == "foo"


async def test_plan_move_collapses(tmp_path: Path) -> None:
    src, dest = _stage_tree(tmp_path)
    source = NativeSource()
    registry = {source.source_id: source}
    tags = [
        Tag(source.source_id, os.path.join(src, "foo")),
        Tag(source.source_id, os.path.join(src, "foo", "bar")),
    ]
    plan = await plan_move(tags, Tag(source.source_id, dest), registry)
    assert plan.collapsed_tags == 1
    assert len(plan.items) == 1
    assert plan.items[0].src_path == os.path.join(src, "foo")


async def test_plan_delete_collapses(tmp_path: Path) -> None:
    src, _ = _stage_tree(tmp_path)
    source = NativeSource()
    registry = {source.source_id: source}
    tags = [
        Tag(source.source_id, os.path.join(src, "foo")),
        Tag(source.source_id, os.path.join(src, "foo", "bar", "baz.txt")),
    ]
    plan = await plan_delete(tags, registry)
    assert plan.collapsed_tags == 1
    assert len(plan.items) == 1


# ---------------------------------------------------------------------------
# E2E - the bug this closes
# ---------------------------------------------------------------------------


async def test_recursive_tag_copy_no_duplicate_siblings(
    tmp_path: Path,
) -> None:
    """Recursive-tag foo, copy to dest -> dest contains ONLY foo/.

    Pre-dedup this produced dest/foo + dest/bar + dest/baz.txt.
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "foo" / "bar").mkdir(parents=True)
    (root / "foo" / "bar" / "baz.txt").write_text("payload")
    dest = root / "dest"
    dest.mkdir()

    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        tree.focus()
        await pilot.pause()
        # Cursor onto foo (first child row) and recursive-tag it.
        assert await tree.reveal_path(str(root / "foo"))
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        for _ in range(4):
            await pilot.pause()
        assert len(app.tagged_set) == 3  # foo, bar, baz.txt

        app.query_one(ContentsPane).focus()
        await pilot.press("c")  # copy
        await pilot.pause()
        # Destination prompt: type dest and confirm.
        from wtree.widgets.prompt import PromptDialog
        from textual.widgets import Input

        dialog = app.screen
        assert isinstance(dialog, PromptDialog)
        dialog.query_one(Input).value = str(dest)
        await pilot.press("enter")
        await pilot.pause()
        for _ in range(10):  # queue drain
            await pilot.pause()

        produced = sorted(p.name for p in dest.iterdir())
        assert produced == ["foo"]  # no flattened bar / baz.txt siblings
        assert (dest / "foo" / "bar" / "baz.txt").read_text() == "payload"

        # The collapse count rode the plan into the app (the flash text
        # itself may have been replaced by later flashes - assert the
        # stable record).
        assert app.last_plan is not None
        assert app.last_plan.collapsed_tags == 2
