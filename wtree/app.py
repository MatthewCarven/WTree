"""Textual application - two-pane explorer view with a session-wide tagged set.

Left pane (``TreePane``) is the directory hierarchy under ``root_path``.
Right pane (``ContentsPane``) is the table of entries for whichever
directory the tree cursor is sitting on. The panes are coupled - when
the tree's cursor moves, the contents pane refreshes. See ``design.md``
Layout section.

Bottom of the screen (per ``design.md``): an MC-style F-key cheat-sheet
bar (:class:`~wtree.widgets.keybar.KeyBar`) above a one-line transient
status display (:class:`~wtree.widgets.status_line.StatusLine`).
StatusLine reflects whichever state is most volatile right now - the
running operation if one's in flight, otherwise the cursor entry's path
+ size + mtime. While incremental search (``/``) is active the
StatusLine is hidden and the :class:`SearchBar` takes the same row.

User-immediate feedback ("X rejected", "X cancelled", "Logged: ...")
goes through :meth:`flash`, which routes to
:meth:`StatusLine.flash <wtree.widgets.status_line.StatusLine.flash>`.
Queue-completion notifications stay as toast notifies via
:meth:`notify` because they may fire async when the user has looked
away. The split is "did the user just press a key and want a reply" =
flash, "did something complete on its own time" = toast.

The app owns the central state objects:

* :class:`~wtree.tagged_set.TaggedSet` - the per-session set of tagged
  entries (``design.md`` Tagged set scope).
* ``sources: dict[source_id, EntrySource]`` - the registry the ops
  layer uses to look up sources by id.
* :class:`~wtree.ops.OperationQueue` - serial FIFO of plans being
  executed in the background.

Action helpers (kept private, called from action_* methods):

* :meth:`_plan_modal_enqueue` - destination-typed ops (Copy, Move).
  Opens a ``PromptDialog`` for a destination path.
* :meth:`_plan_confirm_enqueue` - destinationless ops (Delete). Opens
  a ``ConfirmDialog`` for a yes/no gate.
* :meth:`_finalise_plan` - shared post-planner tail used by both
  helpers above and by action_rename: record last_plan, enqueue, clear
  tagged set, notify, refresh status.

Rename does not use the two helpers: it's single-entry only per
``design.md`` Selection rule, the input is a basename (not a path),
and the dialog default is the current basename. The action body
inlines the Selection rule + PromptDialog + plan_rename and then
calls :meth:`_finalise_plan` for the tail.

View (V / F3) and Edit (E / F4) both bypass the planner machinery
entirely: they are read-only or shell-out UI flows, not plan-producing
operations. View pushes :class:`ViewerScreen`; Edit suspends Textual
and runs ``$VISUAL`` / ``$EDITOR`` via :mod:`wtree.editor`.

Make-new (N / F7) is its own shape: a chooser modal asks dir-or-file,
then a PromptDialog asks for the name; the planner takes the displayed
parent dir plus the chosen kind plus the typed name and emits a single
PlanItem. Tagged set is silently ignored - Make-new is "create here",
not Selection-rule. See :meth:`action_make_new`.

Left-on-root ascend (2026-05-22): when the tree pane's cursor is on
the root node and the user presses Left, ``TreePane`` posts an
:class:`~wtree.widgets.tree_pane.TreePane.AscendRequested` message.
The app handles it by re-rooting the tree at the parent path - XTree's
"widen the logged window upward" idiom. At the filesystem root (no
parent) the action emits a status nudge and stays put. See
:meth:`on_tree_pane_ascend_requested`.

Incremental search (``/``, 2026-05-22): activates an inline SearchBar
that takes the StatusLine row. Substring case-insensitive matching
against the focused pane's labels (basename in the contents pane,
visible-node label in the tree pane). See :meth:`action_search` and
the ``on_search_bar_*`` handlers.

Pane auto-refresh (2026-05-22): when an operation completes (Copy,
Move, Delete, Rename, Make-new), the contents pane re-shows its
current path so the on-disk change is visible without the user
pressing anything. Wired in :meth:`_on_plan_complete` via
``asyncio.create_task(self._refresh_panes_after_op())``. Tree-pane
auto-refresh is parked - the lazy-loaded ``_loaded`` memo makes that
non-trivial.
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
    ItemResult,
    OperationKind,
    OperationQueue,
    OperationResult,
    Plan,
    plan_copy,
    plan_delete,
    plan_make_new,
    plan_move,
    plan_rename,
)
from wtree.sources.base import EntrySource, Kind
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag, TaggedSet
from wtree.widgets.confirm import ConfirmDialog
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.keybar import KeyBar
from wtree.widgets.kind_chooser import KindChooserDialog
from wtree.widgets.prompt import PromptDialog
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
        ("question_mark", "noop", "Help"),
        ("tab", "cycle_focus", "Switch pane"),
        ("ctrl+u", "untag_all", "Untag all"),
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

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield TreePane(self._source, self._root_path, id="tree-pane")
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
        await contents.show_path(self._root_path)
        self.query_one(TreePane).focus()
        self._refresh_status()

    async def on_unmount(self) -> None:
        if self.op_queue is not None:
            await self.op_queue.stop()

    async def on_tree_node_highlighted(
        self, event: Tree.NodeHighlighted[str]
    ) -> None:
        contents = self.query_one(ContentsPane)
        await contents.show_path(event.node.data)
        self._refresh_status()

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        self._refresh_status()

    def on_contents_pane_tags_changed(
        self, event: ContentsPane.TagsChanged
    ) -> None:
        self._update_subtitle()
        self._refresh_status()

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
        """Ctrl+U - clear the tagged set, refresh markers in the pane."""
        if not self.tagged_set:
            return
        self.tagged_set.clear()
        self.query_one(ContentsPane).refresh_tag_markers()
        self._update_subtitle()
        self._refresh_status()

    # ------------------------------------------------------------------
    # Flash convenience - route user-immediate feedback to StatusLine
    # ------------------------------------------------------------------

    def flash(self, message: str, *, timeout: float = 3.0) -> None:
        """Show a transient status-line message ("X rejected", "Logged: Y").

        Convenience over ``self.query_one(StatusLine).flash(...)`` with
        an early-mount defensive try/except so callers don't have to
        worry about firing before compose has placed the widget.
        Replaces any active flash (the previous timer is cancelled).

        Use this for user-immediate feedback that the user is expected
        to be looking at right now. Reserve :meth:`notify` for queue
        completion and other things that may fire async after the user
        has moved on - the toast layer queues them visibly even when
        the user has stopped watching the status line.
        """
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
        """R / F2 - rename the cursor entry to a typed new basename.

        Single-entry only per ``design.md`` Selection rule. If the
        tagged set is non-empty when R is pressed, the operation is
        rejected with a status-line flash ("rename works on one entry;
        clear tags first") and no dialog opens. This is the *only*
        v0 op that doesn't follow the standard Selection rule.

        The modal prompts for a new basename (not a destination path).
        The default is the current basename so the user can tweak it
        instead of retyping. If the typed name contains a path
        separator, ``plan_rename`` rejects it with an ``InvalidName``
        error - rename is basename-only.
        """
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
        path, _kind = cursor
        tag = Tag(source_id=self._source.source_id, path=path)
        current_basename = posixpath.basename(path.rstrip("/"))

        typed = await self.push_screen_wait(
            PromptDialog(
                title=f"Rename {path} to:",
                initial=current_basename,
                placeholder="new name (no path separators)",
                hint="Enter to confirm  -  Esc to cancel",
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
        # If the planner rejected (NoChange / InvalidName / etc.) we
        # surface the cause and don't enqueue. Errors are in-band data;
        # no exception was raised.
        if plan.is_empty:
            self.flash("Rename: planner produced no items.")
            return
        if plan.errors and not plan.items:
            err = plan.errors[0]
            self.flash(f"Rename: {err.message}")
            self.last_plan = plan
            return

        self._finalise_plan(plan, [tag], "Rename", destination_path=None)

    def action_view(self) -> None:
        """V / F3 - open the cursor entry in the built-in pager.

        Single-entry op (no Selection rule - viewing the tagged set
        makes no sense). The action validates the cursor entry's kind
        and either pushes :class:`ViewerScreen` (FILE / SYMLINK) or
        emits a status flash with a hint about what to press instead
        (DIR -> Enter to navigate; OTHER -> kind name).
        """
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

        # FILE or SYMLINK - push the viewer. Symlinks are followed by
        # the underlying open() call inside ViewerScreen's load.
        self.push_screen(ViewerScreen(path))

    @work
    async def action_edit(self) -> None:
        """E / F4 - shell out to ``$VISUAL`` / ``$EDITOR`` / platform default.

        Single-entry op operating on the cursor entry, mirroring
        ``action_view``. Kind validation: DIR/OTHER flash a status
        nudge and bail without touching the terminal. After the
        editor returns, the contents pane re-shows the current dir so
        any on-disk change is reflected.

        ``with self.suspend()`` is factored into
        :meth:`_launch_editor_blocking` so tests can monkeypatch it -
        the headless driver doesn't support suspend.
        """
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

        # Refresh the contents pane in case the file's metadata changed
        # (mtime, size).
        if contents.current_path is not None:
            await contents.show_path(contents.current_path)
        self._refresh_status()

    def _launch_editor_blocking(
        self, argv: Sequence[str], path: str
    ) -> int:
        """Suspend Textual, run the editor, resume; return the exit code.

        Carved out so tests can monkeypatch it and skip the
        ``app.suspend()`` call - the headless driver raises
        :class:`SuspendNotSupported`.
        """
        with self.suspend():
            return launch_editor_blocking(argv, path)

    @work
    async def action_make_new(self) -> None:
        """N / F7 - create a new dir or file in the pane's current dir.

        See :class:`KindChooserDialog` + :func:`plan_make_new`. Parent
        dir is :attr:`ContentsPane.current_path`. Tagged set silently
        ignored - Make-new is "create here", not Selection-rule.
        """
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
        if plan.is_empty:
            self.flash("Make-new: planner produced no items.")
            return
        if plan.errors and not plan.items:
            err = plan.errors[0]
            self.flash(f"Make-new: {err.message}")
            self.last_plan = plan
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
        self._finalise_plan(plan, tags, verb, destination_path=destination.path)

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
        """Common tail: record last_plan, enqueue, clear tagged set, notify.

        The "X (queued)" message stays as a toast notify - it carries
        non-trivial info (plan summary + sources + destination) that's
        worth queuing in Textual's notification stack even if the user
        looks away. The "X: planner produced no items" rejection,
        however, is an immediate user-action result -> flash.
        """
        assert self.op_queue is not None
        self.last_plan = plan

        if plan.is_empty:
            self.flash(f"{verb}: planner produced no items.")
            return

        self.op_queue.enqueue(plan)
        if self.tagged_set:
            self.tagged_set.clear()
            self.query_one(ContentsPane).refresh_tag_markers()

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
        """Re-root the tree at the parent of the current root.

        Posted by :class:`TreePane` when the user presses Left while
        the cursor is on the root node. ``os.path.dirname(root) ==
        root`` flashes a "no parent" nudge; otherwise re-roots and
        lands the cursor on the old-root row.
        """
        event.stop()
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

    # ------------------------------------------------------------------
    # Incremental search (``/``) - see design.md § Modality
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

    def action_noop(self) -> None:
        """Placeholder action so the cheat sheet stays honest."""

    # ------------------------------------------------------------------
    # OperationQueue callbacks
    # ------------------------------------------------------------------

    def _on_plan_start(self, plan: Plan, queue: OperationQueue) -> None:
        self._update_subtitle()
        self._refresh_status()

    def _on_item_progress(
        self, item: ItemResult, queue: OperationQueue
    ) -> None:
        self._refresh_status()

    def _on_plan_complete(
        self, result: OperationResult, queue: OperationQueue
    ) -> None:
        """Toast the result and schedule a pane auto-refresh.

        The completion toast stays as ``notify()`` - it may fire async
        long after the user's looked away (a multi-GB copy completing
        ten minutes later). The notification stack in Textual will
        queue it visibly so they catch it on their next return to the
        terminal.

        Pane auto-refresh fires unconditionally on completion (even on
        partial-success / failure), because *some* items may have
        touched disk and the user should see the new state. The
        refresh is an async task so we don't block the queue worker;
        it's wrapped so a refresh failure doesn't propagate back.
        """
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
        # Schedule pane auto-refresh outside this sync callback so the
        # queue worker can move on to the next plan without waiting.
        asyncio.create_task(self._refresh_panes_after_op())

    async def _refresh_panes_after_op(self) -> None:
        """Re-show the contents pane's current path so on-disk changes
        appear without the user pressing anything.

        Tree-pane auto-refresh is parked for v0 - the lazy-loaded
        ``_loaded`` memo makes invalidation non-trivial, and most ops
        affect entries within the displayed directory rather than the
        tree's expansion structure. When tree-pane refresh lands it
        will likely need a "which paths changed" signal from the
        planner / executor pair.
        """
        try:
            contents = self.query_one(ContentsPane)
            if contents.current_path is not None:
                await contents.show_path(contents.current_path)
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
