"""Tests for the Ctrl+I Properties inspector.

Three surfaces under test, matching the layout of tests/test_help.py:

* Pure body renderers (``_render_*``) - unit-test the Rich Text
  output without instantiating the screen.
* Owner / group lookup helper (``wtree._owner.lookup``) - patched
  ``pwd`` / ``grp`` so we cover the POSIX happy path, the
  KeyError-numeric-fallback path, and the Windows ``HAS_PWD_GRP=False``
  branch.
* End-to-end via the app: Ctrl+I from the tree pane opens dir mode,
  from the contents pane on a file opens file mode, with tags opens
  tagged mode, and on an empty selection flashes "Nothing to inspect"
  without opening a modal. Plus the cancel-walk-then-dismiss flow.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from wtree import _owner
from wtree.app import WTreeApp
from wtree.sources.base import Kind
from wtree.tagged_set import Tag
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.properties import (
    DirProps,
    FileProps,
    PropertiesScreen,
    TaggedProps,
    _render_dir_complete,
    _render_dir_initial,
    _render_file,
    _render_tagged,
    _WalkSummary,
    _walk_directory,
)
from wtree.widgets.tree_pane import TreePane


# ---------------------------------------------------------------------------
# Owner / group lookup (POSIX + Windows branches)
# ---------------------------------------------------------------------------


class _FakeStat:
    """Stand-in for ``os.stat_result`` - only need uid/gid."""

    def __init__(self, uid: int, gid: int) -> None:
        self.st_uid = uid
        self.st_gid = gid


class _FakePwd:
    def __init__(self, name: str) -> None:
        self.pw_name = name


class _FakeGrp:
    def __init__(self, name: str) -> None:
        self.gr_name = name


def test_owner_lookup_posix_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both pwd and grp resolve, names come back as strings."""
    monkeypatch.setattr(_owner, "HAS_PWD_GRP", True)
    monkeypatch.setattr(
        _owner,
        "_pwd",
        type("P", (), {"getpwuid": staticmethod(lambda u: _FakePwd("alice"))})(),
    )
    monkeypatch.setattr(
        _owner,
        "_grp",
        type("G", (), {"getgrgid": staticmethod(lambda g: _FakeGrp("staff"))})(),
    )
    owner, group = _owner.lookup(_FakeStat(1000, 50))
    assert owner == "alice"
    assert group == "staff"


def test_owner_lookup_keyerror_falls_back_to_numeric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown uid/gid (KeyError) -> render the numeric id as a string.

    Common on container images and synthetic mounts where the local
    NSS database doesn't carry every numeric id the filesystem has.
    """
    monkeypatch.setattr(_owner, "HAS_PWD_GRP", True)

    def boom_pwd(_uid: int) -> Any:
        raise KeyError("uid")

    def boom_grp(_gid: int) -> Any:
        raise KeyError("gid")

    monkeypatch.setattr(
        _owner, "_pwd", type("P", (), {"getpwuid": staticmethod(boom_pwd)})()
    )
    monkeypatch.setattr(
        _owner, "_grp", type("G", (), {"getgrgid": staticmethod(boom_grp)})()
    )
    owner, group = _owner.lookup(_FakeStat(4242, 7777))
    assert owner == "4242"
    assert group == "7777"


def test_owner_lookup_windows_returns_na(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows pwd/grp aren't importable; render 'n/a' for both."""
    monkeypatch.setattr(_owner, "HAS_PWD_GRP", False)
    owner, group = _owner.lookup(_FakeStat(0, 0))
    assert owner == "n/a"
    assert group == "n/a"


# ---------------------------------------------------------------------------
# Pure body renderers - assert on the rendered text directly
# ---------------------------------------------------------------------------


def test_render_file_shows_required_rows(tmp_path: Path) -> None:
    """File body covers Path / Name / Kind / Size / Modified / Perms / Owner / Group."""
    target = tmp_path / "report.txt"
    target.write_text("hello, world\n")
    body = str(_render_file(FileProps(path=str(target), kind=Kind.FILE)))
    for label in (
        "Path",
        "Name",
        "Kind",
        "Size",
        "Modified",
        "Permissions",
        "Owner",
        "Group",
    ):
        assert label in body, f"missing row label: {label!r}"
    assert "report.txt" in body
    assert "file" in body  # kind value


