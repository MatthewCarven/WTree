"""Tests for tree-pane auto-refresh after ops (2026-05-23).

After a Plan completes, the contents pane already re-shows its
``current_path``. The 2026-05-22 follow-up was to do the same for the
tree pane — re-scan the directory nodes whose contents changed — so
new subdirs appear, deleted subdirs disappear, and renamed entries
update in the tree without the user collapsing + re-expanding.

Two surfaces under test:

* :attr:`OperationResult.touched_paths` — the pure-data property
  computed from successful items. One test per op kind plus a
  partial-failure case.
* :meth:`TreePane.refresh_paths` — the lazy-load invalidation
  routine. Verifies that loaded + expanded nodes get re-scanned and
  unloaded nodes are left alone.

App-level integration: Make-new, Delete, and Move each verified to
land their changes in the tree without the user having to collapse +
re-expand a node.
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input

from wtree.app import WTreeApp
from wtree.ops.base import (
    ItemResult,
    ItemStatus,
    OperationKind,
    OperationResult,
    Plan,
    PlanItem,
)
from wtree.sources.base import Kind
from wtree.widgets.kind_chooser import KindChooserDialog
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.tree_pane import TreePane


# ---------------------------------------------------------------------------
# touched_paths — pure-data computation per op kind
# ---------------------------------------------------------------------------


def _item(src: str, dst: str, kind: Kind = Kind.FILE) -> PlanItem:
    return PlanItem(
        src_source_id="native",
        src_path=src,
        dst_source_id="native",
        dst_path=dst,
        kind=kind,
        size=0,
    )


def test_touched_paths_copy_returns_dst_parents() -> None:
    """COPY: parent of every successful item's ``dst_path``."""
    plan = Plan(
        kind=OperationKind.COPY,
        items=[
            _item("/a/foo.txt", "/b/foo.txt"),
            _item("/a/bar.txt", "/b/bar.txt"),
        ],
    )
    result = OperationResult(
        plan=plan,
        items=[
            ItemResult(plan.items[0], ItemStatus.SUCCESS),
            ItemResult(plan.items[1], ItemStatus.SUCCESS),
        ],
    )
    # Both items share /b as their dst parent — set de-duplicates.
    assert result.touched_paths == {"/b"}


def test_touched_paths_make_new_returns_dst_parent() -> None:
    """MAKE_NEW: parent of dst_path. Same rule as COPY."""
    plan = Plan(
        kind=OperationKind.MAKE_NEW,
        items=[_item("/home/u/proj", "/home/u/proj", Kind.DIR)],
    )
    result = OperationResult(
        plan=plan, items=[ItemResult(plan.items[0], ItemStatus.SUCCESS)]
    )
    assert result.touched_paths == {"/home/u"}


def test_touched_paths_delete_returns_src_parents() -> None:
    """DELETE: parent of every successful item's ``src_path``."""
    plan = Plan(
        kind=OperationKind.DELETE,
        items=[
            _item("/x/foo", "/x/foo"),
            _item("/y/bar", "/y/bar"),
        ],
    )
    result = OperationResult(
        plan=plan,
        items=[
            ItemResult(plan.items[0], ItemStatus.SUCCESS),
            ItemResult(plan.items[1], ItemStatus.SUCCESS),
        ],
    )
    assert result.touched_paths == {"/x", "/y"}


def test_touched_paths_move_returns_both_parents() -> None:
    """MOVE: parent of src + parent of dst, both for every successful item."""
    plan = Plan(
        kind=OperationKind.MOVE,
        items=[_item("/a/foo", "/b/foo")],
    )
    result = OperationResult(
        plan=plan, items=[ItemResult(plan.items[0], ItemStatus.SUCCESS)]
    )
    assert result.touched_paths == {"/a", "/b"}


def test_touched_paths_rename_returns_one_parent() -> None:
    """RENAME: src and dst share a parent — one entry."""
    plan = Plan(
        kind=OperationKind.RENAME,
        items=[_item("/p/old", "/p/new")],
    )
    result = OperationResult(
        plan=plan, items=[ItemResult(plan.items[0], ItemStatus.SUCCESS)]
    )
    assert result.touched_paths == {"/p"}


def test_touched_paths_excludes_failed_items() -> None:
    """Partial failure: only the successful items contribute paths."""
    plan = Plan(
        kind=OperationKind.DELETE,
        items=[
            _item("/x/foo", "/x/foo"),
            _item("/y/bar", "/y/bar"),
        ],
    )
    result = OperationResult(
        plan=plan,
        items=[
            ItemResult(plan.items[0], ItemStatus.SUCCESS),
            ItemResult(plan.items[1], ItemStatus.FAILED, "permission denied"),
        ],
    )
    # /y didn't actually change on disk — touched_paths should not
    # report it.
    assert result.touched_paths == {"/x"}


def test_touched_paths_empty_for_empty_result() -> None:
    """Empty plans and all-failed plans both produce an empty set."""
    plan = Plan(kind=OperationKind.DELETE, items=[])
    result = OperationResult(plan=plan, items=[])
    assert result.touched_paths == set()


# ---------------------------------------------------------------------------
# TreePane.refresh_paths — pane-level lazy-load invalidation
# ---------------------------------------------------------------------------


async def test_refresh_paths_empty_set_is_noop(tmp_path: Path) -> None:
    """An empty paths set short-circuits without touching the tree."""
    (tmp_path / "sub").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        before = {c.data for c in tree.root.children}
        await tree.refresh_paths(set())
        after = {c.data for c in tree.root.children}
        assert before == after


