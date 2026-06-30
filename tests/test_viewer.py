"""Tests for :class:`ViewerScreen` and the underlying ``_load_file_sync``.

The unit tests below hit the loader directly (no Textual pilot needed)
- they're cheap and exercise every refusal path. The pilot tests for
``action_view`` integration live in ``test_view_e2e.py``.
"""

from __future__ import annotations

from pathlib import Path


from wtree.widgets.viewer import (
    ViewerScreen,
    _load_file_sync,
)


# ---------------------------------------------------------------------------
_BIG = 64 * 1024 * 1024  # generous per-page limit so small test files load fully


# _load_file_sync - happy paths
# ---------------------------------------------------------------------------


def test_load_utf8_text_file(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("hello world\nsecond line\n", encoding="utf-8")
    result = _load_file_sync(str(f), limit=_BIG)
    assert result.refusal == ""
    assert result.text == "hello world\nsecond line\n"
    assert result.encoding == "utf-8"
    # st_size reflects byte length on POSIX (24 with the trailing newline).
    assert result.byte_size == len(f.read_bytes())


def test_load_unicode_content_decodes_clean(tmp_path: Path) -> None:
    """Emoji + accented chars decode as UTF-8 without falling back."""
    f = tmp_path / "unicode.txt"
    f.write_text("café résumé 🐍", encoding="utf-8")
    result = _load_file_sync(str(f), limit=_BIG)
    assert result.refusal == ""
    assert result.encoding == "utf-8"
    assert "café" in result.text and "🐍" in result.text


def test_load_falls_back_to_latin1_for_invalid_utf8(tmp_path: Path) -> None:
    """A file with byte 0xff alone (invalid UTF-8) should decode via
    the latin-1 fallback rather than refuse."""
    f = tmp_path / "latin.txt"
    # 0xff is invalid as a UTF-8 start byte; in latin-1 it's ÿ.
    f.write_bytes(b"plain ASCII\nthen \xff weird\n")
    result = _load_file_sync(str(f), limit=_BIG)
    assert result.refusal == ""
    assert "fallback" in result.encoding
    assert "weird" in result.text


# ---------------------------------------------------------------------------
# _load_file_sync - refusal paths
# ---------------------------------------------------------------------------


def test_load_binary_is_not_refused_and_flagged(tmp_path: Path) -> None:
    """Binary files used to be refused; now they load (no refusal), are
    flagged ``is_binary`` (the viewer defaults them to the hex view), and
    keep their raw bytes for that view."""
    f = tmp_path / "binary.bin"
    f.write_bytes(b"ELF\x00\x01\x02\x03some binary garbage")
    result = _load_file_sync(str(f), limit=_BIG)
    assert result.refusal == ""
    assert result.is_binary is True
    assert result.data.startswith(b"ELF\x00")


def test_load_oversize_truncates_not_refuses(tmp_path: Path) -> None:
    """A file larger than the per-page limit is NOT refused any more - it
    loads the first ``limit`` bytes with ``truncated=True`` (the viewer's
    ``m`` key pulls the rest)."""
    f = tmp_path / "big.txt"
    f.write_text("x" * 100, encoding="utf-8")
    result = _load_file_sync(str(f), limit=40)
    assert result.refusal == ""
    assert result.truncated is True
    assert result.loaded_bytes == 40
    assert result.byte_size == 100
    assert len(result.text) == 40


def test_load_full_when_under_limit(tmp_path: Path) -> None:
    f = tmp_path / "small.txt"
    f.write_text("x" * 30, encoding="utf-8")
    result = _load_file_sync(str(f), limit=40)
    assert result.truncated is False
    assert result.loaded_bytes == 30


def test_load_refuses_missing_file(tmp_path: Path) -> None:
    result = _load_file_sync(str(tmp_path / "no-such-file"), limit=_BIG)
    assert result.refusal
    assert "could not stat" in result.refusal.lower()


def test_load_refuses_unreadable_file(tmp_path: Path, monkeypatch) -> None:
    """If open() raises after stat succeeds, the refusal should surface
    the OS error rather than crashing the viewer."""
    f = tmp_path / "ok.txt"
    f.write_text("content", encoding="utf-8")

    original_open = open
    def explosive_open(*args, **kwargs):
        if args and str(args[0]).endswith("ok.txt") and "b" in (args[1] if len(args) > 1 else kwargs.get("mode", "")):
            raise PermissionError("simulated permission denied")
        return original_open(*args, **kwargs)

    import builtins
    monkeypatch.setattr(builtins, "open", explosive_open)
    result = _load_file_sync(str(f), limit=_BIG)
    assert result.refusal
    assert "could not read" in result.refusal.lower()


# ---------------------------------------------------------------------------
# ViewerScreen mounts and renders
# ---------------------------------------------------------------------------


async def test_viewer_screen_displays_file_contents(tmp_path: Path) -> None:
    """End-to-end at the widget level: push a ViewerScreen and verify
    the body widget reflects the file content."""
    f = tmp_path / "demo.txt"
    f.write_text("Demo content\nline two\n", encoding="utf-8")

    from wtree.app import WTreeApp
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ViewerScreen(str(f)))
        # ModalScreen mounts asynchronously and the file load awaits in
        # on_mount - give the worker a chance.
        await pilot.pause()
        await pilot.pause()

        from textual.widgets import Static
        body = app.screen.query_one("#viewer-body", Static)
        rendered = str(body.render())
        assert "Demo content" in rendered
        assert "line two" in rendered


