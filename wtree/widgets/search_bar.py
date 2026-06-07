"""``SearchBar`` - inline incremental-search input docked at screen bottom.

Per ``design.md`` Modality: pressing ``/`` swaps the StatusLine row for
this widget. While the SearchBar holds focus, printable characters
extend the query, Backspace shrinks it, Down/Ctrl+G step to the next
match (wrap), Up steps to the previous match, Enter commits (cursor
stays at the current match), Esc cancels (cursor restores to the
pre-search position). Empty query is a no-op. No-match is indicated
in red with a ``(no match)`` suffix.

The SearchBar is *not* an :class:`Input` - it manages its own ``query``
string and intercepts keys directly via ``on_key``. Using a real Input
would either swallow our special keys (Down/Up/Ctrl+G/Enter/Esc) via
Input's own bindings, or require fighting Textual's focus and event
flow. A bare custom widget with explicit key handling is cleaner.

The widget knows nothing about *what* it's searching - it just posts
messages and the app does the matching. Both ContentsPane and TreePane
implement a tiny ``SearchTarget`` protocol (``iter_searchable`` /
``set_search_cursor`` / ``get_search_cursor``); the app picks the right
one based on which pane had focus at ``/``-press time.

Why Widget + render() instead of Static + update(): Static's update()
called from ``__init__`` (or close to it) caused ``visual=None`` in
Textual 8.x's Visual rendering pipeline (``render_strips``). Subclassing
``Widget`` and implementing ``render()`` directly skips the Visual
indirection - we just hand Rich a ``Text`` object every frame.
"""

from __future__ import annotations

from rich.text import Text

from typing import Iterable
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget


def compute_matches(
    rows: "Iterable[tuple[int, str]]",
    query: str,
    anchor: int = 0,
) -> tuple[list[int], int]:
    """Substring-CI match over ``(row, label)`` pairs -> (matches, idx).

    The match rule both `/`-search surfaces share (the app's pane search
    and the destination browser's filter - extracted 2026-06-07 so they
    can't drift). ``matches`` is the row indices whose label contains
    ``query`` case-insensitively; ``idx`` is the index *into matches* of
    the first match at-or-after ``anchor`` (wrapping to 0), i.e. where
    the cursor should land first. ``(matches=[], idx=0)`` when nothing
    matches; callers handle the empty-query case themselves (it means
    "no filter", not "no matches").
    """
    needle = query.lower()
    matches = [row for row, label in rows if needle in label.lower()]
    if not matches:
        return matches, 0
    idx = next((i for i, row in enumerate(matches) if row >= anchor), 0)
    return matches, idx


