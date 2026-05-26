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

Tagged-node visual style (2026-05-23): tree-pane nodes whose backing
path is in the :class:`~wtree.tagged_set.TaggedSet` render with the same
bold-yellow style used for tagged rows in the contents pane. Implemented
via :meth:`render_label` override — Textual's documented extension hook
for per-node styling. The tagged-set lookup happens on every render, which
is cheap (set membership). The alternative — rebuild each node's stored
label on every mutation — was rejected because lazy-expanded subtrees
would silently miss the tagged style until they were re-rebuilt. With
``render_label`` the rule is simply "ask the tagged set when painting",
so nodes that pop in via lazy load inherit the correct style on first
paint.

The pane re-renders on tag mutations via :meth:`refresh_tag_styles`,
which is just ``self.refresh()`` wrapped behind a descriptive name. The
app's bulk-mutation paths (``Ctrl+A``, ``Ctrl+U``, ``+`` / ``-``,
recursive tree-pane Space) call it alongside the existing
``ContentsPane.refresh_tag_markers``. Single-row toggles flowing through
``ContentsPane.action_toggle_tag`` reach this pane via the
``TagsChanged`` message handler in the app.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable, Iterator

from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from wtree.sources.base import Entry, EntrySource, Kind, ScanError
from wtree.tagged_set import TaggedSet
from wtree.widgets.scan_screen import SCAN_CHUNK_SIZE, ScanContext


