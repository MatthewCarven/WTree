"""``PropertiesScreen`` - the ``Ctrl+I`` inspector modal.

Per ``design.md`` Keymap: ``Ctrl+I`` opens Properties. The dialog
reads from the design's Selection rule with one extension - dir
mode is a distinct surface so directory inspections can show
recursive totals computed asynchronously.

Three modes, picked by the action handler before the screen is
constructed:

* ``"tagged"`` - the tagged set is non-empty. Renders count + a
  files/dirs/others breakdown + a total file-size sum (dirs skip,
  no recursion - the aggregate would be misleading anyway when
  tagged dirs may overlap).
* ``"file"`` - the focused pane's cursor is on a non-directory.
  Renders identity (path, basename, kind), size + mtime,
  POSIX permissions, owner + group.
* ``"dir"`` - the focused pane's cursor is on a directory. Same
  identity rows up top, then a ``(computing...)`` line that the
  screen replaces with the recursive total (size + file count +
  dir count) once the background walk finishes.

Cancellation contract (2026-05-25):

* While the walk is in flight, ``Esc`` cancels the walk but leaves
  the dialog open showing the partial result.
* A second ``Esc`` (or any ``Esc`` once the walk is done) dismisses
  the modal.
* ``Q`` always dismisses regardless of walk state.

The walk runs in :meth:`on_mount`, polling an :class:`asyncio.Event`
that the Esc handler sets to ask for cancellation. Yielding control
to the event loop after each entry keeps Textual responsive on
huge subtrees.

Empty cases ("nothing under the cursor and no tags") never reach
this screen - the action handler flashes a nudge and returns
without constructing it.
"""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from wtree._owner import lookup as _owner_lookup
from wtree.sources.base import ISO_DATE_FORMAT, Kind
from wtree.tagged_set import Tag


# ---------------------------------------------------------------------------
# Data containers (frozen - the action handler builds, the screen reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileProps:
    """Single-file inspection payload."""

    path: str
    kind: Kind


@dataclass(frozen=True, slots=True)
class DirProps:
    """Single-directory inspection payload (walked async in the screen)."""

    path: str


@dataclass(frozen=True, slots=True)
class TaggedProps:
    """Tagged-set inspection payload."""

    tags: tuple[Tag, ...]


@dataclass(frozen=True, slots=True)
class _WalkSummary:
    """Result of a recursive directory walk."""

    total_bytes: int
    file_count: int
    dir_count: int
    cancelled: bool
    errors: int = 0


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