async def test_viewer_screen_binary_defaults_to_hex(tmp_path: Path) -> None:
    """A binary file opens directly in the hex view (offset + bytes + ascii),
    not a refusal."""
    f = tmp_path / "blob.bin"
    f.write_bytes(b"hello\x00world\x00")

    from wtree.app import WTreeApp
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ViewerScreen(str(f)))
        await pilot.pause()
        await pilot.pause()

        screen = app.screen
        assert screen._render_mode == "hex"
        from textual.widgets import Static
        rendered = str(app.screen.query_one("#viewer-body", Static).render())
        # An offset column + the ascii gutter for "hello.world."
        assert "00000000" in rendered
        assert "hello.world." in rendered


async def test_viewer_screen_esc_dismisses(tmp_path: Path) -> None:
    """Esc closes the viewer; control returns to the previous screen."""
    f = tmp_path / "small.txt"
    f.write_text("x", encoding="utf-8")

    from wtree.app import WTreeApp
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ViewerScreen(str(f)))
        await pilot.pause()
        assert isinstance(app.screen, ViewerScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ViewerScreen)


async def test_viewer_screen_q_dismisses(tmp_path: Path) -> None:
    """Q is an alias for Esc - close the viewer."""
    f = tmp_path / "small.txt"
    f.write_text("x", encoding="utf-8")

    from wtree.app import WTreeApp
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ViewerScreen(str(f)))
        await pilot.pause()
        assert isinstance(app.screen, ViewerScreen)
        await pilot.press("q")
        await pilot.pause()
        assert not isinstance(app.screen, ViewerScreen)


# ---------------------------------------------------------------------------
# BOM detection + lexer guess (2026-06-30, Session 5)
# ---------------------------------------------------------------------------


def test_detect_bom() -> None:
    from wtree.widgets.viewer import _detect_bom

    assert _detect_bom(b"\xef\xbb\xbfhello") == "utf-8-sig"
    assert _detect_bom(b"\xff\xfeh\x00") == "utf-16"
    assert _detect_bom(b"\xfe\xff\x00h") == "utf-16"
    assert _detect_bom(b"plain text") is None


def test_load_utf8_bom_is_stripped(tmp_path: Path) -> None:
    f = tmp_path / "bom.txt"
    f.write_bytes(b"\xef\xbb\xbfhello\n")
    r = _load_file_sync(str(f), limit=_BIG)
    assert r.refusal == ""
    assert r.text == "hello\n"          # BOM stripped by utf-8-sig
    assert "BOM" in r.encoding


def test_load_utf16_not_refused_as_binary(tmp_path: Path) -> None:
    """A UTF-16 file is full of NUL bytes; the BOM check must run before the
    binary heuristic so it decodes as text instead of being refused."""
    f = tmp_path / "u16.txt"
    f.write_bytes("hello\nworld\n".encode("utf-16"))  # BOM + interleaved NULs
    r = _load_file_sync(str(f), limit=_BIG)
    assert r.refusal == ""
    assert r.text == "hello\nworld\n"
    assert "utf-16" in r.encoding


