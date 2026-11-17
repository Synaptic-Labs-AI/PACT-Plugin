"""Plugin-root conftest for the PACT plugin suite.

Location: pact-plugin/conftest.py

Summary: Makes every skills/*/scripts dir importable for the pin test files
beside skill artifacts (skills/<skill>/test_*.py), which sit outside
tests/conftest.py's subtree, and runs what every session needs: the
import-identity harness, the pytest-randomly seed gate, the HOME and
child-pytest censuses and the CI in-band plugins. tests/conftest.py owns the
full path block for tests/; the membership guard keeps the overlap a no-op
when both conftests load in one run.

Used by: pytest (loaded for every run rooted at or below pact-plugin/).
The plugin root itself is inserted explicitly below (lead-ruled: deliberate
source, not reliance on pytest's conftest-basedir mechanics); the telegram
test family's `from telegram.X import ...` imports resolve through it.

IMPORT CHARTER: at any scope, guarded or not, this file imports only the
standard library, pytest, and tests/ modules whose own imports, followed
through every tests/ module they import, are only those. Never import a
skills/*/scripts module, a plugin-root module or any other package here. A
session-wide plugin that needs another package is registered from
tests/conftest.py instead.

No dynamic import call (importlib.import_module, __import__, importlib.reload,
find_spec, an importlib.util spec or loader call, runpy, exec or eval, an
object that runs source text it is given, such as code.InteractiveConsole,
trace.Trace or pdb.runeval, or a standard-library helper that imports a module it is given by name, such as
pkgutil.resolve_name, pydoc.locate, pydoc.safeimport, unittest.main or
logging.config.dictConfig,
pytest.importorskip, or a pytest plugin-manager method that imports a plugin by
name, such as import_plugin, consider_pluginarg or load_setuptools_entrypoints)
appears in this file or in any tests/ module it follows, at any scope, and
none of them binds pytest_plugins. Any other helper
that imports a module by name is forbidden here too, whether or not the pin
sees it. Exactly two functions deviate, and only they may import outside this
charter:
- tests/ci_environment_binding.py, `_load_verify_script`, runs only when
  GITHUB_ACTIONS=true. It loads the repo-root scripts/verify_ci_environment.py,
  which imports `packaging` (or pip's vendored copy), so a CI session imports
  that package.
- tests/import_identity_map.py, `resolve_origins`, runs only when
  PACT_IDENTITY_MAP_OUT is set, at session finish. Its find_spec imports the
  parent packages of the names the collected tests import, skills/*/scripts
  and plugin-root packages included.
Add no other deviation. tests/test_root_conftest_import_charter.py enforces
this charter. It cannot see a callable reached through getattr or a computed
name. It cannot see a helper it matches by dotted path when that helper's
module is bound other than by an import (a parameter, an assignment, a star
import), and it may not see one whose import name is reused for another module
elsewhere in the file. It cannot see pytest_plugins bound through setattr or
globals(), or a directory tests/conftest.py puts on sys.path other than by
sys.path.insert. It cannot see a module named by a string to a
standard-library helper whose name is too common to refuse, among them
unittest.mock.patch's target, pickle.load and pickle.loads,
importlib.resources.files, an importlib.metadata entry point's load(), the run
of cProfile, profile or pdb, a warning category named in a filter string
(pytest's filterwarnings), and a multiprocessing spawn, whose child imports the
parent's main module. It cannot see a pydoc helper that imports a module only
when it is named by a string (resolve, render_doc, doc, writedoc, help) unless
that name is a literal string, nor doctest.testfile or DocFileSuite given a
package that is not a literal string, the help method of a pydoc.Helper
instance, the run method of a pydoc.ModuleScanner, or an encoding named by a
string, as codecs.lookup, str.encode and bytes.decode take one. Never reach an
import any of these ways here.
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
# once tests/conftest.py has run. The harness imports only the standard
# library; its one dynamic import is a deviation the import charter names.
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
    """Refuse an unseeded pytest-randomly, install the HOME and child-pytest
    censuses, then register the in-band plugins that run inside this process.

    The seed gate and those censuses run here, not in tests/conftest.py, so a
    session that collects only skills-adjacent tests is gated and censused
    too. The censuses and the two CI plugins are plugin objects, because this
    module already re-exports a pytest_sessionstart and a
    pytest_sessionfinish. The two CI plugins import only the standard library
    and pytest, except the CI binding's script load, which the import charter
    names. Both are gated on GITHUB_ACTIONS, so outside CI they do nothing.
    The full-run recorder is registered here rather than through
    PYTEST_PLUGINS: PYTEST_PLUGINS would inject it into every nested pytest
    subprocess the suite spawns.
    """
    import randomly_seed_gate

    randomly_seed_gate.refuse_unseeded(config)

    import child_pytest_census
    import home_census

    home_census.register(config)
    child_pytest_census.register(config)

    from ci_environment_binding import CIEnvironmentBinding
    from ci_full_run_record import FullRunRecord

    if not config.pluginmanager.has_plugin("ci-environment-binding"):
        config.pluginmanager.register(
            CIEnvironmentBinding(), "ci-environment-binding")
    if not config.pluginmanager.has_plugin("full-run-record"):
        config.pluginmanager.register(FullRunRecord(), "full-run-record")