def test_render_file_handles_missing_path(tmp_path: Path) -> None:
    """Non-existent path: identity rows render; stat failure shown inline."""
    body = str(
        _render_file(FileProps(path=str(tmp_path / "ghost"), kind=Kind.FILE))
    )
    assert "Could not stat" in body


def test_render_dir_initial_shows_computing_placeholder(tmp_path: Path) -> None:
    """Pre-walk dir body shows the 'Computing recursive total...' line."""
    body = str(_render_dir_initial(DirProps(path=str(tmp_path))))
    assert "Computing" in body
    assert "Directory" in body


def test_render_dir_complete_shows_totals(tmp_path: Path) -> None:
    """Post-walk dir body lists Total size + Files + Subdirectories."""
    summary = _WalkSummary(
        total_bytes=2048, file_count=3, dir_count=1, cancelled=False, errors=0
    )
    body = str(_render_dir_complete(DirProps(path=str(tmp_path)), summary))
    assert "Total size" in body
    assert "Files" in body
    assert "Subdirectories" in body
    assert "2,048 bytes" in body
    assert "(cancelled" not in body


def test_render_dir_complete_marks_cancellation(tmp_path: Path) -> None:
    """Cancelled walks get a '(cancelled - partial)' tag on the totals header."""
    summary = _WalkSummary(
        total_bytes=512, file_count=1, dir_count=0, cancelled=True, errors=0
    )
    body = str(_render_dir_complete(DirProps(path=str(tmp_path)), summary))
    assert "cancelled" in body


def test_render_dir_complete_lists_walk_errors(tmp_path: Path) -> None:
    """Walk errors counted (e.g. permission denied) get their own row."""
    summary = _WalkSummary(
        total_bytes=0, file_count=0, dir_count=0, cancelled=False, errors=3
    )
    body = str(_render_dir_complete(DirProps(path=str(tmp_path)), summary))
    assert "Walk errors" in body
    assert "3" in body


def test_render_tagged_breaks_down_by_kind(tmp_path: Path) -> None:
    """Tagged-set body counts files vs directories and sums file sizes only."""
    f1 = tmp_path / "a.txt"
    f1.write_text("x" * 100)
    f2 = tmp_path / "b.txt"
    f2.write_text("y" * 200)
    d1 = tmp_path / "sub"
    d1.mkdir()
    tags = (
        Tag("native", str(f1)),
        Tag("native", str(f2)),
        Tag("native", str(d1)),
    )
    body = str(_render_tagged(TaggedProps(tags=tags)))
    assert "Tagged set" in body
    assert "3 entries" in body
    assert "Files" in body
    assert "Directories" in body
    assert "Total size" in body
    # Files contribute 300; dir contributes 0.
    assert "300 B" in body


def test_render_tagged_counts_unreadable(tmp_path: Path) -> None:
    """Tagged paths that can't be stat'd land in an 'Unreadable' row."""
    tags = (Tag("native", str(tmp_path / "ghost")),)
    body = str(_render_tagged(TaggedProps(tags=tags)))
    assert "Unreadable" in body


# ---------------------------------------------------------------------------
# Walk function - directly, without the screen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_walk_directory_sums_recursive_size(tmp_path: Path) -> None:
    """Recursive walk sums file sizes and counts files/dirs."""
    (tmp_path / "a").write_bytes(b"x" * 10)
    (tmp_path / "b").write_bytes(b"y" * 20)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c").write_bytes(b"z" * 30)
    summary = await _walk_directory(str(tmp_path), asyncio.Event())
    assert summary.total_bytes == 60
    assert summary.file_count == 3
    assert summary.dir_count == 1
    assert summary.cancelled is False
    assert summary.errors == 0


@pytest.mark.asyncio
async def test_walk_directory_respects_cancel(tmp_path: Path) -> None:
    """Cancellation signal stops the walk between directory visits."""
    # Make sure there's something to walk - a few files in the root.
    for i in range(5):
        (tmp_path / f"f{i}").write_bytes(b"x")
    cancel = asyncio.Event()
    cancel.set()  # cancel before the first iteration
    summary = await _walk_directory(str(tmp_path), cancel)
    assert summary.cancelled is True
    # We bail before scanning anything so totals stay zero.
    assert summary.total_bytes == 0
    assert summary.file_count == 0


