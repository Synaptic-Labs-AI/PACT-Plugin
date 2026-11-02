"""Plugin-root conftest for the PACT plugin suite.

Location: pact-plugin/conftest.py

Summary: Owns skills-adjacent coverage ONLY. Pin test files living beside
skill artifacts (skills/<skill>/test_*.py) sit outside tests/conftest.py's
subtree, so this conftest guarantees every skills/*/scripts dir is importable
for them. tests/conftest.py owns the full path block for tests/; the
membership guard keeps the overlap a no-op when both conftests load in one
run.

Used by: pytest (loaded for every run rooted at or below pact-plugin/).
The plugin root itself is inserted explicitly below (lead-ruled: deliberate
source, not reliance on pytest's conftest-basedir mechanics); the telegram
test family's `from telegram.X import ...` imports resolve through it.

NO-IMPORT CHARTER: this file path-INSERTS only. Never import from a scripts
dir at conftest scope — a missing optional dependency there becomes a total
suite collection failure.
"""

import sys
from pathlib import Path

for _scripts_dir in sorted(Path(__file__).parent.glob("skills/*/scripts")):
    _entry = str(_scripts_dir)
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

# The plugin root itself, for the telegram test family's package imports.
_plugin_root = str(Path(__file__).parent)
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

# Import-identity harness registration (layer 4 guard). tests/ is inserted so
# the harness module resolves at conftest load; the insert is a guarded no-op
# once tests/conftest.py has run. The harness is stdlib-only, so this import
# cannot trip the no-import charter's optional-dependency failure mode.
# Name-based hook discovery picks up the re-exported callables session-wide
# (this conftest's tree spans both tests/ and the skills-adjacent files).
_tests_dir = str(Path(__file__).parent / "tests")
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

from import_identity_map import (  # noqa: E402
    pytest_collection_modifyitems,  # noqa: F401 — hook-registration re-export; pytest's name-based discovery is the consumer, the linter can't see it
    pytest_sessionfinish,  # noqa: F401 — hook-registration re-export
    pytest_sessionstart,  # noqa: F401 — hook-registration re-export
)


def pytest_configure(config):
    """Register the in-band plugins that run inside this process.

    Plugin objects, because this module already re-exports a
    pytest_sessionstart. Both are stdlib-only and gated on GITHUB_ACTIONS, so
    outside CI they do nothing. The full-run recorder is registered here
    rather than through PYTEST_PLUGINS: PYTEST_PLUGINS would inject it into
    every nested pytest subprocess the suite spawns.
    """
    from ci_environment_binding import CIEnvironmentBinding
    from ci_full_run_record import FullRunRecord

    if not config.pluginmanager.has_plugin("ci-environment-binding"):
        config.pluginmanager.register(
            CIEnvironmentBinding(), "ci-environment-binding")
    if not config.pluginmanager.has_plugin("full-run-record"):
        config.pluginmanager.register(FullRunRecord(), "full-run-record")
