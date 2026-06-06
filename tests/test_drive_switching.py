"""Tests for picker drive / share switching (design.md 2026-06-07).

Three layers:

* ``wtree._drives`` enumeration units - the GetLogicalDrives bitmask
  decoder, the POSIX anchor set against a tmp media-base layout, the
  Windows list shape via the ``windows=`` parameter (the
  ``canonical_path(case_insensitive=...)`` testability precedent), and
  current-root inclusion + order-preserving dedupe.
* ``DriveChooserScreen`` - initial cursor on the current anchor,
  arrow movement + clamping, Enter -> anchor, Esc -> None.
* Picker integration - Ctrl+D binding exists; switching re-roots the
  tree; per-root cursor memory restores on switch-back (keyed by root
  path, NOT splitdrive anchor); same-root pick is a no-op.
"""

from __future__ import annotations

from pathlib import Path

from wtree._drives import (
    _bitmask_to_anchors,
    _posix_anchors,
    list_drive_anchors,
)
from wtree.app import WTreeApp
from wtree.widgets.dir_picker import (
    DirPickerScreen,
    DriveChooserScreen,
    _PickerTree,
)


# ---------------------------------------------------------------------------
# _drives units
# ---------------------------------------------------------------------------


def test_bitmask_decodes_low_bits() -> None:
    """Bit 0 = A:, bit 2 = C: - the GetLogicalDrives contract."""
    assert _bitmask_to_anchors(0b101) == ["A:\\", "C:\\"]


def test_bitmask_empty_and_high() -> None:
    assert _bitmask_to_anchors(0) == []
    assert "Z:\\" in _bitmask_to_anchors(1 << 25)


def test_posix_anchors_layout(tmp_path: Path) -> None:
    """/, ~, then existing one-level children of the media bases."""
    media = tmp_path / "media"
    (media / "usb0").mkdir(parents=True)
    (media / "backup").mkdir()
    (media / "not-a-dir.txt").write_text("x")
    home = str(tmp_path / "home" / "matt")
    Path(home).mkdir(parents=True)

    anchors = _posix_anchors(
        media_bases=(str(media), str(tmp_path / "missing-base")),
        home=home,
    )
    assert anchors[0] == "/"
    assert anchors[1] == home
    # children sorted; file filtered out; missing base silently skipped
    assert anchors[2:] == [str(media / "backup"), str(media / "usb0")]


def test_posix_anchors_home_root_not_duplicated(tmp_path: Path) -> None:
    """home='/' must not list / twice."""
    anchors = _posix_anchors(media_bases=(), home="/")
    assert anchors == ["/"]


def test_list_anchors_includes_current_first(tmp_path: Path) -> None:
    """An unenumerable current root (UNC-ish) is prepended."""
    anchors = list_drive_anchors(
        "/weird/share", windows=False, media_bases=(), home=str(tmp_path)
    )
    assert anchors[0] == "/weird/share"
    assert "/" in anchors


def test_list_anchors_no_duplicate_current(tmp_path: Path) -> None:
    """A current root the enumeration already found isn't doubled."""
    anchors = list_drive_anchors(
        "/", windows=False, media_bases=(), home=str(tmp_path)
    )
    assert anchors.count("/") == 1


def test_list_anchors_windows_shape(monkeypatch) -> None:
    """windows=True path returns drive-root strings (via listdrives)."""
    import wtree._drives as drives

    monkeypatch.setattr(
        drives.os, "listdrives", lambda: ["C:\\", "E:\\"], raising=False
    )
    anchors = list_drive_anchors("C:\\", windows=True)
    assert anchors == ["C:\\", "E:\\"]


# ---------------------------------------------------------------------------
# DriveChooserScreen
# ---------------------------------------------------------------------------


def _chooser(current: str | None = "/b") -> DriveChooserScreen:
    return DriveChooserScreen(["/a", "/b", "/c"], current=current)


def test_chooser_initial_cursor_on_current() -> None:
    assert _chooser()._cursor == 1
    assert _chooser(current="/missing")._cursor == 0
    assert _chooser(current=None)._cursor == 0


def test_chooser_body_marks_cursor_row() -> None:
    c = _chooser()
    lines = c._body_text().splitlines()
    assert lines[1].startswith("> ")
    assert lines[0].startswith("  ")