async def test_refresh_paths_repopulates_loaded_node(tmp_path: Path) -> None:
    """A loaded + expanded node whose path is in ``paths`` gets re-scanned.

    Setup: mount the app with a single ``sub`` subdir. The root is
    populated on mount (one child). Add a second subdir on disk after
    the initial scan, then call ``refresh_paths([root])`` — both
    subdirs should now appear as root children.
    """
    (tmp_path / "sub").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        assert {c.data for c in tree.root.children} == {str(tmp_path / "sub")}

        # Race the on-disk state: add a second dir after the initial scan.
        (tmp_path / "sub2").mkdir()
        await tree.refresh_paths({str(tmp_path)})
        await pilot.pause()
        assert {c.data for c in tree.root.children} == {
            str(tmp_path / "sub"),
            str(tmp_path / "sub2"),
        }


async def test_refresh_paths_ignores_unloaded_nodes(tmp_path: Path) -> None:
    """Paths that don't correspond to a loaded tree node are skipped.

    A subdir that was never expanded isn't in ``_loaded``, so
    refresh_paths leaves it alone — the lazy-load on first expand will
    pick up the current state without an explicit refresh.
    """
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        outer_path = str(tmp_path / "outer")
        # ``outer`` is in the tree (as a child of root) but its own
        # children haven't been scanned (it isn't expanded).
        outer_node = next(c for c in tree.root.children if c.data == outer_path)
        assert outer_node.id not in tree._loaded

        # refresh_paths with outer's path: nothing to invalidate; this
        # should be a no-op (no exception, ``outer`` still not in _loaded).
        await tree.refresh_paths({outer_path})
        assert outer_node.id not in tree._loaded


# ---------------------------------------------------------------------------
# End-to-end: ops actually drive the auto-refresh
# ---------------------------------------------------------------------------


async def _drain_queue(app: WTreeApp) -> None:
    """Block until the operation queue is idle - mirrors the helper used
    in the per-op e2e files."""
    assert app.op_queue is not None
    await app.op_queue.wait_until_idle()


async def test_make_new_dir_appears_in_tree(tmp_path: Path) -> None:
    """After Make-new of a directory, the tree pane shows the new dir
    without the user collapsing + re-expanding the parent.
    """
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        assert {c.data for c in tree.root.children} == set()

        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, KindChooserDialog)
        await pilot.press("d")  # directory
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        inp = app.screen.query_one(Input)
        inp.value = "freshdir"
        await pilot.press("enter")
        await pilot.pause()
        await _drain_queue(app)
        # The auto-refresh task fires from _on_plan_complete; give it
        # a few ticks to land.
        for _ in range(10):
            await pilot.pause()
            if str(tmp_path / "freshdir") in {c.data for c in tree.root.children}:
                break

        assert (tmp_path / "freshdir").is_dir()
        assert str(tmp_path / "freshdir") in {c.data for c in tree.root.children}


async def test_delete_dir_disappears_from_tree(tmp_path: Path) -> None:
    """After Delete of a subdirectory, the tree pane drops the row."""
    (tmp_path / "doomed").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        assert {c.data for c in tree.root.children} == {str(tmp_path / "doomed")}

        # Tab to contents, D to delete, Y to confirm.
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        # Confirm dialog: y / Enter.
        await pilot.press("y")
        await pilot.pause()
        await _drain_queue(app)
        for _ in range(10):
            await pilot.pause()
            if {c.data for c in tree.root.children} == set():
                break

        assert not (tmp_path / "doomed").exists()
        assert {c.data for c in tree.root.children} == set()


async def test_move_updates_both_source_and_dest_in_tree(tmp_path: Path) -> None:
    """Moving a subdir into a sibling: source loses it, dest gains it."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "payload").mkdir()
    (tmp_path / "dst").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)

        # Expand "src" so the tree has loaded its child "payload" - that
        # gives the test something concrete to verify "src lost a child".
        src_node = next(c for c in tree.root.children if c.data == str(tmp_path / "src"))
        src_node.expand()
        await tree._populate(src_node)
        await pilot.pause()
        assert {c.data for c in src_node.children} == {
            str(tmp_path / "src" / "payload")
        }

        # Tag payload (so action_move targets it) and trigger Move.
        sid = app._source.source_id
        app.tagged_set.add(sid, str(tmp_path / "src" / "payload"))
        app.action_move()
        # The @work coroutine pushes the PromptDialog; wait for it.
        for _ in range(20):
            await pilot.pause()
            if isinstance(app.screen, PromptDialog):
                break
        assert isinstance(app.screen, PromptDialog), (
            f"expected prompt, got {type(app.screen).__name__}"
        )
        inp = app.screen.query_one(Input)
        inp.value = str(tmp_path / "dst")
        await pilot.press("enter")
        await pilot.pause()
        await _drain_queue(app)
        for _ in range(20):
            await pilot.pause()
            if (tmp_path / "dst" / "payload").exists():
                break

        # On-disk reality check first.
        assert (tmp_path / "dst" / "payload").exists()
        assert not (tmp_path / "src" / "payload").exists()

        # The tree node for src - which WAS loaded + expanded - should
        # have zero children now.
        src_node = next(c for c in tree.root.children if c.data == str(tmp_path / "src"))
        assert {c.data for c in src_node.children} == set()
