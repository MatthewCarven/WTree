"""Pilot tests for ``WTreeApp`` with the panes wired to a ``MockSource``.

These tests use Textual's ``run_test`` headless pilot. They cover the
two-pane wiring (tree shows dirs only, contents follows cursor, errors
become leaves) and the tagged-set integration (Space/T toggle, Ctrl+U
clears, tag markers survive pane refreshes, subtitle reflects count).

``os.path.join`` is used to build child paths so the tests match what
``TreePane._populate`` will actually look up in ``MockSource`` on every OS.
"""

from __future__ import annotations

import os
from datetime import datetime

from textual.coordinate import Coordinate

from wtree.app import WTreeApp
from wtree.sources.base import Entry, Kind, ScanError
from wtree.sources.mock import MockSource
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.tree_pane import TreePane


# A fixed timestamp keeps assertions stable.
_MTIME = datetime(2026, 5, 20, 12, 0, 0)


def _entry(name: str, kind: Kind = Kind.FILE, size: int = 0) -> Entry:
    return Entry(
        name=name,
        kind=kind,
        size=size,
        mtime=_MTIME,
        permissions="-rw-r--r--",
    )


async def test_tree_pane_renders_only_directories() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={
            root: [
                _entry("file.txt", Kind.FILE, 100),
                _entry("alpha", Kind.DIR),
                _entry("beta", Kind.DIR),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = pilot.app.query_one(TreePane)
        labels = [str(c.label) for c in tree.root.children]
        # Files must not appear in the tree.
        assert "file.txt" not in labels
        # Directory entries do, in sorted order.
        assert labels == ["alpha", "beta"]


async def test_contents_pane_shows_root_on_mount() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={
            root: [
                _entry("alpha.txt", Kind.FILE, 50),
                _entry("zeta", Kind.DIR),
                _entry("middle.bin", Kind.FILE, 99),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        # All three entries (file + dir + file) show up.
        assert contents.row_count == 3
        assert contents.current_path == root


async def test_contents_pane_follows_tree_cursor_down() -> None:
    root = os.path.abspath(os.sep + "root")
    childdir_path = os.path.join(root, "childdir")
    src = MockSource(
        contents={
            root: [
                _entry("childdir", Kind.DIR),
            ],
            childdir_path: [
                _entry("inside.txt", Kind.FILE, 42),
                _entry("inside.log", Kind.FILE, 17),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Cursor starts on the root, contents shows root (one dir entry).
        contents = pilot.app.query_one(ContentsPane)
        assert contents.row_count == 1
        # Move down — into ``childdir`` — and let the events flush.
        await pilot.press("down")
        await pilot.pause()
        assert contents.current_path == childdir_path
        assert contents.row_count == 2


async def test_scan_error_becomes_leaf_in_tree() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={
            root: [
                _entry("ok_dir", Kind.DIR),
            ],
        },
        errors={
            root: ScanError(path=root, message="Permission denied", cause="PermissionError"),
        },
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = pilot.app.query_one(TreePane)
        # The directory-level error wins over the scripted contents in
        # ``MockSource``, so we expect exactly one error leaf and no
        # directory children at the root.
        labels = [str(c.label) for c in tree.root.children]
        assert len(labels) == 1
        assert labels[0].startswith("⚠")



# ---------------------------------------------------------------------------
# Tagged set integration
# ---------------------------------------------------------------------------


async def test_space_toggles_tag_on_focused_row() -> None:
    root = os.path.abspath(os.sep + "root")
    file_path = os.path.join(root, "alpha.txt")
    src = MockSource(
        contents={
            root: [_entry("alpha.txt", Kind.FILE, 50)],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Tagging is a pane-local action — focus the contents pane first.
        pilot.app.query_one(ContentsPane).focus()
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert len(app.tagged_set) == 1
        assert app.tagged_set.contains("mock", file_path)
        # A second press un-tags.
        await pilot.press("space")
        await pilot.pause()
        assert len(app.tagged_set) == 0


async def test_t_letter_also_toggles_tag() -> None:
    """``T`` is the XTree primary; ``space`` the comfortable alias. Both
    must reach ``action_toggle_tag``.
    """
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={root: [_entry("alpha.txt", Kind.FILE, 50)]}
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one(ContentsPane).focus()
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        assert len(app.tagged_set) == 1


async def test_tag_marker_renders_in_t_column() -> None:
    """After a toggle, the leading "T" column shows "*"."""
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={root: [_entry("alpha.txt", Kind.FILE, 50)]}
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one(ContentsPane).focus()
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        marker = contents.get_cell_at(Coordinate(0, 0))
        # Tagged cells are now Rich Text (bold yellow) since 2026-05-22;
        # str() returns the plain glyph for both plain-str and Text cells.
        assert str(marker) == "*"


async def test_tag_marker_persists_when_pane_refreshes() -> None:
    """Tags survive a pane refresh — the set lives on the app, not the pane."""
    root = os.path.abspath(os.sep + "root")
    sub_path = os.path.join(root, "sub")
    src = MockSource(
        contents={
            root: [_entry("sub", Kind.DIR)],
            sub_path: [],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Pre-tag the dir directly (simulates "tagged in an earlier action").
        app.tagged_set.add("mock", sub_path)
        contents = pilot.app.query_one(ContentsPane)
        # Force a refresh by re-showing the same path — emulates what the
        # tree's NodeHighlighted handler does when you navigate.
        await contents.show_path(root)
        await pilot.pause()
        marker = contents.get_cell_at(Coordinate(0, 0))
        assert str(marker) == "*"


async def test_ctrl_u_clears_tagged_set_and_refreshes_markers() -> None:
    root = os.path.abspath(os.sep + "root")
    a_path = os.path.join(root, "a.txt")
    b_path = os.path.join(root, "b.txt")
    src = MockSource(
        contents={
            root: [
                _entry("a.txt", Kind.FILE, 1),
                _entry("b.txt", Kind.FILE, 2),
            ],
        }
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Pre-tag both rows.
        app.tagged_set.add("mock", a_path)
        app.tagged_set.add("mock", b_path)
        contents = pilot.app.query_one(ContentsPane)
        # Refresh markers so they actually render (we tagged after mount
        # finished its initial paint).
        contents.refresh_tag_markers()
        await pilot.pause()
        assert str(contents.get_cell_at(Coordinate(0, 0))) == "*"
        # Ctrl+U clears the set and the markers.
        await pilot.press("ctrl+u")
        await pilot.pause()
        assert len(app.tagged_set) == 0
        # After untag, cells revert to plain "" — untagged style is plain str.
        assert str(contents.get_cell_at(Coordinate(0, 0))) == ""
        assert str(contents.get_cell_at(Coordinate(1, 0))) == ""


async def test_subtitle_reflects_tag_count() -> None:
    root = os.path.abspath(os.sep + "root")
    src = MockSource(
        contents={root: [_entry("alpha.txt", Kind.FILE, 50)]}
    )
    app = WTreeApp(source=src, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Untagged → just the version string.
        assert "tagged" not in app.sub_title
        pilot.app.query_one(ContentsPane).focus()
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert "1 tagged" in app.sub_title
        await pilot.press("space")
        await pilot.pause()
        assert "tagged" not in app.sub_title


async def test_error_rows_are_not_taggable() -> None:
    """Pressing Space with the cursor on an error row is a no-op."""
    root = os.path.abspath(os.sep + "root")
    # ``MockSource`` lets you script an Entry *and* a ScanError in the same
    # ``contents`` list — that's how we put the cursor on an error row.
    error_then_ok = MockSource(
        contents={
            root: [
                ScanError(path=root, message="bad item", cause="OSError"),
                _entry("alpha.txt", Kind.FILE, 1),
            ],
        }
    )
    app = WTreeApp(source=error_then_ok, root_path=root)
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = pilot.app.query_one(ContentsPane)
        contents.focus()
        await pilot.pause()
        # Cursor is on row 0 — the error row. Space must be a no-op.
        await pilot.press("space")
        await pilot.pause()
        assert len(app.tagged_set) == 0
