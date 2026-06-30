"""``ViewerScreen`` - built-in text pager for ``V`` / F3.

Per ``design.md`` Keymap: ``V`` / F3 is the built-in pager. Read-only,
modal, opens on the cursor entry. Operates on a single file (no
Selection rule - viewing a tagged set doesn't make sense).

What the viewer handles:

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

Incremental search (``/``, design.md 2026-06-10):

* ``/`` opens the in-viewer :class:`~wtree.widgets.search_bar.SearchBar`
  (same widget the panes and the destination picker use). Substring,
  case-insensitive - the rule shared with every other ``/`` surface.
* As the query is typed the viewer jumps to the first match at or after
  the line that was on screen when ``/`` was pressed (wrapping), styles
  every match, and brightens the current one.
* While the bar is open, Down / Ctrl+G step to the next match and Up to
  the previous (wrap). Enter commits - the bar closes but the highlights
  and position stay, and ``n`` / ``N`` step forward / back pager-style.
* Esc abandons an open search (restores the pre-search scroll position);
  with a *committed* search, the first Esc clears the highlights and the
  second Esc - or ``q`` at any time - closes the viewer.
* Search is available only once a text load has succeeded; on a refusal
  body (binary / too-large / unreadable) ``/`` is a no-op.

What stays parking-lot for now:

* Syntax highlighting (would require Textual's ``TextArea``).
* Line-number column.
* Streaming paged read for huge files - the viewer reads the whole file
  into memory after the size guard passes.
* Hex mode for binary files.

Modal contract:

* ``Esc`` (no committed search) or ``Q`` dismisses with ``None``.
* Scrolling is handled by the ``VerticalScroll`` container - arrow
  keys, PgUp/PgDn, Home/End all just work.
* The viewer never mutates the file or the underlying source.
"""

from __future__ import annotations

import asyncio
import errno
import os
import re
from dataclasses import dataclass
from typing import Optional

from rich.syntax import Syntax
from rich.text import Text

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from wtree.widgets.search_bar import SearchBar


# Hard ceiling - reads above this size are refused. 10 MB is generous
# for a text file; anything bigger probably wants the user's $PAGER.
MAX_BYTES: int = 4 * 1024 * 1024  # default per-page / initial read (4 MiB)

#: Env override for the per-page read size. A file at or under this loads
#: fully on open; a larger one loads the first page and "m" pulls the next.
VIEW_MAX_BYTES_ENV = "WTREE_VIEW_MAX_BYTES"


def _max_bytes() -> int:
    """Per-page byte budget, from :data:`VIEW_MAX_BYTES_ENV` or the default.

    A bad / non-positive value falls back to :data:`MAX_BYTES` rather than
    breaking the viewer (config must never make a file unviewable).
    """
    raw = os.environ.get(VIEW_MAX_BYTES_ENV, "").strip()
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return MAX_BYTES

# How many bytes to peek at for the binary-detection heuristic.
_PEEK_BYTES: int = 8 * 1024

# Syntax highlighting (2026-06-30). Pygments ships with Rich, so this adds no
# new dependency. Files at or below HIGHLIGHT_MAX_BYTES highlight on open;
# larger files default to plain (the `h` key forces highlighting either way).
HIGHLIGHT_MAX_BYTES: int = 512 * 1024
_SYNTAX_THEME: str = "ansi_dark"   # 16-colour, respects the terminal palette
_GUTTER_STYLE: str = "dim"
_GUTTER_SEP: str = " \u2502 "       # " | " between the line number and content

# Byte-order marks recognised as text *before* the NUL binary heuristic
# (UTF-16 is mostly NULs for ASCII and would otherwise be refused as binary).
_BOM_UTF8 = b"\xef\xbb\xbf"
_BOM_UTF16_LE = b"\xff\xfe"
_BOM_UTF16_BE = b"\xfe\xff"

# Search-highlight styles. Provisional palette - a future theme pass can
# swap these. Every match shares ``_MATCH_STYLE``; the current match (the
# one ``n`` / ``N`` is sitting on) gets ``_CURRENT_MATCH_STYLE`` so it
# stands out from the rest.
_MATCH_STYLE: str = "black on yellow"
_CURRENT_MATCH_STYLE: str = "black on cyan"

