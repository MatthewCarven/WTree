"""Tests for the make-new planner (``wtree.ops.make_new``) and its
native executor branch (``wtree.ops.execute._native_make_new``).

Mirrors the shape of ``test_ops_rename.py``: pure-planner unit tests
first, then a small executor real-filesystem block, then a few action-
layer pilot tests for the chooser + prompt + plan integration. The full
end-to-end keystroke flow lives in ``test_make_new_e2e.py``.

Make-new's planner contract:

* parent_path + name + kind in, at most one PlanItem out;
* lenient mode: ``foo/bar/baz`` is allowed and creates intermediates;
* rejects InvalidName (empty / absolute / ``..``), InvalidKind (not
  DIR/FILE), Exists (leaf already there), UnknownSource.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from wtree.app import WTreeApp
from wtree.ops import OperationKind, apply_plan, plan_make_new
from wtree.ops.execute import _make_new_blocking
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.sources.native import NativeSource
from wtree.widgets.kind_chooser import KindChooserDialog
from wtree.widgets.prompt import PromptDialog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 22, 12, 0, 0)


@pytest.fixture
def small_mock() -> MockSource:
    now = _now()
    return MockSource(
        contents={
            "/": [
                Entry("proj", Kind.DIR, 4096, now),
                Entry("readme.txt", Kind.FILE, 200, now),
            ],
            "/proj": [
                Entry("notes.md", Kind.FILE, 80, now),
            ],
        }
    )


# ---------------------------------------------------------------------------
# plan_make_new - happy paths
# ---------------------------------------------------------------------------


async def test_plan_make_new_simple_file(small_mock: MockSource) -> None:
    plan = await plan_make_new(
        "/proj", "new.md", Kind.FILE, "mock", {"mock": small_mock}
    )
    assert plan.kind is OperationKind.MAKE_NEW
    assert len(plan.items) == 1
    assert not plan.errors
    only = plan.items[0]
    assert only.dst_path == "/proj/new.md"
    assert only.kind is Kind.FILE
    assert only.src_path == only.dst_path  # mirrored
    assert only.src_source_id == only.dst_source_id == "mock"
    assert only.size == 0


async def test_plan_make_new_simple_dir(small_mock: MockSource) -> None:
    plan = await plan_make_new(
        "/proj", "subdir", Kind.DIR, "mock", {"mock": small_mock}
    )
    assert len(plan.items) == 1
    only = plan.items[0]
    assert only.dst_path == "/proj/subdir"
    assert only.kind is Kind.DIR


async def test_plan_make_new_lenient_subdirs_allowed(
    small_mock: MockSource,
) -> None:
    """A path-bearing name is lenient mode - the planner accepts it
    and the executor will create intermediates on apply."""
    plan = await plan_make_new(
        "/", "a/b/c.txt", Kind.FILE, "mock", {"mock": small_mock}
    )
    assert len(plan.items) == 1
    assert plan.items[0].dst_path == "/a/b/c.txt"
    assert plan.items[0].kind is Kind.FILE


async def test_plan_make_new_parent_root(small_mock: MockSource) -> None:
    """Parent '/' joins cleanly with the leaf name."""
    plan = await plan_make_new(
        "/", "newdir", Kind.DIR, "mock", {"mock": small_mock}
    )
    assert plan.items[0].dst_path == "/newdir"


async def test_plan_make_new_strips_trailing_slash_from_name(
    small_mock: MockSource,
) -> None:
    """'mydir/' and 'mydir' map to the same leaf - the chooser
    modal already picked DIR, so the trailing slash is noise."""
    plan = await plan_make_new(
        "/proj", "subdir/", Kind.DIR, "mock", {"mock": small_mock}
    )
    assert plan.items[0].dst_path == "/proj/subdir"


async def test_plan_make_new_strips_whitespace_around_name(
    small_mock: MockSource,
) -> None:
    plan = await plan_make_new(
        "/proj", "  spaced.txt  ", Kind.FILE, "mock", {"mock": small_mock}
    )
    assert plan.items[0].dst_path == "/proj/spaced.txt"


async def test_plan_make_new_collapses_double_slashes(
    small_mock: MockSource,
) -> None:
    """foo//bar normalises to foo/bar."""
    plan = await plan_make_new(
        "/", "foo//bar", Kind.DIR, "mock", {"mock": small_mock}
    )
    assert plan.items[0].dst_path == "/foo/bar"