# ---------------------------------------------------------------------------
# App integration - Ctrl+I from each starting state
# ---------------------------------------------------------------------------


async def test_ctrl_i_with_tags_opens_tagged_mode(tmp_path: Path) -> None:
    """Tagged set non-empty -> PropertiesScreen in 'tagged' mode."""
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("y")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Manually seed the tagged set - cheaper than driving the UI.
        sid = app._source.source_id
        app.tagged_set.add(sid, str(tmp_path / "a.txt"))
        app.tagged_set.add(sid, str(tmp_path / "b.txt"))
        await pilot.press("ctrl+i")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PropertiesScreen)
        assert screen._mode == "tagged"


async def test_ctrl_i_on_file_in_contents_pane_opens_file_mode(
    tmp_path: Path,
) -> None:
    """Contents pane cursor on a file -> 'file' mode."""
    (tmp_path / "only.txt").write_text("hello")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        contents.focus()
        await pilot.pause()
        # Cursor should be on the only row (a file).
        await pilot.press("ctrl+i")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PropertiesScreen)
        assert screen._mode == "file"


async def test_ctrl_i_from_tree_pane_opens_dir_mode(tmp_path: Path) -> None:
    """Tree-pane cursor on a directory -> 'dir' mode."""
    (tmp_path / "child").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        tree.focus()
        await pilot.pause()
        await pilot.press("ctrl+i")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PropertiesScreen)
        assert screen._mode == "dir"


async def test_ctrl_i_with_empty_selection_flashes(tmp_path: Path) -> None:
    """No tags, no usable cursor -> flash 'Nothing to inspect', no modal."""
    # Empty directory: contents pane has no rows; tree pane root has no
    # children to descend into.
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        contents = app.query_one(ContentsPane)
        contents.focus()
        await pilot.pause()
        await pilot.press("ctrl+i")
        await pilot.pause()
        # No modal pushed - app screen is still the default.
        assert not isinstance(app.screen, PropertiesScreen)


async def test_esc_dismisses_completed_modal(tmp_path: Path) -> None:
    """Esc on a file-mode modal (walk-done by definition) dismisses."""
    (tmp_path / "only.txt").write_text("x")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(ContentsPane).focus()
        await pilot.pause()
        await pilot.press("ctrl+i")
        await pilot.pause()
        assert isinstance(app.screen, PropertiesScreen)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert not any(
            isinstance(s, PropertiesScreen) for s in app.screen_stack
        )


async def test_q_dismisses_modal_unconditionally(tmp_path: Path) -> None:
    """Q closes regardless of walk state - mirrors Viewer / Help."""
    (tmp_path / "only.txt").write_text("x")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(ContentsPane).focus()
        await pilot.pause()
        await pilot.press("ctrl+i")
        await pilot.pause()
        assert isinstance(app.screen, PropertiesScreen)
        await pilot.press("q")
        await pilot.pause()
        await pilot.pause()
        assert not any(
            isinstance(s, PropertiesScreen) for s in app.screen_stack
        )


async def test_dir_mode_walk_cancels_on_first_esc(tmp_path: Path) -> None:
    """First Esc during an in-flight walk cancels but doesn't dismiss.

    The dialog stays open showing the partial result; a second Esc
    closes it.
    """
    # Construct the screen directly so we control the walk timing.
    screen = PropertiesScreen("dir", directory=DirProps(path=str(tmp_path)))
    assert screen._walk_done is False
    screen.action_escape_pressed()
    # First Esc set the cancel event but didn't mark walk done; the
    # dismiss path isn't taken. We can't easily assert "not dismissed"
    # without an app context, but the cancel event must be set.
    assert screen._cancel_event.is_set() is True


# ---------------------------------------------------------------------------
# Bad mode rejected at construction
# ---------------------------------------------------------------------------


def test_bad_mode_raises_value_error() -> None:
    """PropertiesScreen rejects unknown modes early - typo guard."""
    with pytest.raises(ValueError):
        PropertiesScreen("bogus")
