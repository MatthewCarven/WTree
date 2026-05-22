"""Shell-out editor helpers for ``E`` / F4.

Per ``design.md`` § Editing files: an inline editor is out of scope for
v0. Instead, Edit suspends Textual, runs the user's preferred external
editor (or the platform default), and resumes once it exits.

Two pieces live here:

* :func:`resolve_editor` — picks the editor argv to invoke, in the
  documented precedence ``$VISUAL`` → ``$EDITOR`` → platform default
  (``notepad`` on Windows; ``nano`` if available, else ``vi`` on Unix).
  Returns a list (already shlex-split) so callers can append the path
  argument directly.
* :func:`launch_editor_blocking` — spawns the subprocess and waits for
  it to exit, returning the exit code. No Textual dependency; the
  caller wraps it in ``app.suspend()`` so the terminal is handed over.

Kept as a top-level module (not under ``wtree/ops``) because Edit is
not a :class:`~wtree.ops.Plan`-producing operation. It mirrors the
shape of :mod:`wtree.widgets.viewer` — a UI shell-out, not a queued
data-mutating plan.

Both helpers are intentionally easy to monkeypatch from tests: the
resolver reads ``os.environ`` directly (test fixtures override env),
and the launcher does a single ``subprocess.run`` call with no I/O on
the caller side.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Sequence


# Platform default editors. On Windows we go to ``notepad`` straight
# away — it's always present. On Unix we prefer ``nano`` because it's
# friendlier than ``vi`` for users who never agreed to learn modal
# editing; if it isn't installed we fall back to ``vi`` which is part
# of POSIX and effectively always available.
_WINDOWS_DEFAULT: tuple[str, ...] = ("notepad",)
_UNIX_PREFERRED: tuple[str, ...] = ("nano",)
_UNIX_FALLBACK: tuple[str, ...] = ("vi",)


def resolve_editor() -> list[str]:
    """Return the editor argv to launch, per design.md precedence.

    Resolution order:

    1. ``$VISUAL`` — if set and non-empty, shlex-split and return.
    2. ``$EDITOR`` — same.
    3. Platform default — ``notepad`` on Windows; ``nano`` if it's on
       ``PATH``, otherwise ``vi`` on Unix-like.

    The env values are shlex-split so users can configure
    ``EDITOR="code --wait"`` and get the wait flag honoured. POSIX
    parsing on Unix; Windows-style (no backslash escaping) on Windows.
    """
    posix = os.name != "nt"
    for env_var in ("VISUAL", "EDITOR"):
        raw = os.environ.get(env_var, "").strip()
        if raw:
            return shlex.split(raw, posix=posix)

    if os.name == "nt":
        return list(_WINDOWS_DEFAULT)

    # Unix-like: nano if installed, else vi (always available per POSIX).
    if shutil.which(_UNIX_PREFERRED[0]) is not None:
        return list(_UNIX_PREFERRED)
    return list(_UNIX_FALLBACK)


def launch_editor_blocking(argv: Sequence[str], path: str) -> int:
    """Run ``argv + [path]`` synchronously, return the exit code.

    The caller is responsible for handing the terminal over before this
    runs (typically by entering ``app.suspend()``); this helper itself
    is Textual-agnostic. ``subprocess.run`` inherits stdin/stdout/stderr
    so the editor sees the real terminal.

    Re-raises :class:`FileNotFoundError` if the editor binary isn't on
    ``PATH``, so the action layer can surface a friendly message.
    """
    cmd = list(argv) + [path]
    completed = subprocess.run(cmd)
    return completed.returncode
