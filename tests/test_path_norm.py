"""Cross-platform path normalisation: ``to_posix`` / ``canonical_path``
and the two identity checks they back (``_same_location`` at plan time,
``_would_destroy_source`` in the executor).

The host running these tests is POSIX, so the *case-folding* branch can't
be exercised via the os-default flag - those assertions pass
``case_insensitive=`` explicitly to ``canonical_path`` (the whole point of
the flag: deterministic, host-independent coverage). The *separator*
unification, by contrast, fires regardless of platform, so the
``_same_location`` / ``_would_destroy_source`` tests below use a
Windows-style ``\\`` destination against a POSIX ``/`` source and assert
they're judged identical on any host.
"""

from __future__ import annotations

from pathlib import Path

from wtree.app import WTreeApp
from wtree.ops.base import (
    PlanItem,
    canonical_path,
    resolve_relative_leaf,
    to_posix,
)
from wtree.ops.conflicts import _same_location
from wtree.ops.execute import _would_destroy_source
from wtree.sources.base import Kind
from wtree.widgets.conflict import ConflictDialog
from wtree.widgets.prompt import PromptDialog


def _item(src: str, dst: str, kind: Kind = Kind.FILE) -> PlanItem:
    return PlanItem(
        src_source_id="mock",
        src_path=src,
        dst_source_id="mock",
        dst_path=dst,
        kind=kind,
        size=0,
    )


# ---------------------------------------------------------------------------
# to_posix
# ---------------------------------------------------------------------------


def test_to_posix_flips_backslashes():
    assert to_posix(r"C:\dest\foo.txt") == "C:/dest/foo.txt"


def test_to_posix_noop_on_forward_slashes():
    assert to_posix("/d/proj/a.txt") == "/d/proj/a.txt"


def test_to_posix_mixed_separators():
    assert to_posix(r"C:\dest/sub\file") == "C:/dest/sub/file"


# ---------------------------------------------------------------------------
# canonical_path
# ---------------------------------------------------------------------------


def test_canonical_collapses_dots_and_slashes():
    assert canonical_path("/d/./proj//a.txt", case_insensitive=False) == (
        "/d/proj/a.txt"
    )


def test_canonical_unifies_separators():
    assert canonical_path(r"C:\d\proj", case_insensitive=False) == (
        canonical_path("C:/d/proj", case_insensitive=False)
    )


def test_canonical_case_sensitive_keeps_case():
    assert canonical_path("/d/Proj", case_insensitive=False) == "/d/Proj"
    assert canonical_path("/d/Proj", case_insensitive=False) != (
        canonical_path("/d/proj", case_insensitive=False)
    )


def test_canonical_case_insensitive_folds_case_and_separators():
    # Windows/NTFS view: backslash + drive case + name case all collapse.
    assert canonical_path(r"C:\Dest\Foo.TXT", case_insensitive=True) == (
        canonical_path("c:/dest/foo.txt", case_insensitive=True)
    )


def test_canonical_default_flag_matches_host():
    # On this POSIX host the default flag is case-sensitive; assert the
    # default-arg call agrees with the explicit case-sensitive call so the
    # os.name wiring is exercised without hard-coding the platform.
    import os

    expected_ci = os.name == "nt"
    p = "/d/Proj"
    assert canonical_path(p) == canonical_path(p, case_insensitive=expected_ci)


# ---------------------------------------------------------------------------
# _same_location - separator unification (fires on any platform)
# ---------------------------------------------------------------------------


def test_same_location_unifies_separators():
    # Windows-style typed destination vs POSIX source: previously compared
    # unequal under bare posixpath.normpath; now canonical.
    assert _same_location(_item("/d/proj", r"\d\proj")) is True


def test_same_location_collapses_dot_segments_cross_separator():
    assert _same_location(_item("/d/proj", r"\d\.\proj\\")) is True


