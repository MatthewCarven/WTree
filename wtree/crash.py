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
    install,
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


# Frames from these packages are framework plumbing in a WTree crash -
# the vendored reporter tags + collapses them so the report reads as
# "your code, with honest [N framework frames hidden] markers". WTREE_DEBUG
# disables the collapse (debugging posture wants everything).
_SKIP_MODULES = ("textual", "rich")

# Byte budget for a report. WTREE_DEBUG lifts it: with locals on, a big
# tagged-set crash can legitimately need the space, and the debugging
# posture prefers completeness over log hygiene.
_REPORT_BYTE_BUDGET = 512 * 1024


def build_report(error: BaseException) -> ErrorReport:
    """``describe_error`` with WTree's policies applied.

    Default posture (no ``WTREE_DEBUG``): no frame locals, textual/rich
    frames collapsed, 512 KiB report budget (drops locals first, then
    source context, with honest markers - the vendored budget rule).
    ``WTREE_DEBUG=1``: locals on, nothing collapsed, no budget.
    """
    debug = locals_enabled()
    return describe_error(
        error,
        include_locals=debug,
        skip_modules=() if debug else _SKIP_MODULES,
        max_report_bytes=None if debug else _REPORT_BYTE_BUDGET,
    )


_hooks_installed = False


def install_thread_hooks() -> None:
    """Wire the vendored ``threading`` + ``unraisable`` hooks (idempotent).

    Covers the errors neither net sees today: a stray worker thread
    raising outside the event loop, and unraisables (``__del__`` and
    friends). Concise report to stderr - these are diagnostics, not
    app-fatal; Textual's loop crashes stay with ``_handle_exception``
    and the ``sys.excepthook`` slot is deliberately left alone
    (``main()``'s outer try/except owns that layer).
    """
    global _hooks_installed
    if _hooks_installed:
        return
    try:
        install(hooks=("threading", "unraisable"), style="concise")
        _hooks_installed = True
    except Exception:  # noqa: BLE001 - setup must never break app startup
        pass


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
