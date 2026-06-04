"""Textual application - two-pane explorer view with a session-wide tagged set.

Left pane (``TreePane``) is the directory hierarchy under ``root_path``.
Right pane (``ContentsPane``) is the table of entries for whichever
directory the tree cursor is sitting on. The panes are coupled - when
the tree's cursor moves, the contents pane refreshes. See ``design.md``
Layout section.

Bottom of the screen (per ``design.md``): an MC-style F-key cheat-sheet
bar (:class:`~wtree.widgets.keybar.KeyBar`) above a one-line transient
status display (:class:`~wtree.widgets.status_line.StatusLine`).
Top of the screen: :class:`~wtree.widgets.menu_bar.MenuBar` (always
visible, MC-style chrome row). F9 pushes
:class:`~wtree.widgets.menu_screen.MenuScreen`, which owns the
interactive menu navigation while it's open. While search (``/``) is
active the SearchBar replaces the StatusLine row.

User-immediate feedback ("X rejected", "X cancelled", "Logged: ...")
goes through :meth:`flash`, which routes to
:meth:`StatusLine.flash <wtree.widgets.status_line.StatusLine.flash>`.
Queue-completion notifications stay as toast notifies via
:meth:`notify` because they may fire async when the user has looked
away. The split is "did the user just press a key and want a reply" =
flash, "did something complete on its own time" = toast.

Pane auto-refresh (2026-05-22): ``_on_plan_complete`` schedules
``asyncio.create_task(self._refresh_panes_after_op())`` to re-show
the contents pane's current path so on-disk changes are visible
without the user pressing anything. Tree-pane auto-refresh is parked.

Menu bar (2026-05-22, F9): the MC-style menu bar at the top is
always visible. Pressing F9 pushes :class:`MenuScreen`; the user
navigates with arrows + letter accelerators + Enter, then the modal
dismisses with an action name which the app dispatches via
``getattr(self, f"action_{name}")()``. Menu items map 1:1 to the
keyboard shortcuts the user could've pressed directly - the menu is
discoverability chrome, not a parallel control path.
"""

from __future__ import annotations

import asyncio
import os
import posixpath
from collections.abc import Awaitable, Callable, Mapping, Sequence

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Header, Tree

from wtree import __version__
from wtree.editor import launch_editor_blocking, resolve_editor
from wtree.ops import (
    ConflictKind,
    ItemResult,
    OperationKind,
    OperationQueue,
    OperationResult,
    Plan,
    Resolution,
    plan_copy,
    plan_delete,
    plan_make_new,
    plan_move,
    plan_rename,
    preview_renamed_dst,
    resolve_conflicts,
    select_range_for_rename,
)
from wtree.ops.queue import (
    PROGRESS_MODAL_BYTES,
    PROGRESS_MODAL_DELAY_SECONDS,
    PROGRESS_MODAL_ITEMS,
)
from wtree.sources.base import EntrySource, Kind
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag, TaggedSet
from wtree.widgets.confirm import ConfirmDialog
from wtree.widgets.conflict import ConflictDialog
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.help import HelpScreen
from wtree.widgets.keybar import KeyBar
from wtree.widgets.kind_chooser import KindChooserDialog
from wtree.widgets.menu_bar import MenuBar
from wtree.widgets.menu_screen import MenuScreen
from wtree.widgets.progress_screen import ProgressScreen
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.scan_screen import (
    SCAN_CHUNK_SIZE,
    SCAN_MODAL_DELAY_SECONDS,
    ScanContext,
    ScanScreen,
)
from wtree.widgets.properties import (
    DirProps,
    FileProps,
    PropertiesScreen,
    TaggedProps,
)
from wtree.widgets.search_bar import SearchBar
from wtree.widgets.status_line import StatusLine
from wtree.widgets.tree_pane import TreePane
from wtree.widgets.viewer import ViewerScreen


# Planner signature for ops with a destination: (tags, dest, registry) -> Plan
DestPlanner = Callable[
    [Sequence[Tag], Tag, Mapping[str, EntrySource]],
    Awaitable[Plan],
]
# Planner signature for ops without a destination: (tags, registry) -> Plan
NoDestPlanner = Callable[
    [Sequence[Tag], Mapping[str, EntrySource]],
    Awaitable[Plan],
]


