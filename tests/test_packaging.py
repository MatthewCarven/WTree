"""Smoke tests for the package surface.

These exist to catch silent regressions in the public import paths -
small but easy-to-miss if e.g. someone reorganises ``wtree.sources``
without remembering to keep the package-level re-exports in sync.
"""

from __future__ import annotations


def test_sources_reexports_native_and_mock() -> None:
    """``from wtree.sources import NativeSource, MockSource`` works."""
    from wtree.sources import MockSource, NativeSource
    # Sanity: they're the same classes the submodules expose.
    from wtree.sources.mock import MockSource as _Mock
    from wtree.sources.native import NativeSource as _Native
    assert NativeSource is _Native
    assert MockSource is _Mock


def test_sources_all_lists_concrete_implementations() -> None:
    """The concrete sources show up in ``__all__`` so ``from wtree.sources
    import *`` doesn't silently miss them."""
    import wtree.sources as mod
    assert "NativeSource" in mod.__all__
    assert "MockSource" in mod.__all__
    # The base types stay too.
    assert "Entry" in mod.__all__
    assert "EntrySource" in mod.__all__
    assert "Kind" in mod.__all__


def test_main_module_exposes_main() -> None:
    """``python -m wtree`` works via ``wtree.__main__`` which imports
    ``main`` from ``wtree.app``. The module imports cleanly without
    invoking the app (the ``if __name__ == '__main__'`` guard)."""
    import wtree.__main__ as main_mod
    assert callable(main_mod.main)
    # Same callable as the console script entry point.
    from wtree.app import main as app_main
    assert main_mod.main is app_main
