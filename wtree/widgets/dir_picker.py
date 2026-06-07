"""``DirPickerScreen`` - modal directory browser for choosing a Copy/Move
destination.

See ``design.md`` -> User interface -> Destination browser (Copy / Move).

Reached from the Copy/Move destination prompt via Ctrl+B (the prompt
dismisses with :data:`PromptDialog.BROWSE`; the app pushes this screen). The
user navigates a **dir-only** tree, and Enter on a directory dismisses with
its path - which the app feeds back into the prompt as its prefill, so the
prompt stays the single confirm point and the picked path is still editable.
Esc dismisses with ``None`` (browse cancelled; the app re-opens the prompt
unchanged).

Rooted at the *anchor* of the current destination (``drive_anchor`` - ``/``
on POSIX, ``C:\\`` on Windows) with the cursor revealed at the current
directory, so the user starts where they are but can roam the whole drive.
Drive/share *switching* is a parked phase-2 stretch.

The picker is deliberately a *dedicated* widget rather than a reused
:class:`~wtree.widgets.tree_pane.TreePane`: it lifts TreePane's proven lazy
populate, Left/Right navigation, and ``reveal_path`` walk, but drops the
tagging gestures, tagged-set styling, and "authoritative app cursor"
coupling that a picker has no use for. (Extracting the shared dir-populate
loop so the two don't drift is a noted follow-up.)
"""

from __future__ import annotations

import asyncio
import os

from textual import events, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static, Tree
from textual.widgets.tree import TreeNode

from wtree._drives import list_drive_anchors
from wtree.ops.base import resolve_relative_leaf, to_posix
from wtree.ops.execute import _make_new_blocking
from wtree.sources.base import EntrySource, Kind, ScanError
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.scan_screen import ScanContext, populate_dir_node
from wtree.widgets.search_bar import SearchBar, compute_matches


