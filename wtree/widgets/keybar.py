"""``KeyBar`` - MC-style F-key reference docked at the bottom of the screen.

Per ``design.md`` Keymap: "An MC-style key bar across the bottom of the
screen displays the F-key bindings as a permanent visual cheat sheet."

The bar lists every canonical F-key from the design's keymap regardless
of whether that operation is implemented yet. This is intentional - MC's
bar is *documentation*, not "what's available right now". Users learn
the F-key cheat sheet by seeing it always; we just gray the unwired
entries so expectations are calibrated.

Currently wired: F1 (Help), F2 (Ren), F3 (View), F4 (Edit), F5 (Copy), F6 (Move), F7 (New), F8 (Del), F9 (Menu), F10 (Quit). The full F-row is now wired.
Implementations land one at a time; this widget stays unchanged - the
``_WIRED`` set below gets updated as bindings appear in ``WTreeApp``.
"""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static


# F-key cheat sheet, in canonical screen order. Pulled directly from
# design.md Keymap canonical bindings - keep this in sync if the design
# changes (currently locked).
_LABELS: tuple[tuple[int, str], ...] = (
    (1, "Help"),
    (2, "Ren"),
    (3, "View"),
    (4, "Edit"),
    (5, "Copy"),
    (6, "Move"),
    (7, "New"),
    (8, "Del"),
    (9, "Menu"),
    (10, "Quit"),
)

# Which F-keys actually do something in the current app. Drives the
# dim/bold styling. Update as bindings are wired.
_WIRED: frozenset[int] = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10})


class KeyBar(Static):
    """One-line F-key reference. Dock at screen bottom."""

    DEFAULT_CSS = """
    KeyBar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text;
    }
    """

    # Reactive so future enhancements (e.g. dimming F5 mid-copy because
    # a queue is in flight) can just reassign and trigger a render.
    wired: reactive[frozenset[int]] = reactive(_WIRED)

    def render(self) -> Text:
        """One line of ``F#`` number + label cells.

        Layout: dim two-digit ``F#`` followed by a tight cell-coloured
        label. The visual contrast (dim number / bold label) matches
        MC's appearance closely enough that anyone who used MC will
        recognise it on sight.
        """
        text = Text()
        for num, name in _LABELS:
            num_style = "bold cyan" if num in self.wired else "dim cyan"
            name_style = (
                "reverse" if num in self.wired else "dim reverse"
            )
            # Two-char F-number column so single and double digits align.
            text.append(f"{num:>2}", style=num_style)
            text.append(f"{name:<5} ", style=name_style)
        return text
