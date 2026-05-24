"""Tests for L log new source (2026-05-23).

XTree's "L" was "log a new drive". WTree generalises: type any
absolute or relative path, the tree re-roots there. Tags survive
(absolute paths). Blank-Enter = ascend (per design.md's layered
discoverability hint).

Tests:

* L opens the prompt.
* Absolute path re-roots.
* Relative path resolves against the current root.
* ``~`` expansion works (smoke; uses an existing dir).
* Blank Enter = ascend (parent of current root).
* Nonexistent path flashes error, doesn't re-root.
* File (not a directory) flashes error.
* Esc cancels.
* Tagged set survives re-root.
* BINDINGS includes L.
* Commands menu has Log new source.
* Help screen mentions L.
* Regression: Left-on-root tree gesture still ascends.
"""

from __future__ import annotations

import os
from pathlib import Path

from textual.widgets import Input

from wtree.app import WTreeApp
from wtree.widgets.menu_bar import MENUS
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.tree_pane import TreePane


async def _drive_log(pilot, app: WTreeApp, value: str) -> None:
    """Trigger L, set the prompt's input value, Enter. Polls for the
    prompt to open and close. Use this for the success path; the
    error path keeps the prompt open in the action's flash-only
    branches."""
    app.action_log_new_source()
    for _ in range(20):
        await pilot.pause()
        if isinstance(app.screen, PromptDialog):
            break
    assert isinstance(app.screen, PromptDialog), (
        f"prompt didn't open; current screen: {type(app.screen).__name__}"
    )
    inp = app.screen.query_one(Input)
    inp.value = value
    await pilot.press("enter")
    for _ in range(30):
        await pilot.pause()
        if not isinstance(app.screen, PromptDialog):
            break


# ---------------------------------------------------------------------------
# Prompt + happy paths
# ---------------------------------------------------------------------------


async def test_l_opens_prompt(tmp_path: Path) -> None:
    """Pressing L pushes a PromptDialog."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l")
        for _ in range(20):
            await pilot.pause()
            if isinstance(app.screen, PromptDialog):
                break
        assert isinstance(app.screen, PromptDialog)
        # Cancel cleanly.
        await pilot.press("escape")
        await pilot.pause()


async def test_absolute_path_re_roots(tmp_path: Path) -> None:
    """An absolute path replaces the tree root."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    app = WTreeApp(root_path=str(tmp_path / "alpha"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        assert tree.root.data == str(tmp_path / "alpha")

        await _drive_log(pilot, app, str(tmp_path / "beta"))

        assert app._root_path == str(tmp_path / "beta")
        assert tree.root.data == str(tmp_path / "beta")


async def test_relative_path_resolves_against_root(tmp_path: Path) -> None:
    """Relative paths resolve against the current root, not cwd."""
    (tmp_path / "alpha" / "child").mkdir(parents=True)
    (tmp_path / "sibling").mkdir()
    app = WTreeApp(root_path=str(tmp_path / "alpha"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)

        # Relative "../sibling" from /alpha = /sibling.
        await _drive_log(pilot, app, "../sibling")
        assert app._root_path == str(tmp_path / "sibling")
        assert tree.root.data == str(tmp_path / "sibling")


async def test_dotslash_relative_resolves_to_child(tmp_path: Path) -> None:
    """A `./child` style relative path resolves to a subdir of root."""
    (tmp_path / "alpha" / "child").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path / "alpha"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)

        await _drive_log(pilot, app, "./child")
        assert app._root_path == str(tmp_path / "alpha" / "child")
        assert tree.root.data == str(tmp_path / "alpha" / "child")


