"""Tests for crash-handler phase 2 (design.md 2026-06-07).

Re-vendored error_handler (the 2026-06-06 upstream drop) + the new
WTree policies: textual/rich frame collapse + 512 KiB budget by
default, everything-on under WTREE_DEBUG=1; threading/unraisable
hooks installed by main(); and the owned exit screen - a clean panel
in _exit_renderables instead of Textual's raw Rich dump.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from wtree import crash
from wtree.app import WTreeApp
from wtree.crash import build_report, install_thread_hooks
from wtree.error_handler import uninstall


# ---------------------------------------------------------------------------
# build_report policies
# ---------------------------------------------------------------------------


def _kwargs_probe(monkeypatch):
    seen = {}

    def fake_describe(error, **kwargs):
        seen.update(kwargs)

        class _Stub:
            pass

        return _Stub()

    monkeypatch.setattr(crash, "describe_error", fake_describe)
    return seen


def test_default_policy_collapses_and_budgets(monkeypatch) -> None:
    monkeypatch.delenv("WTREE_DEBUG", raising=False)
    seen = _kwargs_probe(monkeypatch)
    build_report(ValueError("x"))
    assert seen["include_locals"] is False
    assert seen["skip_modules"] == ("textual", "rich")
    assert seen["max_report_bytes"] == 512 * 1024


def test_debug_policy_full_fidelity(monkeypatch) -> None:
    monkeypatch.setenv("WTREE_DEBUG", "1")
    seen = _kwargs_probe(monkeypatch)
    build_report(ValueError("x"))
    assert seen["include_locals"] is True
    assert seen["skip_modules"] == ()
    assert seen["max_report_bytes"] is None


def test_build_report_real_call_round_trips() -> None:
    """No stubs: the vendored describe_error accepts WTree's kwargs."""
    try:
        raise KeyError("probe")
    except KeyError as e:
        report = build_report(e)
    assert "KeyError" in str(report)
    assert "probe" in report.for_claude()


# ---------------------------------------------------------------------------
# Thread / unraisable hooks
# ---------------------------------------------------------------------------


def test_install_thread_hooks_wires_and_is_idempotent() -> None:
    before_threading = threading.excepthook
    before_sys = sys.excepthook
    try:
        crash._hooks_installed = False
        install_thread_hooks()
        assert threading.excepthook is not before_threading
        # sys.excepthook deliberately left alone - main()'s outer net.
        assert sys.excepthook is before_sys
        wired = threading.excepthook
        install_thread_hooks()  # idempotent
        assert threading.excepthook is wired
    finally:
        uninstall()
        crash._hooks_installed = False
    # NOTE: no identity assert on the restored hook - pytest's own
    # threadexception plugin re-wraps threading.excepthook around every
    # test, so cross-batch identity is not stable. The vendored
    # uninstall()-restores contract is covered by upstream's own suite.


def test_stray_thread_crash_prints_report(capsys) -> None:
    try:
        crash._hooks_installed = False
        install_thread_hooks()

        def boom() -> None:
            raise RuntimeError("thread-probe")

        t = threading.Thread(target=boom)
        t.start()
        t.join()
        err = capsys.readouterr().err
        assert "RuntimeError" in err
        assert "thread-probe" in err
    finally:
        uninstall()
        crash._hooks_installed = False


# ---------------------------------------------------------------------------
# Owned exit screen
# ---------------------------------------------------------------------------


async def test_fatal_error_renders_panel_not_raw_dump(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("WTREE_DEBUG", raising=False)
    monkeypatch.setattr(crash, "CRASH_DIR", tmp_path / "crashes")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        try:
            raise RuntimeError("exit-screen-probe")
        except RuntimeError as e:
            # Stash like _handle_exception would, then drive the seam.
            app._exception = e
            app._crash_report = build_report(e)
            app._crash_log_path = crash.write_crash_log(
                app._crash_report, crash_dir=tmp_path / "crashes"
            )
            app._fatal_error()

        assert len(app._exit_renderables) == 1
        from rich.console import Console

        console = Console(width=100, record=True)
        console.print(app._exit_renderables[0])
        out = console.export_text()
        assert "WTree crashed" in out
        assert "RuntimeError: exit-screen-probe" in out
        assert "Full report:" in out
        assert "WTREE_DEBUG=1" in out
        # Not Textual's raw dump (that always shows locals header rows).
        assert "Traceback (most recent call last)" not in out

        # The pilot re-raises any stashed exception on context exit (the
        # one place Textual DOES re-raise) - clear the probe so the test
        # harness doesn't see our deliberate crash as a test failure.
        app._exception = None


async def test_fatal_error_debug_appends_traceback(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WTREE_DEBUG", "1")
    monkeypatch.setattr(crash, "CRASH_DIR", tmp_path / "crashes")
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        try:
            raise RuntimeError("debug-probe")
        except RuntimeError as e:
            app._exception = e
            app._crash_report = build_report(e)
            app._crash_log_path = None
            app._fatal_error()

        assert len(app._exit_renderables) == 1
        from rich.console import Console

        console = Console(width=120, record=True)
        console.print(app._exit_renderables[0])
        out = console.export_text()
        assert "WTree crashed" in out
        assert "debug-probe" in out
        # Textual-style traceback rides along under debug.
        assert "Traceback" in out

        app._exception = None  # see note in the non-debug test


async def test_handle_exception_still_stashes_and_logs(
    tmp_path: Path, monkeypatch
) -> None:
    """The phase-1 contract is intact under the new _fatal_error."""
    monkeypatch.delenv("WTREE_DEBUG", raising=False)
    crashes = tmp_path / "crashes"
    monkeypatch.setattr(crash, "CRASH_DIR", crashes)
    import wtree.app as app_mod

    monkeypatch.setattr(
        app_mod, "write_crash_log",
        lambda report: crash.write_crash_log(report, crash_dir=crashes),
    )
    app = WTreeApp(root_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        try:
            raise ValueError("net-probe")
        except ValueError as e:
            app._handle_exception(e)
        assert app._crash_report is not None
        assert app._crash_log_path is not None
        assert app._crash_log_path.exists()
        text = app._crash_log_path.read_text(encoding="utf-8")
        assert "net-probe" in text
        assert "STRUCTURED (to_dict):" in text

        app._exception = None  # see note in the non-debug test