class SearchBar(Widget):
    """Inline incremental-search input docked at the bottom of the screen.

    Sits in the same dock slot as :class:`StatusLine`; the app toggles
    one or the other visible at a time. ``activate()`` resets the query
    and shows the bar; ``deactivate()`` hides it. While visible the bar
    holds keyboard focus and the surrounding panes receive no keys.

    Posts the following messages (all bubble to the app):

    * :class:`QueryChanged` - query string changed (typed char or
      backspace). Emitted on every keystroke that mutates the query.
    * :class:`NextMatch` - Down or Ctrl+G. Step forward.
    * :class:`PrevMatch` - Up. Step backward.
    * :class:`Committed` - Enter. Exit search, keep cursor at current
      match.
    * :class:`Cancelled` - Esc. Exit search, restore cursor.
    """

    DEFAULT_CSS = """
    SearchBar {
        dock: bottom;
        height: 1;
        background: $primary-background;
        color: $text;
        padding: 0 1;
        display: none;
    }

    SearchBar.-active {
        display: block;
    }
    """

    # ``can_focus`` makes ``self.focus()`` actually stick. Without it
    # Textual ignores the focus request and the surrounding panes keep
    # their bindings active during search.
    can_focus = True

    # Reactive so future enhancements (e.g. live highlighting in the
    # focused pane) can subscribe to ``query`` changes without going
    # through the message bus. Reactive changes also auto-refresh the
    # widget so ``render()`` re-runs after every typed character.
    query: reactive[str] = reactive("")
    match_total: reactive[int] = reactive(0)
    match_idx: reactive[int] = reactive(0)

    class QueryChanged(Message):
        """Posted whenever ``query`` mutates (typed char or backspace)."""

        def __init__(self, query: str) -> None:
            super().__init__()
            self.query = query

    class NextMatch(Message):
        """Posted on Down or Ctrl+G - step to the next match."""

    class PrevMatch(Message):
        """Posted on Up - step to the previous match."""

    class Committed(Message):
        """Posted on Enter - exit search, keep cursor at current match."""

    class Cancelled(Message):
        """Posted on Esc - exit search, restore cursor to pre-search row."""

    # ------------------------------------------------------------------
    # Activation / deactivation
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reset state, show the bar, take focus.

        Called by the app's ``action_search`` when ``/`` is pressed.
        """
        self.query = ""
        self.match_total = 0
        self.match_idx = 0
        self.add_class("-active")
        self.focus()

    def deactivate(self) -> None:
        """Hide the bar; the app is responsible for returning focus
        to the previously-focused pane.
        """
        self.remove_class("-active")
        # Reset visible content so the next activation starts clean.
        self.query = ""
        self.match_total = 0
        self.match_idx = 0

    # ------------------------------------------------------------------
    # Match-info display (called by the app)
    # ------------------------------------------------------------------

    def update_match_info(self, total: int, idx: int) -> None:
        """Refresh the match-count display (``idx`` is 1-based, 0 = none).

        Called after the app computes / advances matches in response to
        a :class:`QueryChanged`, :class:`NextMatch`, or :class:`PrevMatch`
        message.
        """
        self.match_total = total
        self.match_idx = idx

    # ------------------------------------------------------------------
    # Convenience predicate for tests
    # ------------------------------------------------------------------

    @property
    def no_match(self) -> bool:
        """True when there's a non-empty query and no matching rows.

        Used by tests to assert the no-match state without poking at
        CSS classes; also handy for future styling work.
        """
        return bool(self.query) and self.match_total == 0

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    async def on_key(self, event: events.Key) -> None:
        """Custom key handling so the search bar's modal-input feel
        coexists with the rest of the app's key bindings.

        Letters and digits extend the query; Backspace shrinks; the
        navigation/commit/cancel keys post messages and let the app
        coordinate the exit. Everything else is swallowed - while
        search is active, typing is the only thing that makes sense.
        A future enhancement (parked in todo.md) could let unhandled
        keys cancel-and-fall-through, but v0 keeps the contract tight.
        """
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.post_message(self.Cancelled())
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Committed())
            return
        if event.key in ("down", "ctrl+g"):
            event.stop()
            event.prevent_default()
            self.post_message(self.NextMatch())
            return
        if event.key == "up":
            event.stop()
            event.prevent_default()
            self.post_message(self.PrevMatch())
            return
        if event.key == "backspace":
            event.stop()
            event.prevent_default()
            if self.query:
                self.query = self.query[:-1]
                self.post_message(self.QueryChanged(self.query))
            return
        # Printable character - extend query. ``event.is_printable`` is
        # True for the user-typed letters/digits/punctuation; non-
        # printable special keys (function keys, modifiers alone, etc.)
        # fall through to the swallow branch below.
        if event.is_printable and event.character is not None:
            event.stop()
            event.prevent_default()
            self.query = self.query + event.character
            self.post_message(self.QueryChanged(self.query))
            return
        # Swallow anything else so it doesn't propagate to the pane
        # behind us mid-search. This is the conservative v0 choice;
        # the "let unhandled keys cancel and pass through" UX is on
        # the follow-ups list.
        event.stop()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self) -> Text:
        """Render the bar from current state.

        Subclassing ``Widget`` and overriding ``render()`` skips the
        Static/Visual indirection that bit us when called from
        ``__init__``. Rich ``Text`` is returned directly so colour
        styling for the no-match case lives here, not in a CSS class
        that has to be toggled by the message handlers.
        """
        if not self.query:
            return Text("/")
        if self.match_total == 0:
            # No-match: bar text rendered in error colour with an
            # explicit suffix. Pull the colour from the rich palette
            # for now; a theme pass can swap this later.
            return Text(
                f"/{self.query} (no match)",
                style="bold red",
            )
        return Text(f"/{self.query} ({self.match_idx}/{self.match_total})")
