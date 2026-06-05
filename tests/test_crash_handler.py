"""Tests for the crash-reporting glue and its app wiring.

Covers:

* ``wtree.crash`` glue: default redactors scrub secrets; ``WTREE_DEBUG``
  gates frame-locals capture; ``write_crash_log`` writes the for_claude +
  to_dict payload and never raises (returns ``None`` on failure).
* ``WTreeApp._handle_exception`` builds + stashes a report, writes a log,
  and still delegates to Textual's ``super()._handle_exception`` (the
  teardown path) — the only reliable interception point because Textual's
  ``run()`` does not re-raise in-loop crashes.
* ``main()`` two-net behaviour: the outer ``try/except`` catches
  construction/teardown errors (writes log, exits non-zero); the post-run
  check surfaces an in-loop crash stashed on the app (exits non-zero); a
  clean run exits normally.
"""

from __future__ import annotations


import pytest

import wtree.app as app_mod
import wtree.crash as crash
from wtree.error_handler import ErrorReport


@pytest.fixture(autouse=True)
def _tmp_crash_dir(tmp_path, monkeypatch):
    """Redirect crash logs into a temp dir and reset redactor state."""
    monkeypatch.setattr(crash, "CRASH_DIR", tmp_path / "crashes")
    monkeypatch.setattr(crash, "_redactors_installed", False)
    # Start each test from a clean global redactor registry.
    from wtree.error_handler import clear_redactors

    clear_redactors()
    yield
    clear_redactors()


# --- glue -----------------------------------------------------------------

def test_default_redactors_scrub_secrets():
    crash.install_crash_redactors()
    # Build secrets at runtime so the literals aren't in this test's source
    # (describe_error captures caller-frame source lines; we want to prove the
    # message channel is redacted, not exercise source-context capture).
    secret = "hunter" + "2"
    key = "sk-" + "A" * 20
    report = crash.describe_error(ValueError("password=" + secret + " and " + key))
    text = str(report)
    assert secret not in text
    assert key not in text
    assert "<redacted>" in text


def test_install_redactors_is_idempotent():
    crash.install_crash_redactors()
    crash.install_crash_redactors()
    assert crash._redactors_installed is True
    # Scrubbing still works after a double install.
    secret = "hunter" + "2"
    assert secret not in str(crash.describe_error(ValueError("password=" + secret)))


def test_wtree_debug_gates_locals(monkeypatch):
    monkeypatch.delenv("WTREE_DEBUG", raising=False)
    assert crash.locals_enabled() is False
    monkeypatch.setenv("WTREE_DEBUG", "1")
    assert crash.locals_enabled() is True


def test_build_report_includes_locals_only_with_debug(monkeypatch):
    def boom(secret_local):  # value arrives as a runtime arg, not a source literal
        raise RuntimeError("kaboom")

    secret = "topsecret" + "value123"

    monkeypatch.delenv("WTREE_DEBUG", raising=False)
    try:
        boom(secret)
    except RuntimeError as e:
        off = crash.build_report(e).for_claude()
    monkeypatch.setenv("WTREE_DEBUG", "1")
    try:
        boom(secret)
    except RuntimeError as e:
        on = crash.build_report(e).for_claude()
    # Off: no frame locals captured anywhere → the value can't appear.
    assert secret not in off
    # On: boom's frame local `secret_local` carries the value.
    assert secret in on


def test_write_crash_log_writes_payload(tmp_path):
    report = crash.build_report(RuntimeError("diskboom"))
    path = crash.write_crash_log(report, crash_dir=tmp_path / "c")
    assert path is not None and path.exists()
    body = path.read_text(encoding="utf-8")
    assert "diskboom" in body
    assert "STRUCTURED (to_dict)" in body
    assert path.name.startswith("crash-") and path.suffix == ".log"


def test_write_crash_log_never_raises(monkeypatch):
    report = crash.build_report(RuntimeError("x"))

    class Boom:
        def mkdir(self, *a, **k):
            raise OSError("nope")

    # Bad directory object → must return None, not raise.
    assert crash.write_crash_log(report, crash_dir=Boom()) is None  # type: ignore[arg-type]


# --- app override ---------------------------------------------------------

def test_handle_exception_stashes_report_and_delegates(monkeypatch):
    called = []
    # Patch the Textual parent so super()._handle_exception is a no-op recorder.
    import textual.app as txapp

    monkeypatch.setattr(txapp.App, "_handle_exception", lambda self, e: called.append(e))

    app = app_mod.WTreeApp()
    assert app._crash_report is None
    err = RuntimeError("inloop")
    app._handle_exception(err)

    assert isinstance(app._crash_report, ErrorReport)
    assert "inloop" in str(app._crash_report)
    assert app._crash_log_path is not None and app._crash_log_path.exists()
    assert called == [err]  # delegated to Textual's teardown


def test_handle_exception_never_raises_even_if_logging_fails(monkeypatch):
    import textual.app as txapp

    monkeypatch.setattr(txapp.App, "_handle_exception", lambda self, e: None)
    monkeypatch.setattr(crash, "build_report", lambda e: (_ for _ in ()).throw(Exception("reporter down")))

    app = app_mod.WTreeApp()
    # Must not propagate the reporter failure.
    app._handle_exception(RuntimeError("inloop"))


# --- main() nets ----------------------------------------------------------

def test_main_outer_net_catches_construction_errors(monkeypatch, capsys):
    monkeypatch.setattr(app_mod.WTreeApp, "run", lambda self: (_ for _ in ()).throw(RuntimeError("ctorboom")))
    with pytest.raises(SystemExit) as ei:
        app_mod.main()
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "ctorboom" in err
    assert "full report written to" in err


def test_main_inloop_net_surfaces_stashed_report(monkeypatch, capsys, tmp_path):
    logpath = tmp_path / "crash-x.log"
    logpath.write_text("stub", encoding="utf-8")

    def fake_run(self):
        self._crash_report = crash.build_report(RuntimeError("loopboom"))
        self._crash_log_path = logpath
        return None

    monkeypatch.setattr(app_mod.WTreeApp, "run", fake_run)
    with pytest.raises(SystemExit) as ei:
        app_mod.main()
    assert ei.value.code == 1
    assert str(logpath) in capsys.readouterr().err


def test_main_clean_run_exits_normally(monkeypatch):
    monkeypatch.setattr(app_mod.WTreeApp, "run", lambda self: None)
    # No crash stashed → main returns without SystemExit.
    assert app_mod.main() is None