class WTreeApp(App):
    """The top-level WTree application."""

    CSS = """
    Screen {
        layout: vertical;
    }

    Horizontal {
        height: 1fr;
    }

    TreePane, ContentsPane {
        width: 1fr;
        height: 1fr;
        border: round $primary;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f10", "quit", "Quit"),
        ("f1", "help", "Help"),
        ("question_mark", "help", "Help"),
        ("tab", "cycle_focus", "Switch pane"),
        ("ctrl+a", "tag_all", "Tag all in dir"),
        ("ctrl+u", "untag_all", "Untag all"),
        ("plus", "tag_pattern", "Tag by glob"),
        ("minus", "untag_pattern", "Untag by glob"),
        ("c", "copy", "Copy"),
        ("f5", "copy", "Copy"),
        ("m", "move", "Move"),
        ("f6", "move", "Move"),
        ("d", "delete", "Delete"),
        ("delete", "delete", "Delete"),
        ("f8", "delete", "Delete"),
        ("r", "rename", "Rename"),
        ("f2", "rename", "Rename"),
        ("v", "view", "View"),
        ("f3", "view", "View"),
        ("e", "edit", "Edit"),
        ("f4", "edit", "Edit"),
        ("n", "make_new", "New"),
        ("f7", "make_new", "New"),
        ("slash", "search", "Search"),
        ("ctrl+f", "find_tree", "Find tree"),
        ("ctrl+g", "next_match", "Next match"),
        ("l", "log_new_source", "Log new source"),
        ("ctrl+r", "refresh_source", "Refresh source"),
        ("ctrl+i", "properties", "Properties"),
        ("ctrl+p", "show_progress", "Show progress"),
        ("f9", "menu_bar", "Menu"),
    ]

    TITLE = "WTree"

    def __init__(
        self,
        source: EntrySource | None = None,
        root_path: str | None = None,
    ) -> None:
        super().__init__()
        self._source = source if source is not None else NativeSource()
        self._root_path = (
            os.path.abspath(root_path) if root_path is not None else os.getcwd()
        )
        self.tagged_set = TaggedSet()
        self.sources: dict[str, EntrySource] = {
            self._source.source_id: self._source
        }
        self.last_plan: Plan | None = None
        self.last_result: OperationResult | None = None
        self.op_queue: OperationQueue | None = None

        # Incremental search state. See action_search + on_search_bar_*.
        self._search_target: ContentsPane | TreePane | None = None
        self._search_cursor_pre: int | None = None
        self._search_matches: list[int] = []
        self._search_match_idx: int = 0

        # Find-across-tree state (Ctrl+F + Ctrl+G). Distinct from the
        # ``/`` incremental search: this one walks the *entire* source
        # tree under ``_root_path`` (not just visible nodes), caches the
        # matches, and lets Ctrl+G step through them.
        self._tree_find_query: str | None = None
        self._tree_find_matches: list[str] = []
        self._tree_find_idx: int = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield MenuBar()
        with Horizontal():
            yield TreePane(
                self._source,
                self._root_path,
                self.tagged_set,
                id="tree-pane",
            )
            yield ContentsPane(
                self._source, self.tagged_set, id="contents-pane"
            )
        yield StatusLine()
        yield SearchBar(id="search-bar")
        yield KeyBar()

    async def on_mount(self) -> None:
        self.op_queue = OperationQueue(
            registry=self.sources,
            on_plan_start=self._on_plan_start,
            on_plan_complete=self._on_plan_complete,
            on_item_progress=self._on_item_progress,
        )
        self.op_queue.start()
        self._update_subtitle()
        contents = self.query_one(ContentsPane)
        # First scan of the initial root. Wrapped so a huge directory
        # at app-launch time gets the scan dialog instead of freezing
        # the splash. Fast roots (the common case) never see a flash
        # because the delayed-show timer cancels itself.
        await self._run_scan_with_dialog(
            self._root_path,
            self._source,
            lambda ctx: contents.show_path(self._root_path, ctx=ctx),
        )
        self.query_one(TreePane).focus()
        self._refresh_status()

    async def on_unmount(self) -> None:
        if self.op_queue is not None:
            await self.op_queue.stop()

    async def on_tree_node_highlighted(
        self, event: Tree.NodeHighlighted[str]
    ) -> None:
        contents = self.query_one(ContentsPane)
        path = event.node.data
        if path is None:
            # Error-placeholder leaf - clear the pane without invoking
            # the scan-dialog gate (there's no scan to gate).
            await contents.show_path(None)
        else:
            # Wrap so cursor movement onto a (potentially huge) dir
            # surfaces the scan dialog after the threshold instead of
            # freezing the UI. The common case (small dirs) never sees
            # a flash because the delayed-show timer cancels itself.
            await self._run_scan_with_dialog(
                path,
                self._source,
                lambda ctx: contents.show_path(path, ctx=ctx),
            )
        self._refresh_status()

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        self._refresh_status()

    def on_contents_pane_tags_changed(
        self, event: ContentsPane.TagsChanged
    ) -> None:
        # The contents pane restyles its own row in ``action_toggle_tag``;
        # we still need to refresh the tree pane so a tagged dir's row
        # picks up (or drops) the bold-yellow style. Cheap full-pane
        # re-render via ``Tree.refresh()``.
        try:
            self.query_one(TreePane).refresh_tag_styles()
        except Exception:  # noqa: BLE001 - early-mount safety
            pass
        self._update_subtitle()
        self._refresh_status()

    def _refresh_tag_visuals(self) -> None:
        """Restyle both panes after a bulk tagged-set mutation.

        Single source of truth for the "tags changed; repaint" path so
        ``Ctrl+A``, ``Ctrl+U``, ``+`` / ``-``, the recursive tree-pane
        Space gesture, and the after-op tagged-set clear all share one
        callsite. Each pane's refresh method is internally cheap (the
        contents pane re-styles in place; the tree pane just calls
        ``self.refresh()``), so the cost is dominated by Textual's
        rendering loop, which would happen anyway.
        """
        try:
            self.query_one(ContentsPane).refresh_tag_markers()
        except Exception:  # noqa: BLE001 - early-mount safety
            pass
        try:
            self.query_one(TreePane).refresh_tag_styles()
        except Exception:  # noqa: BLE001 - early-mount safety
            pass

    def action_cycle_focus(self) -> None:
        """Tab - swap focus between TreePane and ContentsPane."""
        tree = self.query_one(TreePane)
        contents = self.query_one(ContentsPane)
        if self.focused is tree:
            contents.focus()
        else:
            tree.focus()
        self._refresh_status()

    def action_untag_all(self) -> None:
        """Ctrl+U - clear the tagged set, refresh markers in both panes."""
        if not self.tagged_set:
            return
        self.tagged_set.clear()
        self._refresh_tag_visuals()
        self._update_subtitle()
        self._refresh_status()

    def action_tag_all(self) -> None:
        """Ctrl+A - tag every taggable entry in the contents pane's current dir.

        Scope is the contents pane (not the tree pane) because that's
        where the "current dir" lives - ContentsPane.current_path is
        the directory the user is looking at, regardless of which pane
        has focus. Error rows are silently skipped (they're non-taggable
        by design). Idempotent: pressing again when everything is
        already tagged is a no-op with a flash explaining why.
        """
        contents = self.query_one(ContentsPane)
        paths = contents.row_paths()
        if not paths:
            self.flash("Tag all: nothing to tag.")
            return
        sid = self._source.source_id
        delta = self.tagged_set.add_many((sid, p) for p in paths)
        self._refresh_tag_visuals()
        self._update_subtitle()
        self._refresh_status()
        if delta == 0:
            self.flash(f"Tag all: {len(paths)} entries already tagged.")
        else:
            self.flash(f"Tagged {delta} entries.")

    @work
    async def action_tag_pattern(self) -> None:
        """``+`` - prompt for a glob and tag every contents-pane row matching it.

        Uses ``fnmatch.fnmatch`` (Matthew's pick 2026-05-22) for platform-
        default casing: case-sensitive on POSIX, case-insensitive on
        Windows. Matches against entry basename (no path separators).
        Scope is the contents pane's current dir, not recursive.
        """
        await self._tag_pattern_impl(add=True)

    @work
    async def action_untag_pattern(self) -> None:
        """``-`` - prompt for a glob and untag every contents-pane row matching it."""
        await self._tag_pattern_impl(add=False)

    async def _tag_pattern_impl(self, *, add: bool) -> None:
        from fnmatch import fnmatch

        contents = self.query_one(ContentsPane)
        paths = contents.row_paths()
        if not paths:
            self.flash(("Tag" if add else "Untag") + " pattern: nothing here.")
            return

        verb = "Tag" if add else "Untag"
        typed = await self.push_screen_wait(
            PromptDialog(
                title=f"{verb} by glob pattern:",
                placeholder="*.png  (basename match, platform-default case)",
                hint="Enter to apply  -  Esc to cancel",
            )
        )
        if typed is None:
            self.flash(f"{verb} pattern: cancelled.")
            return
        pattern = typed.strip()
        if not pattern:
            self.flash(f"{verb} pattern: cancelled (empty pattern).")
            return

        sid = self._source.source_id
        matches = [
            (sid, p) for p in paths if fnmatch(posixpath.basename(p), pattern)
        ]
        if not matches:
            self.flash(f"{verb} pattern: no matches for {pattern!r}.")
            return

        if add:
            delta = self.tagged_set.add_many(matches)
            msg = (
                f"Tagged {delta} new entries matching {pattern!r}"
                if delta
                else f"All {len(matches)} matches already tagged."
            )
        else:
            delta = self.tagged_set.remove_many(matches)
            msg = (
                f"Untagged {delta} entries matching {pattern!r}"
                if delta
                else f"No matches for {pattern!r} were tagged."
            )

        self._refresh_tag_visuals()
        self._update_subtitle()
        self._refresh_status()
        self.flash(msg)

    # ------------------------------------------------------------------
    # Recursive subtree tag/untag (TreePane Space)
    # ------------------------------------------------------------------

    @work
    async def on_tree_pane_tag_requested(
        self, event: TreePane.TagRequested
    ) -> None:
        """Handle Space on a tree node - recursive toggle of the subtree.

        Semantics (Matthew's pick 2026-05-22): the directory node's
        own current tagged state is the toggle signal. If the node
        is tagged -> recursively untag the node and every descendant.
        If not -> recursively tag everything. Predictable from the
        cursor and inverse-able by pressing Space again.

        Symlinks are treated as leaves (not followed) to avoid cycles
        - the symlink entry itself gets tagged but its target subtree
        isn't walked. ScanErrors on subdirectories are silently
        skipped per the errors-as-data principle (design.md): a
        permission-denied branch doesn't abort the whole gesture.

        Feedback (2026-06-03): the subtree walk runs under the
        scan-dialog gate (:meth:`_run_scan_with_dialog`) so a large
        subtree surfaces a live "Tagging N..." modal after the
        delayed-show threshold instead of freezing silently. The walk
        is chunked (``await asyncio.sleep(0)`` every
        ``SCAN_CHUNK_SIZE`` entries) so Textual paints frames during
        it, and Esc leaves the tagged set untouched - the mutation is
        applied only after an un-cancelled completion (atomic commit,
        mirroring the contents pane's atomic commit on scan cancel).
        See design.md -> User interface -> Scan dialog.
        """
        path = event.path
        sid = self._source.source_id
        currently_tagged = self.tagged_set.contains(sid, path)
        header = "Untagging" if currently_tagged else "Tagging"
        await self._run_scan_with_dialog(
            path,
            self._source,
            lambda ctx: self._recursive_tag_walk(
                path, sid, currently_tagged, ctx
            ),
            header=header,
        )

    async def _recursive_tag_walk(
        self,
        path: str,
        sid: str,
        currently_tagged: bool,
        ctx: ScanContext,
    ) -> None:
        """Walk the subtree under ``path`` and toggle its tagged state.

        The ``do_work`` body for the recursive-tag scan-dialog gate.
        Consumes :meth:`_walk_subtree` in ``SCAN_CHUNK_SIZE`` chunks,
        writing ``ctx.entries_seen`` (drives the live "Tagging N..."
        count) and polling ``ctx.cancelled`` so the dialog paints and
        Esc responds. **Atomic commit**: the tagged-set mutation runs
        only after the walk completes un-cancelled, so Esc leaves the
        set untouched - mirroring ``ContentsPane.show_path``'s atomic
        commit on scan cancel. Exposed as a named method (not an inline
        closure) so tests can drive it with a pre-cancelled ctx, the
        same way the scan-dialog tests drive ``show_path(ctx=...)``.
        """
        pairs: list[tuple[str, str]] = []
        count = 0
        async for sub in self._walk_subtree(path):
            pairs.append((sid, sub))
            count += 1
            ctx.entries_seen = count
            if count % SCAN_CHUNK_SIZE == 0:
                if ctx.cancelled.is_set():
                    return
                await asyncio.sleep(0)
        if ctx.cancelled.is_set():
            return

        if currently_tagged:
            delta = self.tagged_set.remove_many(pairs)
            verb = "Untagged"
        else:
            delta = self.tagged_set.add_many(pairs)
            verb = "Tagged"

        self._refresh_tag_visuals()
        self._update_subtitle()
        self._refresh_status()
        name = posixpath.basename(path.rstrip("/")) or path
        self.flash(f"{verb} {delta} entries under {name}")

    async def _walk_subtree(self, root_path: str):
        """Yield every absolute path in the subtree rooted at ``root_path``.

        Includes ``root_path`` itself as the first yielded value, then
        every descendant reachable through ``EntrySource.scan()``.
        Symlink entries are yielded but **not recursed into** (cycle
        guard). ScanErrors are silently skipped per errors-as-data.
        Iterative (stack-based) so deep trees don't blow Python's
        recursion limit.
        """
        from wtree.sources.base import Entry as _Entry

        yield root_path
        stack: list[str] = [root_path]
        while stack:
            current = stack.pop()
            try:
                async for item in self._source.scan(current):
                    if isinstance(item, _Entry):
                        child = os.path.join(current, item.name)
                        yield child
                        if item.kind is Kind.DIR:
                            stack.append(child)
                    # ScanError items: skip silently.
            except Exception:  # noqa: BLE001 - defensive vs source contract
                # NativeSource / MockSource yield ScanError objects rather
                # than raising, but a future source might raise; don't
                # abort the gesture on one bad branch.
                continue

    # ------------------------------------------------------------------
    # Flash convenience - route user-immediate feedback to StatusLine
    # ------------------------------------------------------------------

    def flash(self, message: str, *, timeout: float = 3.0) -> None:
        """Show a transient status-line message ("X rejected", "Logged: Y")."""
        try:
            status = self.query_one(StatusLine)
        except Exception:  # noqa: BLE001 - early-mount safety
            return
        status.flash(message, timeout=timeout)

    @work
    async def action_copy(self) -> None:
        """C / F5 - plan a copy of the current Selection and enqueue it."""
        await self._plan_modal_enqueue(
            verb="Copy", planner=plan_copy, kind=OperationKind.COPY
        )

    @work
    async def action_move(self) -> None:
        """M / F6 - plan a move of the current Selection and enqueue it."""
        await self._plan_modal_enqueue(
            verb="Move", planner=plan_move, kind=OperationKind.MOVE
        )

    @work
    async def action_delete(self) -> None:
        """D / Del / F8 - plan a delete and enqueue after confirmation."""
        await self._plan_confirm_enqueue(
            verb="Delete",
            planner=plan_delete,
            kind=OperationKind.DELETE,
        )

    @work
    async def action_rename(self) -> None:
        """R / F2 - rename the cursor entry to a typed new basename."""
        assert self.op_queue is not None, "op_queue constructed in on_mount"

        if self.tagged_set:
            self.flash(
                "Rename works on one entry; clear tags first (Ctrl+U)."
            )
            return

        contents = self.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        if cursor is None:
            self.flash("Rename: nothing under the cursor.")
            return
        path, kind = cursor
        tag = Tag(source_id=self._source.source_id, path=path)
        current_basename = posixpath.basename(path.rstrip("/"))
        # Pre-select the basename stem so typing replaces the name
        # while keeping the extension (Finder / Explorer convention).
        stem_range = select_range_for_rename(current_basename, kind)

        typed = await self.push_screen_wait(
            PromptDialog(
                title=f"Rename {path} to:",
                initial=current_basename,
                placeholder="new name (no path separators)",
                hint="Enter to confirm  -  Esc to cancel",
                select_initial=stem_range,
            )
        )
        if typed is None:
            self.flash("Rename: cancelled.")
            return
        typed = typed.strip()
        if not typed:
            self.flash("Rename: cancelled (empty name).")
            return

        plan = await plan_rename(tag, typed, self.sources)
        if plan.is_empty:
            self.flash("Rename: planner produced no items.")
            return
        if plan.errors and not plan.items:
            err = plan.errors[0]
            self.flash(f"Rename: {err.message}")
            self.last_plan = plan
            return

        plan = await self._resolve_plan_conflicts(plan, "Rename")
        if plan is None:
            return

        self._finalise_plan(plan, [tag], "Rename", destination_path=None)

    def action_view(self) -> None:
        """V / F3 - open the cursor entry in the built-in pager."""
        contents = self.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        if cursor is None:
            self.flash("View: nothing under the cursor.")
            return
        path, kind = cursor

        if kind is Kind.DIR:
            self.flash(
                "View: that's a directory. Press Enter to navigate into it."
            )
            return

        if kind not in (Kind.FILE, Kind.SYMLINK):
            self.flash(f"View: cannot view a {kind.value}.")
            return

        self.push_screen(ViewerScreen(path))

    @work
    async def action_edit(self) -> None:
        """E / F4 - shell out to ``$VISUAL`` / ``$EDITOR`` / platform default."""
        contents = self.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        if cursor is None:
            self.flash("Edit: nothing under the cursor.")
            return
        path, kind = cursor

        if kind is Kind.DIR:
            self.flash(
                "Edit: that's a directory. Press Enter to navigate into it."
            )
            return

        if kind not in (Kind.FILE, Kind.SYMLINK):
            self.flash(f"Edit: cannot edit a {kind.value}.")
            return

        argv = resolve_editor()
        try:
            rc = await asyncio.to_thread(
                self._launch_editor_blocking, argv, path
            )
        except FileNotFoundError:
            self.flash(
                f"Edit: editor not found ({argv[0]!r}). "
                "Set $VISUAL or $EDITOR."
            )
            return
        except Exception as exc:  # noqa: BLE001 - surface any spawn error
            self.flash(f"Edit: {type(exc).__name__}: {exc}")
            return

        if rc != 0:
            self.flash(f"Edit: {argv[0]} exited with status {rc}.")

        if contents.current_path is not None:
            await contents.show_path(contents.current_path)
        self._refresh_status()

    def _launch_editor_blocking(
        self, argv: Sequence[str], path: str
    ) -> int:
        """Suspend Textual, run the editor, resume; return the exit code."""
        with self.suspend():
            return launch_editor_blocking(argv, path)

    @work
    async def action_make_new(self) -> None:
        """N / F7 - create a new dir or file in the pane's current dir."""
        assert self.op_queue is not None, "op_queue constructed in on_mount"

        contents = self.query_one(ContentsPane)
        parent_path = contents.current_path
        if parent_path is None:
            self.flash("Make-new: no directory under the contents pane.")
            return

        kind = await self.push_screen_wait(KindChooserDialog())
        if kind is None:
            self.flash("Make-new: cancelled.")
            return

        kind_label = "directory" if kind is Kind.DIR else "file"
        typed = await self.push_screen_wait(
            PromptDialog(
                title=f"New {kind_label} in {parent_path}:",
                initial="",
                placeholder=f"name (or path/to/{kind_label})",
                hint="Enter to confirm  -  Esc to cancel",
            )
        )
        if typed is None:
            self.flash("Make-new: cancelled.")
            return
        if not typed.strip():
            self.flash("Make-new: cancelled (empty name).")
            return

        plan = await plan_make_new(
            parent_path,
            typed,
            kind,
            self._source.source_id,
            self.sources,
        )
        if plan.errors and not plan.items:
            err = plan.errors[0]
            self.flash(f"Make-new: {err.message}")
            self.last_plan = plan
            return
        if plan.is_empty:
            self.flash("Make-new: planner produced no items.")
            return

        # A leaf-already-exists collision is now annotated on the item (not a
        # planner error), so route Make-new through the shared conflict dialog
        # for Skip / Overwrite / Rename - same flow as Copy / Move. No
        # collision: _resolve_plan_conflicts returns the plan unchanged.
        plan = await self._resolve_plan_conflicts(plan, "Make-new")
        if plan is None:
            return

        synthetic_tag = Tag(
            source_id=self._source.source_id,
            path=plan.items[0].dst_path,
        )
        self._finalise_plan(
            plan, [synthetic_tag], "Make-new", destination_path=None
        )

    async def _plan_modal_enqueue(
        self,
        *,
        verb: str,
        planner: DestPlanner,
        kind: OperationKind,
    ) -> None:
        """Shared body of every "plan -> destination modal -> enqueue" action."""
        assert self.op_queue is not None, "op_queue constructed in on_mount"
        tags = self._resolve_selection_tags()
        if not tags:
            self.flash(
                f"{verb}: nothing to {verb.lower()} "
                "(no tags, no cursor entry)."
            )
            return

        contents = self.query_one(ContentsPane)
        default_dest = contents.current_path or self._root_path
        title = (
            f"{verb} {len(tags)} tagged item(s) to:" if len(tags) > 1
            else f"{verb} {tags[0].path} to:"
        )
        typed = await self.push_screen_wait(
            PromptDialog(
                title=title,
                initial=default_dest,
                placeholder="destination directory path",
                hint="Enter to confirm  -  Esc to cancel",
            )
        )
        if typed is None:
            self.flash(f"{verb}: cancelled.")
            return
        typed = typed.strip()
        if not typed:
            self.flash(f"{verb}: cancelled (empty destination).")
            return

        destination = Tag(source_id=self._source.source_id, path=typed)
        plan = await planner(tags, destination, self.sources)
        if not plan.items and not plan.errors:
            # Every item resolved to a no-op. The only producer of this is
            # the self-target drop in plan_move (Move/Rename of an entry into
            # the directory it already lives in). Copy self-targets survive
            # as SELF items, so Copy never lands here. Give the user a gentle
            # nudge instead of silently doing nothing.
            self.flash(
                f"{verb}: already there - nothing to {verb.lower()}."
            )
            return
        plan = await self._resolve_plan_conflicts(plan, verb)
        if plan is None:
            return
        self._finalise_plan(plan, tags, verb, destination_path=destination.path)

    async def _resolve_plan_conflicts(
        self, plan: Plan, verb: str
    ) -> Plan | None:
        """Surface plan-time conflicts and fold the user's choices back in.

        Returns the plan to enqueue - unchanged if nothing collided, or
        rebuilt by :func:`resolve_conflicts` once the user has chosen
        per-conflict resolutions. Returns ``None`` (and flashes) when the
        user cancels the whole operation in :class:`ConflictDialog`, or when
        every conflicting item was skipped leaving nothing to do.

        See ``design.md`` -> User interface -> Conflict resolution dialog.
        """
        conflicts = [
            i for i in plan.items if i.conflict is not ConflictKind.NONE
        ]
        if not conflicts:
            return plan
        # Precompute each conflict's RENAME target so the dialog can show a
        # live preview when a row is set to Rename (SELF rows default to it).
        previews = [
            await preview_renamed_dst(i, self.sources) for i in conflicts
        ]
        resolutions = await self.push_screen_wait(
            ConflictDialog(conflicts, previews=previews)
        )
        if resolutions is None:
            self.flash(f"{verb}: cancelled.")
            return None
        resolved = await resolve_conflicts(plan, resolutions, self.sources)
        if not resolved.items:
            self.flash(f"{verb}: nothing to do (all conflicts skipped).")
            return None
        return resolved

    async def _plan_confirm_enqueue(
        self,
        *,
        verb: str,
        planner: NoDestPlanner,
        kind: OperationKind,
    ) -> None:
        """Shared body of "plan -> yes/no confirm -> enqueue" actions."""
        assert self.op_queue is not None, "op_queue constructed in on_mount"
        tags = self._resolve_selection_tags()
        if not tags:
            self.flash(
                f"{verb}: nothing to {verb.lower()} "
                "(no tags, no cursor entry)."
            )
            return

        title = (
            f"{verb} {len(tags)} tagged item(s)?" if len(tags) > 1
            else f"{verb} {tags[0].path}?"
        )
        body = [t.path for t in tags]
        confirmed = await self.push_screen_wait(
            ConfirmDialog(title=title, body=body)
        )
        if not confirmed:
            self.flash(f"{verb}: cancelled.")
            return

        plan = await planner(tags, self.sources)
        self._finalise_plan(plan, tags, verb, destination_path=None)

    def _finalise_plan(
        self,
        plan: Plan,
        tags: Sequence[Tag],
        verb: str,
        *,
        destination_path: str | None,
    ) -> None:
        """Common tail: record last_plan, enqueue, clear tagged set, notify."""
        assert self.op_queue is not None
        self.last_plan = plan

        if plan.is_empty:
            self.flash(f"{verb}: planner produced no items.")
            return

        self.op_queue.enqueue(plan)
        if self.tagged_set:
            self.tagged_set.clear()
            self._refresh_tag_visuals()

        body_paths = [t.path for t in tags[:3]]
        body = ", ".join(body_paths)
        if len(tags) > 3:
            body += f", ... (+{len(tags) - 3} more)"
        depth = self.op_queue.depth
        depth_note = "" if depth <= 1 else f"  [{depth - 1} ahead in queue]"
        tail = (
            f" -> {destination_path}" if destination_path is not None else ""
        )
        self.notify(
            f"{plan.summary()} - from {body}{tail}{depth_note}",
            title=f"{verb} (queued)",
        )
        self._update_subtitle()
        self._refresh_status()

    def _resolve_selection_tags(self) -> list[Tag]:
        """Apply the design's Selection rule (NOT used by Rename)."""
        if self.tagged_set:
            return sorted(
                (Tag(t.source_id, t.path) for t in self.tagged_set),
                key=lambda t: (t.source_id, t.path),
            )
        contents = self.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        if cursor is None:
            return []
        path, _kind = cursor
        return [Tag(source_id=self._source.source_id, path=path)]

    async def on_tree_pane_ascend_requested(
        self, event: TreePane.AscendRequested
    ) -> None:
        """Re-root the tree at the parent of the current root."""
        event.stop()
        await self._do_ascend()

    async def _do_ascend(self) -> None:
        """Re-root the tree at the parent of the current root.

        Shared by the Left-on-root tree gesture
        (:meth:`on_tree_pane_ascend_requested`) and the blank-Enter
        branch of :meth:`action_log_new_source` - both express the
        same "widen the logged window" intent. No-op (with flash) at
        the filesystem root.

        After re-rooting, the cursor lands on the old-root row in the
        new tree so the user can immediately drill back in. Tags
        survive because they're stored as absolute paths.
        """
        old_root = self._root_path
        new_root = os.path.dirname(old_root)
        if not new_root or new_root == old_root:
            self.flash(f"Already at the filesystem root ({old_root}).")
            return

        self._root_path = new_root
        tree = self.query_one(TreePane)
        await tree.re_root(new_root)
        await tree.focus_child_of_root(old_root)
        self.flash(f"Logged: {new_root} (ascended from {old_root})")
        self._refresh_status()

    @work
    async def action_refresh_source(self) -> None:
        """Ctrl+R - force a re-scan of both panes against the source.

        Used when the user thinks the on-disk state may have drifted
        from what's displayed (other process modified the tree, mount
        re-sync, etc.). Equivalent to "tap to refresh" in a browser.

        Two-stage:

        * The contents pane re-runs ``show_path`` against its
          current path - replacing every row with a fresh scan.
        * The tree pane runs :meth:`TreePane.refresh_all` which
          snapshots expanded paths + cursor, wipes the tree, and
          re-walks the snapshot so the user's drilled-down context
          survives the refresh.

        Exceptions are swallowed per-pane so a refresh failure on
        one pane doesn't block the other and never propagates back
        to the action loop. Mirrors the structure of
        :meth:`_refresh_panes_after_op`.
        """
        try:
            contents = self.query_one(ContentsPane)
            path = contents.current_path
            if path is not None:
                # Scan-dialog gate. Big-dir refresh now surfaces the
                # dialog after the threshold instead of freezing.
                await self._run_scan_with_dialog(
                    path,
                    self._source,
                    lambda ctx: contents.show_path(path, ctx=ctx),
                )
        except Exception:  # noqa: BLE001 - per-pane isolation
            pass
        try:
            tree = self.query_one(TreePane)
            await self._run_scan_with_dialog(
                self._root_path,
                self._source,
                lambda ctx: tree.refresh_all(ctx=ctx),
            )
        except Exception:  # noqa: BLE001 - per-pane isolation
            pass
        self.flash("Source refreshed.")
        self._refresh_status()

    @work
    async def action_log_new_source(self) -> None:
        """L - log a new source (re-root the tree at a typed path).

        XTree's "L" command was "log a new drive". WTree generalises:
        the user types any absolute or relative path, and the tree
        re-roots there. Tags survive (they're absolute paths).

        Path resolution:

        * ``~`` is expanded.
        * Absolute paths are used as-is.
        * Relative paths resolve against the current root (not cwd).
          So ``../sibling`` walks sideways from the current logged
          context, which matches the XTree "I'm in a place, switch
          to a related place" intuition.

        Special case: a blank submission means "ascend to my parent",
        same as Left-on-root. Per the 2026-05-22 design conversation,
        this layered discoverability hint lets a user who's already
        in the prompt fall back to ascend without having to escape and
        re-press Left.

        Validation errors (missing path, not a directory) flash a
        nudge without changing the root. Esc cancels.
        """
        old_root = self._root_path
        typed = await self.push_screen_wait(
            PromptDialog(
                title=f"Log new source (current: {old_root}):",
                placeholder="absolute path, or relative to current root",
                hint="Enter to log  -  blank Enter to ascend  -  Esc to cancel",
            )
        )
        if typed is None:
            self.flash("Log: cancelled.")
            return
        typed = typed.strip()

        # Blank submission = ascend (parent of current root).
        if not typed:
            await self._do_ascend()
            return

        # Resolve ~, then relative paths against the current root.
        candidate = os.path.expanduser(typed)
        if not os.path.isabs(candidate):
            candidate = os.path.normpath(
                os.path.join(self._root_path, candidate)
            )
        candidate = os.path.abspath(candidate)

        if not os.path.exists(candidate):
            self.flash(f"Log: path doesn't exist: {candidate}")
            return
        if not os.path.isdir(candidate):
            self.flash(f"Log: not a directory: {candidate}")
            return

        # Re-root. ``re_root`` wipes the existing tree subtree and
        # re-populates from the new root; tags survive because they're
        # absolute paths. Wrapped in the scan-dialog gate so logging a
        # 100 k-entry folder shows "Scanning ... via os.scandir ..."
        # rather than freezing the UI.
        self._root_path = candidate
        tree = self.query_one(TreePane)
        await self._run_scan_with_dialog(
            candidate,
            self._source,
            lambda ctx: tree.re_root(candidate, ctx=ctx),
        )
        self.flash(f"Logged: {candidate}")
        self._refresh_status()

    # ------------------------------------------------------------------
    # Incremental search (``/``) - see design.md
    # ------------------------------------------------------------------

    def action_search(self) -> None:
        """``/`` - activate the inline incremental-search bar."""
        target: ContentsPane | TreePane | None = None
        if isinstance(self.focused, ContentsPane):
            target = self.focused
        elif isinstance(self.focused, TreePane):
            target = self.focused
        if target is None:
            self.flash("Search: focus a pane first (Tab to switch).")
            return

        self._search_target = target
        self._search_cursor_pre = target.get_search_cursor()
        self._search_matches = []
        self._search_match_idx = 0

        self.query_one(StatusLine).display = False
        bar = self.query_one(SearchBar)
        bar.activate()

    def on_search_bar_query_changed(
        self, event: SearchBar.QueryChanged
    ) -> None:
        """Recompute matches when the query string changes."""
        if self._search_target is None:
            return
        query = event.query
        bar = self.query_one(SearchBar)

        if not query:
            self._search_matches = []
            self._search_match_idx = 0
            bar.update_match_info(0, 0)
            return

        needle = query.lower()
        matches = [
            row
            for row, label in self._search_target.iter_searchable()
            if needle in label.lower()
        ]
        self._search_matches = matches
        if not matches:
            self._search_match_idx = 0
            bar.update_match_info(0, 0)
            return

        anchor = self._search_cursor_pre or 0
        idx_after = next(
            (i for i, row in enumerate(matches) if row >= anchor),
            0,
        )
        self._search_match_idx = idx_after
        self._search_target.set_search_cursor(matches[idx_after])
        bar.update_match_info(len(matches), idx_after + 1)

    def on_search_bar_next_match(self, event: SearchBar.NextMatch) -> None:
        """Down / Ctrl+G - step forward through matches (wrap)."""
        self._step_match(1)

    def on_search_bar_prev_match(self, event: SearchBar.PrevMatch) -> None:
        """Up - step backward through matches (wrap)."""
        self._step_match(-1)

    def _step_match(self, direction: int) -> None:
        """Move forward/backward through ``_search_matches`` with wrap."""
        if not self._search_matches or self._search_target is None:
            return
        n = len(self._search_matches)
        self._search_match_idx = (self._search_match_idx + direction) % n
        self._search_target.set_search_cursor(
            self._search_matches[self._search_match_idx]
        )
        self.query_one(SearchBar).update_match_info(
            n, self._search_match_idx + 1
        )

    def on_search_bar_committed(self, event: SearchBar.Committed) -> None:
        """Enter - exit search, leave cursor at the current match."""
        self._exit_search(restore=False)

    def on_search_bar_cancelled(self, event: SearchBar.Cancelled) -> None:
        """Esc - exit search, restore cursor to pre-search position."""
        self._exit_search(restore=True)

    def _exit_search(self, *, restore: bool) -> None:
        """Tear down search state and restore the regular UI."""
        if self._search_target is None:
            return
        if restore and self._search_cursor_pre is not None:
            self._search_target.set_search_cursor(self._search_cursor_pre)

        target = self._search_target
        self._search_target = None
        self._search_cursor_pre = None
        self._search_matches = []
        self._search_match_idx = 0

        self.query_one(SearchBar).deactivate()
        self.query_one(StatusLine).display = True
        target.focus()
        self._refresh_status()

    # ------------------------------------------------------------------
    # Find across tree (Ctrl+F + Ctrl+G)
    # ------------------------------------------------------------------
    #
    # Distinct from the ``/`` incremental search:
    #
    # * ``/`` searches *visible* rows in whichever pane is focused. Local,
    #   modeless, the matcher runs against displayed labels.
    # * ``Ctrl+F`` searches the *entire* tree under the logged root by
    #   walking the source recursively. The user types a query into a
    #   ``PromptDialog``; on submit the app walks every directory, builds
    #   a list of matches (basename substring, case-insensitive), and
    #   jumps the tree cursor to the first match. Subsequent ``Ctrl+G``
    #   presses step through the cached list with wrap.
    #
    # The cached match list lives on the app and survives until a fresh
    # ``Ctrl+F`` replaces it. A future variant could surface a results
    # modal listing all matches; v0 keeps it in-place to mirror the
    # XTree "step through" feel.

    @work
    async def action_find_tree(self) -> None:
        """Ctrl+F - find across the full logged tree (not just visible).

        Walks the source under ``self._root_path`` via
        :meth:`_walk_subtree` (the same async generator the recursive
        tree-pane Space gesture uses), filters by basename substring
        case-insensitive, caches the result list on the app, and jumps
        the tree cursor onto the first match via
        :meth:`TreePane.reveal_path`. Subsequent ``Ctrl+G`` steps
        through the cache.

        Errors mid-walk are silently skipped - the underlying
        ``_walk_subtree`` already filters ``ScanError`` items per
        errors-as-data. A partially-failed walk still produces a
        partial match list, which is the v0 behaviour we want
        (better than refusing to search at all).
        """
        typed = await self.push_screen_wait(
            PromptDialog(
                title="Find across tree:",
                placeholder="basename substring (case-insensitive)",
                hint="Enter to search  -  Esc to cancel",
            )
        )
        if typed is None:
            self.flash("Find: cancelled.")
            return
        query = typed.strip()
        if not query:
            self.flash("Find: cancelled (empty query).")
            return

        needle = query.lower()
        matches: list[str] = []
        async for path in self._walk_subtree(self._root_path):
            if path == self._root_path:
                continue  # Don't match the root itself.
            basename = posixpath.basename(path.rstrip("/")) or path
            if needle in basename.lower():
                matches.append(path)

        # Cache and announce. Always update the cached query - even
        # for zero matches - so Ctrl+G's "no active search" flash
        # carries the right context.
        self._tree_find_query = query
        self._tree_find_matches = matches
        self._tree_find_idx = 0

        if not matches:
            self.flash(f"Find: no matches for {query!r}.")
            return

        tree = self.query_one(TreePane)
        revealed = await tree.reveal_path(matches[0])
        first = posixpath.basename(matches[0].rstrip("/")) or matches[0]
        n = len(matches)
        if revealed:
            self.flash(f"Find: {n} match(es) for {query!r}; 1/{n} - {first}")
        else:
            # Match exists in the cache but the tree couldn't navigate
            # to it (e.g. the source raised mid-reveal). The cache is
            # still useful - Ctrl+G might land on a later one.
            self.flash(
                f"Find: {n} match(es) for {query!r}; "
                f"couldn't reveal {first}, try Ctrl+G."
            )

    @work
    async def action_next_match(self) -> None:
        """Ctrl+G - jump to the next find-across-tree match (wrap).

        Steps through the cached match list from the most recent
        Ctrl+F. With no cached matches the action flashes a nudge
        rather than no-op'ing silently - a user reaching for Ctrl+G
        after a `/` commit (parked follow-up, ``_last_query``-style
        re-run) should get a hint about what's missing.
        """
        if not self._tree_find_matches:
            if self._tree_find_query is not None:
                self.flash(
                    f"Find: no matches for {self._tree_find_query!r}. "
                    "Press Ctrl+F to search again."
                )
            else:
                self.flash("Find: no active search (press Ctrl+F first).")
            return
        n = len(self._tree_find_matches)
        self._tree_find_idx = (self._tree_find_idx + 1) % n
        match = self._tree_find_matches[self._tree_find_idx]
        tree = self.query_one(TreePane)
        await tree.reveal_path(match)
        basename = posixpath.basename(match.rstrip("/")) or match
        cur = self._tree_find_idx + 1
        self.flash(f"Find: {cur}/{n} - {basename}")

    # ------------------------------------------------------------------
    # Menu bar (F9) - see design.md
    # ------------------------------------------------------------------

    @work
    async def action_menu_bar(self) -> None:
        """F9 - open the menu modal and dispatch the chosen action.

        Push :class:`MenuScreen`; await its dismiss. ``None`` =
        cancelled (Esc); otherwise dispatch ``action_<name>`` to
        execute the chosen menu item. Menu items map 1:1 to
        keyboard shortcuts the user could've pressed directly - the
        menu is a discoverability surface, not a parallel control
        path.

        Unknown action names (which shouldn't happen if MENUS in
        ``menu_bar.py`` stays in sync with the action methods) flash
        a diagnostic. The dispatch is via ``getattr`` so adding a
        new menu item is as simple as adding a ``MenuItem`` with
        the right ``action`` string.
        """
        chosen = await self.push_screen_wait(MenuScreen())
        if chosen is None:
            return
        method = getattr(self, f"action_{chosen}", None)
        if method is None:
            self.flash(f"Menu: unknown action {chosen!r}.")
            return
        result = method()
        # Some actions are sync (action_view, action_untag_all);
        # others are @work-decorated coroutines (action_copy, etc).
        # @work returns None synchronously after spawning a worker,
        # so we only await if the method returned an actual coroutine.
        if asyncio.iscoroutine(result):
            await result

    def action_properties(self) -> None:
        """Ctrl+I - open the Properties inspector for the current Selection.

        Mode picked here, before constructing :class:`PropertiesScreen`:

        * Tagged set non-empty -> tagged mode (count + breakdown + total
          file-size sum, dirs skipped).
        * Else focused pane's cursor on a non-directory -> file mode
          (identity, size, mtime, permissions, owner).
        * Else focused pane's cursor on a directory -> dir mode
          (identity rows plus an async recursive walk for total size /
          file count / dir count; Esc cancels the walk).
        * Else (no tags, no cursor entry) -> flash "Nothing to inspect"
          per the 2026-05-25 design call. Cheaper than opening an
          empty modal.

        Source-of-cursor follows the existing op convention (View / Edit
        / Rename): whichever pane has focus. Tree-pane cursor entries
        are always directories (tree node ``data`` is a dir path or
        ``None`` for error placeholders); contents-pane cursor entries
        carry their own kind.
        """
        if self.tagged_set:
            tags = tuple(
                sorted(
                    (Tag(t.source_id, t.path) for t in self.tagged_set),
                    key=lambda t: (t.source_id, t.path),
                )
            )
            self.push_screen(
                PropertiesScreen("tagged", tagged=TaggedProps(tags=tags))
            )
            return

        path: str | None = None
        kind: Kind | None = None

        if isinstance(self.focused, TreePane):
            node = self.focused.cursor_node
            if node is not None and node.data is not None:
                path = node.data
                kind = Kind.DIR
        else:
            contents = self.query_one(ContentsPane)
            cursor = contents.cursor_entry()
            if cursor is not None:
                path, kind = cursor

        if path is None or kind is None:
            self.flash("Properties: nothing to inspect.")
            return

        if kind is Kind.DIR:
            self.push_screen(
                PropertiesScreen("dir", directory=DirProps(path=path))
            )
        else:
            self.push_screen(
                PropertiesScreen(
                    "file", file=FileProps(path=path, kind=kind)
                )
            )

    def action_show_progress(self) -> None:
        """Ctrl+P - re-open a minimized progress dialog.

        The ``OperationQueue`` keeps running whether or not a
        :class:`ProgressScreen` is on the stack. Minimize (``m`` on
        the dialog) dismisses the screen without setting
        ``cancel_requested``; Ctrl+P from anywhere in the app
        re-pushes a fresh ``ProgressScreen`` bound to the same queue.
        The new screen polls live queue state on first paint, so
        it comes up at whatever percentage the op has actually
        reached - no stale snapshot.

        If the queue isn't running anything, flash a nudge through
        :meth:`StatusLine.flash` (same idiom ``Ctrl+G`` uses with an
        empty find-tree cache). If a ``ProgressScreen`` is already
        on the stack, no-op so spamming Ctrl+P doesn't double-stack.
        """
        queue = self.op_queue
        if queue is None or queue.running is None:
            self.flash("No operation in progress")
            return
        for screen in self.screen_stack:
            if isinstance(screen, ProgressScreen):
                return
        self.push_screen(ProgressScreen(queue))

    def action_help(self) -> None:
        """F1 / ``?`` / Help menu - open the About + keymap modal.

        Pushes :class:`HelpScreen` (read-only; dismisses on Esc / Q).
        Same screen serves both the F1 cheat-sheet role and the Help
        menu's About item - the modal contains the version,
        attribution, and a categorised keymap reference grouped by
        Navigation / Tagging / File operations / Search / Application
        / Selection rule.
        """
        self.push_screen(HelpScreen())

    # ------------------------------------------------------------------
    # OperationQueue callbacks
    # ------------------------------------------------------------------

    def _on_plan_start(self, plan: Plan, queue: OperationQueue) -> None:
        self._update_subtitle()
        self._refresh_status()
        self._maybe_push_progress_dialog(plan, queue)

    async def _run_scan_with_dialog(
        self,
        path: str,
        source: EntrySource,
        do_work: Callable[[ScanContext], Awaitable[None]],
        *,
        header: str = "Scanning",
    ) -> None:
        """Run ``do_work(ctx)`` under the scan-dialog gate.

        Builds a fresh :class:`ScanContext` tied to ``path`` and the
        source's :attr:`scan_method_label`. Schedules a
        ``set_timer(SCAN_MODAL_DELAY_SECONDS)`` that pushes
        :class:`ScanScreen` only if the work is still running when it
        fires - so fast scans never see a flash. Awaits ``do_work``,
        then ``ctx.completed.set()`` so the dialog (if it was pushed)
        notices on its next redraw tick and dismisses itself.

        Cancellation: the user pressing Esc inside the dialog sets
        ``ctx.cancelled``; the consumer (``show_path`` / ``_populate``
        / ``refresh_all`` / ``re_root``) checks it between
        :data:`SCAN_CHUNK_SIZE`-entry chunks and returns without
        committing, leaving the pane on its previous listing. No
        exception is raised - cancellation is just early-return.

        Surfaces this helper is wired at (design.md User interface
        -> Scan dialog -> Application surfaces):

        * ``L`` log new source - the new root's first-level scan.
        * ``Ctrl+R`` refresh source - both panes' re-scans.
        * Tree NodeHighlighted - cursor onto a (potentially huge)
          dir triggers a contents-pane scan.
        * Initial mount - the very first contents-pane scan on app
          launch.

        Future call sites (parked on todo.md): tree-pane Right-arrow
        expand of a node with many children; ``focus_dir_under_cursor``
        on Enter into a big dir; ``Ctrl+F`` find-across-tree's walker.
        """
        ctx = ScanContext(
            path=path,
            method_label=source.scan_method_label,
            header=header,
        )

        def _push_if_still_running() -> None:
            # Don't push if the work finished/cancelled before the
            # timer fired, or if a ScanScreen is already on the stack
            # (defensive; the helper itself runs serially per call,
            # but a concurrent _run_scan_with_dialog would race here).
            if ctx.completed.is_set() or ctx.cancelled.is_set():
                return
            for screen in self.screen_stack:
                if isinstance(screen, ScanScreen):
                    return
            self.push_screen(ScanScreen(ctx))

        delay_timer = self.set_timer(
            SCAN_MODAL_DELAY_SECONDS, _push_if_still_running
        )
        try:
            await do_work(ctx)
        finally:
            ctx.completed.set()
            delay_timer.stop()
            # The dialog's own polling timer dismisses on
            # ctx.completed - we don't have to chase it down here.
            # But if push happened during this method's await
            # window and the user is still looking at the dialog,
            # we explicitly dismiss to avoid a stale frame.
            for screen in list(self.screen_stack):
                if isinstance(screen, ScanScreen) and screen._ctx is ctx:
                    screen.dismiss()
                    break

    def _maybe_push_progress_dialog(
        self, plan: Plan, queue: OperationQueue
    ) -> None:
        """Threshold gate for the progress modal (design.md 2026-05-25).

        Push immediately if the plan trips the size or item-count
        Push immediately if the plan trips the size or item-count
        threshold; otherwise schedule a delayed-show that pushes only
        if the plan is still running ``PROGRESS_MODAL_DELAY_SECONDS``
        later. Tiny ops never trip and never see a modal.

        Same-drive rename-fast-path moves report ``bytes_total > 0``
        because the planner sums file sizes regardless of whether the
        execution path actually moves bytes - so they may show the
        modal briefly. The modal's Rate / Drag render an em-dash for
        those, and the dialog dismisses as soon as ``os.rename``
        returns.
        """
        if (
            plan.total_bytes > PROGRESS_MODAL_BYTES
            or len(plan.items) > PROGRESS_MODAL_ITEMS
        ):
            self._push_progress_dialog_if_running(plan, queue)
            return

        async def _delayed() -> None:
            await asyncio.sleep(PROGRESS_MODAL_DELAY_SECONDS)
            self._push_progress_dialog_if_running(plan, queue)

        asyncio.create_task(_delayed())

    def _push_progress_dialog_if_running(
        self, plan: Plan, queue: OperationQueue
    ) -> None:
        """Push ``ProgressScreen`` for ``plan`` iff it's still running
        and no progress dialog is already on the stack.

        The "still running" check is a plan-identity comparison (``is``,
        not ``==``) - if the queue has moved on to the next plan, this
        plan finished faster than the delayed-show timer and no dialog
        is warranted. The "already on stack" check avoids the racy
        double-push that could occur if the immediate-push branch and
        a stale delayed-show fire close together.
        """
        if queue.running is not plan:
            return
        for screen in self.screen_stack:
            if isinstance(screen, ProgressScreen):
                return
        self.push_screen(ProgressScreen(queue))

    def _on_item_progress(
        self, item: ItemResult, queue: OperationQueue
    ) -> None:
        self._refresh_status()

    def _on_plan_complete(
        self, result: OperationResult, queue: OperationQueue
    ) -> None:
        """Toast the result and schedule a pane auto-refresh."""
        self.last_result = result
        verb = result.plan.kind.value.capitalize()
        if result.all_succeeded:
            self.notify(result.summary(), title=f"{verb} (done)")
        else:
            self.notify(
                result.summary(),
                title=f"{verb} (done with errors)",
                severity="warning",
            )
        self._update_subtitle()
        self._refresh_status()
        # Schedule pane auto-refresh outside this sync callback.
        asyncio.create_task(self._refresh_panes_after_op())

    async def _refresh_panes_after_op(self) -> None:
        """Refresh both panes' on-disk view after a Plan completes.

        Two steps:

        1. Re-show the contents pane's ``current_path`` so the listing
           the user is looking at reflects the new on-disk state. This
           is the original behaviour from the Move-era follow-up.
        2. Refresh the tree pane targeted at ``result.touched_paths``
           (2026-05-23). Only the directory nodes whose listings
           actually changed get re-scanned; the rest of the tree is
           left alone, preserving expansion state for the unaffected
           subtrees. Reads ``self.last_result`` (set just before this
           coroutine is scheduled in :meth:`_on_plan_complete`).

        Exceptions are swallowed per-pane so a refresh failure on one
        pane doesn't block the other and never propagates back to the
        queue worker. Tree-pane refresh wrapped in its own try/except
        so a malformed touched-paths set on a future op kind can't
        regress the long-standing contents-pane refresh contract.
        """
        try:
            contents = self.query_one(ContentsPane)
            if contents.current_path is not None:
                await contents.show_path(contents.current_path)
        except Exception:  # noqa: BLE001 - don't propagate to queue worker
            pass
        try:
            if self.last_result is not None:
                tree = self.query_one(TreePane)
                await tree.refresh_paths(self.last_result.touched_paths)
        except Exception:  # noqa: BLE001 - don't propagate to queue worker
            pass

    # ------------------------------------------------------------------
    # Status surfaces
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        try:
            status = self.query_one(StatusLine)
        except Exception:  # noqa: BLE001 - early-mount safety
            return
        status.refresh_from(self)

    def _update_subtitle(self) -> None:
        n = len(self.tagged_set)
        self.sub_title = (
            f"v{__version__}" if n == 0 else f"v{__version__} - {n} tagged"
        )


def main() -> None:
    """Console-script entry point - the ``wtree`` command launches the app."""
    WTreeApp().run()


if __name__ == "__main__":
    main()
