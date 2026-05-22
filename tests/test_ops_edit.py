"""Unit tests for :mod:`wtree.editor`.

Covers :func:`resolve_editor` (env precedence + platform default) and
:func:`launch_editor_blocking` (subprocess passthrough + exit code).

The action-layer integration (``WTreeApp.action_edit`` with cursor
validation, suspend(), and pane refresh) is exercised in
``test_edit_e2e.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from wtree.editor import launch_editor_blocking, resolve_editor


# ---------------------------------------------------------------------------
# resolve_editor — env precedence
# ---------------------------------------------------------------------------


def test_visual_takes_precedence_over_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    """$VISUAL wins over $EDITOR when both are set."""
    monkeypatch.setenv("VISUAL", "my-visual")
    monkeypatch.setenv("EDITOR", "my-editor")
    assert resolve_editor() == ["my-visual"]


def test_editor_used_when_visual_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty / missing $VISUAL falls through to $EDITOR."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "ed")
    assert resolve_editor() == ["ed"]


def test_empty_visual_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only $VISUAL is ignored - we fall through to $EDITOR."""
    monkeypatch.setenv("VISUAL", "   ")
    monkeypatch.setenv("EDITOR", "ed")
    assert resolve_editor() == ["ed"]


def test_command_with_args_is_shlex_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """``EDITOR="code --wait"`` returns a list of argv tokens."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "code --wait")
    assert resolve_editor() == ["code", "--wait"]


def test_quoted_args_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quoted arguments survive shlex parsing as a single token."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", 'nvim --cmd "set background=dark"')
    assert resolve_editor() == ["nvim", "--cmd", "set background=dark"]


# ---------------------------------------------------------------------------
# resolve_editor — platform default
# ---------------------------------------------------------------------------


def test_platform_default_unix_prefers_nano(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When VISUAL/EDITOR unset on Unix, prefer nano if available."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("os.name", "posix")
    # Pretend nano is on PATH.
    monkeypatch.setattr(
        "wtree.editor.shutil.which", lambda name: "/usr/bin/nano"
    )
    assert resolve_editor() == ["nano"]


def test_platform_default_unix_falls_back_to_vi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If nano isn't installed, vi is the POSIX fallback."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setattr("wtree.editor.shutil.which", lambda name: None)
    assert resolve_editor() == ["vi"]


def test_platform_default_windows_is_notepad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows default is notepad (always present in C:\\Windows\\System32)."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("os.name", "nt")
    assert resolve_editor() == ["notepad"]


# ---------------------------------------------------------------------------
# launch_editor_blocking — subprocess wiring
# ---------------------------------------------------------------------------


def test_launch_editor_passes_path_as_last_arg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The path is appended to argv before subprocess.run sees it."""
    target = tmp_path / "doc.txt"
    target.write_text("x", encoding="utf-8")
    captured: dict[str, list[str]] = {}

    class FakeCompleted:
        returncode = 0

    def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = list(cmd)
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = launch_editor_blocking(["my-ed", "--flag"], str(target))
    assert rc == 0
    assert captured["cmd"] == ["my-ed", "--flag", str(target)]


def test_launch_editor_returns_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-zero exit codes propagate to the caller."""
    target = tmp_path / "doc.txt"
    target.write_text("x", encoding="utf-8")

    class FakeCompleted:
        returncode = 17

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeCompleted())
    assert launch_editor_blocking(["ed"], str(target)) == 17


@pytest.mark.skipif(sys.platform == "win32", reason="no /bin/true on Windows")
def test_launch_editor_real_subprocess(tmp_path: Path) -> None:
    """End-to-end with a real subprocess - /bin/true exits 0 immediately."""
    target = tmp_path / "doc.txt"
    target.write_text("x", encoding="utf-8")
    # /bin/true ignores its args and exits 0; perfect zero-side-effect stand-in.
    rc = launch_editor_blocking(["true"], str(target))
    assert rc == 0


def test_launch_editor_raises_when_binary_missing(tmp_path: Path) -> None:
    """Missing editor binary surfaces as FileNotFoundError."""
    target = tmp_path / "doc.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        launch_editor_blocking(
            ["definitely-not-a-real-editor-xyzzy"], str(target)
        )
