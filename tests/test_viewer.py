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
# _load_file_sync - happy paths
# ---------------------------------------------------------------------------


def test_load_utf8_text_file(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("hello world\nsecond line\n", encoding="utf-8")
    result = _load_file_sync(str(f))
    assert result.refusal == ""
    assert result.text == "hello world\nsecond line\n"
    assert result.encoding == "utf-8"
    # st_size reflects byte length on POSIX (24 with the trailing newline).
    assert result.byte_size == len(f.read_bytes())


def test_load_unicode_content_decodes_clean(tmp_path: Path) -> None:
    """Emoji + accented chars decode as UTF-8 without falling back."""
    f = tmp_path / "unicode.txt"
    f.write_text("café résumé 🐍", encoding="utf-8")
    result = _load_file_sync(str(f))
    assert result.refusal == ""
    assert result.encoding == "utf-8"
    assert "café" in result.text and "🐍" in result.text


def test_load_falls_back_to_latin1_for_invalid_utf8(tmp_path: Path) -> None:
    """A file with byte 0xff alone (invalid UTF-8) should decode via
    the latin-1 fallback rather than refuse."""
    f = tmp_path / "latin.txt"
    # 0xff is invalid as a UTF-8 start byte; in latin-1 it's ÿ.
    f.write_bytes(b"plain ASCII\nthen \xff weird\n")
    result = _load_file_sync(str(f))
    assert result.refusal == ""
    assert "fallback" in result.encoding
    assert "weird" in result.text


# ---------------------------------------------------------------------------
# _load_file_sync - refusal paths
# ---------------------------------------------------------------------------


def test_load_refuses_binary_with_nul_bytes(tmp_path: Path) -> None:
    f = tmp_path / "binary.bin"
    f.write_bytes(b"ELF\x00\x01\x02\x03some binary garbage")
    result = _load_file_sync(str(f))
    assert result.refusal
    assert "binary" in result.refusal.lower()
    assert result.text == ""


def test_load_refuses_oversize_file(tmp_path: Path, monkeypatch) -> None:
    """We can't easily allocate a 10 MB temp file in a test; lower
    MAX_BYTES via monkeypatch to a tiny threshold and use a small file."""
    f = tmp_path / "big.txt"
    f.write_text("x" * 100, encoding="utf-8")
    # Patch the module constant in place.
    import wtree.widgets.viewer as viewer_mod
    monkeypatch.setattr(viewer_mod, "MAX_BYTES", 50)
    result = _load_file_sync(str(f))
    assert result.refusal
    assert "larger than" in result.refusal.lower()
    # byte_size is reported even on refusal.
    assert result.byte_size == 100


def test_load_refuses_missing_file(tmp_path: Path) -> None:
    result = _load_file_sync(str(tmp_path / "no-such-file"))
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
    result = _load_file_sync(str(f))
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


async def test_viewer_screen_renders_binary_refusal(tmp_path: Path) -> None:
    """Pushing a viewer at a binary file shows the refusal text in the body."""
    f = tmp_path / "blob.bin"
    f.write_bytes(b"hello\x00world\x00")

    from wtree.app import WTreeApp
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ViewerScreen(str(f)))
        await pilot.pause()
        await pilot.pause()

        from textual.widgets import Static
        body = app.screen.query_one("#viewer-body", Static)
        rendered = str(body.render())
        assert "binary" in rendered.lower()


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
    r = _load_file_sync(str(f))
    assert r.refusal == ""
    assert r.text == "hello\n"          # BOM stripped by utf-8-sig
    assert "BOM" in r.encoding


def test_load_utf16_not_refused_as_binary(tmp_path: Path) -> None:
    """A UTF-16 file is full of NUL bytes; the BOM check must run before the
    binary heuristic so it decodes as text instead of being refused."""
    f = tmp_path / "u16.txt"
    f.write_bytes("hello\nworld\n".encode("utf-16"))  # BOM + interleaved NULs
    r = _load_file_sync(str(f))
    assert r.refusal == ""
    assert r.text == "hello\nworld\n"
    assert "utf-16" in r.encoding


def test_load_guesses_python_lexer(tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert _load_file_sync(str(f)).lexer == "python"


def test_load_txt_is_plain_lexer(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("just text\n", encoding="utf-8")
    assert _load_file_sync(str(f)).lexer in ("text", "default")


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
