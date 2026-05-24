"""Tests for the bold-yellow tagged-node visual style in the tree pane.

Companion to the contents-pane styling tests. The tree pane uses a
different mechanism because Textual's ``Tree`` doesn't expose
cell-level styling like ``DataTable`` does — we override
``render_label`` and apply bold-yellow when the node's backing path is
in the tagged set.
"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

from wtree.app import WTreeApp
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.tree_pane import TreePane, _TAGGED_STYLE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_node(tree: TreePane, node_path: str) -> Text:
    """Return the Rich Text produced by ``render_label`` for the node at
    ``node_path``. Walks the tree from the root.
    """
    from rich.style import Style

    blank = Style()
    target = None
    if tree.root.data == node_path:
        target = tree.root
    else:
        for child in tree.root.children:
            if child.data == node_path:
                target = child
                break
    assert target is not None, f"node not found in tree: {node_path}"
    return tree.render_label(target, blank, blank)


def _is_bold_yellow(text: Text) -> bool:
    """True if any span in ``text`` is styled bold-yellow."""
    for span in text.spans:
        style_str = str(span.style).lower()
        if "yellow" in style_str and "bold" in style_str:
            return True
    return False


# ---------------------------------------------------------------------------
# Pure render_label behaviour
# ---------------------------------------------------------------------------


async def test_untagged_node_renders_plain(tmp_path: Path) -> None:
    """A node whose backing path is NOT in the tagged set renders plain."""
    (tmp_path / "sub").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        sub_path = str(tmp_path / "sub")
        rendered = _render_node(tree, sub_path)
        assert not _is_bold_yellow(rendered)


async def test_tagged_node_renders_bold_yellow(tmp_path: Path) -> None:
    """Tagging a path causes its tree node to render bold-yellow."""
    (tmp_path / "sub").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        sub_path = str(tmp_path / "sub")
        app.tagged_set.add(app._source.source_id, sub_path)
        rendered = _render_node(tree, sub_path)
        assert _is_bold_yellow(rendered)


async def test_root_node_can_be_tagged_and_renders_bold_yellow(tmp_path: Path) -> None:
    """The root node carries a backing path and can be tagged like any
    other node. Verifies the override doesn't special-case the root."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        root_path = str(tmp_path)
        app.tagged_set.add(app._source.source_id, root_path)
        rendered = _render_node(tree, root_path)
        assert _is_bold_yellow(rendered)


# ---------------------------------------------------------------------------
# Integration: app-level paths that mutate tags
# ---------------------------------------------------------------------------


async def test_contents_pane_toggle_restyles_tree(tmp_path: Path) -> None:
    """Toggling a dir's tag via the contents pane re-renders the tree."""
    (tmp_path / "sub").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")  # focus contents pane
        await pilot.pause()
        await pilot.press("space")  # tag "sub/"
        await pilot.pause()
        tree = app.query_one(TreePane)
        sub_path = str(tmp_path / "sub")
        rendered = _render_node(tree, sub_path)
        assert _is_bold_yellow(rendered)


async def test_ctrl_u_clears_tree_styling(tmp_path: Path) -> None:
    """Ctrl+U (untag all) restores plain style to previously-tagged
    tree nodes."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        sid = app._source.source_id
        a_path = str(tmp_path / "a")
        b_path = str(tmp_path / "b")
        app.tagged_set.add(sid, a_path)
        app.tagged_set.add(sid, b_path)
        tree = app.query_one(TreePane)
        assert _is_bold_yellow(_render_node(tree, a_path))
        assert _is_bold_yellow(_render_node(tree, b_path))

        await pilot.press("ctrl+u")
        await pilot.pause()

        assert not _is_bold_yellow(_render_node(tree, a_path))
        assert not _is_bold_yellow(_render_node(tree, b_path))


async def test_ctrl_a_tags_visible_dirs_and_restyles_tree(tmp_path: Path) -> None:
    """Ctrl+A tags every entry in the contents pane's current dir; the
    corresponding tree-pane rows pick up bold-yellow."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        await pilot.pause()
        tree = app.query_one(TreePane)
        assert _is_bold_yellow(_render_node(tree, str(tmp_path / "a")))
        assert _is_bold_yellow(_render_node(tree, str(tmp_path / "b")))


async def test_tree_pane_space_recursive_styles_subtree(tmp_path: Path) -> None:
    """Recursive Space on a tree node tags the whole subtree."""
    (tmp_path / "sub" / "child").mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # cursor onto "sub"
        await pilot.pause()
        await pilot.press("space")  # recursive subtree toggle
        await pilot.pause()
        await pilot.pause()
        sid = app._source.source_id
        sub_path = str(tmp_path / "sub")
        child_path = str(tmp_path / "sub" / "child")
        assert app.tagged_set.contains(sid, sub_path)
        assert app.tagged_set.contains(sid, child_path)
        tree = app.query_one(TreePane)
        assert _is_bold_yellow(_render_node(tree, sub_path))


async def test_lazy_expanded_subtree_picks_up_existing_tag(tmp_path: Path) -> None:
    """When the user expands a previously-collapsed subtree, any nodes
    matching an already-tagged path render bold-yellow on first paint —
    no extra refresh call needed.

    Motivating property for picking ``render_label`` over "rebuild each
    node's stored label on every mutation".
    """
    deep = tmp_path / "outer" / "inner"
    deep.mkdir(parents=True)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        sid = app._source.source_id
        inner_path = str(deep)
        # Tag BEFORE expanding "outer" — when the tree pane lazy-loads
        # "outer", its child "inner" should render bold-yellow on the
        # first paint without any explicit refresh.
        app.tagged_set.add(sid, inner_path)
        tree = app.query_one(TreePane)
        # Cursor onto "outer", then expand it directly. Textual 8.x's
        # ``Tree`` doesn't ship a ``right``-arrow expand binding (it has
        # ``enter`` and ``space``; ours intercepts space for tagging).
        # A keyboard ``right``-arrow expand is a separate UX follow-up.
        outer_path = str(tmp_path / "outer")
        await pilot.press("down")
        await pilot.pause()
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == outer_path
        outer_node = tree.cursor_node
        outer_node.expand()
        await tree._populate(outer_node)
        await pilot.pause()
        inner_node = None
        for child in outer_node.children:
            if child.data == inner_path:
                inner_node = child
                break
        assert inner_node is not None, "inner not populated under outer"

        from rich.style import Style

        blank = Style()
        rendered = tree.render_label(inner_node, blank, blank)
        assert _is_bold_yellow(rendered)


# ---------------------------------------------------------------------------
# Public-API smoke tests
# ---------------------------------------------------------------------------


async def test_refresh_tag_styles_is_callable(tmp_path: Path) -> None:
    """TreePane.refresh_tag_styles() takes no args and returns None."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(TreePane)
        result = tree.refresh_tag_styles()
        assert result is None


async def test_refresh_tag_visuals_helper_on_app(tmp_path: Path) -> None:
    """WTreeApp._refresh_tag_visuals() exists and doesn't raise."""
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._refresh_tag_visuals()
        await pilot.pause()


def test_tagged_style_constant_matches_contents_pane() -> None:
    """The tree-pane's ``_TAGGED_STYLE`` matches the contents-pane's
    convention — both panes paint tagged things in bold yellow.

    Kept as separate module-level constants (rather than a shared
    import) so neither widget reaches into the other, but tested for
    drift here.
    """
    from wtree.widgets.contents_pane import _TAGGED_STYLE as contents_style

    assert _TAGGED_STYLE == contents_style == "bold yellow"
