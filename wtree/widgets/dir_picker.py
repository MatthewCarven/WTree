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
from textual.widgets import Label, Tree
from textual.widgets.tree import TreeNode

from wtree.ops.base import resolve_relative_leaf, to_posix
from wtree.ops.execute import _make_new_blocking
from wtree.sources.base import Entry, EntrySource, Kind, ScanError
from wtree.widgets.prompt import PromptDialog
from wtree.widgets.scan_screen import SCAN_CHUNK_SIZE, ScanContext


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
        if node.id in self._loaded:
            return
        self._loaded.add(node.id)
        path = node.data
        if path is None:
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
                        self._loaded.discard(node.id)
                        return
        # Final cancel check (Esc during the last partial chunk).
        if ctx is not None and ctx.cancelled.is_set():
            self._loaded.discard(node.id)
            return
        directories.sort(key=lambda e: e.name.lower())
        for err in errors:
            node.add_leaf(f"⚠ {err.message}", data=None)
        for entry in directories:
            node.add(
                entry.name,
                data=os.path.join(path, entry.name),
                allow_expand=True,
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

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Pick a destination directory", classes="title")
            yield _PickerTree(self._source, self._start_root)
            yield Label(
                self._footer_text(self._start_root),
                classes="hint",
                id="picker-hint",
            )

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
            f"Enter pick  -  n new folder  -  Backspace/Left parent  -  "
            f"Esc cancel    [{items}]"
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