def test_same_location_still_distinguishes_real_difference():
    assert _same_location(_item("/d/proj", r"\d\other")) is False


# ---------------------------------------------------------------------------
# _would_destroy_source - separator unification
# ---------------------------------------------------------------------------


def test_would_destroy_source_identity_cross_separator():
    assert _would_destroy_source("/d/proj", r"\d\proj") is True


def test_would_destroy_source_ancestor_cross_separator():
    # Removing /d/proj would take the \d\proj\file source down with it.
    assert _would_destroy_source("/d/proj", r"\d\proj\file") is True


def test_would_destroy_source_unrelated_is_false():
    assert _would_destroy_source("/d/proj", r"\d\projector\file") is False


# ---------------------------------------------------------------------------
# App boundary: a typed back-slash destination is canonicalised so
# self-target detection fires (Copy into own dir -> SELF/duplicate dialog).
# ---------------------------------------------------------------------------


async def test_e2e_typed_backslash_dest_into_own_dir_is_self(tmp_path: Path):
    from textual.widgets import Input

    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("data")

    app = WTreeApp(root_path=str(work))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        # Type the file's own directory with Windows separators. to_posix at
        # the boundary flips them back to "/", so this resolves to work's own
        # path -> a Copy-into-own-dir self-target.
        backslashed = str(work).replace("/", "\\")
        app.screen.query_one(Input).value = backslashed
        await pilot.press("enter")
        await pilot.pause()
        # Surfaces as a SELF conflict (duplicate-in-place), defaulting Rename.
        assert isinstance(app.screen, ConflictDialog)
        from wtree.ops.base import Resolution

        assert app.screen._res == [Resolution.RENAME]


# ---------------------------------------------------------------------------
# resolve_relative_leaf - shared by Make-new and the ConflictDialog editor
# ---------------------------------------------------------------------------


def test_resolve_relative_leaf_basename():
    leaf, err = resolve_relative_leaf("/d/dest", "report.txt")
    assert err is None
    assert leaf == "/d/dest/report.txt"


def test_resolve_relative_leaf_subpath_lenient():
    leaf, err = resolve_relative_leaf("/d/dest", "sub/deep/x.txt")
    assert err is None
    assert leaf == "/d/dest/sub/deep/x.txt"


def test_resolve_relative_leaf_flips_backslashes():
    leaf, err = resolve_relative_leaf("/d/dest", r"sub\x.txt")
    assert err is None
    assert leaf == "/d/dest/sub/x.txt"


def test_resolve_relative_leaf_collapses_dots():
    leaf, err = resolve_relative_leaf("/d/dest", "./a//b")
    assert err is None
    assert leaf == "/d/dest/a/b"


def test_resolve_relative_leaf_root_parent():
    leaf, err = resolve_relative_leaf("/", "x")
    assert (leaf, err) == ("/x", None)


def test_resolve_relative_leaf_empty_parent():
    leaf, err = resolve_relative_leaf("", "x/y")
    assert (leaf, err) == ("x/y", None)


def test_resolve_relative_leaf_rejects_empty():
    leaf, err = resolve_relative_leaf("/d", "   ")
    assert leaf is None
    assert "empty" in err


def test_resolve_relative_leaf_rejects_absolute():
    leaf, err = resolve_relative_leaf("/d", "/etc/passwd")
    assert leaf is None
    assert "absolute" in err.lower()


def test_resolve_relative_leaf_rejects_drive_absolute():
    leaf, err = resolve_relative_leaf("/d", r"C:\windows")
    assert leaf is None
    assert "absolute" in err.lower()


def test_resolve_relative_leaf_rejects_dotdot():
    leaf, err = resolve_relative_leaf("/d", "../escape")
    assert leaf is None
    assert ".." in err


def test_resolve_relative_leaf_rejects_only_dots():
    leaf, err = resolve_relative_leaf("/d", "./.")
    assert leaf is None
    assert "no path components" in err
