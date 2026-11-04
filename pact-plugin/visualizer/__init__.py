"""Location: pact-plugin/visualizer/__init__.py
Summary: Package marker for the PACT Mission Control backend. Exposes only the
         product identity; the modules are imported by the entry points
         (``python -m visualizer`` or ``bin/pact-viz``) so importing the
         package stays cheap and side-effect free.
Used by: visualizer.__main__, cli, server, demo, tests.
"""

from __future__ import annotations

from .settings import APP_LABEL, APP_NAME, VERSION

__all__ = ["APP_LABEL", "APP_NAME", "VERSION", "__version__"]
__version__ = VERSION