# Lines of context to keep above the current match when scrolling it into
# view, so the match doesn't sit jammed against the top edge.
_SCROLL_CONTEXT: int = 2

_HINT_DEFAULT: str = (
    "Esc / Q close   -   arrows / PgUp PgDn / Home End scroll   -   "
    "/ search   -   h highlight   -   m more   -   x hex"
)
_HINT_SEARCH: str = (
    "n / N next / prev match   -   Esc clear search   -   Q close"
)


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
    byte_size: int = 0      # total file size
    lexer: str = ""
    loaded_bytes: int = 0   # bytes actually read (<= byte_size)
    truncated: bool = False # byte_size > loaded_bytes (more to load)
    is_binary: bool = False # NUL bytes seen -> default to hex view
    data: bytes = b""       # the loaded raw bytes (for hex mode)


@dataclass(frozen=True, slots=True)
class _Match:
    """One substring match.

    ``line`` is the 0-based line index (for scroll-into-view);
    ``start`` / ``end`` are absolute character offsets into the full
    text (for Rich ``Text.stylize``).
    """

    line: int
    start: int
    end: int


def find_matches(text: str, query: str) -> "list[_Match]":
    """All case-insensitive substring matches of ``query`` in ``text``.

    Same substring-CI *rule* as
    :func:`wtree.widgets.search_bar.compute_matches` (the pane / picker
    filter) - kept in spirit so the ``/``-search surfaces don't drift -
    but the viewer needs every occurrence as an absolute ``(start, end)``
    span (a single line can hold several matches) plus the line number
    for scroll-into-view, which the row-index model doesn't carry.

    ``re.finditer`` with ``re.IGNORECASE`` matches against the *original*
    string, so the offsets stay valid even for text whose case-fold would
    change length (``str.lower()`` can desync offsets - e.g. a few Unicode
    code points lower to two characters). Matches are non-overlapping, in
    document order. An empty query yields no matches.
    """
    if not query:
        return []
    out: "list[_Match]" = []
    line = 0
    scanned = 0
    for m in re.finditer(re.escape(query), text, re.IGNORECASE):
        i = m.start()
        # Count only the newlines between the previous match and this one.
        # ``str.count`` is C-level, so this stays cheap even on big files.
        line += text.count("\n", scanned, i)
        scanned = i
        out.append(_Match(line=line, start=i, end=m.end()))
    return out


def _detect_bom(peek: bytes) -> Optional[str]:
    """Codec implied by a leading byte-order mark, or ``None``.

    Checked before the NUL binary heuristic (a UTF-16 file is mostly NUL
    bytes for ASCII and would otherwise be refused as binary). The UTF-8 BOM
    maps to ``utf-8-sig`` (which strips it); a UTF-16 BOM maps to ``utf-16``
    (whose decoder reads the BOM to choose LE / BE).
    """
    if peek.startswith(_BOM_UTF8):
        return "utf-8-sig"
    if peek.startswith(_BOM_UTF16_LE) or peek.startswith(_BOM_UTF16_BE):
        return "utf-16"
    return None


def line_start_offsets(text: str) -> "list[int]":
    """Absolute character offset where each ``text.split('\n')`` line begins.
    Maps an absolute match offset to a line-relative column for the overlay."""
    offsets: "list[int]" = []
    pos = 0
    for line in text.split("\n"):
        offsets.append(pos)
        pos += len(line) + 1  # + 1 for the newline split consumed
    return offsets


