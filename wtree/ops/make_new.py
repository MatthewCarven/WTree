"""Make-new planner.

The second v0 op (after Rename) that doesn't take a tagged set: Make-new
creates a single new entry in a parent directory the user is *looking
at*, not on a Selection-rule set. Tagged set is silently ignored; cursor
position is irrelevant. The parent dir is whatever ``ContentsPane`` is
showing at the moment N / F7 is pressed.

Per ``design.md`` Keymap row "Make new (dir or file)" and Decision log
2026-05-19 ("Make-new (dir or file) bound to N (with dir/file sub-prompt)
and F7"), the action layer first asks the user which kind to create
(dir or file) and then prompts for a name. By the time ``plan_make_new``
runs, the choice has been made and the planner is given ``kind=DIR`` or
``kind=FILE`` directly.

Semantics: unlike Rename, the typed name MAY contain path separators -
``foo/bar/baz`` is a request to create the leaf with intermediate
directories created on the way (``os.makedirs(parent_of_leaf,
exist_ok=True)``). This was the "lenient" choice in the 2026-05-22
design conversation; Rename rejects separators because rename-as-move
would smuggle a directory move into a basename op, but Make-new starts
from "no existing entry", so creating intermediates is the same scope
of work the user expects.

Rejections:

* ``UnknownSource`` - ``source_id`` isn't in the registry.
* ``InvalidKind`` - kind not in {DIR, FILE}. SYMLINK and OTHER are not
  user-creatable through this op in v0.
* ``InvalidName`` - name is empty / whitespace / absolute / contains
  ``..`` segments / collapses to nothing.
* ``Exists`` - the leaf path already exists. Intermediate dirs that
  exist along the way are fine (that's lenient mode); the leaf itself
  must be new. The pre-check uses the source's ``entry_at`` so the
  check stays source-agnostic.

PlanItem shape:

* ``src_source_id == dst_source_id == source_id`` (no cross-source
  Make-new in v0).
* ``src_path == dst_path`` (Make-new has no "from" path; the executor
  branches on ``OperationKind.MAKE_NEW`` and ignores ``src_path``).
* ``kind`` is the chosen DIR or FILE.
* ``size`` is ``0`` - new entries are empty at birth.
"""

from __future__ import annotations

from collections.abc import Mapping

from wtree.ops.base import (
    OperationKind,
    Plan,
    PlanError,
    PlanItem,
)
from wtree.sources.base import EntrySource, Kind, ScanError


# Kinds the user can ask Make-new to create. SYMLINK and OTHER are
# excluded - symlinks need a separate "what target?" prompt, and OTHER
# (sockets, devices, fifos) isn't a sensible filemanager op.
_MAKEABLE: frozenset[Kind] = frozenset({Kind.DIR, Kind.FILE})


def _to_posix(path: str) -> str:
    """Replace backslashes with forward slashes.

    Mirror of the convention every other planner follows: planner paths
    are POSIX-flavoured, and :func:`wtree.ops.execute._normalise_dst`
    flips them back to native separators on Windows. Centralising the
    flip here keeps the planner readable for the absolute-path /
    segment-walk checks below.
    """
    return path.replace("\\", "/")


