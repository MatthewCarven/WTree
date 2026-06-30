"""Tests for in-viewer ``/`` search (design.md 2026-06-10).

Two layers:

* ``find_matches`` units - pure, no pilot. Cover the substring-CI rule,
  per-line multiplicity, absolute offsets, line numbering, literal
  (regex-escaped) queries, and the Unicode-offset-stays-valid property.
* ``ViewerScreen`` pilot integration - activation, incremental jump,
  Down/Up/Ctrl+G stepping with wrap, Enter-commits-and-keeps-highlights,
  pager-style ``n`` / ``N`` after commit, the two-stage Esc, ``q``
  always-quits, no-match, scroll-into-view, and ``/`` being a no-op on a
  refusal (binary) body.

The body widget renders as a Textual ``Content`` whose span styles are
resolved ``Style`` objects, so the highlight assertions check span
*positions* and that the current match is visually *distinct* from the
others rather than string-comparing against the style constants.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from textual.widgets import Label, Static

from wtree.app import WTreeApp
from wtree.widgets.search_bar import SearchBar
from wtree.widgets.viewer import (
    ViewerScreen,
    find_matches,
    _CURRENT_MATCH_STYLE,
    _MATCH_STYLE,
    _HINT_DEFAULT,
    _HINT_SEARCH,
)


# ---------------------------------------------------------------------------
# find_matches - units
# ---------------------------------------------------------------------------


def test_find_matches_empty_query_is_empty() -> None:
    assert find_matches("anything at all", "") == []


def test_find_matches_no_match_is_empty() -> None:
    assert find_matches("hello world", "zzz") == []


def test_find_matches_multiple_per_line() -> None:
    matches = find_matches("banana", "a")
    assert [(m.start, m.end) for m in matches] == [(1, 2), (3, 4), (5, 6)]
    assert all(m.line == 0 for m in matches)


def test_find_matches_line_numbers_and_absolute_offsets() -> None:
    text = "foo\nbar foo\nbaz\nfoo"
    matches = find_matches(text, "foo")
    assert [m.line for m in matches] == [0, 1, 3]
    # Offsets index the original text, not per-line columns.
    assert [text[m.start:m.end] for m in matches] == ["foo", "foo", "foo"]


def test_find_matches_case_insensitive() -> None:
    text = "Foo FOO foo fOo"
    matches = find_matches(text, "foo")
    assert len(matches) == 4
    assert all(text[m.start:m.end].lower() == "foo" for m in matches)


def test_find_matches_non_overlapping() -> None:
    # "aa" in "aaaa" yields two non-overlapping spans, not three.
    matches = find_matches("aaaa", "aa")
    assert [(m.start, m.end) for m in matches] == [(0, 2), (2, 4)]


def test_find_matches_query_is_literal_not_regex() -> None:
    # A query with regex metacharacters matches literally (re.escape).
    text = "a.b a.b axb"
    matches = find_matches(text, "a.b")
    assert [(m.start, m.end) for m in matches] == [(0, 3), (4, 7)]


def test_find_matches_unicode_offsets_stay_valid() -> None:
    # 'é' / 'É' are single code points; re.IGNORECASE matches against the
    # original string so the offsets index the real characters.
    text = "café au É lait"
    matches = find_matches(text, "é")
    assert len(matches) == 2
    assert all(text[m.start:m.end].lower() == "é" for m in matches)


def test_match_styles_are_distinct() -> None:
    # The contract the highlight assertions lean on.
    assert _CURRENT_MATCH_STYLE != _MATCH_STYLE


# ---------------------------------------------------------------------------
# Pilot helpers
# ---------------------------------------------------------------------------


async def _open_viewer(app: WTreeApp, path: Path, pilot) -> ViewerScreen:
    await app.push_screen(ViewerScreen(str(path)))
    # ModalScreen mounts async + the load awaits in on_mount.
    await pilot.pause()
    await pilot.pause()
    return app.screen  # type: ignore[return-value]


def _spans(screen: ViewerScreen):
    """Only the search-MATCH spans. Since 2026-06-30 the body also carries a
    'dim' line-number gutter (and, for source files, syntax-colour spans);
    the match styles are the only ones with a yellow / cyan background, so we
    filter on that. The viewer-search tests all use .txt files (lexer=plain),
    so the gutter is the only other styling present."""
    body = screen.query_one("#viewer-body", Static)
    return [
        s
        for s in body.render().spans
        if "yellow" in str(s.style) or "cyan" in str(s.style)
    ]


def _span_offsets(screen: ViewerScreen):
    return [(s.start, s.end) for s in _spans(screen)]


def _match_texts(screen: ViewerScreen):
    """The display substring under each match span - gutter-agnostic, so the
    assertion survives the line-number prefix without hard-coding offsets."""
    rendered = screen.query_one("#viewer-body", Static).render()
    plain = rendered.plain
    return [plain[s.start : s.end] for s in _spans(screen)]


def _current_span_index(screen: ViewerScreen):
    """Index of the uniquely-styled (current) span, or None if ambiguous.

    The current match carries ``_CURRENT_MATCH_STYLE`` and every other
    match ``_MATCH_STYLE``; with >= 2 non-current matches the current one
    is the lone style, so we can find it without depending on how Textual
    resolves the style string to a ``Style`` object.
    """
    styles = [str(s.style) for s in _spans(screen)]
    counts = Counter(styles)
    for i, st in enumerate(styles):
        if counts[st] == 1:
            return i
    return None


def _hint_text(screen: ViewerScreen) -> str:
    return str(screen.query_one("#viewer-hint", Label).render())


async def _activate_and_type(pilot, screen: ViewerScreen, query: str) -> None:
    await pilot.press("slash")
    await pilot.pause()
    for ch in query:
        await pilot.press(ch)
    await pilot.pause()


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


async def test_slash_activates_search_and_hides_hint(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        await pilot.press("slash")
        await pilot.pause()
        bar = screen.query_one("#viewer-search", SearchBar)
        assert bar.has_class("-active")
        assert not screen.query_one("#viewer-hint", Label).display


async def test_typing_highlights_and_counts(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\nbar\nfoo\nbaz\nfoo\n", encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        await _activate_and_type(pilot, screen, "foo")
        bar = screen.query_one("#viewer-search", SearchBar)
        assert bar.match_total == 3
        assert bar.match_idx == 1            # 1-based current
        assert screen._match_idx == 0        # anchor 0 -> first match
        # Every match highlighted (the gutter shifts absolute offsets, so we
        # assert on the underlying text rather than hard-coded positions); the
        # current one is distinct.
        expected = find_matches(screen._loaded.text, "foo")
        assert len(_spans(screen)) == len(expected)
        assert _match_texts(screen) == ["foo"] * len(expected)
        assert _current_span_index(screen) == 0


async def test_anchor_lands_at_or_after_scroll_line(tmp_path: Path) -> None:
    # Matches on lines 1, 5, 9; anchor at line 5 should land on the line-5
    # match (wrapping handled by the at-or-after rule).
    lines = ["foo" if i in (1, 5, 9) else "x" for i in range(12)]
    f = tmp_path / "f.txt"
    f.write_text("\n".join(lines), encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        await pilot.press("slash")
        await pilot.pause()
        screen._scroll_pre = 5               # pretend we were scrolled here
        for ch in "foo":
            await pilot.press(ch)
        await pilot.pause()
        assert screen._matches[screen._match_idx].line == 5


# ---------------------------------------------------------------------------
# Stepping
# ---------------------------------------------------------------------------


async def test_down_up_ctrlg_step_with_wrap(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\nfoo\nfoo\n", encoding="utf-8")   # 3 matches
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        await _activate_and_type(pilot, screen, "foo")
        assert screen._match_idx == 0
        await pilot.press("down"); await pilot.pause()
        assert screen._match_idx == 1
        await pilot.press("ctrl+g"); await pilot.pause()
        assert screen._match_idx == 2
        await pilot.press("down"); await pilot.pause()   # wrap forward
        assert screen._match_idx == 0
        await pilot.press("up"); await pilot.pause()      # wrap backward
        assert screen._match_idx == 2
        # The distinct (current) highlight tracks the step.
        assert _current_span_index(screen) == 2


# ---------------------------------------------------------------------------
# Commit + pager-style n / N
# ---------------------------------------------------------------------------


async def test_enter_commits_keeps_highlights(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\nfoo\nfoo\n", encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        await _activate_and_type(pilot, screen, "foo")
        await pilot.press("enter"); await pilot.pause()
        bar = screen.query_one("#viewer-search", SearchBar)
        assert not bar.has_class("-active")       # bar closed
        assert screen._committed is True
        assert screen.query_one("#viewer-hint", Label).display    # hint back
        assert _HINT_SEARCH in _hint_text(screen)
        assert len(_spans(screen)) == 3           # highlights survive Enter


async def test_n_and_N_step_after_commit(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\nfoo\nfoo\n", encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        await _activate_and_type(pilot, screen, "foo")
        await pilot.press("enter"); await pilot.pause()
        await pilot.press("n"); await pilot.pause()
        assert screen._match_idx == 1
        await pilot.press("n"); await pilot.pause()
        assert screen._match_idx == 2
        await pilot.press("N"); await pilot.pause()       # prev
        assert screen._match_idx == 1
        assert _current_span_index(screen) == 1


async def test_n_is_noop_without_committed_search(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\nfoo\n", encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        # No search yet - n must do nothing and certainly not crash.
        await pilot.press("n"); await pilot.pause()
        assert screen._match_idx == 0
        assert screen._matches == []
        assert isinstance(app.screen, ViewerScreen)


# ---------------------------------------------------------------------------
# Esc semantics
# ---------------------------------------------------------------------------


async def test_escape_cancels_open_search(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\nfoo\n", encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        await _activate_and_type(pilot, screen, "foo")
        await pilot.press("escape"); await pilot.pause()
        bar = screen.query_one("#viewer-search", SearchBar)
        assert not bar.has_class("-active")
        assert screen._committed is False
        assert len(_spans(screen)) == 0                 # highlights cleared
        assert screen.query_one("#viewer-hint", Label).display
        assert isinstance(app.screen, ViewerScreen)     # still in the viewer


async def test_two_stage_escape_after_commit(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\nfoo\n", encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        await _activate_and_type(pilot, screen, "foo")
        await pilot.press("enter"); await pilot.pause()
        # First Esc clears the committed search but stays in the viewer.
        await pilot.press("escape"); await pilot.pause()
        assert isinstance(app.screen, ViewerScreen)
        assert screen._committed is False
        assert len(_spans(screen)) == 0
        assert _HINT_DEFAULT in _hint_text(screen)
        # Second Esc dismisses.
        await pilot.press("escape"); await pilot.pause()
        assert not isinstance(app.screen, ViewerScreen)


async def test_q_quits_even_with_committed_search(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\nfoo\n", encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        await _activate_and_type(pilot, screen, "foo")
        await pilot.press("enter"); await pilot.pause()
        assert screen._committed is True
        await pilot.press("q"); await pilot.pause()
        assert not isinstance(app.screen, ViewerScreen)


# ---------------------------------------------------------------------------
# No-match + unavailable
# ---------------------------------------------------------------------------


async def test_no_match_shows_red_and_no_highlights(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("hello world\n", encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        await _activate_and_type(pilot, screen, "zzz")
        bar = screen.query_one("#viewer-search", SearchBar)
        assert bar.match_total == 0
        assert bar.no_match is True
        assert len(_spans(screen)) == 0


async def test_slash_is_noop_on_binary_refusal(tmp_path: Path) -> None:
    f = tmp_path / "blob.bin"
    f.write_bytes(b"hello\x00world\x00")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        assert screen._searchable is False
        await pilot.press("slash"); await pilot.pause()
        bar = screen.query_one("#viewer-search", SearchBar)
        assert not bar.has_class("-active")     # / did nothing


# ---------------------------------------------------------------------------
# Scroll-into-view (coarse - geometry-dependent)
# ---------------------------------------------------------------------------


async def test_search_scrolls_match_into_view(tmp_path: Path) -> None:
    lines = ["x"] * 60 + ["needle here"] + ["x"] * 10   # match on line 60
    f = tmp_path / "long.txt"
    f.write_text("\n".join(lines), encoding="utf-8")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = await _open_viewer(app, f, pilot)
        from textual.containers import VerticalScroll
        scroll = screen.query_one("#viewer-scroll", VerticalScroll)
        assert scroll.scroll_y == 0
        await _activate_and_type(pilot, screen, "needle")
        await pilot.pause()
        assert scroll.scroll_y > 0       # jumped down to the match
