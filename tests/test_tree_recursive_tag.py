"""E2E pilot tests for the tree-pane Space gesture (2026-05-22).

Semantics under test (Matthew's pick): Space on a tree node toggles the
entire subtree based on the node's own current tagged state. If the
directory entry is already tagged -> recursive untag. Otherwise -> recursive
tag. Symlinks treated as leaves; ScanErrors silently skipped.
"""

from __future__ import annotations

import os
from datetime import datetime

from wtree.app import WTreeApp
from wtree.sources.base import Entry, Kind, ScanError
from wtree.sources.mock import MockSource
from wtree.widgets.tree_pane import TreePane


_MTIME = datetime(2026, 5, 22, 12, 0, 0)


def _entry(name: str, kind: Kind = Kind.FILE, size: int = 0) -> Entry:
    return Entry(
        name=name,
        kind=kind,
        size=size,
        mtime=_MTIME,
        permissions="-rw-r--r--",
    )


# A small reusable tree fixture:
#   /root/
#     a.txt
#     sub/
#       b.txt
#       inner/
#         c.txt
#     empty/
def _build_fixture():
    root = os.path.abspath(os.sep + "root")
    sub = os.path.join(root, "sub")
    inner = os.path.join(sub, "inner")
    empty = os.path.join(root, "empty")
    src = MockSource(
        contents={
            root: [
                _entry("a.txt", Kind.FILE, 1),
                _entry("sub", Kind.DIR),
                _entry("empty", Kind.DIR),
            ],
            sub: [
                _entry("b.txt", Kind.FILE, 2),
                _entry("inner", Kind.DIR),
            ],
            inner: [
                _entry("c.txt", Kind.FILE, 3),
            ],
            empty: [],
        }
    )
    return src, root, sub, inner, empty


async def test_space_on_root_tags_entire_subtree() -> None:
    src, root, sub, inner, empty = _build_fixture()
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Tree pane is focused on mount; cursor is on root.
        await pilot.press("space")
        await pilot.pause()
        # Every path in the subtree (root + 2 dirs + 1 empty + 3 files + 1 inner dir = 8)
        expected = {
            root,
            os.path.join(root, "a.txt"),
            sub,
            inner,
            os.path.join(sub, "b.txt"),
            os.path.join(inner, "c.txt"),
            empty,
        }
        actual = {t.path for t in app.tagged_set}
        assert actual == expected, f"missing: {expected - actual}, extra: {actual - expected}"


async def test_space_on_root_when_tagged_recursively_untags() -> None:
    src, root, sub, inner, empty = _build_fixture()
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # First Space tags everything.
        await pilot.press("space")
        await pilot.pause()
        assert len(app.tagged_set) == 7
        # Second Space — root is now tagged — recursively untags.
        await pilot.press("space")
        await pilot.pause()
        assert len(app.tagged_set) == 0


async def test_space_on_subdir_only_tags_that_subtree() -> None:
    """Space on the ``sub`` node tags sub + b.txt + inner + inner/c.txt only."""
    src, root, sub, inner, empty = _build_fixture()
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Move cursor onto a child of root. Tree shows: root, sub, empty
        # (dirs only, sorted). Press Down to go from root to first child.
        tree = pilot.app.query_one(TreePane)
        # Find the sub node line.
        for child in tree.root.children:
            if child.data == sub:
                tree.cursor_line = child.line
                break
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        expected = {
            sub,
            inner,
            os.path.join(sub, "b.txt"),
            os.path.join(inner, "c.txt"),
        }
        actual = {t.path for t in app.tagged_set}
        assert actual == expected
        # Sibling paths must NOT be tagged.
        assert os.path.join(root, "a.txt") not in actual
        assert empty not in actual


async def test_space_on_empty_dir_tags_only_that_dir() -> None:
    src, root, sub, inner, empty = _build_fixture()
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = pilot.app.query_one(TreePane)
        for child in tree.root.children:
            if child.data == empty:
                tree.cursor_line = child.line
                break
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        # Just the directory itself — no descendants.
        assert {t.path for t in app.tagged_set} == {empty}


async def test_space_skips_branches_that_raise_scan_errors() -> None:
    """A ScanError under the subtree doesn't abort — other branches still tag."""
    root = os.path.abspath(os.sep + "root")
    bad_dir = os.path.join(root, "bad")
    good_dir = os.path.join(root, "good")
    src = MockSource(
        contents={
            root: [
                _entry("bad", Kind.DIR),
                _entry("good", Kind.DIR),
            ],
            # bad/ scan yields a ScanError instead of entries.
            bad_dir: [
                ScanError(path=bad_dir, message="denied", cause="OSError"),
            ],
            good_dir: [_entry("ok.txt", Kind.FILE, 1)],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        # Expected: root + bad + good + good/ok.txt — error in bad/ skipped.
        expected = {
            root,
            bad_dir,
            good_dir,
            os.path.join(good_dir, "ok.txt"),
        }
        assert {t.path for t in app.tagged_set} == expected


async def test_space_on_error_placeholder_is_a_noop() -> None:
    """Tree-pane error leaves carry ``data=None`` — Space must NOT tag anything."""
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={
            root: [
                ScanError(path=root, message="boom", cause="OSError"),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = pilot.app.query_one(TreePane)
        # Move cursor onto the error leaf (it's the only child of root).
        if tree.root.children:
            tree.cursor_line = tree.root.children[0].line
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        # No path tagged — error leaves are non-taggable by design.
        assert len(app.tagged_set) == 0


async def test_space_recursive_then_partial_untag_via_contents_pane() -> None:
    """Recursive tag, then contents-pane Space on one row toggles just that one.

    Sanity check: the bulk gesture and the single-toggle don't interfere.
    """
    src, root, sub, inner, empty = _build_fixture()
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")  # recursive tag of root
        await pilot.pause()
        assert len(app.tagged_set) == 7
        # Switch to contents pane and press Space — toggles the row under
        # cursor (whatever sorted first; the tree-pane recursive tag
        # already put it in the set).
        from wtree.widgets.contents_pane import ContentsPane

        pilot.app.query_one(ContentsPane).focus()
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        # Lost exactly one tag — contents pane shows root's entries.
        assert len(app.tagged_set) == 6