async def test_dotdot_alone_resolves_to_parent(tmp_path: Path) -> None:
    """`..` alone in the relative form ascends one level."""
    (tmp_path / "alpha").mkdir()
    app = WTreeApp(root_path=str(tmp_path / "alpha"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        await _drive_log(pilot, app, "..")
        assert app._root_path == str(tmp_path)
        assert tree.root.data == str(tmp_path)


async def test_tilde_expansion(tmp_path: Path) -> None:
    """``~`` expansion works (uses os.path.expanduser).

    Sandboxed test: temporarily point ``HOME`` at ``tmp_path`` so
    ``~`` resolves to a known directory we can verify against.
    """
    monkey_home = tmp_path / "home"
    monkey_home.mkdir()
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(monkey_home)
    try:
        app = WTreeApp(root_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            tree = app.query_one(TreePane)
            await _drive_log(pilot, app, "~")
            assert app._root_path == str(monkey_home)
            assert tree.root.data == str(monkey_home)
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)


async def test_blank_enter_ascends(tmp_path: Path) -> None:
    """Blank submission = ascend (parent of current root)."""
    (tmp_path / "alpha").mkdir()
    app = WTreeApp(root_path=str(tmp_path / "alpha"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        await _drive_log(pilot, app, "")
        assert app._root_path == str(tmp_path)
        assert tree.root.data == str(tmp_path)


async def test_whitespace_only_treated_as_blank(tmp_path: Path) -> None:
    """Whitespace input is stripped — equivalent to blank submission."""
    (tmp_path / "alpha").mkdir()
    app = WTreeApp(root_path=str(tmp_path / "alpha"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        await _drive_log(pilot, app, "   ")
        assert app._root_path == str(tmp_path)


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


async def test_nonexistent_path_does_not_reroot(tmp_path: Path) -> None:
    """Typing a path that doesn't exist flashes an error and keeps root."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        before = tree.root.data
        await _drive_log(pilot, app, str(tmp_path / "ghost"))
        # Root unchanged.
        assert app._root_path == before
        assert tree.root.data == before


async def test_file_not_directory_does_not_reroot(tmp_path: Path) -> None:
    """Typing a file path (not a directory) flashes an error."""
    f = tmp_path / "note.txt"
    f.write_text("hi")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        before = tree.root.data
        await _drive_log(pilot, app, str(f))
        assert app._root_path == before
        assert tree.root.data == before


async def test_esc_cancels(tmp_path: Path) -> None:
    """Esc on the L prompt cancels — root unchanged."""
    (tmp_path / "alpha").mkdir()
    app = WTreeApp(root_path=str(tmp_path / "alpha"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        before = tree.root.data
        app.action_log_new_source()
        for _ in range(20):
            await pilot.pause()
            if isinstance(app.screen, PromptDialog):
                break
        await pilot.press("escape")
        for _ in range(20):
            await pilot.pause()
            if not isinstance(app.screen, PromptDialog):
                break
        assert app._root_path == before


# ---------------------------------------------------------------------------
# Tagged-set survival
# ---------------------------------------------------------------------------


async def test_tagged_set_survives_re_root(tmp_path: Path) -> None:
    """Tags are absolute paths — they persist across a re-root."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    app = WTreeApp(root_path=str(tmp_path / "alpha"))
    async with app.run_test() as pilot:
        await pilot.pause()
        sid = app._source.source_id
        app.tagged_set.add(sid, str(tmp_path / "alpha"))
        app.tagged_set.add(sid, str(tmp_path / "beta"))
        assert len(app.tagged_set) == 2

        await _drive_log(pilot, app, str(tmp_path / "beta"))

        # Both absolute paths still tagged after the re-root.
        assert len(app.tagged_set) == 2
        assert app.tagged_set.contains(sid, str(tmp_path / "alpha"))
        assert app.tagged_set.contains(sid, str(tmp_path / "beta"))


# ---------------------------------------------------------------------------
# Wiring + regression
# ---------------------------------------------------------------------------


def test_bindings_include_l() -> None:
    """BINDINGS contains the L key."""
    assert ("l", "log_new_source", "Log new source") in WTreeApp.BINDINGS


def test_commands_menu_has_log_new_source() -> None:
    """Commands menu lists a Log new source item with action log_new_source."""
    commands = MENUS[1]
    actions = [i.action for i in commands.items]
    assert "log_new_source" in actions
    log_item = next(i for i in commands.items if i.action == "log_new_source")
    assert log_item.label == "Log new source"
    assert log_item.accelerator == "l"


def test_help_content_mentions_l() -> None:
    """Help screen body mentions L in the Navigation section."""
    from wtree.widgets.help import _help_content

    text = str(_help_content())
    # Find the Navigation block; the L line should be within it.
    assert "Log new source" in text
    nav_idx = text.find("Navigation")
    log_idx = text.find("Log new source")
    assert nav_idx < log_idx, "L should appear under Navigation"


async def test_left_on_root_still_ascends(tmp_path: Path) -> None:
    """Regression: the tree-pane Left-on-root gesture still re-roots
    at the parent.

    Both Left-on-root and blank-Enter L go through ``_do_ascend``;
    this test pins the original gesture so the refactor didn't break
    it.
    """
    (tmp_path / "alpha").mkdir()
    app = WTreeApp(root_path=str(tmp_path / "alpha"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        # Cursor on root after mount.
        await pilot.press("left")
        for _ in range(15):
            await pilot.pause()
            if tree.root.data == str(tmp_path):
                break
        assert tree.root.data == str(tmp_path)
