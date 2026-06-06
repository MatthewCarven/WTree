"""Regression: contents-pane Right-arrow must drill deeper, not
"jump back" to the logged folder.

Bug (2026-05-27): from a few levels deep in the tree, tab to the
contents pane and press Right to descend into a child folder. The
first Right works fine (the logged root was auto-expanded+populated
at mount, so its line indexer is fresh). The second Right needs to
expand the just-cursored-onto child (which the tree has never
visited before), and `focus_dir_under_cursor` was reading
`child.line` before yielding to let the line indexer rebuild — the
freshly-added child reported `line == -1`, and assigning
`cursor_line = -1` deselects, falling back to row 0 = the logged
root. Symptom: cursor "jumps back" to the logged folder.

Fix: `await asyncio.sleep(0)` after `_populate(node)` in
`focus_dir_under_cursor`, mirroring the same yield in
`focus_child_of_root` (whose docstring already calls out the trap).

This test exercises the two-level descent and asserts both
intermediate states.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


from wtree.app import WTreeApp
from wtree.sources.base import Entry, Kind
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.tree_pane import TreePane


_MTIME = datetime(2026, 5, 26, 12, 0, 0)


def _entry(name: str, kind: Kind = Kind.DIR) -> Entry:
    return Entry(
        name=name, kind=kind, size=0, mtime=_MTIME, permissions="drwxr-xr-x",
    )


async def test_double_right_arrow_drills_two_levels_deep(
    tmp_path: Path,
) -> None:
    """Two Right presses from contents pane should descend two levels.

    Tree shape:
        /root/
          alpha/
            a1/
              a1a/      # deeper to go
              a1b/
            a2/
          beta/
    """
    # Build the same layout on a real disk so NativeSource works.
    root = tmp_path
    (root / "alpha" / "a1" / "a1a").mkdir(parents=True)
    (root / "alpha" / "a1" / "a1b").mkdir()
    (root / "alpha" / "a2").mkdir()
    (root / "beta").mkdir()

    app = WTreeApp(root_path=str(root))
    async with app.run_test() as pilot:
        await pilot.pause()

        tree = app.query_one(TreePane)
        contents = app.query_one(ContentsPane)

        # Step 1: Navigate tree cursor down into alpha (level 1).
        # The root is at line 0; press Right to expand, then Down to
        # land on the first child.
        tree.focus()
        await pilot.pause()
        # Root is auto-expanded at mount; just press Down to land on
        # the first child (alpha).
        await pilot.press("down")
        await pilot.pause()

        # Confirm we're on alpha.
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == str(root / "alpha")
        # Contents pane should show alpha's children: a1, a2.
        assert contents.current_path == str(root / "alpha")

        # Step 2: Tab to contents pane.
        await pilot.press("tab")
        await pilot.pause()
        # Cursor should be on first row (a1) in contents.
        assert contents.cursor_row == 0
        # And a1 is the path of row 0.
        assert contents._row_paths[0] == str(root / "alpha" / "a1")

        # Step 3: First Right — should descend into a1.
        await pilot.press("right")
        await pilot.pause()
        await pilot.pause()  # extra tick for the async refresh

        # Tree cursor should now be on a1.
        assert tree.cursor_node is not None, "tree cursor lost"
        assert tree.cursor_node.data == str(root / "alpha" / "a1"), (
            f"after 1st Right, tree cursor on "
            f"{tree.cursor_node.data!r}, expected "
            f"{str(root / 'alpha' / 'a1')!r}"
        )
        # Contents pane should now show a1's children: a1a, a1b.
        assert contents.current_path == str(root / "alpha" / "a1"), (
            f"after 1st Right, contents on {contents.current_path!r}, "
            f"expected {str(root / 'alpha' / 'a1')!r}"
        )
        # Cursor at row 0 of the new listing (on a1a).
        assert contents.cursor_row == 0
        assert contents._row_paths[0] == str(root / "alpha" / "a1" / "a1a")

        # Step 4: Second Right — should descend into a1a.
        await pilot.press("right")
        await pilot.pause()
        await pilot.pause()

        # Tree cursor should now be on a1a (NOT back on a1, NOT back
        # on alpha). This is the bug.
        assert tree.cursor_node is not None, "tree cursor lost after 2nd Right"
        actual_path = tree.cursor_node.data
        expected_path = str(root / "alpha" / "a1" / "a1a")
        assert actual_path == expected_path, (
            f"BUG REPRODUCED: after 2nd Right, tree cursor on "
            f"{actual_path!r}, expected {expected_path!r}. "
            f"contents.current_path={contents.current_path!r}"
        )
        # Contents pane should follow.
        assert contents.current_path == expected_path, (
            f"after 2nd Right, contents on {contents.current_path!r}, "
            f"expected {expected_path!r}"
        )