async def plan_make_new(
    parent_path: str,
    name: str,
    kind: Kind,
    source_id: str,
    registry: Mapping[str, EntrySource],
) -> Plan:
    """Build a :class:`Plan` of :attr:`OperationKind.MAKE_NEW`.

    ``parent_path`` is the directory the user is "looking at" - typically
    :attr:`ContentsPane.current_path`. ``name`` is the typed entry name,
    possibly containing forward-slash separators for lenient subdir
    creation. ``kind`` is :attr:`Kind.DIR` or :attr:`Kind.FILE` -
    decided in the action layer via the kind-chooser modal. ``source_id``
    identifies which source the new entry lives in (``"native"`` for v0).

    Returns a Plan with one PlanItem (success) or one PlanError (any
    rejection cause documented in the module docstring).
    """
    src = registry.get(source_id)
    if src is None:
        return Plan(
            kind=OperationKind.MAKE_NEW,
            errors=[
                PlanError(
                    source_id=source_id,
                    path=parent_path,
                    message=f"no source registered for id {source_id!r}",
                    cause="UnknownSource",
                )
            ],
        )

    if kind not in _MAKEABLE:
        return Plan(
            kind=OperationKind.MAKE_NEW,
            errors=[
                PlanError(
                    source_id=source_id,
                    path=parent_path,
                    message=(
                        f"cannot create a {kind.value} via Make-new; "
                        "v0 supports dir and file only"
                    ),
                    cause="InvalidKind",
                )
            ],
        )

    # Normalise the typed name. Strip surrounding whitespace (common
    # typo) and trailing slashes (the kind is already chosen via the
    # chooser modal, so "foo/" vs "foo" means the same thing).
    cleaned = _to_posix(name.strip()).rstrip("/")
    if not cleaned:
        return Plan(
            kind=OperationKind.MAKE_NEW,
            errors=[
                PlanError(
                    source_id=source_id,
                    path=parent_path,
                    message="new name is empty",
                    cause="InvalidName",
                )
            ],
        )

    # Refuse absolute paths - Make-new lands under the displayed parent,
    # not at the user's typed root. Catches POSIX-absolute ("/etc/...")
    # and Windows-absolute ("C:\\foo" became "C:/foo" after the
    # backslash flip; UNC "\\\\srv\\sh\\x" became "//srv/sh/x" which
    # starts with "/" and is caught by the first check).
    if cleaned.startswith("/") or (len(cleaned) >= 2 and cleaned[1] == ":"):
        return Plan(
            kind=OperationKind.MAKE_NEW,
            errors=[
                PlanError(
                    source_id=source_id,
                    path=parent_path,
                    message=(
                        f"new name {cleaned!r} is absolute; "
                        "Make-new creates entries under the current pane "
                        "directory - use a relative name or navigate first"
                    ),
                    cause="InvalidName",
                )
            ],
        )

    # Walk the segments. Drop "." silently (noise, not intent); reject
    # ".." (would escape the parent, contradicts the "create under pane
    # parent" contract). Collapsing "" between separators handles
    # double-slash inputs like "foo//bar".
    segments = [s for s in cleaned.split("/") if s and s != "."]
    if not segments:
        return Plan(
            kind=OperationKind.MAKE_NEW,
            errors=[
                PlanError(
                    source_id=source_id,
                    path=parent_path,
                    message="new name resolves to no path components",
                    cause="InvalidName",
                )
            ],
        )
    if any(seg == ".." for seg in segments):
        return Plan(
            kind=OperationKind.MAKE_NEW,
            errors=[
                PlanError(
                    source_id=source_id,
                    path=parent_path,
                    message=(
                        f"new name {cleaned!r} contains a '..' segment; "
                        "Make-new cannot escape the current directory"
                    ),
                    cause="InvalidName",
                )
            ],
        )

    # Build the leaf path under ``parent_path``. Three shapes:
    #   parent_path == ""    -> leaf is relative (just the segments).
    #   parent_path == "/"   -> leaf is "/" + segments.
    #   parent_path == X     -> leaf is X (trailing-slash-trimmed) + "/" + segments.
    parent_posix = _to_posix(parent_path)
    name_rel = "/".join(segments)
    if parent_posix == "":
        leaf_path = name_rel
    elif parent_posix == "/":
        leaf_path = "/" + name_rel
    else:
        leaf_path = parent_posix.rstrip("/") + "/" + name_rel

    # Refuse to clobber the leaf. Intermediate dirs may exist (lenient
    # mode); the leaf itself must be new. We use the source's
    # ``entry_at`` so the check stays source-agnostic - a future
    # ArchiveSource would use the same call. A ScanError here is
    # treated as "doesn't exist" for Make-new purposes; the executor
    # surfaces real errors (permission denied, etc.) at apply time.
    existing = await src.entry_at(leaf_path)
    if not isinstance(existing, ScanError):
        return Plan(
            kind=OperationKind.MAKE_NEW,
            errors=[
                PlanError(
                    source_id=source_id,
                    path=leaf_path,
                    message=f"path already exists: {leaf_path}",
                    cause="Exists",
                )
            ],
        )

    item = PlanItem(
        src_source_id=source_id,
        src_path=leaf_path,  # Make-new has no "from"; mirror dst for executor symmetry.
        dst_source_id=source_id,
        dst_path=leaf_path,
        kind=kind,
        size=0,
    )
    return Plan(kind=OperationKind.MAKE_NEW, items=[item])
