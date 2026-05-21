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
"""

from __future__ import annotations

import os

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
