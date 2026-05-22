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

        # Incremental search state. ``_search_target`` is the pane that
        # had focus when ``/`` was pressed (None when search is not
        # active). ``_search_cursor_pre`` is the row/line the cursor was
        # on at activation - used to restore on Esc. ``_search_matches``
        # is the list of matching row indices, computed on every
        # QueryChanged. ``_search_match_idx`` is the index INTO that
        # list pointing to the currently-highlighted match.
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
        rejected with a notify nudge ("rename works on one entry;
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
            self.notify(
                "Rename works on one entry; clear tags first (Ctrl+U).",
                severity="warning",
                title="Rename rejected",
            )
            return

        contents = self.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        if cursor is None:
            self.notify(
                "Rename: nothing under the cursor.",
                severity="warning",
            )
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
            self.notify("Rename: cancelled.")
            return
        typed = typed.strip()
        if not typed:
            self.notify(
                "Rename: cancelled (empty name).", severity="warning"
            )
            return

        plan = await plan_rename(tag, typed, self.sources)
        # If the planner rejected (NoChange / InvalidName / etc.) we
        # surface the cause and don't enqueue. Errors are in-band data;
        # no exception was raised.
        if plan.is_empty:
            self.notify(
                "Rename: planner produced no items.", severity="warning"
            )
            return
        if plan.errors and not plan.items:
            err = plan.errors[0]
            self.notify(
                f"Rename: {err.message}",
                severity="warning",
                title=f"Rename ({err.cause})",
            )
            self.last_plan = plan
            return

        self._finalise_plan(plan, [tag], "Rename", destination_path=None)

    def action_view(self) -> None:
        """V / F3 - open the cursor entry in the built-in pager.

        Single-entry op (no Selection rule - viewing the tagged set
        makes no sense). The action validates the cursor entry's kind
        and either pushes :class:`ViewerScreen` (FILE / SYMLINK) or
        emits a notify with a hint about what to press instead
        (DIR -> Enter to navigate; OTHER -> kind name).

        Push is synchronous via ``push_screen`` - we don't wait for the
        viewer to dismiss because the action doesn't care about the
        return value. The viewer loads file bytes in its own
        ``on_mount`` so the modal frame appears immediately.
        """
        contents = self.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        if cursor is None:
            self.notify(
                "View: nothing under the cursor.",
                severity="warning",
            )
            return
        path, kind = cursor

        if kind is Kind.DIR:
            self.notify(
                "View: that's a directory. Press Enter to navigate into it.",
                severity="warning",
            )
            return

        if kind not in (Kind.FILE, Kind.SYMLINK):
            self.notify(
                f"View: cannot view a {kind.value}.",
                severity="warning",
            )
            return

        # FILE or SYMLINK - push the viewer. Symlinks are followed by
        # the underlying open() call inside ViewerScreen's load.
        self.push_screen(ViewerScreen(path))

    @work
    async def action_edit(self) -> None:
        """E / F4 - shell out to ``$VISUAL`` / ``$EDITOR`` / platform default.

        Single-entry op operating on the cursor entry, mirroring
        ``action_view``: an external editor with multiple file
        arguments is editor-specific (vim opens tabs, ``code`` opens
        windows, etc.), so v0 keeps the contract small and obvious.

        Sequence:

        1. Validate cursor: nothing under cursor / DIR / OTHER all
           emit a notify and bail without touching the terminal.
        2. Resolve the editor argv via :func:`wtree.editor.resolve_editor`.
        3. Enter ``app.suspend()`` so Textual stops driving the
           terminal; run the editor subprocess on a worker thread via
           ``asyncio.to_thread`` (keeps the event loop free for any
           background operation queue work that was already in flight).
        4. After the editor exits, re-show the current directory in
           the contents pane so any on-disk change is reflected; refresh
           the status line.

        The ``with self.suspend()`` part is factored into
        :meth:`_launch_editor_blocking` so tests can monkeypatch it -
        the headless test driver doesn't support suspend, and we don't
        want pytest accidentally spawning a real editor.
        """
        contents = self.query_one(ContentsPane)
        cursor = contents.cursor_entry()
        if cursor is None:
            self.notify(
                "Edit: nothing under the cursor.",
                severity="warning",
            )
            return
        path, kind = cursor

        if kind is Kind.DIR:
            self.notify(
                "Edit: that's a directory. Press Enter to navigate into it.",
                severity="warning",
            )
            return

        if kind not in (Kind.FILE, Kind.SYMLINK):
            self.notify(
                f"Edit: cannot edit a {kind.value}.",
                severity="warning",
            )
            return

        argv = resolve_editor()
        try:
            rc = await asyncio.to_thread(
                self._launch_editor_blocking, argv, path
            )
        except FileNotFoundError:
            self.notify(
                f"Edit: editor not found ({argv[0]!r}). "
                "Set $VISUAL or $EDITOR.",
                severity="error",
                title="Edit failed",
            )
            return
        except Exception as exc:  # noqa: BLE001 - surface any spawn error
            self.notify(
                f"Edit: {type(exc).__name__}: {exc}",
                severity="error",
                title="Edit failed",
            )
            return

        if rc != 0:
            self.notify(
                f"Edit: {argv[0]} exited with status {rc}.",
                severity="warning",
            )

        # Refresh the contents pane in case the file's metadata changed
        # (mtime, size). Match the post-write refresh strategy other
        # ops will eventually share.
        if contents.current_path is not None:
            await contents.show_path(contents.current_path)
        self._refresh_status()

    def _launch_editor_blocking(
        self, argv: Sequence[str], path: str
    ) -> int:
        """Suspend Textual, run the editor, resume; return the exit code.

        Carved out of :meth:`action_edit` so tests can monkeypatch it
        and skip the ``app.suspend()`` call (the headless driver used
        by ``run_test()`` raises :class:`SuspendNotSupported`). In
        production this is the one place that touches both the Textual
        driver and the subprocess module.
        """
        with self.suspend():
            return launch_editor_blocking(argv, path)

    @work
    async def action_make_new(self) -> None:
        """N / F7 - create a new dir or file in the pane's current dir.

        Sub-prompt flow per ``design.md`` Keymap row "Make new (dir or
        file)":

        1. Push :class:`KindChooserDialog` - the user picks D or F (or
           Esc to cancel).
        2. Push :class:`PromptDialog` for the new entry's name. The
           name may contain forward-slash separators - lenient mode,
           intermediate dirs are created on apply (2026-05-22 design
           call). Absolute paths and ``..`` segments are rejected by
           the planner.
        3. Build a Plan via :func:`plan_make_new` and enqueue it
           through :meth:`_finalise_plan`.

        Parent dir is :attr:`ContentsPane.current_path` - the directory
        the user is *looking at*. Tagged set and cursor entry are
        silently ignored: Make-new is a "create here" op, not a
        Selection-rule op (mirrors View / Edit's stance on the tagged
        set, with the additional twist that there's no per-op
        "destination" to wire through).

        The planner is the canonical source of validation - the action
        body only does the UX (chooser + prompt). Empty name / absolute
        path / ``..`` / pre-existing leaf all surface as PlanError
        through the standard ``last_plan`` + notify path; the queue is
        not asked to run an empty plan.
        """
        assert self.op_queue is not None, "op_queue constructed in on_mount"

        contents = self.query_one(ContentsPane)
        parent_path = contents.current_path
        if parent_path is None:
            self.notify(
                "Make-new: no directory under the contents pane.",
                severity="warning",
            )
            return

        kind = await self.push_screen_wait(KindChooserDialog())
        if kind is None:
            self.notify("Make-new: cancelled.")
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
            self.notify("Make-new: cancelled.")
            return
        if not typed.strip():
            self.notify(
                "Make-new: cancelled (empty name).", severity="warning"
            )
            return

        plan = await plan_make_new(
            parent_path,
            typed,
            kind,
            self._source.source_id,
            self.sources,
        )
        if plan.is_empty:
            self.notify(
                "Make-new: planner produced no items.", severity="warning"
            )
            return
        if plan.errors and not plan.items:
            err = plan.errors[0]
            self.notify(
                f"Make-new: {err.message}",
                severity="warning",
                title=f"Make-new ({err.cause})",
            )
            self.last_plan = plan
            return

        # No source tags to clear (Make-new doesn't consume the tagged
        # set) and no destination_path in the user-friendly sense (the
        # destination IS the new path itself). Pass the synthesised
        # "make X" tag list so the notify body has something to show
        # and the standard tail formatting works unchanged.
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
        """Shared body of every "plan -> destination modal -> enqueue" action.

        ``verb`` is the user-facing label ("Copy", "Move") used in the
        modal title and notification text. ``planner`` is the planner
        coroutine. ``kind`` is the :class:`OperationKind` corresponding
        to ``planner`` - only used to keep notify titles consistent
        with the plan's own ``kind.value``.
        """
        assert self.op_queue is not None, "op_queue constructed in on_mount"
        tags = self._resolve_selection_tags()
        if not tags:
            self.notify(
                f"{verb}: nothing to {verb.lower()} "
                "(no tags, no cursor entry).",
                severity="warning",
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
            self.notify(f"{verb}: cancelled.")
            return
        typed = typed.strip()
        if not typed:
            self.notify(
                f"{verb}: cancelled (empty destination).",
                severity="warning",
            )
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
        """Shared body of "plan -> yes/no confirm -> enqueue" actions.

        Mirror of :meth:`_plan_modal_enqueue` for destinationless
        operations.
        """
        assert self.op_queue is not None, "op_queue constructed in on_mount"
        tags = self._resolve_selection_tags()
        if not tags:
            self.notify(
                f"{verb}: nothing to {verb.lower()} "
                "(no tags, no cursor entry).",
                severity="warning",
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
            self.notify(f"{verb}: cancelled.")
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

        Factored out so :meth:`_plan_modal_enqueue`,
        :meth:`_plan_confirm_enqueue`, and :meth:`action_rename` don't
        duplicate the post-planner bookkeeping. ``destination_path`` is
        included in the notify body when present (Copy/Move); omitted
        for Delete/Rename.
        """
        assert self.op_queue is not None
        self.last_plan = plan

        if plan.is_empty:
            self.notify(
                f"{verb}: planner produced no items.", severity="warning"
            )
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

        Posted by :class:`TreePane` when the user presses Left while the
        cursor is on the root node. Implements the "log the directory
        above" gesture per the 2026-05-22 design conversation - XTree's
        "logged disk" model widened upward.
        """
        event.stop()
        old_root = self._root_path
        new_root = os.path.dirname(old_root)
        if not new_root or new_root == old_root:
            self.notify(
                f"Already at the filesystem root ({old_root}).",
                severity="warning",
                title="Ascend",
            )
            return

        self._root_path = new_root
        tree = self.query_one(TreePane)
        await tree.re_root(new_root)
        await tree.focus_child_of_root(old_root)
        self.notify(
            f"Logged: {new_root} (ascended from {old_root})",
            title="Ascend",
        )
        self._refresh_status()

    # ------------------------------------------------------------------
    # Incremental search (``/``) - see design.md § Modality
    # ------------------------------------------------------------------

    def action_search(self) -> None:
        """``/`` - activate the inline incremental-search bar.

        Captures the focused pane's current cursor (so Esc can restore
        it), hides the StatusLine, shows the SearchBar, and hands focus
        to the bar. The bar will post QueryChanged / NextMatch /
        PrevMatch / Committed / Cancelled messages; the rest of the
        search lifecycle lives in the on_search_bar_* handlers below.

        Search is local to whichever pane has focus. If focus isn't on
        a pane (e.g. on a modal or transient widget), search is a
        no-op with a status nudge - this should be rare in practice.
        """
        target: ContentsPane | TreePane | None = None
        if isinstance(self.focused, ContentsPane):
            target = self.focused
        elif isinstance(self.focused, TreePane):
            target = self.focused
        if target is None:
            self.notify(
                "Search: focus a pane first (Tab to switch).",
                severity="warning",
            )
            return

        self._search_target = target
        self._search_cursor_pre = target.get_search_cursor()
        self._search_matches = []
        self._search_match_idx = 0

        # Hide the status line for the duration of the search; the bar
        # takes the same screen row. On exit we flip them back.
        self.query_one(StatusLine).display = False
        bar = self.query_one(SearchBar)
        bar.activate()

    def on_search_bar_query_changed(
        self, event: SearchBar.QueryChanged
    ) -> None:
        """Recompute matches when the query string changes.

        Substring case-insensitive match against the pane's iter_searchable
        labels. The cursor jumps to the first match at or after the
        pre-search position (so typing "rep" while you were on row 5
        prefers row 7 over row 2); falls back to wrap-around to the
        first match if no at-or-after candidate exists.
        """
        if self._search_target is None:
            return
        query = event.query
        bar = self.query_one(SearchBar)

        if not query:
            # Empty query - no matches, no movement. Bar shows just "/".
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
            # Leave cursor where it is - we don't move on no-match.
            return

        # Pick the first match at-or-after the pre-search cursor; wrap
        # to the first match if none qualify.
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
        """Tear down search state and restore the regular UI.

        ``restore=True`` puts the cursor back where it was at /-press
        time (Esc semantics); ``restore=False`` leaves it where it is
        (Enter semantics). Either way, the SearchBar hides, the
        StatusLine reappears, and focus returns to the pane that owned
        the search.
        """
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
        # Return focus to the pane that owned the search so the user's
        # next keystroke goes to the pane, not the (now-hidden) bar.
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