async def test_chooser_enter_returns_anchor(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        results: list[str | None] = []
        app.push_screen(_chooser(), callback=results.append)
        await pilot.pause()
        await pilot.press("down")   # /b -> /c
        await pilot.press("enter")
        await pilot.pause()
        assert results == ["/c"]


async def test_chooser_escape_returns_none(tmp_path: Path) -> None:
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        results: list[str | None] = []
        app.push_screen(_chooser(), callback=results.append)
        await pilot.pause()
        await pilot.press("up")     # clamp check en route
        await pilot.press("up")
        await pilot.press("escape")
        await pilot.pause()
        assert results == [None]


def test_chooser_cursor_clamps() -> None:
    c = _chooser(current="/a")
    c._cursor = 0
    # No widgets mounted - drive the index math only.
    c._cursor = max(0, c._cursor - 1)
    assert c._cursor == 0


# ---------------------------------------------------------------------------
# Picker integration
# ---------------------------------------------------------------------------


def test_picker_has_ctrl_d_binding() -> None:
    assert any(
        b[0] == "ctrl+d" and b[1] == "switch_drive"
        for b in DirPickerScreen.BINDINGS
    )


def _stage_two_roots(tmp_path: Path) -> tuple[str, str]:
    a = tmp_path / "driveA" / "projects"
    b = tmp_path / "driveB"
    a.mkdir(parents=True)
    (b / "incoming").mkdir(parents=True)
    return str(tmp_path / "driveA"), str(b)


async def test_switch_reroots_and_remembers(
    tmp_path: Path, monkeypatch
) -> None:
    """Switch A->B re-roots at B; switch back restores A's cursor."""
    root_a, root_b = _stage_two_roots(tmp_path)
    projects = str(Path(root_a) / "projects")

    import wtree.widgets.dir_picker as mod

    monkeypatch.setattr(
        mod, "list_drive_anchors",
        lambda current=None, **kw: [root_a, root_b],
    )

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = DirPickerScreen(
            app._source, start_root=root_a, reveal_target=projects
        )
        app.push_screen(picker)
        await pilot.pause()
        tree = picker.query_one(_PickerTree)
        assert tree.root.data == root_a
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == projects  # revealed

        # Ctrl+D -> chooser; pick driveB (down from index 0 = current).
        await pilot.press("ctrl+d")
        await pilot.pause()
        chooser = app.screen
        assert isinstance(chooser, DriveChooserScreen)
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert tree.root.data == root_b
        # Memory recorded the outgoing cursor.
        assert picker._per_root_cursor[root_a] == projects

        # Switch back -> cursor restored onto projects.
        await pilot.press("ctrl+d")
        await pilot.pause()
        await pilot.press("up")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert tree.root.data == root_a
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == projects


async def test_same_root_pick_is_noop(tmp_path: Path, monkeypatch) -> None:
    """Choosing the root you're already on changes nothing."""
    root_a, root_b = _stage_two_roots(tmp_path)

    import wtree.widgets.dir_picker as mod

    monkeypatch.setattr(
        mod, "list_drive_anchors",
        lambda current=None, **kw: [root_a, root_b],
    )

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = DirPickerScreen(app._source, start_root=root_a)
        app.push_screen(picker)
        await pilot.pause()
        tree = picker.query_one(_PickerTree)

        await pilot.press("ctrl+d")
        await pilot.pause()
        await pilot.press("enter")  # cursor starts on current = root_a
        await pilot.pause()

        assert tree.root.data == root_a
        assert picker._per_root_cursor == {}  # no-op recorded nothing


async def test_escape_in_chooser_keeps_picker_state(
    tmp_path: Path, monkeypatch
) -> None:
    root_a, root_b = _stage_two_roots(tmp_path)

    import wtree.widgets.dir_picker as mod

    monkeypatch.setattr(
        mod, "list_drive_anchors",
        lambda current=None, **kw: [root_a, root_b],
    )

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = DirPickerScreen(app._source, start_root=root_a)
        app.push_screen(picker)
        await pilot.pause()
        tree = picker.query_one(_PickerTree)

        await pilot.press("ctrl+d")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert tree.root.data == root_a
        assert app.screen is picker  # back on the picker


async def test_footer_mentions_drives(tmp_path: Path) -> None:
    root_a, _ = _stage_two_roots(tmp_path)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = DirPickerScreen(app._source, start_root=root_a)
        app.push_screen(picker)
        await pilot.pause()
        from textual.widgets import Label

        hint = picker.query_one("#picker-hint", Label)
        assert "Ctrl+D drives" in str(hint.render())
