"""Module entry point - enables ``python -m wtree``.

Mirrors the ``wtree`` console script in ``pyproject.toml``: both ultimately
call :func:`wtree.app.main`. Useful when the script isn't on the user's
PATH (fresh installs, sandboxed environments, ad-hoc invocations against
an editable install).
"""

from wtree.app import main

if __name__ == "__main__":
    main()