class _PickerTree(Tree[str]):
    """Dir-only navigable tree. ``node.data`` is the absolute path (native
    separators), or ``None`` for an error-placeholder leaf.

    Ports the load-bearing bits of :class:`~wtree.widgets.tree_pane.TreePane`
    - lazy ``_populate``, Left/Right expand/collapse/drill, Backspace-to-
    parent, and ``reveal_path`` - without tagging or ascend-above-root.
    """

    BINDINGS = [
        ("backspace", "focus_parent", "Parent"),
    ]

    def __init__(self, source: EntrySource, root_path: str) -> None:
        root_path = os.path.abspath(root_path)
        super().__init__(label=root_path, data=root_path)
        self._source = source
        self._loaded: set[int] = set()
        # Files-greyed toggle (f). Consulted by _populate; flipping it
        # rebuilds via set_show_files.
        self.show_files = False
        # Filter dim-state: None = no active filter; else the node ids
        # that MATCH (everything else renders dim). Set by the screen's
        # SearchBar handlers, consulted by render_label.
        self._filter_match_ids: set[int] | None = None

    async def on_mount(self) -> None:
        await self._populate(self.root)
        self.root.expand()

    async def _populate(
        self, node: TreeNode[str], *, ctx: ScanContext | None = None
    ) -> None:
        """Scan ``node``'s path; add directory children + error leaves.

        Dir-only (files live elsewhere - you pick a directory). Idempotent
        via ``_loaded``. With a ``ctx`` (supplied by the scan-dialog gate for
        interactive expands) the loop yields every ``SCAN_CHUNK_SIZE`` entries,
        writes the running count, and polls ``ctx.cancelled`` - on cancel it
        drops the ``_loaded`` marker and returns **before** adding any
        children (atomic: a cancelled expand leaves the node empty +
        re-expandable, exactly as if it never happened). Without a ``ctx`` it
        is the legacy one-shot drain (reveal walk, tests).
        """
        # Delegates to the shared dir-populate helper (see scan_screen) so
        # the picker tree and TreePane stay in lock-step.
        await populate_dir_node(
            node, self._source, self._loaded,
            ctx=ctx, include_files=self.show_files,
        )

    async def on_tree_node_expanded(
        self, event: Tree.NodeExpanded[str]
    ) -> None:
        await self._expand_with_dialog(event.node)

    async def _expand_with_dialog(self, node: TreeNode[str]) -> None:
        """Populate ``node`` through the app's scan-dialog gate, so a slow
        directory shows a cancellable :class:`ScanScreen` instead of freezing.

        The gate (``WTreeApp._run_scan_with_dialog``) only pushes the dialog
        if the scan is still running after a short delay, so fast expands
        never flash it. Falls back to a bare populate when there's no gate on
        the app (keeps ``_PickerTree`` usable outside ``WTreeApp``). The
        reveal walk and the initial root populate deliberately stay bare -
        the cancel-UI is for *interactive* expands, not programmatic ones.
        """
        if node.data is None or node.id in self._loaded:
            return
        gate = getattr(self.app, "_run_scan_with_dialog", None)
        if gate is None:
            await self._populate(node)
            return
        await gate(
            node.data,
            self._source,
            lambda ctx: self._populate(node, ctx=ctx),
        )

    async def on_key(self, event: events.Key) -> None:
        """Left = collapse / cursor-to-parent; Right = expand / drill-in.

        No ascend-above-root (the picker is rooted at the drive anchor;
        climbing past it is the parked drive-switching feature). Up/Down and
        Enter (select) stay with Textual's ``Tree`` defaults.
        """
        node = self.cursor_node
        if event.key == "left":
            if node is None:
                return
            event.stop()
            event.prevent_default()
            if node is self.root:
                return  # nowhere to go above the anchor
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
                if node.allow_expand:
                    # expand() posts NodeExpanded -> on_tree_node_expanded ->
                    # _expand_with_dialog (gated). No inline populate here, so
                    # the slow-dir dialog gets its chance.
                    node.expand()
                return
            if node.children:
                await asyncio.sleep(0)
                self.cursor_line = node.children[0].line
            return

    def action_focus_parent(self) -> None:
        node = self.cursor_node
        if node is None or node.parent is None:
            return
        self.cursor_line = node.parent.line

    async def reveal_path(self, target: str) -> bool:
        """Expand root -> ``target`` (lazy-populating each segment) and move
        the cursor onto it. ``False`` if ``target`` is outside the root or a
        segment can't be resolved."""
        node = await self._walk_to_node(target)
        if node is None:
            return False
        self.cursor_line = node.line
        return True

    async def _walk_to_node(self, target: str) -> TreeNode[str] | None:
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
            found = None
            for child in current.children:
                if child.data == next_path:
                    found = child
                    break
            if found is None:
                return None
            current = found
            current_path = next_path
        return current

    async def repopulate(self, node: TreeNode[str]) -> None:
        """Drop + re-scan a node's children (after a folder is created)."""
        node.remove_children()
        self._loaded.discard(node.id)
        await self._populate(node)

    # ------------------------------------------------------------------
    # SearchTarget protocol + filter dim-state (picker type-to-filter)
    # ------------------------------------------------------------------
    #
    # Same three-method shape as TreePane/ContentsPane so the screen's
    # SearchBar handlers mirror the app's. Scope = visible rows only
    # (pane-`/` consistency). Line numbering counts EVERY visible row
    # (files + error leaves included - Textual numbers them) but only
    # selectable dir rows are yielded, so greyed files and error
    # placeholders never match (they carry data=None).

    def iter_searchable(self):
        yield from self._walk_visible(self.root, [0])

    def _walk_visible(self, node, line):
        if not (node is self.root and not self.show_root):
            # The root row is counted but never yielded: its label is the
            # full anchor path, which would substring-match almost any
            # query and pollute every filter. (TreePane's pane-search
            # includes its root; the picker diverges deliberately -
            # design.md 2026-06-07.)
            if node.data is not None and node is not self.root:
                yield line[0], str(node.label)
            line[0] += 1
        if node.is_expanded:
            for child in node.children:
                yield from self._walk_visible(child, line)

    def get_search_cursor(self) -> int:
        return self.cursor_line

    def set_search_cursor(self, line: int) -> None:
        self.cursor_line = line

    def set_filter_matches(self, match_lines: "set[int] | None") -> None:
        """Install the dim-state: rows NOT in ``match_lines`` render dim.

        ``None`` clears the filter (everything renders normal). Lines are
        translated to node ids here (ids are stable across repaints;
        lines shift when nodes expand - but the filter is recomputed on
        every query change, so line-time translation is safe).
        """
        if match_lines is None:
            self._filter_match_ids = None
        else:
            ids: set[int] = set()
            line = [0]
            self._collect_match_ids(self.root, line, match_lines, ids)
            self._filter_match_ids = ids
        self.refresh()

    def _collect_match_ids(self, node, line, match_lines, ids) -> None:
        if not (node is self.root and not self.show_root):
            if line[0] in match_lines:
                ids.add(node.id)
            line[0] += 1
        if node.is_expanded:
            for child in node.children:
                self._collect_match_ids(child, line, match_lines, ids)

    def render_label(self, node, base_style, style):
        """Dim non-matching rows while a filter query is active.

        The tagged-styling pattern: consult live state on every paint.
        Greyed files are pre-dimmed at populate time; this hook only
        adds filter-dimming for selectable rows outside the match set.
        """
        label = super().render_label(node, base_style, style)
        if (
            self._filter_match_ids is not None
            and node.id not in self._filter_match_ids
        ):
            label = label.copy()
            label.stylize("dim")
        return label

    async def set_show_files(self, show: bool) -> None:
        """Flip the files-greyed overlay and rebuild in place.

        Snapshot expanded dir paths + cursor path, re-root (wipes +
        repopulates with the new flag), re-expand shallowest-first,
        reveal the cursor again. Cursor-on-file falls back to the root
        row (files carry data=None - nothing to restore to).
        """
        if show == self.show_files:
            return
        self.show_files = show
        root_path = self.root.data
        if root_path is None:
            return
        expanded = [
            n.data for n in self._walk_all_nodes(self.root)
            if n.is_expanded and n.data is not None and n is not self.root
        ]
        expanded.sort(key=len)  # shallowest first
        cursor = self.cursor_node
        cursor_path = (
            cursor.data if cursor is not None and cursor.data is not None
            else None
        )
        await self.re_root(root_path)
        for path in expanded:
            node = await self._walk_to_node(path)
            if node is not None and not node.is_expanded:
                node.expand()
                await self._populate(node)
        await asyncio.sleep(0)
        if cursor_path is not None:
            await self.reveal_path(cursor_path)

    def _walk_all_nodes(self, node):
        yield node
        for child in node.children:
            yield from self._walk_all_nodes(child)

    async def re_root(self, new_root_path: str) -> None:
        """Re-root the tree in place at ``new_root_path`` (drive switch).

        Mirrors :meth:`TreePane.re_root`: wipe the subtree, reset the root
        node's label + data, clear the ``_loaded`` memo (every tracked node
        ID is gone), re-populate, re-expand. Bare populate - programmatic,
        like the initial root populate (the scan-dialog cancel-UI is for
        interactive expands).
        """
        new_root_path = os.path.abspath(new_root_path)
        self.root.remove_children()
        self.root.set_label(new_root_path)
        self.root.data = new_root_path
        self._loaded.clear()
        await self._populate(self.root)
        self.root.expand()