def highlight_lines(text: str, lexer: str, *, enabled: bool) -> "list[Text]":
    """Per-line Rich ``Text`` for the body, syntax-highlighted when enabled.

    ``text.split('\n')`` is the line model everything else uses (matches,
    gutter, scroll-to-line). When ``enabled`` and ``lexer`` is a real lexer
    (not the plain ``text`` / ``default`` fallback), the whole document is
    highlighted once via Rich's ``Syntax`` (Pygments) and split back into line
    ``Text`` objects with styles intact; otherwise each line is plain. Any
    mismatch between the highlighted and plain splits (a trailing-newline
    artifact, a line whose plain text drifted) falls back to plain for that
    line so the gutter / match / scroll maths stay exact.
    """
    plain_lines = text.split("\n")
    if not enabled or lexer in ("", "text", "default"):
        return [Text(line) for line in plain_lines]
    try:
        highlighted = Syntax(text, lexer, theme=_SYNTAX_THEME).highlight(text)
    except Exception:  # noqa: BLE001 - highlighting must never break the view
        return [Text(line) for line in plain_lines]
    # Pygments commonly appends a trailing newline; trim it so the styled
    # Text lines up character-for-character with the source. If the content
    # drifted any other way, fall back to plain so the per-line slice (and the
    # match overlay that rides on it) can't desync.
    if highlighted.plain == text + "\n":
        highlighted = highlighted[: len(text)]
    if highlighted.plain != text:
        return [Text(line) for line in plain_lines]
    # ``Text.split`` is O(lines + spans); per-line slicing would be
    # O(lines x spans) - catastrophic on big files. Rich drops the trailing
    # empty segment that ``str.split`` keeps, so pad to reconcile, then a
    # per-line plain-equality guard catches any residual drift.
    hl_lines = list(highlighted.split("\n"))
    if len(hl_lines) < len(plain_lines):
        hl_lines += [Text("")] * (len(plain_lines) - len(hl_lines))
    if len(hl_lines) != len(plain_lines):
        return [Text(line) for line in plain_lines]
    return [
        hl if hl.plain == pl else Text(pl)
        for pl, hl in zip(plain_lines, hl_lines)
    ]


def gutter_width(n_lines: int) -> int:
    """Column width for the line-number gutter (digits of the last line)."""
    return len(str(max(1, n_lines)))


_HEX_BYTES_PER_ROW = 16


def hex_lines(data: bytes, *, base: int = 0) -> "list[Text]":
    """Classic hex dump: ``OFFSET  HH HH ... HH  |ascii|``, 16 bytes/row.

    Pure (testable). ``base`` is the byte offset of ``data[0]`` in the file
    (0 for the first page). Non-printable bytes render as ``.`` in the ASCII
    gutter. Returns one ``Text`` per row; the offset column is the row's own
    gutter, so the body renders these WITHOUT the line-number gutter.
    """
    out: "list[Text]" = []
    for i in range(0, len(data), _HEX_BYTES_PER_ROW):
        chunk = data[i : i + _HEX_BYTES_PER_ROW]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        # Pad the hex column so the ASCII gutter lines up on short last rows.
        hexpart = f"{hexpart:<{_HEX_BYTES_PER_ROW * 3 - 1}}"
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(Text(f"{base + i:08x}  {hexpart}  |{ascii_part}|"))
    return out


def _stat_refusal(path: str, exc: OSError) -> str:
    """Friendlier message for a failed ``os.stat`` on a symlink.

    A dangling link (``ENOENT``) names the missing target; a symlink loop
    (``ELOOP``) says so plainly. Everything else keeps the generic message.
    """
    if os.path.islink(path):
        try:
            target = os.readlink(path)
        except OSError:
            target = "?"
        if getattr(exc, "errno", None) == errno.ELOOP:
            return f"Symlink loop: {path} does not resolve to a real file."
        if isinstance(exc, FileNotFoundError):
            return f"Symlink target missing: {target}"
    return f"Could not stat file: {type(exc).__name__}: {exc}"


def render_body(
    lines: "list[Text]",
    line_starts: "list[int]",
    matches: "list[_Match]",
    match_idx: int,
    *,
    gutter_w: int,
    gutter: bool = True,
) -> "Text":
    """Assemble the body ``Text`` - ``<gutter> <line>`` per row, with the
    search-match spans overlaid using line-relative columns (so the gutter
    width never enters the offset maths)."""
    by_line: "dict[int, list[tuple[int, _Match]]]" = {}
    for k, m in enumerate(matches):
        by_line.setdefault(m.line, []).append((k, m))
    body = Text()
    n = len(lines)
    for i, line_text in enumerate(lines):
        if gutter:
            body.append(f"{i + 1:>{gutter_w}}", style=_GUTTER_STYLE)
            body.append(_GUTTER_SEP, style=_GUTTER_STYLE)
        lt = line_text.copy()
        if i in by_line:
            base = line_starts[i] if i < len(line_starts) else 0
            length = len(lt.plain)
            for k, m in by_line[i]:
                col_s = max(0, m.start - base)
                col_e = min(length, m.end - base)
                if col_e > col_s:
                    style = (
                        _CURRENT_MATCH_STYLE if k == match_idx else _MATCH_STYLE
                    )
                    lt.stylize(style, col_s, col_e)
        body.append(lt)
        if i < n - 1:
            body.append("\n")
    return body


