"""Plugin-root conftest for the PACT plugin suite.

Location: pact-plugin/conftest.py

Summary: Puts on sys.path what a run needs before tests/conftest.py loads --
every skills/*/scripts dir, the plugin root, hooks/ and tests/ -- and registers
two session-wide harnesses by re-export. Pin test files living beside skill
artifacts (skills/<skill>/test_*.py) sit outside tests/conftest.py's subtree,
so this conftest guarantees every skills/*/scripts dir is importable for them.
tests/conftest.py owns the full path block for tests/; the membership guard
keeps the overlap a no-op when both conftests load in one run.

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

# The hooks dir, for the CLAUDE.md guard's `shared.paths` and
# `shared.project_scope` imports, which run at pytest_configure. It must
# resolve under THAT name -- the one every test uses -- so the guard binds the
# same module objects the tests do. LOAD-BEARING in one invocation class: when
# every path argument lies outside tests/ (a skills-adjacent test file alone),
# tests/conftest.py is not an initial conftest and nothing else puts hooks/ on
# the path before the guard's configure runs. When tests/ is collected,
# tests/conftest.py inserts the same entry as well and this one is redundant.
# No new sanctioned root: test_path_setup_pin.py already lists hooks/.
_hooks_dir = str(Path(__file__).parent / "hooks")
if _hooks_dir not in sys.path:
    sys.path.insert(0, _hooks_dir)

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

# CLAUDE.md session tripwire (the child-process half). Same re-export idiom:
# the logic lives in tests/claude_md_guard.py, which keeps this file thin per
# the no-import charter above and keeps the guard testable on its own. Its
# `shared` imports happen inside its hook bodies, not here.
from claude_md_guard import (  # noqa: E402
    pytest_configure,  # noqa: F401 — hook-registration re-export
    pytest_unconfigure,  # noqa: F401 — hook-registration re-export
)
