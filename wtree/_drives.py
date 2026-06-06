"""Drive / location anchor enumeration - platform shim.

Sibling of ``_owner.py``: the cross-platform messiness is quarantined
here so callers (the destination browser's ``Ctrl+D`` drive chooser)
just ask for "the list of places a user might want to jump to".

Windows: real drive roots (``C:\\``, ``D:\\``...) via ``os.listdrives()``
on Python 3.12+, else a ``ctypes`` ``GetLogicalDrives()`` bitmask, else
an exists-probe over ``A:``-``Z:``. No ``pywin32`` - same constraint as
the owner lookup (design.md, owner-lookup precedent).

POSIX / macOS: there is no "drive" - we offer the pragmatic
file-manager set: ``/``, ``~`` (expanded), and existing one-level
children of the removable-media bases (``/mnt``, ``/media/$USER``,
``/run/media/$USER``, ``/Volumes``). Full mount-table parsing was
rejected as noisy and distro-dependent (design.md 2026-06-07).

The caller's *current root* is always included even when enumeration
wouldn't find it (e.g. a UNC share you're already browsing - share
*discovery* stays parked under Network discovery).
"""

from __future__ import annotations

import os
import string

__all__ = ["list_drive_anchors"]


# Removable-media bases probed on POSIX/macOS, in display order.
# $USER-suffixed bases are expanded at call time.
_POSIX_MEDIA_BASES = (
    "/mnt",
    "/media/{user}",
    "/run/media/{user}",
    "/Volumes",
)


def _bitmask_to_anchors(mask: int) -> list[str]:
    """``GetLogicalDrives()`` bitmask -> drive roots. Bit 0 = ``A:``."""
    out = []
    for i, letter in enumerate(string.ascii_uppercase):
        if mask & (1 << i):
            out.append(f"{letter}:\\")
    return out


def _windows_anchors() -> list[str]:
    """Enumerate Windows drive roots, best available method first."""
    listdrives = getattr(os, "listdrives", None)  # Python 3.12+
    if listdrives is not None:
        try:
            return list(listdrives())
        except OSError:  # pragma: no cover - listdrives exists but failed
            pass
    try:  # pragma: no cover - exercised only on real Windows
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        if mask:
            return _bitmask_to_anchors(mask)
    except Exception:  # noqa: BLE001 - ctypes/windll missing or failed
        pass
    return [  # pragma: no cover - last-ditch probe, real Windows only
        f"{letter}:\\"
        for letter in string.ascii_uppercase
        if os.path.exists(f"{letter}:\\")
    ]


def _posix_anchors(
    *,
    media_bases: tuple[str, ...] = _POSIX_MEDIA_BASES,
    home: str | None = None,
) -> list[str]:
    """``/``, ``~``, then existing dirs one level under the media bases.

    ``media_bases`` / ``home`` are parameters so the layout is unit-
    testable against a tmp tree (the ``canonical_path`` flag precedent).
    """
    home = home if home is not None else os.path.expanduser("~")
    user = os.path.basename(home.rstrip("/")) or "root"
    out = ["/"]
    if home and home != "/":
        out.append(home)
    for base in media_bases:
        base = base.format(user=user)
        try:
            with os.scandir(base) as it:
                mounts = sorted(
                    e.path for e in it
                    if e.is_dir(follow_symlinks=True)
                )
        except OSError:
            continue
        out.extend(mounts)
    return out


def list_drive_anchors(
    current: str | None = None,
    *,
    windows: bool | None = None,
    media_bases: tuple[str, ...] = _POSIX_MEDIA_BASES,
    home: str | None = None,
) -> list[str]:
    """The drive / location anchors a user can jump to, current first-class.

    ``current`` (the picker's current root) is prepended if enumeration
    didn't already produce it, so the chooser always lists where you
    are. ``windows`` defaults to ``os.name == 'nt'``; it is a parameter
    (not a probe inside) so the Windows list shape is testable on the
    POSIX sandbox - the ``canonical_path(case_insensitive=...)``
    precedent.
    """
    windows = windows if windows is not None else (os.name == "nt")
    anchors = (
        _windows_anchors() if windows
        else _posix_anchors(media_bases=media_bases, home=home)
    )
    # Dedupe preserving order (a media base could re-list home, etc.).
    seen: set[str] = set()
    unique = []
    for a in anchors:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    if current and current not in seen:
        unique.insert(0, current)
    return unique
