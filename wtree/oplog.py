"""Per-operation result log — ``~/.wtree/operations.log``.

Why this exists (2026-06-11): a Copy that ends "done with errors" surfaces
only a transient toast; once it fades there is no way to learn *which*
items failed or why. ``write_result`` persists every completed plan as one
summary line plus a detail line per non-SUCCESS item, so a surprising
result can be read back after the fact instead of reconstructed from
memory. The app calls it from ``_on_plan_complete``; the
done-with-errors toast names the log path.

Design constraints (mirrors ``crash.py``'s posture):

* **Never raises.** A logging failure must not take down the queue
  callback. Every filesystem touch is wrapped; on any failure
  ``write_result`` returns ``None``.
* **Append-only, bounded.** The log appends so consecutive operations
  read chronologically. When the file exceeds :data:`MAX_LOG_BYTES`
  *before* a write, it is rotated to ``operations.log.1`` (one
  generation, overwriting the previous ``.1``) — no logging-framework
  dependency, same tunable-constant spirit as ``ops/queue.py``.
* **Quiet on success, loud on trouble.** Per-item lines are written
  only for FAILED / SKIPPED items. A clean 10k-file copy is one line;
  a cancelled one says exactly where it stopped. (A future verbose
  toggle can widen this; v0 keeps logs skimmable.)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from wtree.ops.base import ItemStatus, OperationResult

#: Default log location. Lives beside ``crashes/`` under ``~/.wtree``.
OPLOG_PATH = Path.home() / ".wtree" / "operations.log"

#: Rotate when the existing log exceeds this size (checked pre-write).
MAX_LOG_BYTES = 1024 * 1024  # 1 MiB

#: Cap on per-operation detail lines. Rotation bounds the FILE between
#: writes but not one write: a mass failure (the 2026-06-11 backslash
#: bug failed every item of a 100k-entry copy in one plan) would append
#: tens of MB in a single entry. Past the cap the entry ends with an
#: honest "... and N more" line - the failure SHAPE repeats anyway.
MAX_DETAIL_LINES = 200

#: Environment variable that widens the log to include SUCCESS items too
#: (default: only non-SUCCESS items get detail lines, to keep logs
#: skimmable). Read at write time, mirroring the ``WTREE_DEBUG`` precedent
#: in ``crash.py`` - no config-persistence layer needed.
VERBOSE_ENV = "WTREE_OPLOG_VERBOSE"

_TRUTHY = {"1", "true", "yes", "on"}


def verbose_enabled() -> bool:
    """True when :data:`VERBOSE_ENV` is set to a truthy value."""
    return os.environ.get(VERBOSE_ENV, "").strip().lower() in _TRUTHY


def item_arrow(item) -> str:
    """``src -> dst`` for a normal item; just ``src`` for a Delete sentinel
    (whose ``dst_path`` mirrors ``src_path``)."""
    return (
        item.src_path
        if item.dst_path == item.src_path
        else f"{item.src_path} -> {item.dst_path}"
    )


def _item_line(r) -> str:
    """One detail line: ``  STATUS  arrow[: message]``.

    Non-success items with no message keep the explicit ``(no message)``
    placeholder (an empty failure reason is notable). A SUCCESS item with
    no message - the normal case - drops the suffix entirely so verbose
    logs stay clean.
    """
    arrow = item_arrow(r.item)
    if r.message:
        suffix = f": {r.message}"
    elif r.status is ItemStatus.SUCCESS:
        suffix = ""
    else:
        suffix = ": (no message)"
    return f"  {r.status.value.upper():7s} {arrow}{suffix}"


def format_result(
    result: OperationResult,
    *,
    now: datetime | None = None,
    verbose: bool | None = None,
) -> str:
    """Render ``result`` as the text block ``write_result`` appends.

    Pure function — no I/O — so tests can pin the format without a
    filesystem. One header line (UTC timestamp + the same ``summary()``
    the toast shows), then one indented detail line per item:
    ``STATUS  src -> dst: message``. Delete plans carry a sentinel dst
    mirror; for those the arrow collapses to just the source path.

    By default only **non-SUCCESS** items get detail lines (skimmable
    logs). When ``verbose`` is True - or, if ``verbose`` is None, when
    :func:`verbose_enabled` reads a truthy :data:`VERBOSE_ENV` - SUCCESS
    items get lines too. The :data:`MAX_DETAIL_LINES` cap applies to
    whatever set is emitted.
    """
    if verbose is None:
        verbose = verbose_enabled()
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"[{stamp}] {result.summary()}"]
    detailed = 0
    skipped_overflow = 0
    for r in result.items:
        if not verbose and r.status is ItemStatus.SUCCESS:
            continue
        if detailed >= MAX_DETAIL_LINES:
            skipped_overflow += 1
            continue
        lines.append(_item_line(r))
        detailed += 1
    if skipped_overflow:
        noun = "item(s)" if verbose else "non-success item(s)"
        lines.append(
            f"  ... and {skipped_overflow} more {noun} "
            f"(detail capped at {MAX_DETAIL_LINES})"
        )
    return "\n".join(lines) + "\n"


def write_result(
    result: OperationResult,
    path: Path | None = None,
    *,
    now: datetime | None = None,
) -> Path | None:
    """Append ``result`` to the operation log. Best-effort; never raises.

    Returns the path written, or ``None`` when logging itself failed
    (unwritable home, permission wall, disk full) — callers may use the
    return value to decide whether to name the log in a toast.
    """
    target = OPLOG_PATH if path is None else path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(target)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(format_result(result, now=now))
        return target
    except Exception:  # noqa: BLE001 - logging must never raise
        return None


def _rotate_if_needed(target: Path) -> None:
    """One-generation rotation: ``operations.log`` -> ``operations.log.1``.

    Checked before each write so the live file stays under (roughly)
    :data:`MAX_LOG_BYTES`. ``os.replace`` overwrites an existing ``.1``
    atomically on both POSIX and Windows. Rotation failure propagates to
    ``write_result``'s catch-all — losing the rotation must not lose
    the append.
    """
    try:
        size = target.stat().st_size
    except OSError:
        return  # no existing log — nothing to rotate
    if size <= MAX_LOG_BYTES:
        return
    os.replace(target, target.with_suffix(target.suffix + ".1"))
