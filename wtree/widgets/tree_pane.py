"""``TreePane`` — directory hierarchy backed by an ``EntrySource``.

Per ``design.md`` § User interface, this pane shows only directories. Files,
symlinks, and other entry kinds appear in the Contents pane. Population is
lazy per-node: a child directory's own children are only scanned when the
user expands its node.

Errors-as-data (``design.md`` § Errors as data) bubble through to the UI as
non-expandable error leaves rather than exceptions, so a damaged subtree is
navigable past instead of fatal.

Navigation: the tree's cursor is the *authoritative* selection in the app.
Textual's ``Tree`` already provides ↑/↓ (cursor) and ←/→ (collapse/expand).
We additionally bind Backspace = move cursor to parent node, per
``design.md`` § Keymap. ``focus_dir_under_cursor`` is exposed so the
Contents pane can drive the cursor when the user presses → / Enter on a
directory row.

Left-on-root (2026-05-22 decision): when the cursor is on the root node
and there's nowhere to collapse to, Left posts an :class:`AscendRequested`
message. The app handles it by re-rooting the tree at the parent path —
widening the "logged disk" window upward, XTree-style. Left on any
non-root node keeps Textual's default collapse-or-cursor-to-parent
behaviour by letting the event bubble.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

from textual import events
from textual.message import Message
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from wtree.sources.base import Entry, EntrySource, Kind, ScanError


class TreePane(Tree[str]):
    """Directory tree. ``node.data`` is the absolute path string, or ``None``
    for error placeholder leaves.
    """

    BORDER_TITLE = "Tree"

    # Pane-local bindings layered on top of Textual's ``Tree`` defaults
    # (which already cover up/down/left/right/enter/space). Backspace
    # "move cursor to parent node" is design-defined; Textual's Tree
    # doesn't ship that behaviour by default.
    BINDINGS = [
        ("backspace", "focus_parent", "Parent"),
    ]

    class AscendRequested(Message):
        """Posted when Left is pressed on the root node.

        The app's handler re-roots the tree at the parent path (if one
        exists) — i.e. "log the directory above". Carries no data; the
        app already owns the current root and knows how to compute the
        new one.
        """

    def __init__(
        self,
        source: EntrySource,
        root_path: str,
        *,
        id: str | None = None,  # noqa: A002 — Textual API uses ``id``
    ) -> None:
        root_path = os.path.abspath(root_path)
        # ``Tree`` is parameterised by the data type; the root label is the
        # absolute path so the user can see where they are at a glance.
        super().__init__(label=root_path, data=root_path, id=id)
        self._source = source
        # Node IDs we've already scanned. Cheap idempotency for re-expand /
        # re-collapse cycles.
        self._loaded: set[int] = set()
        self.guide_depth = 2
        self.show_root = True

    async def on_mount(self) -> None:
        # Populate the root immediately and expand it so the user sees
        # something the moment the app draws. The NodeExpanded event that
        # ``expand()`` triggers below is a no-op because ``_loaded`` already
        # records the root.
        await self._populate(self.root)
        self.root.expand()

    async def on_key(self, event: events.Key) -> None:
        """Intercept Left-on-root only; let every other key fall through.

        Textual's ``Tree`` default Left handler does the right thing on
        deeper nodes (collapse if expanded, else cursor-to-parent). The
        root node has no parent and is meaningless to collapse — the
        Left keystroke is a free affordance to overload for "log the
        directory above". We consume the event only in that specific
        case so the default Left behaviour on every other row remains
        unchanged.
        """
        if event.key == "left" and self.cursor_node is self.root:
            event.stop()
            event.prevent_default()
            self.post_message(self.AscendRequested())

    async def on_tree_node_expanded(self, event: Tree.NodeExpanded[str]) -> None:
        # Lazy populate. ``_populate`` is itself idempotent, but checking
        # ``_loaded`` here avoids the no-op event handler altogether.
        if event.node.id in self._loaded:
            return
        await self._populate(event.node)

    async def _populate(self, node: TreeNode[str]) -> None:
        """Scan ``node``'s backing path and add directory children + error
        leaves. Files and other non-directory kinds are excluded — the
        Contents pane is responsible for those.
        """
        if node.id in self._loaded:
            return
        # Mark loaded *before* scanning so re-entry during the async scan is a
        # no-op. Worst case on a scan failure: the node is "loaded" with no
        # children, exactly as a real empty directory would look.
        self._loaded.add(node.id)

        path = node.data
        if path is None:
            # Error placeholder leaves carry no path; nothing to expand into.
            return

        directories: list[Entry] = []
        errors: list[ScanError] = []
        async for item in self._source.scan(path):
            if isinstance(item, Entry):
                if item.kind is Kind.DIR:
                    directories.append(item)
            elif isinstance(item, ScanError):
                errors.append(item)

        # Case-insensitive alpha sort, like XTree and most file managers.
        directories.sort(key=lambda e: e.name.lower())

        # Errors first so the user notices them before scrolling through a
        # long directory; explicit "⚠ " prefix marks them visually until we
        # have proper styling.
        for err in errors:
            node.add_leaf(f"⚠ {err.message}", data=None)
        for entry in directories:
            child_path = os.path.join(path, entry.name)
            node.add(entry.name, data=child_path, allow_expand=True)

    # ------------------------------------------------------------------
    # Re-root API used by Left-on-root ascend
    # ------------------------------------------------------------------

    async def re_root(self, new_root_path: str) -> None:
        """Re-root the tree in place at ``new_root_path``.

        Wipes the existing node subtree under the root, resets the root
        node's label and data to the new absolute path, clears the
        ``_loaded`` memo (every node ID we tracked is now gone), then
        re-populates and re-expands. Cheap-v0 strategy: the old expansion
        state under the previous root is *not* grafted into the new tree.
        A pricier variant that preserves user-expanded subtrees is parked
        in todo.md.

        The cursor lands wherever Textual's ``Tree`` puts it after the
        rebuild (typically the root row). The caller is responsible for
        moving the cursor onto the row representing the old root via
        :meth:`focus_child_of_root` if that's the desired UX.
        """
        new_root_path = os.path.abspath(new_root_path)
        self.root.remove_children()
        self.root.set_label(new_root_path)
        self.root.data = new_root_path
        self._loaded.clear()
        await self._populate(self.root)
        if not self.root.is_expanded:
            self.root.expand()

    async def focus_child_of_root(self, child_path: str) -> bool:
        """Move the cursor onto the root's child whose ``data`` matches
        ``child_path``. Returns ``True`` on success, ``False`` otherwise.

        Used after :meth:`re_root` to land the cursor on the old root's
        row so the user can immediately Right-arrow back into it.

        Yields once via ``asyncio.sleep(0)`` after the expand so
        Textual's internal line indexer (which builds lazily on next
        render) catches up - without the yield, ``child.line`` returns
        ``-1`` for nodes that were just added, and assigning
        ``cursor_line = -1`` deselects rather than moves.
        """
        if not self.root.is_expanded:
            self.root.expand()
        await self._populate(self.root)
        await asyncio.sleep(0)
        for child in self.root.children:
            if child.data == child_path:
                self.cursor_line = child.line
                return True
        return False

    # ------------------------------------------------------------------
    # Navigation API used by the contents pane
    # ------------------------------------------------------------------

    def action_focus_parent(self) -> None:
        """Move the cursor to the parent of the current node.

        No-op at the tree root or on a detached cursor. ``cursor_line`` is
        a Textual reactive — assigning it fires ``NodeHighlighted``, which
        keeps the contents pane in sync without a special-case path here.
        """
        node = self.cursor_node
        if node is None or node.parent is None:
            return
        self.cursor_line = node.parent.line

    async def focus_dir_under_cursor(self, child_path: str) -> bool:
        """Move the cursor onto the child directory at ``child_path``.

        ``child_path`` must be a direct child of the currently-selected
        node. The node is expanded (and populated, if not already) before
        the cursor move so the child is actually visible. Returns ``True``
        on success, ``False`` if no matching child was found (e.g., the
        user pressed → on a file row that has no tree representation).

        Awaiting ``_populate`` directly — instead of relying on the
        ``NodeExpanded`` event handler — keeps this call deterministic for
        the contents pane's synchronous-feeling "drill in" gesture.
        """
        node = self.cursor_node
        if node is None or node.data is None:
            return False
        # Expand first so populated children are actually visible; the
        # NodeExpanded handler is a no-op once ``_loaded`` is set.
        if not node.is_expanded:
            node.expand()
        await self._populate(node)
        for child in node.children:
            if child.data == child_path:
                self.cursor_line = child.line
                return True
        return False

    # ------------------------------------------------------------------
    # SearchTarget protocol (used by incremental search ``/``)
    # ------------------------------------------------------------------
    #
    # Same shape as ContentsPane's three SearchTarget methods. v0
    # tree-pane search scope is "visible nodes only" - collapsed
    # subtrees are NOT walked into. Auto-expand-to-find is parked on
    # the follow-ups list. ``_walk_visible`` produces ``(line_index,
    # label)`` pairs where ``line_index`` aligns with Textual's
    # ``cursor_line`` numbering so ``set_search_cursor`` can drop the
    # cursor there without any translation step.

    def iter_searchable(self) -> Iterator[tuple[int, str]]:
        """Yield ``(line_index, label)`` for every visible tree row.

        Visible = the root + every descendant whose ancestors are all
        expanded. Collapsed subtrees are skipped (their children aren't
        on screen so the cursor can't land on them). The root row is
        included so the user can find the root by typing part of its
        path - sometimes useful after an ascend.
        """
        yield from self._walk_visible(self.root, 0, [0])

    def _walk_visible(
        self,
        node: TreeNode[str],
        depth: int,
        line: list[int],
    ) -> Iterator[tuple[int, str]]:
        """Depth-first walk of visible nodes.

        ``line`` is a single-element list used as a mutable counter so
        nested generators share the running line index without
        ``nonlocal``-in-recursive-generator gymnastics. Each yielded
        ``(line_index, label)`` pair has ``line_index`` matching
        Textual's ``cursor_line`` numbering (root is 0).
        """
        # Skip the root if the widget is configured to hide it - in
        # that case Textual numbers the first visible child as line 0.
        if node is self.root and not self.show_root:
            pass
        else:
            label = str(node.label)
            yield line[0], label
            line[0] += 1
        if node.is_expanded:
            for child in node.children:
                yield from self._walk_visible(child, depth + 1, line)

    def set_search_cursor(self, line: int) -> None:
        """Move the tree cursor to the given visible-line index.

        Out-of-range values are clamped to a valid line if possible,
        else ignored. The Textual ``cursor_line`` reactive will fire
        ``NodeHighlighted`` which the app's existing handler uses to
        refresh the contents pane.
        """
        self.cursor_line = line

    def get_search_cursor(self) -> int:
        """Current visible-line index of the cursor."""
        return self.cursor_line
