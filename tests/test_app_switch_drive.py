"""Tests for app-level Ctrl+D - switch drive / location on the logged tree.

The browsable cousin of ``L`` (design.md 2026-06-07): the same
``DriveChooserScreen`` the destination browser uses, wired to re-root
the main tree. Coverage mirrors test_log_new_source.py's shape:
static surface (binding / menu / help), then pilot integration for
pick / cancel / same-root / vanished-anchor, tag survival across the
switch, and the flash messages.
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.widgets.dir_picker import DriveChooserScreen
from wtree.widgets.help import _help_content
from wtree.widgets.menu_bar import MENUS
from wtree.widgets.status_line import StatusLine
from wtree.widgets.tree_pane import TreePane


# ---------------------------------------------------------------------------
# Static surface
# ---------------------------------------------------------------------------


def test_app_has_ctrl_d_binding() -> None:
    assert any(
        b[0] == "ctrl+d" and b[1] == "switch_drive"
        for b in WTreeApp.BINDINGS
    )


def test_app_has_action_switch_drive() -> None:
    assert hasattr(WTreeApp, "action_switch_drive")
    assert callable(WTreeApp.action_switch_drive)


def test_commands_menu_has_switch_drive() -> None:
    commands = next(m for m in MENUS if m.name == "Commands")
    items = [
        (i.label, i.accelerator, i.action)
        for i in commands.items
        if not i.separator
    ]
    assert ("Switch drive", "d", "switch_drive") in items
    # Sits right after Log new source - they're a conceptual pair.
    labels = [i.label for i in commands.items]
    assert labels.index("Switch drive") == labels.index("Log new source") + 1


def test_help_lists_ctrl_d() -> None:
    text = str(_help_content())
    assert "Ctrl+D" in text
    assert "Switch drive" in text


# ---------------------------------------------------------------------------
# Pilot integration
# ---------------------------------------------------------------------------


def _stage(tmp_path: Path) -> tuple[str, str]:
    a = tmp_path / "rootA"
    b = tmp_path / "rootB"
    (a / "sub").mkdir(parents=True)
    b.mkdir()
    return str(a), str(b)


def _patch_anchors(monkeypatch, anchors: list[str]) -> None:
    import wtree.app as app_mod

    monkeypatch.setattr(
        app_mod, "list_drive_anchors",
        lambda current=None, **kw: anchors,
    )


async def test_pick_reroots_tree(tmp_path: Path, monkeypatch) -> None:
    root_a, root_b = _stage(tmp_path)
    _patch_anchors(monkeypatch, [root_a, root_b])
    app = WTreeApp(root_path=root_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert isinstance(app.screen, DriveChooserScreen)
        await pilot.press("down")   # rootA (current) -> rootB
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert app._root_path == root_b
        tree = app.query_one(TreePane)
        assert tree.root.data == root_b
        status = app.query_one(StatusLine)
        assert status._flash_message is not None
        assert "Logged:" in status._flash_message


async def test_tags_survive_switch(tmp_path: Path, monkeypatch) -> None:
    """Tags are absolute paths - re-rooting must not drop them."""
    root_a, root_b = _stage(tmp_path)
    _patch_anchors(monkeypatch, [root_a, root_b])
    app = WTreeApp(root_path=root_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(TreePane).focus()
        await pilot.pause()
        await pilot.press("down")   # cursor onto "sub"
        await pilot.press("space")  # tag it
        await pilot.pause()
        assert len(app.tagged_set) == 1

        await pilot.press("ctrl+d")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert app._root_path == root_b
        assert len(app.tagged_set) == 1  # survived


async def test_escape_cancels_without_reroot(
    tmp_path: Path, monkeypatch
) -> None:
    root_a, root_b = _stage(tmp_path)
    _patch_anchors(monkeypatch, [root_a, root_b])
    app = WTreeApp(root_path=root_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app._root_path == root_a
        status = app.query_one(StatusLine)
        assert status._flash_message is not None
        assert "cancelled" in status._flash_message


async def test_same_root_pick_is_noop(tmp_path: Path, monkeypatch) -> None:
    root_a, root_b = _stage(tmp_path)
    _patch_anchors(monkeypatch, [root_a, root_b])
    app = WTreeApp(root_path=root_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
        await pilot.press("enter")  # cursor starts on current root_a
        await pilot.pause()

        assert app._root_path == root_a
        tree = app.query_one(TreePane)
        assert tree.root.data == root_a


async def test_vanished_anchor_flashes_no_reroot(
    tmp_path: Path, monkeypatch
) -> None:
    """An anchor that stopped existing (unplugged drive) nudges, no-ops."""
    root_a, _ = _stage(tmp_path)
    gone = str(tmp_path / "unplugged")
    _patch_anchors(monkeypatch, [root_a, gone])
    app = WTreeApp(root_path=root_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert app._root_path == root_a
        status = app.query_one(StatusLine)
        assert status._flash_message is not None
        assert "not available" in status._flash_message


async def test_menu_route_activates_switch_drive(
    tmp_path: Path, monkeypatch
) -> None:
    """F9 -> Commands -> Switch drive pushes the chooser."""
    root_a, root_b = _stage(tmp_path)
    _patch_anchors(monkeypatch, [root_a, root_b])
    app = WTreeApp(root_path=root_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("right")  # Commands
        await pilot.pause()
        for _ in range(4):          # Search -> ... -> Switch drive (4)
            await pilot.press("down")
            await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert isinstance(app.screen, DriveChooserScreen)
        await pilot.press("escape")
        await pilot.pause()
