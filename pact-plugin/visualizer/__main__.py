"""Location: pact-plugin/visualizer/__main__.py
Summary: The ``python -m visualizer`` entry point. Delegates to cli.main and
         propagates its exit code; the CLI owns the Python >= 3.12 floor, the
         argument surface, and the foreground/detached lifecycle.
Used by: pact-plugin/bin/pact-viz (execs exactly this module), docs/README
         launch instructions.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