# Rich style applied to the rendered label of a tagged tree node.
# Matches the contents pane's ``_TAGGED_STYLE`` so a tagged path looks
# the same in both panes. Kept module-local rather than imported from
# ``contents_pane`` to avoid a cross-widget import — the string is the
# entire shared contract.
_TAGGED_STYLE = "bold yellow"


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

    class TagRequested(Message):
        """Posted when Space is pressed on a tree node with a backing path.

        Carries the absolute path of the node under the cursor. The
        app's handler kicks off a recursive walk of the subtree and
        toggles the whole subtree's tagged state based on whether the
        node itself is currently tagged (Matthew's pick 2026-05-22):
        if the dir entry is already tagged -> recursive untag, else
        recursive tag.

        Error-placeholder nodes (``data is None``) don't post this
        message — they're non-taggable for the same reason error rows
        are non-taggable in the contents pane.
        """

        def __init__(self, path: str) -> None:
            super().__init__()
            self.path = path

    def __init__(
        self,
        source: EntrySource,
        root_path: str,
        tagged_set: TaggedSet,
        *,
        id: str | None = None,  # noqa: A002 — Textual API uses ``id``
    ) -> None:
        root_path = os.path.abspath(root_path)
        # ``Tree`` is parameterised by the data type; the root label is the
        # absolute path so the user can see where they are at a glance.
        super().__init__(label=root_path, data=root_path, id=id)
        self._source = source
        # The pane borrows a reference to the tagged set; ownership lives
        # on ``WTreeApp`` so it outlives any pane mount/unmount cycle.
        # Used by :meth:`render_label` for the bold-yellow tagged-node
        # visual style.
        self._tagged = tagged_set
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
        """Own the four navigation keys on the tree pane: Left, Right, Space.

        The design's pane-modal arrow semantics give the tree explicit
        expand/collapse and drill-in behaviour rather than relying on
        Textual's ``Tree`` defaults (which in 8.x ship no ``left`` /
        ``right`` bindings at all). Mapping:

        * **Left on the root** posts :class:`AscendRequested` so the app
          re-roots at the parent dir — XTree "widen the logged window".
        * **Left on a non-root expanded node** collapses it. Same gesture
          twice in a row from a leaf row therefore "walks the user out"
          of the current subtree: first press collapses the parent,
          second press jumps to the grandparent.
        * **Left on a non-root collapsed node** moves the cursor to the
          parent. Equivalent to Textual's ``shift+left`` (cursor_parent),
          but rebound here so plain Left does what most file managers
          do.
        * **Right on a collapsed expandable node** expands it. Because
          ``_populate`` is awaited inline, the children land before the
          next paint — the user sees the subtree appear immediately
          rather than after a perceptible flicker.
        * **Right on an already-expanded node** descends to the first
          child (XTree drill-in). On an empty expanded dir this is a
          no-op.
        * **Right on a non-expandable node** (error placeholder, leaf)
          is a no-op — no-op intentionally rather than fall through to
          a Textual default, since Textual 8.x has none anyway.
        * **Space** on any node with a backing path posts
          :class:`TagRequested` so the app can run a recursive subtree
          toggle. Error placeholders (``data is None``) fall through.

        Each branch ``event.stop()`` + ``event.prevent_default()`` so a
        future Textual version that adds a default left/right doesn't
        double-fire.
        """
        node = self.cursor_node

        if event.key == "left":
            if node is self.root:
                event.stop()
                event.prevent_default()
                self.post_message(self.AscendRequested())
                return
            if node is not None:
                event.stop()
                event.prevent_default()
                if node.is_expanded:
                    node.collapse()
                elif node.parent is not None:
                    self.cursor_line = node.parent.line
                return

        if event.key == "right":
            if node is None:
                return
            event.stop()
            event.prevent_default()
            if not node.is_expanded:
                # Only expandable nodes have ``allow_expand=True`` (set
                # in ``_populate``); error placeholders are added as
                # leaves and silently no-op here.
                if node.allow_expand:
                    node.expand()
                    await self._populate(node)
                return
            # Already expanded: drill into the first child if there is
            # one. ``asyncio.sleep(0)`` yields once so the line indexer
            # rebuilds after any pending mutation — same trick used by
            # ``focus_child_of_root``.
            if node.children:
                await asyncio.sleep(0)
                self.cursor_line = node.children[0].line
            return

        if event.key == "space":
            if node is not None and node.data is not None:
                event.stop()
                event.prevent_default()
                self.post_message(self.TagRequested(node.data))

    async def on_tree_node_expanded(self, event: Tree.NodeExpanded[str]) -> None:
        # Lazy populate. ``_populate`` is itself idempotent, but checking
        # ``_loaded`` here avoids the no-op event handler altogether.
        if event.node.id in self._loaded:
            return
        await self._populate(event.node)

    async def _populate(
        self,
        node: TreeNode[str],
        *,
        ctx: ScanContext | None = None,
    ) -> None:
        """Scan ``node``'s backing path and add directory children + error
        leaves. Files and other non-directory kinds are excluded — the
        Contents pane is responsible for those.

        ``ctx`` is an optional :class:`ScanContext` shared with a
        :class:`ScanScreen`. When supplied, the scan loop yields to the
        event loop every :data:`SCAN_CHUNK_SIZE` entries, writes the
        running count to ``ctx.entries_seen``, and polls
        ``ctx.cancelled`` between chunks. On cancel the node is left
        marked-loaded but with no children added — equivalent to "the
        scan never happened" from the user's perspective; pressing
        Right again would re-trigger ``on_tree_node_expanded`` and the
        consumer would still find ``_loaded`` set, so we also drop the
        marker on cancel so the next expand retries cleanly.

        Without ``ctx``, the legacy "drain the iterator in one shot"
        behaviour is preserved for tests and small scans.
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
        i = 0
        async for item in self._source.scan(path):
            if isinstance(item, Entry):
                if item.kind is Kind.DIR:
                    directories.append(item)
            elif isinstance(item, ScanError):
                errors.append(item)
            i += 1
            if ctx is not None:
                ctx.entries_seen = i
                if i % SCAN_CHUNK_SIZE == 0:
                    await asyncio.sleep(0)
                    if ctx.cancelled.is_set():
                        # Drop the _loaded marker so the next expand
                        # retries cleanly. No children added; the node
                        # stays collapsed-but-expandable.
                        self._loaded.discard(node.id)
                        return

        # Final cancel check in case Esc landed during the last partial
        # chunk (entries < SCAN_CHUNK_SIZE).
        if ctx is not None and ctx.cancelled.is_set():
            self._loaded.discard(node.id)
            return

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
    # Tagged-node visual style (2026-05-23)
    # ------------------------------------------------------------------

    def render_label(
        self,
        node: TreeNode[str],
        base_style: Style,
        style: Style,
    ) -> Text:
        """Render a node's label, applying bold-yellow if its path is tagged.

        Override of :meth:`textual.widgets.Tree.render_label` — Textual's
        documented hook for per-node styling. The default implementation
        builds ``[icon][label]`` text with the expand-arrow icon styled
        independently of the label; we let it do that, then stylize
        bold-yellow over the whole thing when the node's backing path is
        in the tagged set.

        Three cases:

        * ``node.data is None`` — error placeholder leaf, non-taggable,
          render plain.
        * Path in the tagged set — stylize bold-yellow over the default
          render (preserves icon position, just changes colour).
        * Otherwise — return the default render unmodified.

        The tagged-set lookup runs once per render per node, which is a
        single Python set membership check. Negligible overhead even for
        deep trees.
        """
        text = super().render_label(node, base_style, style)
        if node.data is None:
            return text
        if self._tagged.contains(self._source.source_id, node.data):
            # ``stylize`` overlays the given style on top of any existing
            # styles in the text — the icon's TOGGLE_STYLE survives in
            # principle but the bold-yellow colour wins where they
            # overlap. Visually, the whole row reads as tagged.
            text = text.copy()
            text.stylize(_TAGGED_STYLE)
        return text

    def refresh_tag_styles(self) -> None:
        """Trigger a re-render so :meth:`render_label` re-evaluates every
        node against the current tagged-set state.

        Cheap: this is just ``self.refresh()`` behind a descriptive
        name. Called by the app after every bulk tag mutation
        (``Ctrl+A``, ``Ctrl+U``, ``+`` / ``-``, recursive subtree
        toggle) plus the single-row contents-pane toggle. Lazy
        expansion of a previously-unseen subtree does not need a
        special-case call — ``render_label`` runs against the live
        tagged set on first paint, so newly-added nodes already
        pick up the correct style.
        """
        self.refresh()

    # ------------------------------------------------------------------
    # Post-op refresh: targeted lazy-load invalidation (2026-05-23)
    # ------------------------------------------------------------------

    async def refresh_paths(self, paths: Iterable[str]) -> None:
        """Re-scan tree nodes whose backing path is in ``paths``.

        Called by the app after a Plan completes
        (:meth:`WTreeApp._refresh_panes_after_op`) with
        ``OperationResult.touched_paths`` — the directories whose
        listings changed. For each tree node whose ``data`` matches
        one of those paths:

        * If the node hasn't been loaded yet, skip it — when (and if)
          the user expands it later, ``_populate`` will scan fresh.
        * If the node *has* been loaded, drop it from ``_loaded``,
          wipe its children, and if it was expanded re-populate it
          so the user sees the new state immediately.

        Tagged-row styling self-heals via :meth:`render_label` — the
        replacement child nodes get rendered against the live tagged
        set, so a tagged dir that just moved keeps its bold-yellow
        marker without any extra wiring.

        Cursor preservation is best-effort: Textual decides where the
        cursor lands when a node's children are wiped + repopulated,
        and for v0 we accept "cursor goes wherever Textual puts it"
        rather than snapshotting line numbers. A future polish pass
        could remember the previous cursor's backing path and try to
        restore it.

        ``paths`` is an iterable rather than a set so callers don't
        need to materialise one — typically
        ``OperationResult.touched_paths`` flows in directly.
        """
        targets = set(paths)
        if not targets:
            return

        # Collect matching nodes in a single pass so the mutation below
        # doesn't interfere with iteration. ``self.root`` itself is
        # checked too — make-new at the displayed root, for instance,
        # touches the root's listing.
        matches: list[TreeNode[str]] = []
        for node in self._walk_all_nodes(self.root):
            if node.data is not None and node.data in targets:
                matches.append(node)

        for node in matches:
            # If the node was never expanded / scanned, ``_loaded``
            # doesn't track it and there are no children to wipe.
            # Leave it alone — the lazy-load on first expand will see
            # the up-to-date listing.
            if node.id not in self._loaded:
                continue
            was_expanded = node.is_expanded
            node.remove_children()
            self._loaded.discard(node.id)
            if was_expanded:
                # Re-populate inline so the new children land before
                # the next paint. ``_populate`` re-adds the node to
                # ``_loaded`` and seeds the children.
                await self._populate(node)

        # Trigger a re-render so the new tag styling, if any, takes
        # effect against the rebuilt subtree.
        self.refresh()

    def _walk_all_nodes(self, node: TreeNode[str]) -> Iterator[TreeNode[str]]:
        """Yield ``node`` then every descendant, depth-first.

        Includes the root + every tree node Textual currently holds,
        whether visible (expanded ancestors) or not. Used by
        :meth:`refresh_paths` to find nodes whose backing path matches
        a touched directory.
        """
        yield node
        for child in node.children:
            yield from self._walk_all_nodes(child)

    # ------------------------------------------------------------------
    # reveal_path: walk + expand the chain root -> target (2026-05-23)
    # ------------------------------------------------------------------

    async def reveal_path(self, target: str) -> bool:
        """Expand the chain of ancestors from the root to ``target``,
        lazy-populating each segment, then move the cursor onto the
        matching node. Returns ``True`` on success, ``False`` if any
        segment can't be resolved (target outside root, missing entry).

        Used by Ctrl+F find-across-tree to jump to arbitrary descendants
        whose tree nodes may not exist yet — segments that haven't been
        scanned get populated on the way down, so the user can land on
        a deeply nested match without manually drilling first.

        ``target`` is an absolute path. It must lie under
        ``self.root.data`` (typically the app's ``_root_path``) — if
        not, the method returns ``False`` rather than re-rooting; the
        caller should `re_root` first if cross-root jumps are needed.

        Cursor placement uses the same ``await asyncio.sleep(0)`` yield
        that ``focus_child_of_root`` does so Textual's line indexer
        rebuilds before ``cursor_line`` is read.

        Factored on top of :meth:`_walk_to_node` so the walk-down
        logic is shared with the refresh-all flow.
        """
        node = await self._walk_to_node(target)
        if node is None:
            return False
        self.cursor_line = node.line
        return True

    async def _walk_to_node(self, target: str) -> TreeNode[str] | None:
        """Walk root → target, lazy-expanding + populating each segment.

        Returns the matching :class:`TreeNode` on success, ``None`` if
        ``target`` lies outside the root or a segment can't be
        resolved. Does NOT move the cursor — callers do that if they
        want; this lets :meth:`refresh_all` re-walk paths without
        clobbering the user's cursor position.

        Internal helper shared by :meth:`reveal_path` and
        :meth:`refresh_all`. The behaviour mirrors what
        ``reveal_path`` did before the refactor — the only change is
        that the cursor-line assignment moved up to the caller.
        """
        root_path = self.root.data
        if root_path is None or not target.startswith(root_path):
            return None
        if os.path.normpath(target) == os.path.normpath(root_path):
            return self.root

        relative = os.path.relpath(target, root_path)
        parts = [p for p in relative.replace("\\", "/").split("/") if p]
        if not parts:
            return None

        current = self.root
        current_path = root_path
        for part in parts:
            if not current.is_expanded:
                current.expand()
            await self._populate(current)
            await asyncio.sleep(0)
            next_path = os.path.join(current_path, part)
            child_found = None
            for child in current.children:
                if child.data == next_path:
                    child_found = child
                    break
            if child_found is None:
                return None
            current = child_found
            current_path = next_path
        return current

    async def refresh_all(self, *, ctx: ScanContext | None = None) -> None:
        """Re-scan every loaded subtree against the live source state.

        Used by ``Ctrl+R`` when the user thinks the on-disk state has
        drifted from what's displayed. Unlike :meth:`refresh_paths`
        (targeted at a known set of paths from
        ``OperationResult.touched_paths``) this nukes the whole tree
        and rebuilds it — then walks the snapshot of previously-
        expanded paths to re-expand each one, preserving the user's
        drilled-down context across the refresh.

        Strategy:

        1. Snapshot the set of currently-expanded paths (excluding
           the root, which always stays expanded) and the cursor's
           backing path.
        2. ``re_root(current_root)`` wipes the tree and re-populates
           one level deep.
        3. For each previously-expanded path, walk down via
           ``_walk_to_node`` (lazy-expanding ancestors along the way),
           then expand that node itself.
        4. Restore the cursor by ``reveal_path``-ing to its old
           backing path.

        Paths that no longer exist on disk are silently skipped —
        ``_walk_to_node`` returns ``None`` for missing segments and
        the loop falls through. The user gets a smaller tree without
        an error toast; the "what changed" story is the on-disk
        state, not a diff.

        Sorted shallowest-first so a child's expand happens after
        its parent's expand has populated the intermediate nodes.
        """
        root_path = self.root.data
        if root_path is None:
            return

        # Snapshot expansion state.
        expanded_paths: list[str] = []
        for node in self._walk_all_nodes(self.root):
            if (
                node is not self.root
                and node.is_expanded
                and node.data is not None
            ):
                expanded_paths.append(node.data)
        # Sort shallowest-first by path-separator count so /a is
        # processed before /a/b.
        expanded_paths.sort(key=lambda p: p.count(os.sep))

        # Snapshot cursor's backing path.
        cursor_node = self.cursor_node
        cursor_path: str | None = (
            cursor_node.data if cursor_node is not None else None
        )

        # Nuke and rebuild from the root. Thread the scan context so
        # the user can cancel a slow refresh and so entries_seen
        # accumulates across every level we re-populate.
        await self.re_root(root_path, ctx=ctx)
        if ctx is not None and ctx.cancelled.is_set():
            return

        # Re-expand each previously-expanded path. ``_walk_to_node``
        # walks down + expands ancestors; we then expand the leaf
        # itself.
        for path in expanded_paths:
            if ctx is not None and ctx.cancelled.is_set():
                return
            node = await self._walk_to_node(path)
            if node is None:
                continue
            if not node.is_expanded and node.allow_expand:
                node.expand()
                await self._populate(node, ctx=ctx)

        # Restore cursor. ``reveal_path`` handles "target == root" by
        # landing on the root, and returns False silently if the
        # cursor's old path no longer exists — in which case the
        # cursor stays on whatever ``re_root`` put it on.
        if cursor_path is not None and cursor_path != root_path:
            await self.reveal_path(cursor_path)

        self.refresh()

    # ------------------------------------------------------------------
    # Re-root API used by Left-on-root ascend
    # ------------------------------------------------------------------

    async def re_root(
        self,
        new_root_path: str,
        *,
        ctx: ScanContext | None = None,
    ) -> None:
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

        ``ctx`` (optional :class:`ScanContext`) is threaded down to
        :meth:`_populate` so a slow scan of the new root's children
        surfaces the scan dialog and can be cancelled with Esc.
        """
        new_root_path = os.path.abspath(new_root_path)
        self.root.remove_children()
        self.root.set_label(new_root_path)
        self.root.data = new_root_path
        self._loaded.clear()
        await self._populate(self.root, ctx=ctx)
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

        Yields once via ``asyncio.sleep(0)`` after the expand+populate
        so Textual's internal line indexer catches up before we read
        ``child.line``. Without this yield, freshly-added children
        report ``line == -1``, and assigning ``cursor_line = -1``
        deselects rather than moves — the user sees the cursor "jump
        back to the logged folder" on the second Right-arrow press
        from the contents pane (first press worked because the logged
        root was auto-expanded+populated at mount). Same trick used
        by :meth:`focus_child_of_root`.
        """
        node = self.cursor_node
        if node is None or node.data is None:
            return False
        # Expand first so populated children are actually visible; the
        # NodeExpanded handler is a no-op once ``_loaded`` is set.
        if not node.is_expanded:
            node.expand()
        await self._populate(node)
        # CRITICAL: yield to let the line indexer rebuild after the
        # expand. See docstring for the bug this prevents.
        await asyncio.sleep(0)
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