class PropertiesScreen(ModalScreen[None]):
    """Read-only properties inspector. Ctrl+I and the File menu push this."""

    DEFAULT_CSS = """
    PropertiesScreen {
        align: center middle;
    }

    PropertiesScreen > VerticalScroll {
        background: $surface;
        border: thick $primary;
        width: 80%;
        height: 80%;
    }

    PropertiesScreen Label.header {
        background: $primary;
        color: $text;
        padding: 0 1;
        text-style: bold;
        dock: top;
    }

    PropertiesScreen Label.hint {
        background: $panel;
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        dock: bottom;
    }

    PropertiesScreen Static.body {
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "escape_pressed", "Cancel walk / Close", show=False),
        Binding("q", "dismiss_screen", "Close", show=False),
    ]

    def __init__(
        self,
        mode: str,
        *,
        file: FileProps | None = None,
        directory: DirProps | None = None,
        tagged: TaggedProps | None = None,
    ) -> None:
        super().__init__()
        if mode not in {"file", "dir", "tagged"}:
            raise ValueError(f"PropertiesScreen: bad mode {mode!r}")
        self._mode = mode
        self._file = file
        self._dir = directory
        self._tagged = tagged
        # Walk state - only used by dir mode. ``_walk_done`` flips True
        # when the walk finishes (or is cancelled). ``_cancel_event`` is
        # the request signal the walk loop polls each iteration.
        self._walk_done: bool = mode != "dir"
        self._cancel_event: asyncio.Event = asyncio.Event()

    # --- compose / mount ---------------------------------------------------

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="properties-scroll"):
            yield Label(self._header_text(), classes="header")
            yield Static(
                self._initial_body(), classes="body", id="properties-body"
            )
            yield Label(self._hint_text(), classes="hint")

    async def on_mount(self) -> None:
        """Kick off the recursive walk for dir mode."""
        if self._mode != "dir" or self._dir is None:
            return
        summary = await _walk_directory(self._dir.path, self._cancel_event)
        await self._render_dir_summary(summary)
        self._walk_done = True
        # Update hint to reflect "walk done, Esc closes".
        try:
            hint = self.query_one(".hint", Label)
        except Exception:  # noqa: BLE001 - screen torn down mid-walk
            return
        hint.update(self._hint_text())

    # --- header / hint -----------------------------------------------------

    def _header_text(self) -> str:
        if self._mode == "tagged" and self._tagged is not None:
            return f"Properties  -  {len(self._tagged.tags)} tagged item(s)"
        if self._mode == "file" and self._file is not None:
            return f"Properties  -  {self._file.path}"
        if self._mode == "dir" and self._dir is not None:
            return f"Properties  -  {self._dir.path}"
        return "Properties"

    def _hint_text(self) -> str:
        if self._mode == "dir" and not self._walk_done:
            return (
                "Esc to cancel walk  -  Q to close  -  "
                "arrow keys / PgUp PgDn to scroll"
            )
        return (
            "Esc / Q to close  -  arrow keys / PgUp PgDn to scroll"
        )

    # --- body builders -----------------------------------------------------

    def _initial_body(self) -> Text:
        if self._mode == "tagged" and self._tagged is not None:
            return _render_tagged(self._tagged)
        if self._mode == "file" and self._file is not None:
            return _render_file(self._file)
        if self._mode == "dir" and self._dir is not None:
            return _render_dir_initial(self._dir)
        return Text("(no data)")

    async def _render_dir_summary(self, summary: _WalkSummary) -> None:
        """Replace the dir-mode body with the completed walk's results."""
        if self._dir is None:
            return
        try:
            body = self.query_one("#properties-body", Static)
        except Exception:  # noqa: BLE001 - screen torn down before mount finish
            return
        body.update(_render_dir_complete(self._dir, summary))

    # --- key handlers ------------------------------------------------------

    def action_escape_pressed(self) -> None:
        """Esc - cancel an in-flight walk on first press, else dismiss.

        Per the 2026-05-25 design call: the user gets a chance to stop
        a runaway walk without losing the partial result they already
        have on screen. A second Esc closes the modal.
        """
        if self._mode == "dir" and not self._walk_done:
            self._cancel_event.set()
            return
        self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        """Q - always dismiss, regardless of walk state."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Body rendering (pure functions - tests assert on these directly)
# ---------------------------------------------------------------------------


def _render_tagged(props: TaggedProps) -> Text:
    """Tagged-set summary: count + kind breakdown + total file size.

    We stat each path to classify and sum; dirs contribute nothing to
    the size sum (recursive walking of every tagged dir would be too
    expensive and the overlap semantics are ambiguous anyway). Files
    and symlinks contribute their own size (lstat - we report what's
    in the entry, not the target).

    Paths that can't be stat'd are counted as "unreadable" so the
    user sees that the set isn't a clean inspection target.
    """
    t = Text()
    t.append("Tagged set", style="bold underline")
    t.append(f"  -  {len(props.tags)} entries\n\n")

    files = 0
    dirs = 0
    symlinks = 0
    others = 0
    unreadable = 0
    total_bytes = 0

    for tag in props.tags:
        try:
            st = os.lstat(tag.path)
        except OSError:
            unreadable += 1
            continue
        if stat.S_ISLNK(st.st_mode):
            symlinks += 1
            total_bytes += st.st_size
        elif stat.S_ISDIR(st.st_mode):
            dirs += 1
        elif stat.S_ISREG(st.st_mode):
            files += 1
            total_bytes += st.st_size
        else:
            others += 1

    _row(t, "Files", str(files))
    _row(t, "Directories", str(dirs))
    if symlinks:
        _row(t, "Symlinks", str(symlinks))
    if others:
        _row(t, "Other", str(others))
    if unreadable:
        _row(t, "Unreadable", str(unreadable))
    _row(t, "Total size", _human_bytes(total_bytes) + "  (files + symlinks)")
    return t


def _render_file(props: FileProps) -> Text:
    """Single-file body - identity, size, mtime, permissions, owner."""
    t = Text()
    t.append("File", style="bold underline")
    t.append("\n\n")
    _row(t, "Path", props.path)
    _row(t, "Name", os.path.basename(props.path.rstrip(os.sep)) or props.path)
    _row(t, "Kind", props.kind.value)

    try:
        st = os.lstat(props.path)
    except OSError as exc:
        t.append("\n")
        t.append(
            f"Could not stat: {type(exc).__name__}: {exc}\n", style="warning"
        )
        return t

    _row(t, "Size", f"{st.st_size:,} bytes  ({_human_bytes(st.st_size)})")
    _row(t, "Modified", _fmt_mtime(st.st_mtime))
    _row(t, "Permissions", _perm_string(st.st_mode))
    owner, group = _owner_lookup(st)
    _row(t, "Owner", owner)
    _row(t, "Group", group)
    return t


def _render_dir_initial(props: DirProps) -> Text:
    """Dir-mode body before the walk finishes (identity rows + placeholder)."""
    t = _render_dir_identity(props)
    t.append("\n")
    t.append("Computing recursive total...", style="italic dim")
    t.append("\n")
    return t


def _render_dir_complete(props: DirProps, summary: _WalkSummary) -> Text:
    """Dir-mode body once the walk has produced (or cancelled with) a summary."""
    t = _render_dir_identity(props)
    t.append("\n")
    label = "Recursive total"
    if summary.cancelled:
        label += "  (cancelled - partial)"
    t.append(label, style="bold underline")
    t.append("\n")
    _row(
        t,
        "Total size",
        f"{summary.total_bytes:,} bytes  ({_human_bytes(summary.total_bytes)})",
    )
    _row(t, "Files", str(summary.file_count))
    _row(t, "Subdirectories", str(summary.dir_count))
    if summary.errors:
        _row(t, "Walk errors", f"{summary.errors}  (silently skipped)")
    return t


def _render_dir_identity(props: DirProps) -> Text:
    """Identity rows shared between the pre-walk and post-walk bodies."""
    t = Text()
    t.append("Directory", style="bold underline")
    t.append("\n\n")
    _row(t, "Path", props.path)
    _row(
        t,
        "Name",
        os.path.basename(props.path.rstrip(os.sep)) or props.path,
    )
    try:
        st = os.lstat(props.path)
    except OSError as exc:
        t.append(
            f"Could not stat: {type(exc).__name__}: {exc}\n", style="warning"
        )
        return t
    _row(t, "Modified", _fmt_mtime(st.st_mtime))
    _row(t, "Permissions", _perm_string(st.st_mode))
    owner, group = _owner_lookup(st)
    _row(t, "Owner", owner)
    _row(t, "Group", group)
    return t


# ---------------------------------------------------------------------------
# Walk (sync per-iteration; runs in the event loop with periodic yields)
# ---------------------------------------------------------------------------


async def _walk_directory(
    root: str, cancel: asyncio.Event
) -> _WalkSummary:
    """Sum sizes + count files/dirs under ``root``.

    Iterative (stack-based) so a deep tree doesn't blow Python's
    recursion limit. Polls ``cancel`` once per directory visited so
    Esc-during-walk takes effect quickly. Permission errors and
    other ``OSError``s are counted but never raised - the user
    sees a partial total with an "errors" line, which is more
    useful than a refused inspection.

    Symlinks are treated as leaves (not followed) to avoid cycles
    and double-counting. The symlink itself contributes its own
    ``lstat`` size to the total, mirroring how the tagged-mode
    summary handles them.
    """
    total = 0
    files = 0
    dirs = 0
    errors = 0
    cancelled = False
    stack: list[str] = [root]

    while stack:
        if cancel.is_set():
            cancelled = True
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        errors += 1
                        continue
                    if stat.S_ISLNK(st.st_mode):
                        # Symlinks contribute their own bytes but are
                        # not recursed into.
                        files += 1
                        total += st.st_size
                    elif entry.is_dir(follow_symlinks=False):
                        dirs += 1
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        files += 1
                        total += st.st_size
                    else:
                        # Sockets, fifos, etc. - count as files for the
                        # purposes of the user-facing summary, contribute
                        # their reported size.
                        files += 1
                        total += st.st_size
        except OSError:
            errors += 1
            continue
        # Yield so Textual can paint and Esc gets a chance to land.
        await asyncio.sleep(0)

    return _WalkSummary(
        total_bytes=total,
        file_count=files,
        dir_count=dirs,
        cancelled=cancelled,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Small helpers (table row, perm string, human bytes, mtime fmt)
# ---------------------------------------------------------------------------


def _row(t: Text, label: str, value: str) -> None:
    """Append a label/value row to the Rich Text body."""
    t.append(f"  {label:<14}", style="cyan")
    t.append(f"  {value}\n")


def _perm_string(mode: int) -> str:
    """Render mode bits in the conventional ``-rwxr-xr-x`` shape.

    Cross-platform: ``os.lstat`` returns mode bits on Windows too,
    but they only encode read/write/execute approximately. Render
    them anyway - the alternative is showing nothing on Windows
    which is less useful than a rough approximation.
    """
    # stat.filemode() handles all the type prefixes (-, d, l, p, s, c, b)
    # and the rwx triples correctly. Stable across Python versions.
    return stat.filemode(mode)


def _human_bytes(n: int) -> str:
    """Compact size string - mirrors viewer + ops conventions ("12.3 KB")."""
    if n < 1024:
        return f"{n} B"
    size: float = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        size = size / 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} PB"


def _fmt_mtime(ts: float) -> str:
    """Render a POSIX timestamp in WTree's canonical date format."""
    try:
        return datetime.fromtimestamp(ts).strftime(ISO_DATE_FORMAT)
    except (OverflowError, ValueError, OSError):
        return "(invalid timestamp)"
