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
+ size + mtime.

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
"""

from __future__ import annotations

import os
import posixpath
from collections.abc import Awaitable, Callable, Mapping, Sequence

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Header, Tree

from wtree import __version__
from wtree.ops import (
    ItemResult,
    OperationKind,
    OperationQueue,
    OperationResult,
    Plan,
    plan_copy,
    plan_delete,
    plan_move,
    plan_rename,
)
from wtree.sources.base import EntrySource
from wtree.sources.native import NativeSource
from wtree.tagged_set import Tag, TaggedSet
from wtree.widgets.confirm import ConfirmDialog
from wtree.widgets.contents_pane import ContentsPane
from wtree.widgets.keybar import KeyBar
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.status_line import StatusLine
from wtree.widgets.tree_pane import TreePane


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

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield TreePane(self._source, self._root_path, id="tree-pane")
            yield ContentsPane(
                self._source, self.tagged_set, id="contents-pane"
            )
        yield StatusLine()
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
