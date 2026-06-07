"""Tests for drive-chooser polish (labels, free space, friendly ~).

design.md 2026-06-07 follow-up. The decoration contract: best-effort
(``None`` degrades to a bare row, never a crash), loaded async after
the modal paints (a dead share's blocking stat can't freeze the list),
display-only (the chooser still returns real paths - ``~`` is shown,
``/home/matt`` is dismissed).
"""

from __future__ import annotations

import os
from pathlib import Path

from wtree._drives import anchor_details, friendly_anchor_name
from wtree.app import WTreeApp
from wtree.widgets.dir_picker import DriveChooserScreen


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_friendly_name_folds_home() -> None:
    assert friendly_anchor_name("/home/matt", home="/home/matt") == "~"


def test_friendly_name_leaves_others() -> None:
    assert friendly_anchor_name("/mnt/usb", home="/home/matt") == "/mnt/usb"
    # Root home (containers) must NOT fold to ~ - "/" is clearer.
    assert friendly_anchor_name("/", home="/") == "/"


def test_anchor_details_real_mount() -> None:
    label, free, total = anchor_details("/")
    assert free is not None and total is not None
    assert 0 <= free <= total


def test_anchor_details_bad_path_degrades() -> None:
    assert anchor_details("/definitely/not/mounted") == (None, None, None)


# ---------------------------------------------------------------------------
# Chooser rendering
# ---------------------------------------------------------------------------


async def test_chooser_decorates_rows(tmp_path: Path, monkeypatch) -> None:
    """Rows gain [label] + free-of-total once details arrive."""
    import wtree.widgets.dir_picker as mod

    def fake_details(anchor: str):
        if anchor == "/data":
            return ("Games", 2 * 1024**3, 8 * 1024**3)
        return (None, None, None)

    monkeypatch.setattr(mod, "anchor_details", fake_details)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        chooser = DriveChooserScreen(["/data", "/other"], current="/data")
        app.push_screen(chooser)
        await pilot.pause()
        await pilot.pause()  # let the to_thread decoration land

        body = chooser._body_text()
        assert "[Games]" in body
        assert "2.0 GB free of 8.0 GB" in body
        # Undecorated row stays bare.
        other_line = next(l for l in body.splitlines() if "/other" in l)
        assert "free" not in other_line
        await pilot.press("escape")
        await pilot.pause()


async def test_chooser_shows_tilde_returns_real_path(
    tmp_path: Path, monkeypatch
) -> None:
    home = os.path.expanduser("~")
    import wtree.widgets.dir_picker as mod

    monkeypatch.setattr(
        mod, "anchor_details", lambda a: (None, None, None)
    )
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        results: list[str | None] = []
        chooser = DriveChooserScreen(["/", home], current="/")
        app.push_screen(chooser, callback=results.append)
        await pilot.pause()

        body = chooser._body_text()
        assert "~" in body
        assert home not in body  # folded for display

        await pilot.press("down")   # onto ~
        await pilot.press("enter")
        await pilot.pause()
        assert results == [home]    # real path returned


async def test_chooser_survives_details_exception(
    tmp_path: Path, monkeypatch
) -> None:
    """A raising decorator (shouldn't happen, but) leaves rows bare."""
    import wtree.widgets.dir_picker as mod

    def boom(anchor: str):
        raise OSError("stat failed")

    monkeypatch.setattr(mod, "anchor_details", boom)
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        chooser = DriveChooserScreen(["/a", "/b"], current="/a")
        app.push_screen(chooser)
        await pilot.pause()
        await pilot.pause()

        body = chooser._body_text()
        assert "/a" in body and "/b" in body
        await pilot.press("escape")
        await pilot.pause()
