"""``ViewerScreen`` - built-in text pager for ``V`` / F3.

Per ``design.md`` Keymap: ``V`` / F3 is the built-in pager. Read-only,
modal, opens on the cursor entry. Operates on a single file (no
Selection rule - viewing a tagged set doesn't make sense).

What v0 handles:

* **Text files** decoded as UTF-8, with a latin-1 fallback that decodes
  any byte sequence (so the viewer never crashes on funny encodings).
* **Binary refusal:** scan the first 8 KB for NUL bytes; if found,
  show a polite refusal in the viewer body rather than dumping
  garbage to the terminal.
* **Size limit:** refuse files larger than ``MAX_BYTES`` (10 MB) with a
  "too big" message and a nudge toward ``$PAGER``.
* **Symlinks** are followed - the viewer shows the target's contents.
* **Directories / other kinds** never reach the viewer; the action
  layer rejects them before pushing this screen.

What stays parking-lot for now:

* In-viewer incremental search (``/``).
* Syntax highlighting (would require Textual's ``TextArea``).
* Line-number column.
* Streaming paged read for huge files - v0 reads the whole file
  into memory after the size guard passes.
* Hex mode for binary files.

Modal contract:

* ``Esc`` or ``Q`` dismisses with ``None``.
* Scrolling is handled by the ``VerticalScroll`` container - arrow
  keys, PgUp/PgDn, Home/End all just work.
* The viewer never mutates the file or the underlying source.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static


# Hard ceiling - reads above this size are refused. 10 MB is generous
# for a text file; anything bigger probably wants the user's $PAGER.
MAX_BYTES: int = 10 * 1024 * 1024

# How many bytes to peek at for the binary-detection heuristic.
_PEEK_BYTES: int = 8 * 1024


@dataclass(frozen=True, slots=True)
class _LoadResult:
    """Outcome of :func:`_load_file_sync` - text or refusal reason.

    Exactly one of ``text`` / ``refusal`` is non-empty. ``encoding`` is
    populated on success so the header can show the user how the
    bytes were decoded; for refusals it's empty.
    """

    text: str = ""
    refusal: str = ""
    encoding: str = ""
    byte_size: int = 0


class ViewerScreen(ModalScreen[None]):
    """Read-only pager modal.

    Pushed via ``await self.app.push_screen(ViewerScreen(path))`` from
    :meth:`~wtree.app.WTreeApp.action_view`. The screen owns its own
    async file load in ``on_mount`` so the action handler can return
    immediately and the user sees the modal frame before bytes arrive.
    """

    DEFAULT_CSS = """
    ViewerScreen {
        align: center middle;
    }

    ViewerScreen > VerticalScroll {
        background: $surface;
        border: thick $primary;
        width: 90%;
        height: 90%;
    }

    ViewerScreen Label.header {
        background: $primary;
        color: $text;
        padding: 0 1;
        text-style: bold;
        dock: top;
    }

    ViewerScreen Label.hint {
        background: $panel;
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        dock: bottom;
    }

    ViewerScreen Static.body {
        padding: 0 1;
    }

    ViewerScreen Static.refusal {
        padding: 1 2;
        color: $warning;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("q", "dismiss_screen", "Close"),
    ]

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path
        self._loaded: Optional[_LoadResult] = None

    def compose(self) -> ComposeResult:
        # Outer container - dock the header at top, hint at bottom, with
        # the scrollable body in between.
        with VerticalScroll(id="viewer-scroll"):
            yield Label(f"Loading: {self._path}", classes="header")
            yield Static("", classes="body", id="viewer-body")
            yield Label(
                "Esc / Q to close   -   arrow keys / PgUp PgDn / Home End to scroll",
                classes="hint",
            )

    async def on_mount(self) -> None:
        # Run the file load off the event loop. Even a 5 MB read
        # blocks for a non-trivial slice on a slow disk.
        result = await asyncio.to_thread(_load_file_sync, self._path)
        self._loaded = result
        await self._render_load_result(result)

    async def _render_load_result(self, result: _LoadResult) -> None:
        """Populate the body and update the header with the load result."""
        try:
            header = self.query_one(".header", Label)
            body = self.query_one("#viewer-body", Static)
        except Exception:  # noqa: BLE001 - screen torn down before mount finished
            return

        if result.refusal:
            header.update(f"{self._path}  -  {result.refusal.splitlines()[0]}")
            body.update(result.refusal)
            body.add_class("refusal")
            return

        # Successful load.
        size_str = _human_bytes(result.byte_size)
        header.update(
            f"{self._path}  -  {size_str}  -  {result.encoding}"
        )
        body.update(result.text)

    def action_dismiss_screen(self) -> None:
        """Esc or Q - close the viewer."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# File loading (sync; called from asyncio.to_thread)
# ---------------------------------------------------------------------------


def _load_file_sync(path: str) -> _LoadResult:
    """Load ``path`` for viewing - returns text or a refusal reason.

    Heuristics in order:

    1. ``os.stat`` failure -> refusal ("could not stat: ...").
    2. Size > :data:`MAX_BYTES` -> refusal ("too large").
    3. Peek first 8 KB for NUL bytes -> binary refusal.
    4. Read full bytes; decode as UTF-8.
    5. UnicodeDecodeError -> fall back to latin-1 (always succeeds; a
       full 256-byte mapping has no invalid sequences).

    The function never raises - every failure mode becomes a refusal
    string that the viewer displays in place of file content.
    """
    try:
        st = os.stat(path)
    except OSError as exc:
        return _LoadResult(
            refusal=f"Could not stat file: {type(exc).__name__}: {exc}"
        )

    size = st.st_size
    if size > MAX_BYTES:
        return _LoadResult(
            byte_size=size,
            refusal=(
                f"File is {_human_bytes(size)}, larger than the viewer's "
                f"{_human_bytes(MAX_BYTES)} limit.\n\n"
                "Use $PAGER (e.g. less, more) externally to view this file."
            ),
        )

    # Peek for binary content. We open in binary mode so we can sniff
    # bytes without decoding tripping up first.
    try:
        with open(path, "rb") as fh:
            peek = fh.read(_PEEK_BYTES)
    except OSError as exc:
        return _LoadResult(
            byte_size=size,
            refusal=f"Could not read file: {type(exc).__name__}: {exc}",
        )

    if b"\x00" in peek:
        return _LoadResult(
            byte_size=size,
            refusal=(
                "This file looks binary (contains NUL bytes).\n\n"
                "The built-in viewer is text-only; a hex view is "
                "post-v0 work. Open externally if you need to inspect "
                "the bytes."
            ),
        )

    # Read the full bytes - the size check above guarantees this is bounded.
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return _LoadResult(
            byte_size=size,
            refusal=f"Could not read file: {type(exc).__name__}: {exc}",
        )

    # Try UTF-8 first; fall back to latin-1 which has a total decoding.
    encoding = "utf-8"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "latin-1 (fallback)"
        text = data.decode("latin-1")

    return _LoadResult(text=text, encoding=encoding, byte_size=size)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _human_bytes(n: int) -> str:
    """Compact size string - mirrors :func:`wtree.ops.base._human_bytes`.

    Duplicated rather than imported to avoid cross-package coupling
    between widgets and ops. Same output format ("12.3 KB").
    """
    if n < 1024:
        return f"{n} B"
    size: float = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        size = size / 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} PB"
