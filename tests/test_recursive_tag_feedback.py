"""Tests for recursive-tag scan-dialog feedback (2026-06-03).

The tree-pane Space recursive tag/untag walk now runs under the
scan-dialog gate so a big subtree surfaces a live "Tagging N..." modal
with a cancel, instead of walking silently. Covers:

* the configurable ``ScanContext.header`` + ``ScanScreen`` rendering it;
* ``_recursive_tag_walk`` tagging / untagging a subtree and writing
  ``entries_seen``;
* the atomic-commit guarantee — a cancelled walk leaves the tagged set
  untouched (driven with a pre-cancelled ctx, like the scan-dialog tests
  drive ``show_path(ctx=...)``);
* the handler wiring the right header ("Tagging" / "Untagging").
"""

from __future__ import annotations

from datetime import datetime

import pytest

from wtree.app import WTreeApp
from wtree.sources.base import Entry, Kind
from wtree.sources.mock import MockSource
from wtree.widgets.scan_screen import ScanContext, ScanScreen


def _now() -> datetime:
    return datetime(2026, 6, 3, 12, 0, 0)


@pytest.fixture
def proj_mock() -> MockSource:
    """/proj subtree = /proj, /proj/notes.md, /proj/src, /proj/src/main.py (4)."""
    now = _now()
    return MockSource(
        contents={
            "/": [Entry("proj", Kind.DIR, 4096, now)],
            "/proj": [
                Entry("notes.md", Kind.FILE, 80, now),
                Entry("src", Kind.DIR, 4096, now),
            ],
            "/proj/src": [Entry("main.py", Kind.FILE, 1500, now)],
        }
    )


# ---------------------------------------------------------------------------
# Configurable header
# ---------------------------------------------------------------------------


def test_scan_context_header_default() -> None:
    ctx = ScanContext(path="/x", method_label="mock")
    assert ctx.header == "Scanning"


def test_scan_context_header_custom() -> None:
    ctx = ScanContext(path="/x", method_label="mock", header="Tagging")
    assert ctx.header == "Tagging"


def test_scan_screen_header_reflects_ctx() -> None:
    """ScanScreen renders the ctx header verbatim (no mount needed)."""
    ctx = ScanContext(path="/x", method_label="mock", header="Untagging")
    screen = ScanScreen(ctx)
    assert screen._header_text() == "Untagging"


# ---------------------------------------------------------------------------
# _recursive_tag_walk behaviour
# ---------------------------------------------------------------------------


async def test_recursive_tag_walk_tags_subtree(proj_mock: MockSource) -> None:
    app = WTreeApp(source=proj_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        ctx = ScanContext(path="/proj", method_label="mock")
        await app._recursive_tag_walk("/proj", "mock", False, ctx)
        assert app.tagged_set.contains("mock", "/proj")
        assert app.tagged_set.contains("mock", "/proj/src/main.py")
        assert len(app.tagged_set) == 4
        assert ctx.entries_seen == 4


async def test_recursive_tag_walk_untags_subtree(proj_mock: MockSource) -> None:
    app = WTreeApp(source=proj_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        ctx1 = ScanContext(path="/proj", method_label="mock")
        await app._recursive_tag_walk("/proj", "mock", False, ctx1)
        assert len(app.tagged_set) == 4
        ctx2 = ScanContext(path="/proj", method_label="mock")
        await app._recursive_tag_walk("/proj", "mock", True, ctx2)
        assert len(app.tagged_set) == 0


async def test_recursive_tag_walk_cancel_leaves_set_unchanged(
    proj_mock: MockSource,
) -> None:
    """Atomic commit: a pre-cancelled walk tags nothing."""
    app = WTreeApp(source=proj_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        ctx = ScanContext(path="/proj", method_label="mock")
        ctx.cancelled.set()
        await app._recursive_tag_walk("/proj", "mock", False, ctx)
        assert len(app.tagged_set) == 0


async def test_recursive_tag_walk_cancel_does_not_untag(
    proj_mock: MockSource,
) -> None:
    """A cancelled untag walk leaves the existing tags in place."""
    app = WTreeApp(source=proj_mock, root_path="/")
    async with app.run_test() as pilot:
        await pilot.pause()
        ctx1 = ScanContext(path="/proj", method_label="mock")
        await app._recursive_tag_walk("/proj", "mock", False, ctx1)
        assert len(app.tagged_set) == 4
        ctx2 = ScanContext(path="/proj", method_label="mock")
        ctx2.cancelled.set()
        await app._recursive_tag_walk("/proj", "mock", True, ctx2)
        assert len(app.tagged_set) == 4  # untag was aborted


# ---------------------------------------------------------------------------
# Handler wires the correct header through the gate
# ---------------------------------------------------------------------------


async def test_space_wires_tagging_then_untagging_header(
    proj_mock: MockSource, monkeypatch
) -> None:
    app = WTreeApp(source=proj_mock, root_path="/")
    headers: list[str] = []

    async def fake_gate(path, source, do_work, *, header="Scanning"):
        headers.append(header)
        await do_work(
            ScanContext(path=path, method_label="mock", header=header)
        )

    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "_run_scan_with_dialog", fake_gate)
        await pilot.press("space")  # root untagged -> Tagging
        await pilot.pause()
        await pilot.press("space")  # root now tagged -> Untagging
        await pilot.pause()

    assert headers == ["Tagging", "Untagging"]