class DirPickerScreen(ModalScreen[str | None]):
    """Modal dir browser. Dismisses with the chosen directory path (Enter on
    a directory) or ``None`` (Esc)."""

    DEFAULT_CSS = """
    DirPickerScreen {
        align: center middle;
    }

    DirPickerScreen > Vertical {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 80%;
        max-width: 100;
        height: 80%;
        max-height: 30;
    }

    DirPickerScreen Label.title {
        margin-bottom: 1;
        text-style: bold;
    }

    DirPickerScreen _PickerTree {
        height: 1fr;
        border: round $primary-darken-2;
    }

    DirPickerScreen Label.hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("n", "make_dir", "New folder"),
        ("ctrl+d", "switch_drive", "Drives"),
        ("slash", "filter", "Filter"),
        ("f", "toggle_files", "Files"),
    ]

    def __init__(
        self,
        source: EntrySource,
        *,
        start_root: str,
        reveal_target: str | None = None,
        tagged_count: int = 0,
    ) -> None:
        super().__init__()
        self._source = source
        self._start_root = start_root
        self._reveal_target = reveal_target
        self._tagged_count = tagged_count
        # Per-location cursor memory, session-lifetime. Keyed by *root
        # path*, not splitdrive anchor - on POSIX every path's splitdrive
        # anchor is "/", which would collapse ~ and /mnt/usb into one key
        # (design.md 2026-06-07). Dies with the modal.
        self._per_root_cursor: dict[str, str] = {}
        # Type-to-filter state (mirrors WTreeApp's pane-search trio).
        self._search_cursor_pre: int | None = None
        self._search_matches: list[int] = []
        self._search_match_idx = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Pick a destination directory", classes="title")
            yield _PickerTree(self._source, self._start_root)
            yield Label(
                self._footer_text(self._start_root),
                classes="hint",
                id="picker-hint",
            )
            yield SearchBar(id="picker-search")

    async def on_mount(self) -> None:
        tree = self.query_one(_PickerTree)
        tree.focus()
        if self._reveal_target:
            await tree.reveal_path(self._reveal_target)
        self._refresh_footer()

    # -- rendering ----------------------------------------------------

    def _footer_text(self, target: str) -> str:
        n = self._tagged_count
        items = f"{n} tagged item(s)" if n else "selection"
        return (
            f"-> {target}\n"
            f"Enter pick  -  n new folder  -  / filter  -  f files  -  "
            f"Ctrl+D drives  -  Left parent  -  Esc cancel    [{items}]"
        )

    def _refresh_footer(self) -> None:
        tree = self.query_one(_PickerTree)
        node = tree.cursor_node
        target = (
            node.data if (node is not None and node.data is not None)
            else self._start_root
        )
        self.query_one("#picker-hint", Label).update(self._footer_text(target))

    def on_tree_node_highlighted(
        self, event: Tree.NodeHighlighted[str]
    ) -> None:
        self._refresh_footer()

    # -- actions ------------------------------------------------------

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        # Enter (or click) on a directory chooses it. Error-placeholder
        # leaves carry no path and are ignored.
        if event.node.data is not None:
            self.dismiss(event.node.data)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @work
    async def action_make_dir(self) -> None:
        """``n``: create a folder under the cursor directory and select it.

        Name validated by the shared
        :func:`~wtree.ops.base.resolve_relative_leaf` (a relative subpath is
        allowed; rejects absolute / ``..`` / empty), then verify-free: an
        existing target re-prompts with the reason on the hint line. The
        folder is created directly (the executor's make-new primitive, which
        makes intermediate dirs) - destination setup, not an enqueued op -
        then the parent is re-scanned and the new directory revealed +
        selected so it can be picked immediately.
        """
        tree = self.query_one(_PickerTree)
        node = tree.cursor_node
        if node is None or node.data is None:
            return
        parent = node.data
        parent_posix = to_posix(parent)
        prefill = ""
        hint = "Enter to create  -  Esc to cancel"
        while True:
            typed = await self.app.push_screen_wait(
                PromptDialog(
                    title=f"New folder under {parent}:",
                    initial=prefill,
                    placeholder="folder name (relative subpath allowed)",
                    hint=hint,
                )
            )
            if typed is None:
                return
            leaf_posix, err = resolve_relative_leaf(parent_posix, typed)
            if err is not None:
                prefill, hint = typed, f"! {err}"
                continue
            # Native leaf for the filesystem op + tree reveal.
            sep = parent_posix if parent_posix.endswith("/") else (
                parent_posix + "/"
            )
            rel = (
                leaf_posix[len(sep):] if leaf_posix.startswith(sep)
                else leaf_posix
            )
            segments = [s for s in rel.split("/") if s]
            native_leaf = os.path.join(parent, *segments)
            existing = await self._source.entry_at(leaf_posix)
            if not isinstance(existing, ScanError):
                prefill, hint = typed, f"! already exists: {rel}"
                continue
            try:
                await asyncio.to_thread(
                    _make_new_blocking, native_leaf, Kind.DIR
                )
            except OSError as exc:
                prefill, hint = typed, f"! could not create: {exc}"
                continue
            if not node.is_expanded:
                node.expand()
            await tree.repopulate(node)
            await asyncio.sleep(0)
            await tree.reveal_path(native_leaf)
            self._refresh_footer()
            return
    @work
    async def action_switch_drive(self) -> None:
        """``Ctrl+D``: pick a drive / location anchor and re-root the picker.

        Pushes :class:`DriveChooserScreen`; Enter re-roots the tree at the
        chosen anchor, Esc returns unchanged. The cursor position on the
        outgoing root is remembered (``_per_root_cursor``) and restored via
        ``reveal_path`` when the user switches back; first visit lands at
        the location root. Same-root pick is a no-op.
        """
        tree = self.query_one(_PickerTree)
        current_root = tree.root.data
        if current_root is None:
            return
        anchors = list_drive_anchors(current=current_root)
        picked = await self.app.push_screen_wait(
            DriveChooserScreen(anchors, current=current_root)
        )
        if picked is None or picked == current_root:
            return
        node = tree.cursor_node
        if node is not None and node.data is not None:
            self._per_root_cursor[current_root] = node.data
        await tree.re_root(picked)
        self._start_root = picked
        remembered = self._per_root_cursor.get(picked)
        if remembered is not None:
            await tree.reveal_path(remembered)
        self._refresh_footer()


    # -- type-to-filter (design.md 2026-06-07) --------------------------

    def action_filter(self) -> None:
        """``/``: activate the picker's SearchBar (jump + dim filter)."""
        tree = self.query_one(_PickerTree)
        self._search_cursor_pre = tree.get_search_cursor()
        self._search_matches = []
        self._search_match_idx = 0
        self.query_one("#picker-search", SearchBar).activate()

    def on_search_bar_query_changed(
        self, event: SearchBar.QueryChanged
    ) -> None:
        event.stop()
        tree = self.query_one(_PickerTree)
        bar = self.query_one("#picker-search", SearchBar)
        if not event.query:
            self._search_matches = []
            self._search_match_idx = 0
            tree.set_filter_matches(None)
            bar.update_match_info(0, 0)
            return
        matches, idx = compute_matches(
            tree.iter_searchable(),
            event.query,
            anchor=self._search_cursor_pre or 0,
        )
        self._search_matches = matches
        tree.set_filter_matches(set(matches))
        if not matches:
            self._search_match_idx = 0
            bar.update_match_info(0, 0)
            return
        self._search_match_idx = idx
        tree.set_search_cursor(matches[idx])
        bar.update_match_info(len(matches), idx + 1)

    def on_search_bar_next_match(self, event: SearchBar.NextMatch) -> None:
        event.stop()
        self._step_match(1)

    def on_search_bar_prev_match(self, event: SearchBar.PrevMatch) -> None:
        event.stop()
        self._step_match(-1)

    def _step_match(self, direction: int) -> None:
        if not self._search_matches:
            return
        tree = self.query_one(_PickerTree)
        n = len(self._search_matches)
        self._search_match_idx = (self._search_match_idx + direction) % n
        tree.set_search_cursor(self._search_matches[self._search_match_idx])
        self.query_one("#picker-search", SearchBar).update_match_info(
            n, self._search_match_idx + 1
        )

    def on_search_bar_committed(self, event: SearchBar.Committed) -> None:
        event.stop()
        self._exit_filter(restore=False)

    def on_search_bar_cancelled(self, event: SearchBar.Cancelled) -> None:
        event.stop()
        self._exit_filter(restore=True)

    def _exit_filter(self, *, restore: bool) -> None:
        """Hide the bar, clear the dim-state, return focus to the tree.

        ``restore=True`` (Esc) puts the cursor back where it was when
        ``/`` was pressed; commit (Enter) leaves it on the match - the
        pane-search contract.
        """
        tree = self.query_one(_PickerTree)
        self.query_one("#picker-search", SearchBar).deactivate()
        tree.set_filter_matches(None)
        if restore and self._search_cursor_pre is not None:
            tree.set_search_cursor(self._search_cursor_pre)
        self._search_cursor_pre = None
        self._search_matches = []
        self._search_match_idx = 0
        tree.focus()
        self._refresh_footer()

    # -- files-greyed toggle (design.md 2026-06-07) ----------------------

    @work
    async def action_toggle_files(self) -> None:
        """``f``: overlay / hide dim non-selectable files for context."""
        tree = self.query_one(_PickerTree)
        await tree.set_show_files(not tree.show_files)
        self._refresh_footer()


