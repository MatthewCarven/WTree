"""Cross-platform owner / group lookup from a ``stat`` result.

Used by :class:`~wtree.widgets.properties.PropertiesScreen` to render
the owner + group of a single-file selection. This module is the
landing site for the long-parked "cross-platform owner lookup story"
follow-up from the skeleton era.

POSIX path
----------
``pwd.getpwuid`` and ``grp.getgrgid`` resolve the numeric uid/gid to a
name. Both raise ``KeyError`` when the id isn't in the local NSS
database — common on container images and synthetic mounts — so each
lookup falls back to the numeric id as a string. We never raise.

Windows path
------------
The ``pwd`` / ``grp`` standard-library modules don't exist on
Windows. A full owner lookup needs ``win32security.LookupAccountSid``
via ``pywin32``, which is a heavyweight new dependency we deliberately
avoid in v0. The Windows branch returns ``("n/a", "n/a")`` so the
Properties dialog still has something to render in the owner row.
Replacing this with a real lookup is a follow-up that lands when
``pywin32`` becomes acceptable.

The split between platforms is decided at import time (``HAS_PWD_GRP``)
not per-call, so the cost is one ``try/except ImportError`` at module
load and nothing else at lookup time.
"""

from __future__ import annotations

import os

try:
    import grp as _grp
    import pwd as _pwd

    HAS_PWD_GRP = True
except ImportError:  # pragma: no cover - Windows path; tested via monkeypatch
    _pwd = None  # type: ignore[assignment]
    _grp = None  # type: ignore[assignment]
    HAS_PWD_GRP = False


def lookup(st: os.stat_result) -> tuple[str, str]:
    """Return ``(owner_name, group_name)`` for the given ``stat`` result.

    On POSIX, resolves via ``pwd.getpwuid`` + ``grp.getgrgid`` with a
    numeric-id fallback when the local NSS database doesn't know the
    id. On Windows (where ``pwd``/``grp`` are unavailable), returns
    ``("n/a", "n/a")`` — see the module docstring for why.

    Never raises. Callers can render the result directly into the
    Properties dialog row without an outer try/except.
    """
    if not HAS_PWD_GRP:
        return ("n/a", "n/a")

    try:
        owner = _pwd.getpwuid(st.st_uid).pw_name  # type: ignore[union-attr]
    except KeyError:
        owner = str(st.st_uid)

    try:
        group = _grp.getgrgid(st.st_gid).gr_name  # type: ignore[union-attr]
    except KeyError:
        group = str(st.st_gid)

    return (owner, group)