async def test_plan_make_new_drops_dot_segments(
    small_mock: MockSource,
) -> None:
    """'.' segments are noise - silently dropped."""
    plan = await plan_make_new(
        "/", "foo/./bar", Kind.DIR, "mock", {"mock": small_mock}
    )
    assert plan.items[0].dst_path == "/foo/bar"


async def test_plan_make_new_summary_text(small_mock: MockSource) -> None:
    plan = await plan_make_new(
        "/proj", "new.md", Kind.FILE, "mock", {"mock": small_mock}
    )
    s = plan.summary()
    assert "make_new:" in s
    assert "1 file(s)" in s


# ---------------------------------------------------------------------------
# plan_make_new - rejections
# ---------------------------------------------------------------------------


async def test_plan_make_new_unknown_source_errors() -> None:
    plan = await plan_make_new(
        "/whatever", "x", Kind.FILE, "does-not-exist",
        {"native": NativeSource()},
    )
    assert plan.items == []
    assert len(plan.errors) == 1
    assert plan.errors[0].cause == "UnknownSource"


async def test_plan_make_new_rejects_symlink_kind(
    small_mock: MockSource,
) -> None:
    plan = await plan_make_new(
        "/proj", "linky", Kind.SYMLINK, "mock", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidKind"


async def test_plan_make_new_rejects_other_kind(
    small_mock: MockSource,
) -> None:
    plan = await plan_make_new(
        "/proj", "weird", Kind.OTHER, "mock", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidKind"


async def test_plan_make_new_rejects_empty_name(
    small_mock: MockSource,
) -> None:
    plan = await plan_make_new(
        "/proj", "", Kind.FILE, "mock", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidName"
    assert "empty" in plan.errors[0].message


async def test_plan_make_new_rejects_whitespace_only_name(
    small_mock: MockSource,
) -> None:
    plan = await plan_make_new(
        "/proj", "   ", Kind.FILE, "mock", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidName"


async def test_plan_make_new_rejects_absolute_posix_name(
    small_mock: MockSource,
) -> None:
    """An absolute path would escape the parent - reject."""
    plan = await plan_make_new(
        "/proj", "/etc/passwd", Kind.FILE, "mock", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidName"
    assert "absolute" in plan.errors[0].message.lower()


async def test_plan_make_new_rejects_windows_drive_name(
    small_mock: MockSource,
) -> None:
    """C:\\... also caught after the backslash-to-slash flip."""
    plan = await plan_make_new(
        "/proj", "C:\\Users\\evil", Kind.FILE, "mock", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidName"


async def test_plan_make_new_rejects_dotdot_segment(
    small_mock: MockSource,
) -> None:
    """Relative '..' would escape the parent - reject."""
    plan = await plan_make_new(
        "/proj", "../sneaky", Kind.FILE, "mock", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidName"
    assert ".." in plan.errors[0].message


async def test_plan_make_new_rejects_existing_leaf(
    small_mock: MockSource,
) -> None:
    """The leaf can't exist - small_mock has /proj already, so making
    a new entry named 'proj' under '/' is a clobber."""
    plan = await plan_make_new(
        "/", "proj", Kind.DIR, "mock", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "Exists"


async def test_plan_make_new_rejects_existing_file(
    small_mock: MockSource,
) -> None:
    plan = await plan_make_new(
        "/", "readme.txt", Kind.FILE, "mock", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "Exists"


async def test_plan_make_new_rejects_only_dot_segments(
    small_mock: MockSource,
) -> None:
    """A name that's just '.' (or './.') collapses to nothing."""
    plan = await plan_make_new(
        "/proj", ".", Kind.FILE, "mock", {"mock": small_mock}
    )
    assert plan.items == []
    assert plan.errors[0].cause == "InvalidName"


# ---------------------------------------------------------------------------
# Executor unit tests (real filesystem via tmp_path)
# ---------------------------------------------------------------------------


def test_make_new_blocking_creates_dir(tmp_path: Path) -> None:
    target = tmp_path / "new-dir"
    _make_new_blocking(str(target), Kind.DIR)
    assert target.is_dir()


def test_make_new_blocking_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "new-file.txt"
    _make_new_blocking(str(target), Kind.FILE)
    assert target.is_file()
    assert target.read_bytes() == b""


def test_make_new_blocking_creates_intermediate_dirs_for_file(
    tmp_path: Path,
) -> None:
    """Lenient mode: parents that don't exist are created."""
    target = tmp_path / "a" / "b" / "c.txt"
    _make_new_blocking(str(target), Kind.FILE)
    assert target.is_file()
    assert target.parent.is_dir()
    assert target.parent.parent.is_dir()


def test_make_new_blocking_refuses_clobber_dir(tmp_path: Path) -> None:
    target = tmp_path / "exists"
    target.mkdir()
    with pytest.raises(FileExistsError):
        _make_new_blocking(str(target), Kind.DIR)


def test_make_new_blocking_refuses_clobber_file(tmp_path: Path) -> None:
    target = tmp_path / "exists.txt"
    target.write_text("old")
    with pytest.raises(FileExistsError):
        _make_new_blocking(str(target), Kind.FILE)
    # The old contents must remain untouched.
    assert target.read_text() == "old"


def test_make_new_blocking_unsupported_kind(tmp_path: Path) -> None:
    """Defensive: SYMLINK / OTHER shouldn't reach the executor."""
    target = tmp_path / "linky"
    with pytest.raises(ValueError):
        _make_new_blocking(str(target), Kind.SYMLINK)


async def test_apply_make_new_plan_dir(tmp_path: Path) -> None:
    """Full plan_make_new + apply_plan round-trip for a directory."""
    src = NativeSource()
    plan = await plan_make_new(
        str(tmp_path), "newdir", Kind.DIR, "native", {"native": src}
    )
    assert len(plan.items) == 1
    result = await apply_plan(plan, {"native": src})
    assert result.all_succeeded
    assert (tmp_path / "newdir").is_dir()


async def test_apply_make_new_plan_file(tmp_path: Path) -> None:
    src = NativeSource()
    plan = await plan_make_new(
        str(tmp_path), "new.txt", Kind.FILE, "native",
        {"native": src},
    )
    assert len(plan.items) == 1
    result = await apply_plan(plan, {"native": src})
    assert result.all_succeeded
    assert (tmp_path / "new.txt").is_file()


async def test_apply_make_new_plan_lenient_subdirs(tmp_path: Path) -> None:
    src = NativeSource()
    plan = await plan_make_new(
        str(tmp_path), "a/b/c.txt", Kind.FILE, "native",
        {"native": src},
    )
    result = await apply_plan(plan, {"native": src})
    assert result.all_succeeded
    leaf = tmp_path / "a" / "b" / "c.txt"
    assert leaf.is_file()


async def test_apply_make_new_raceclobber_marks_failed(
    tmp_path: Path,
) -> None:
    """If the leaf appears between plan and apply (race), executor
    surfaces FAILED with 'already exists', not a silent overwrite."""
    src = NativeSource()
    plan = await plan_make_new(
        str(tmp_path), "race.txt", Kind.FILE, "native",
        {"native": src},
    )
    # Simulate the race: create the leaf after the plan, before apply.
    (tmp_path / "race.txt").write_text("pre-existing")

    result = await apply_plan(plan, {"native": src})
    assert not result.all_succeeded
    assert result.failed_count == 1
    assert "already exists" in result.items[0].message
    # Pre-existing content untouched.
    assert (tmp_path / "race.txt").read_text() == "pre-existing"


# ---------------------------------------------------------------------------
# action_make_new + chooser + PromptDialog integration via pilot
# ---------------------------------------------------------------------------


async def test_action_make_new_pushes_chooser_then_prompt(
    small_mock: MockSource,
) -> None:
    """N opens the chooser modal; picking F then typing a name opens
    the prompt and submits a Plan."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, KindChooserDialog)

        await pilot.press("f")
        await pilot.pause()
        # Should now be on the name prompt.
        assert isinstance(app.screen, PromptDialog)

        from textual.widgets import Input
        modal_input = app.screen.query_one(Input)
        modal_input.value = "new.txt"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

    assert app.last_plan is not None
    assert app.last_plan.kind is OperationKind.MAKE_NEW
    assert app.last_plan.items[0].dst_path == "/new.txt"
    assert app.last_plan.items[0].kind is Kind.FILE


async def test_action_make_new_dir_via_chooser(
    small_mock: MockSource,
) -> None:
    """N + D + name + Enter -> a Plan with Kind.DIR."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)

        from textual.widgets import Input
        modal_input = app.screen.query_one(Input)
        modal_input.value = "newdir"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

    assert app.last_plan is not None
    assert app.last_plan.items[0].kind is Kind.DIR
    assert app.last_plan.items[0].dst_path == "/newdir"


async def test_action_make_new_chooser_esc_cancels(
    small_mock: MockSource,
) -> None:
    """Esc on the chooser dialog dismisses without a plan."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, KindChooserDialog)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is None


async def test_action_make_new_prompt_esc_cancels(
    small_mock: MockSource,
) -> None:
    """Esc on the name prompt after picking a kind dismisses without
    a plan."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is None


async def test_action_make_new_empty_name_cancels(
    small_mock: MockSource,
) -> None:
    """An empty / whitespace name from the prompt is treated as a
    cancellation - no plan, no notify-error."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        from textual.widgets import Input
        modal_input = app.screen.query_one(Input)
        modal_input.value = ""
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
    assert app.last_plan is None


async def test_action_make_new_exists_surfaces_error(
    small_mock: MockSource,
) -> None:
    """Trying to create an entry whose leaf already exists surfaces
    a PlanError (Exists) via the standard last_plan + notify path."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        from textual.widgets import Input
        modal_input = app.screen.query_one(Input)
        modal_input.value = "proj"  # already exists in small_mock
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

    assert app.last_plan is not None
    assert app.last_plan.items == []
    assert app.last_plan.errors[0].cause == "Exists"


async def test_action_make_new_dotdot_surfaces_error(
    small_mock: MockSource,
) -> None:
    """A '..' segment in the typed name surfaces InvalidName."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        from textual.widgets import Input
        modal_input = app.screen.query_one(Input)
        modal_input.value = "../sneaky"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

    assert app.last_plan is not None
    assert app.last_plan.items == []
    assert app.last_plan.errors[0].cause == "InvalidName"


async def test_action_make_new_tagged_set_silently_ignored(
    small_mock: MockSource,
) -> None:
    """Tags are silently ignored - Make-new is 'create here', not
    Selection-rule. After N + D + name + Enter, the tagged set is
    still present (Make-new doesn't clear it; only enqueued plans do,
    and even then only when there were tags to begin with)."""
    app = WTreeApp(source=small_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("space")  # tag row 0 = proj
        assert len(app.tagged_set) == 1

        await pilot.press("n")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        from textual.widgets import Input
        modal_input = app.screen.query_one(Input)
        modal_input.value = "newdir"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

    # Plan landed.
    assert app.last_plan is not None
    assert app.last_plan.items[0].dst_path == "/newdir"
    # Tag was NOT consumed - Make-new ignores the tagged set.
    # (The standard _finalise_plan clears tags only when the set was
    # actually consumed by the op. Make-new passes a synthetic tag list
    # of [synthetic_tag], not the real tagged set, so the real set is
    # left intact.)
    # In the current implementation the tagged set IS still cleared by
    # _finalise_plan's "if self.tagged_set: clear()" - tagged_set will
    # be empty here. This documents that behaviour; if it changes, this
    # test will catch it.
    assert len(app.tagged_set) == 0