class DriveChooserScreen(ModalScreen[str | None]):
    """Small modal listing drive / location anchors (``Ctrl+D`` from the
    destination browser). Up/Down move, Enter dismisses with the anchor,
    Esc dismisses with ``None``.

    Same minimal-modal shape as ``KindChooserDialog``: a Static body
    rendered from a cursor index; no Tree, no lazy anything - the anchor
    list is tiny and already enumerated.
    """

    DEFAULT_CSS = """
    DriveChooserScreen {
        align: center middle;
    }

    DriveChooserScreen > Vertical {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: auto;
        min-width: 40;
        max-width: 80;
        height: auto;
        max-height: 20;
    }

    DriveChooserScreen Label.title {
        margin-bottom: 1;
        text-style: bold;
    }

    DriveChooserScreen Label.hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("up", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("enter", "choose", "Switch"),
    ]

    def __init__(self, anchors: list[str], *, current: str | None = None) -> None:
        super().__init__()
        self._anchors = anchors
        self._cursor = (
            anchors.index(current) if current in anchors else 0
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Switch drive / location", classes="title")
            yield Static(self._body_text(), id="drive-list")
            yield Label("Enter switch  -  Esc cancel", classes="hint")

    def _body_text(self) -> str:
        lines = []
        for i, anchor in enumerate(self._anchors):
            marker = ">" if i == self._cursor else " "
            lines.append(f"{marker} {anchor}")
        return "\n".join(lines)

    def _refresh_list(self) -> None:
        self.query_one("#drive-list", Static).update(self._body_text())

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh_list()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._anchors) - 1:
            self._cursor += 1
            self._refresh_list()

    def action_choose(self) -> None:
        self.dismiss(self._anchors[self._cursor])

    def action_cancel(self) -> None:
        self.dismiss(None)