def test_load_guesses_python_lexer(tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert _load_file_sync(str(f), limit=_BIG).lexer == "python"


def test_load_txt_is_plain_lexer(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("just text\n", encoding="utf-8")
    assert _load_file_sync(str(f), limit=_BIG).lexer in ("text", "default")


# ---------------------------------------------------------------------------
# Highlighting / gutter / render_body pure helpers
# ---------------------------------------------------------------------------


def test_highlight_lines_python_has_syntax_spans() -> None:
    from wtree.widgets.viewer import highlight_lines

    lines = highlight_lines("def f():\n    return 1\n", "python", enabled=True)
    assert len(lines) == 3            # split incl. trailing empty line
    assert lines[0].plain == "def f():"
    assert lines[2].plain == ""
    assert any(len(line.spans) for line in lines)   # some token colouring


def test_highlight_lines_disabled_is_plain() -> None:
    from wtree.widgets.viewer import highlight_lines

    lines = highlight_lines("def f():\n    return 1\n", "python", enabled=False)
    assert all(line.spans == [] for line in lines)


def test_highlight_lines_plain_lexer_is_plain() -> None:
    from wtree.widgets.viewer import highlight_lines

    lines = highlight_lines("hello\nworld\n", "text", enabled=True)
    assert all(line.spans == [] for line in lines)


def test_gutter_width() -> None:
    from wtree.widgets.viewer import gutter_width

    assert gutter_width(0) == 1
    assert gutter_width(9) == 1
    assert gutter_width(10) == 2
    assert gutter_width(100) == 3


def test_render_body_has_gutter_and_overlays_match() -> None:
    from wtree.widgets.viewer import (
        find_matches,
        gutter_width,
        highlight_lines,
        line_start_offsets,
        render_body,
    )

    text = "alpha\nbeta gamma\n"
    lines = highlight_lines(text, "text", enabled=False)
    matches = find_matches(text, "gamma")
    body = render_body(
        lines, line_start_offsets(text), matches, 0,
        gutter_w=gutter_width(len(lines)),
    )
    plain = body.plain
    assert "1 \u2502 alpha" in plain
    assert "2 \u2502 beta gamma" in plain
    match_spans = [
        sp for sp in body.spans
        if "yellow" in str(sp.style) or "cyan" in str(sp.style)
    ]
    assert match_spans
    assert plain[match_spans[0].start : match_spans[0].end] == "gamma"


# ---------------------------------------------------------------------------
# Highlight toggle + big-file auto-cap (pilot)
# ---------------------------------------------------------------------------


async def test_highlight_toggle_key(tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("def f():\n    return 1\n", encoding="utf-8")
    from wtree.app import WTreeApp

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ViewerScreen(str(f)))
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert screen._highlight_on is True
        await pilot.press("h")
        await pilot.pause()
        assert screen._highlight_on is False
        await pilot.press("h")
        await pilot.pause()
        assert screen._highlight_on is True


async def test_big_file_defaults_highlight_off(tmp_path: Path, monkeypatch) -> None:
    import wtree.widgets.viewer as vmod

    monkeypatch.setattr(vmod, "HIGHLIGHT_MAX_BYTES", 8)  # tiny cap
    f = tmp_path / "code.py"
    f.write_text("def f():\n    return 1\n", encoding="utf-8")  # > 8 bytes
    from wtree.app import WTreeApp

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ViewerScreen(str(f)))
        await pilot.pause()
        await pilot.pause()
        assert app.screen._highlight_on is False   # auto-capped


# ---------------------------------------------------------------------------
# Paging / hex / config / symlink (2026-06-30, Session 6)
# ---------------------------------------------------------------------------

import os as _os  # noqa: E402

import pytest as _pytest  # noqa: E402


def test_max_bytes_env_override(monkeypatch) -> None:
    import wtree.widgets.viewer as v

    monkeypatch.setenv(v.VIEW_MAX_BYTES_ENV, "2048")
    assert v._max_bytes() == 2048
    monkeypatch.setenv(v.VIEW_MAX_BYTES_ENV, "nonsense")
    assert v._max_bytes() == v.MAX_BYTES        # bad value -> default
    monkeypatch.setenv(v.VIEW_MAX_BYTES_ENV, "0")
    assert v._max_bytes() == v.MAX_BYTES        # non-positive -> default
    monkeypatch.delenv(v.VIEW_MAX_BYTES_ENV, raising=False)
    assert v._max_bytes() == v.MAX_BYTES


def test_hex_lines_single_row() -> None:
    from wtree.widgets.viewer import hex_lines

    rows = hex_lines(b"AB\x00\xff", base=0)
    assert len(rows) == 1
    plain = rows[0].plain
    assert plain.startswith("00000000  41 42 00 ff")
    assert plain.endswith("|AB..|")


def test_hex_lines_rows_and_base_offset() -> None:
    from wtree.widgets.viewer import hex_lines

    rows = hex_lines(bytes(range(20)), base=16)
    assert len(rows) == 2
    assert rows[0].plain.startswith("00000010")   # base offset honoured
    assert rows[1].plain.startswith("00000020")   # + 16 bytes


async def test_load_more_pages_in(tmp_path: Path, monkeypatch) -> None:
    import wtree.widgets.viewer as v

    monkeypatch.setenv(v.VIEW_MAX_BYTES_ENV, "40")
    f = tmp_path / "big.txt"
    f.write_text("x" * 100, encoding="utf-8")
    from wtree.app import WTreeApp

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ViewerScreen(str(f)))
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert screen._loaded.truncated and screen._loaded.loaded_bytes == 40
        await pilot.press("m")
        await pilot.pause()
        await pilot.pause()
        assert screen._loaded.loaded_bytes == 80
        await pilot.press("m")
        await pilot.pause()
        await pilot.pause()
        assert screen._loaded.loaded_bytes == 100
        assert screen._loaded.truncated is False


async def test_toggle_hex_key(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello\nworld\n", encoding="utf-8")
    from wtree.app import WTreeApp

    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ViewerScreen(str(f)))
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert screen._render_mode == "text"
        await pilot.press("x")
        await pilot.pause()
        assert screen._render_mode == "hex"
        await pilot.press("x")
        await pilot.pause()
        assert screen._render_mode == "text"


@_pytest.mark.skipif(_os.name == "nt", reason="symlink perms on Windows CI")
def test_dangling_symlink_message(tmp_path: Path) -> None:
    link = tmp_path / "link"
    _os.symlink(tmp_path / "gone", link)
    result = _load_file_sync(str(link), limit=_BIG)
    assert "target missing" in result.refusal
