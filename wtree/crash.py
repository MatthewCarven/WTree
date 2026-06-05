"""WTree crash-reporting glue around the vendored ``error_handler``.

Keeps ``app.py`` lean and the vendored ``error_handler.py`` untouched.
See design.md "Error handling and crash reporting".

Nothing in here may raise on the crash path: a failure while reporting a
crash must never mask the original crash. Every public helper is wrapped
so the worst case is "no log written", not a second traceback.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from wtree.error_handler import (
    ErrorReport,
    describe_error,
    redact_pattern,
    register_redactor,
)

# Crash logs live on the user's real home filesystem, never inside the
# (flaky) project mount — a crash mid-write there is the worst time to
# trust it.
CRASH_DIR = Path.home() / ".wtree" / "crashes"

# Default scrub patterns. Crash logs persist to disk, so mask obvious
# secret shapes before they land. Applied to source lines, args, messages
# and (with WTREE_DEBUG) frame locals by the vendored redactor hooks.
_DEFAULT_SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9]{16,}",            # OpenAI-style API keys
    r"(?i)password\s*=\s*\S+",
    r"(?i)token\s*=\s*\S+",
)

_redactors_installed = False


def locals_enabled() -> bool:
    """True when ``WTREE_DEBUG=1`` — opt-in frame-locals capture.

    Off by default because frame locals can contain secrets.
    """
    return os.environ.get("WTREE_DEBUG", "") == "1"


def install_crash_redactors() -> None:
    """Register the default secret-scrubbing redactors once (idempotent)."""
    global _redactors_installed
    if _redactors_installed:
        return
    try:
        for pattern in _DEFAULT_SECRET_PATTERNS:
            register_redactor(redact_pattern(pattern))
        _redactors_installed = True
    except Exception:  # noqa: BLE001 - setup must never break app startup
        pass


def build_report(error: BaseException) -> ErrorReport:
    """``describe_error`` with WTree's locals policy applied."""
    return describe_error(error, include_locals=locals_enabled())


def write_crash_log(
    report: ErrorReport, *, crash_dir: Path | None = None
) -> Path | None:
    """Persist ``report`` to ``~/.wtree/crashes/crash-<UTC>-<pid>.log``.

    Writes the ``for_claude()`` flavour (labelled, LLM-friendly — these get
    pasted into a Claude chat for diagnosis) followed by the ``to_dict()``
    JSON. Returns the path written, or ``None`` if logging itself failed —
    crash-logging never raises.
    """
    try:
        directory = crash_dir or CRASH_DIR
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        path = directory / f"crash-{stamp}-{os.getpid()}.log"
        try:
            payload = json.dumps(report.to_dict(), indent=2, default=repr)
        except Exception:  # noqa: BLE001 - to_dict is already safe; belt-and-braces
            payload = "<<to_dict serialization failed>>"
        path.write_text(
            report.for_claude()
            + "\n\n"
            + "=" * 70
            + "\nSTRUCTURED (to_dict):\n"
            + payload
            + "\n",
            encoding="utf-8",
        )
        return path
    except Exception:  # noqa: BLE001 - logging must never mask the crash
        return None