class ViewerScreen(ModalScreen[None]):
    """Read-only pager modal.

    Pushed via ``await self.app.push_screen(ViewerScreen(path))`` from
    :meth:`~wtree.app.WTreeApp.action_view`. The screen owns its own
    async file load in ``on_mount`` so the action handler can return
    immediately and the user sees the modal frame before bytes arrive.

    Incremental ``/`` search is hosted on the screen itself (the modal
    composes its own :class:`SearchBar`, mirroring the destination
    picker). The five ``on_search_bar_*`` handlers are each ``stop()``ed
    so they never bubble to the app's pane-search handlers behind the
    modal.
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
        Binding("escape", "escape", "Close"),
        Binding("q", "dismiss_screen", "Close"),
        Binding("slash", "search", "Search"),
        Binding("n", "next_match", "Next match"),
        Binding("N", "prev_match", "Prev match"),
        Binding("h", "toggle_highlight", "Highlight"),
        Binding("m", "load_more", "Load more"),
        Binding("x", "toggle_hex", "Hex view"),
    ]

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path
        self._loaded: Optional[_LoadResult] = None
        # Search state. ``_matches`` is the live match list; ``_match_idx``
        # the current one. ``_committed`` is True once Enter has been
        # pressed with matches - that's when ``n`` / ``N`` go live and the
        # first Esc clears (rather than dismisses). ``_scroll_pre`` is the
        # top visible line captured at ``/``-press: the search anchor and
        # the position Esc restores.
        self._matches: "list[_Match]" = []
        self._match_idx: int = 0
        self._committed: bool = False
        self._scroll_pre: int = 0
        # Body-render cache (2026-06-30). ``_disp_lines`` are the per-line
        # Texts (syntax-highlighted or plain), recomputed only when the file
        # loads or highlighting is toggled; the cheap per-keystroke match
        # overlay + gutter is rebuilt from them in ``_apply_highlights``.
        self._highlight_on: bool = True
        self._disp_lines: "list[Text]" = []
        self._line_starts: "list[int]" = []
        self._gutter_w: int = 1
        # Paging + view-mode state (2026-06-30). ``_limit`` is the byte budget
        # read so far; ``m`` grows it. ``_render_mode`` is "text" or "hex".
        self._limit: int = MAX_BYTES
        self._render_mode: str = "text"

    def compose(self) -> ComposeResult:
        # Outer container - dock the header at top, hint at bottom, with
        # the scrollable body in between. The SearchBar shares the bottom
        # dock slot with the hint (only one is visible at a time).
        with VerticalScroll(id="viewer-scroll"):
            yield Label(f"Loading: {self._path}", classes="header")
            yield Static("", classes="body", id="viewer-body")
            yield Label(_HINT_DEFAULT, classes="hint", id="viewer-hint")
            yield SearchBar(id="viewer-search")

    async def on_mount(self) -> None:
        self._limit = _max_bytes()
        await self._load()
        # Focus the scroll container so arrow / PgUp scrolling and the
        # screen bindings all reach the viewer.
        try:
            self.query_one("#viewer-scroll", VerticalScroll).focus()
        except Exception:  # noqa: BLE001 - torn down before mount finished
            pass

    async def _load(self) -> None:
        """(Re)load up to ``self._limit`` bytes off the event loop, then
        render. A binary file defaults to the hex view."""
        result = await asyncio.to_thread(
            _load_file_sync, self._path, limit=self._limit
        )
        self._loaded = result
        if not result.refusal:
            self._render_mode = "hex" if result.is_binary else "text"
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

        # Successful load. Highlight by default only up to the size cap;
        # bigger files open plain and the user can force it with ``h``.
        self._highlight_on = result.loaded_bytes <= HIGHLIGHT_MAX_BYTES
        self._prepare_body()
        self._update_header()
        # Single rendering path (with no matches yet) - the body is a literal
        # Rich ``Text`` so file content shows verbatim, never parsed as markup.
        self._apply_highlights()

    # ------------------------------------------------------------------
    # Search availability
    # ------------------------------------------------------------------

    @property
    def _searchable(self) -> bool:
        """True once a successful text load is present AND we're in text mode
        (search runs over the decoded text, not the hex dump)."""
        return (
            self._loaded is not None
            and not self._loaded.refusal
            and self._render_mode == "text"
        )

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _apply_highlights(self) -> None:
        """Rebuild the body ``Text`` with the current match spans styled.

        Cheap to call on every keystroke / step: it rebuilds one ``Text``
        from the cached load and stylizes the (typically few) match spans.
        With no matches it just re-renders the plain text - which is how
        clearing a search restores the unhighlighted body.
        """
        if self._loaded is None or self._loaded.refusal:
            return
        try:
            body = self.query_one("#viewer-body", Static)
        except Exception:  # noqa: BLE001
            return
        if self._render_mode == "hex":
            body.update(
                render_body(self._disp_lines, [], [], 0, gutter_w=0, gutter=False)
            )
        else:
            body.update(
                render_body(
                    self._disp_lines,
                    self._line_starts,
                    self._matches,
                    self._match_idx,
                    gutter_w=self._gutter_w,
                )
            )

    def _prepare_body(self) -> None:
        """Recompute the cached per-line Texts + line-start offsets + gutter
        width. Cheap to call on load and on each highlight toggle; the
        Pygments pass (the only costly part) only runs when highlighting is
        actually on and the file has a known lexer."""
        if self._loaded is None or self._loaded.refusal:
            return
        if self._render_mode == "hex":
            self._disp_lines = hex_lines(self._loaded.data)
            self._line_starts = []
            self._gutter_w = 0
            return
        text = self._loaded.text
        self._line_starts = line_start_offsets(text)
        self._disp_lines = highlight_lines(
            text, self._loaded.lexer, enabled=self._highlight_on
        )
        self._gutter_w = gutter_width(len(self._disp_lines))

    def _update_header(self) -> None:
        """Header line: path, size, encoding, lexer + highlight state."""
        if self._loaded is None:
            return
        try:
            header = self.query_one(".header", Label)
        except Exception:  # noqa: BLE001
            return
        if self._loaded.refusal:
            header.update(
                f"{self._path}  -  {self._loaded.refusal.splitlines()[0]}"
            )
            return
        parts = [self._path, _human_bytes(self._loaded.byte_size)]
        if self._loaded.truncated:
            parts.append(
                f"showing {_human_bytes(self._loaded.loaded_bytes)} (m=more)"
            )
        parts.append(self._loaded.encoding)
        if self._render_mode == "hex":
            parts.append("hex")
        else:
            lex = self._loaded.lexer or "plain"
            if lex in ("text", "default"):
                lex = "plain"
            state = "" if (self._highlight_on and lex != "plain") else " (hl off)"
            parts.append(f"{lex}{state}")
        header.update("  -  ".join(parts))

    def action_toggle_highlight(self) -> None:
        """``h`` - toggle syntax highlighting (forces it on for a big file
        that opened plain, or off if the colours are distracting). The gutter
        and search highlights are unaffected."""
        if not self._searchable:
            return
        self._highlight_on = not self._highlight_on
        self._prepare_body()
        self._update_header()
        self._apply_highlights()

    async def action_load_more(self) -> None:
        """``m`` - pull the next page of a truncated file, keeping the view
        where it is so the freshly-loaded content appears below."""
        if self._loaded is None or self._loaded.refusal or not self._loaded.truncated:
            return
        try:
            scroll = self.query_one("#viewer-scroll", VerticalScroll)
            y = int(scroll.scroll_y)
        except Exception:  # noqa: BLE001
            y = 0
        self._limit += _max_bytes()
        await self._load()
        try:
            self.query_one("#viewer-scroll", VerticalScroll).scroll_to(
                y=y, animate=False
            )
        except Exception:  # noqa: BLE001
            pass

    def action_toggle_hex(self) -> None:
        """``x`` - flip between the decoded text view and a hex dump of the
        loaded bytes. Clears any active search (it runs over text, not hex)."""
        if self._loaded is None or self._loaded.refusal:
            return
        self._render_mode = "hex" if self._render_mode == "text" else "text"
        self._matches = []
        self._match_idx = 0
        self._committed = False
        self._prepare_body()
        self._update_header()
        self._apply_highlights()
        self._set_hint(_HINT_DEFAULT)

    def _scroll_to_current(self) -> None:
        """Scroll so the current match's line is in view (with context)."""
        if not self._matches:
            return
        try:
            scroll = self.query_one("#viewer-scroll", VerticalScroll)
        except Exception:  # noqa: BLE001
            return
        line = self._matches[self._match_idx].line
        scroll.scroll_to(y=max(0, line - _SCROLL_CONTEXT), animate=False)

    def _set_hint(self, text: str) -> None:
        try:
            self.query_one("#viewer-hint", Label).update(text)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def action_search(self) -> None:
        """``/``: open the incremental search bar (successful loads only)."""
        if not self._searchable:
            return
        scroll = self.query_one("#viewer-scroll", VerticalScroll)
        self._scroll_pre = int(scroll.scroll_y)
        self._matches = []
        self._match_idx = 0
        self._committed = False
        self._apply_highlights()  # clear any prior committed highlights
        self.query_one("#viewer-hint", Label).display = False
        self.query_one("#viewer-search", SearchBar).activate()

    # ------------------------------------------------------------------
    # SearchBar message handlers (each stopped so they don't bubble to
    # the app's pane-search handlers on the screen behind the modal)
    # ------------------------------------------------------------------

    def on_search_bar_query_changed(
        self, event: SearchBar.QueryChanged
    ) -> None:
        event.stop()
        bar = self.query_one("#viewer-search", SearchBar)
        if not event.query or self._loaded is None:
            self._matches = []
            self._match_idx = 0
            self._apply_highlights()
            bar.update_match_info(0, 0)
            return
        self._matches = find_matches(self._loaded.text, event.query)
        if not self._matches:
            self._match_idx = 0
            self._apply_highlights()
            bar.update_match_info(0, 0)
            return
        # Land on the first match at or after the line we were looking at
        # when / was pressed, wrapping to the top (mirrors the pane anchor).
        idx = next(
            (k for k, m in enumerate(self._matches) if m.line >= self._scroll_pre),
            0,
        )
        self._match_idx = idx
        self._apply_highlights()
        self._scroll_to_current()
        bar.update_match_info(len(self._matches), idx + 1)

    def on_search_bar_next_match(self, event: SearchBar.NextMatch) -> None:
        event.stop()
        self._step_match(1)

    def on_search_bar_prev_match(self, event: SearchBar.PrevMatch) -> None:
        event.stop()
        self._step_match(-1)

    def _step_match(self, direction: int) -> None:
        """Step the current match by ``direction`` (wrap) and re-render."""
        if not self._matches:
            return
        n = len(self._matches)
        self._match_idx = (self._match_idx + direction) % n
        self._apply_highlights()
        self._scroll_to_current()
        self.query_one("#viewer-search", SearchBar).update_match_info(
            n, self._match_idx + 1
        )

    def on_search_bar_committed(self, event: SearchBar.Committed) -> None:
        event.stop()
        # Enter keeps the highlights and position; n / N stay live as long
        # as there are matches to walk.
        self._committed = bool(self._matches)
        self._close_bar()
        self._set_hint(_HINT_SEARCH if self._committed else _HINT_DEFAULT)

    def on_search_bar_cancelled(self, event: SearchBar.Cancelled) -> None:
        event.stop()
        # Esc abandons the search: clear highlights, restore the scroll.
        self._matches = []
        self._match_idx = 0
        self._committed = False
        self._apply_highlights()
        try:
            self.query_one("#viewer-scroll", VerticalScroll).scroll_to(
                y=self._scroll_pre, animate=False
            )
        except Exception:  # noqa: BLE001
            pass
        self._close_bar()
        self._set_hint(_HINT_DEFAULT)

    def _close_bar(self) -> None:
        """Hide the search bar, restore the hint, refocus the body."""
        self.query_one("#viewer-search", SearchBar).deactivate()
        self.query_one("#viewer-hint", Label).display = True
        try:
            self.query_one("#viewer-scroll", VerticalScroll).focus()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Post-commit match stepping (n / N) - live only after Enter
    # ------------------------------------------------------------------

    def action_next_match(self) -> None:
        """``n``: step to the next match of a committed search (else no-op)."""
        if self._committed and self._matches:
            self._step_committed(1)

    def action_prev_match(self) -> None:
        """``N``: step to the previous match of a committed search."""
        if self._committed and self._matches:
            self._step_committed(-1)

    def _step_committed(self, direction: int) -> None:
        n = len(self._matches)
        self._match_idx = (self._match_idx + direction) % n
        self._apply_highlights()
        self._scroll_to_current()

    # ------------------------------------------------------------------
    # Close gestures
    # ------------------------------------------------------------------

    def action_escape(self) -> None:
        """Esc - two-stage.

        With a committed search, the first Esc clears the highlights and
        returns to the plain view; otherwise Esc dismisses the viewer.
        (While the search bar is *open*, the bar's own ``on_key`` eats Esc
        and posts ``Cancelled`` instead, so this only runs when the bar is
        closed.)
        """
        if self._committed:
            self._clear_committed()
            return
        self.dismiss(None)

    def _clear_committed(self) -> None:
        self._committed = False
        self._matches = []
        self._match_idx = 0
        self._apply_highlights()
        self._set_hint(_HINT_DEFAULT)

    def action_dismiss_screen(self) -> None:
        """Q - always quit the viewer, committed search or not."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# File loading (sync; called from asyncio.to_thread)
# ---------------------------------------------------------------------------


def _load_file_sync(path: str, *, limit: int) -> _LoadResult:
    """Load up to ``limit`` bytes of ``path`` for viewing.

    No hard size refusal any more (2026-06-30): a file larger than ``limit``
    loads its first ``limit`` bytes with ``truncated=True``, and the viewer's
    "m" key pulls the next page by re-loading with a bigger ``limit``. Binary
    files (NUL bytes, no text BOM) are NOT refused either - they load their
    bytes and default to the hex view. The function never raises; failures
    become a refusal string.
    """
    try:
        st = os.stat(path)
    except OSError as exc:
        return _LoadResult(refusal=_stat_refusal(path, exc))

    size = st.st_size
    loaded = min(size, limit)
    try:
        with open(path, "rb") as fh:
            data = fh.read(loaded)
    except OSError as exc:
        return _LoadResult(
            byte_size=size,
            refusal=f"Could not read file: {type(exc).__name__}: {exc}",
        )
    loaded = len(data)
    truncated = size > loaded

    peek = data[:_PEEK_BYTES]
    bom = _detect_bom(peek)
    is_binary = bom is None and b"\x00" in peek

    # Decode. A recognised BOM picks the codec; otherwise UTF-8 with a
    # latin-1 fallback (total decoding, so the viewer never crashes). When the
    # read was truncated mid-UTF-8 character, drop the trailing partial bytes
    # rather than mislabelling the whole file latin-1.
    if bom == "utf-8-sig":
        text = data.decode("utf-8-sig")
        encoding = "utf-8 (BOM)"
    elif bom == "utf-16":
        try:
            text = data.decode("utf-16")
            encoding = "utf-16 (BOM)"
        except UnicodeDecodeError:
            text = data.decode("latin-1")
            encoding = "latin-1 (fallback)"
    else:
        encoding = "utf-8"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            if truncated and e.start >= len(data) - 3:
                text = data[: e.start].decode("utf-8")  # split char at the page edge
            else:
                encoding = "latin-1 (fallback)"
                text = data.decode("latin-1")

    if is_binary:
        lexer = "default"
    else:
        try:
            lexer = Syntax.guess_lexer(path, code=text)
        except Exception:  # noqa: BLE001 - never let lexer guessing break a load
            lexer = "default"

    return _LoadResult(
        text=text,
        encoding=encoding,
        byte_size=size,
        lexer=lexer,
        loaded_bytes=loaded,
        truncated=truncated,
        is_binary=is_binary,
        data=data,
    )


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
