"""Restore ambient environment variables, then reload modules that read them.

Used by test modules that reload a gate under a test-chosen mode variable: on
exit the variables return to the values they had on entry (set, or absent), and
each named module still in ``sys.modules`` is reloaded so its import-time
constants match that restored environment. Modules are reloaded, never evicted:
sibling test files import them at collection and call ``importlib.reload``,
which raises for a module missing from ``sys.modules``.
"""

import contextlib
import importlib
import os
import sys


@contextlib.contextmanager
def restored_env_and_reloaded(env_names, module_names):
    """Snapshot ``env_names``; on exit restore them, then reload ``module_names``.

    A variable absent on entry is removed on exit; a variable present on entry
    gets its entry value back. Modules not present in ``sys.modules`` on exit
    are skipped.
    """
    saved = {name: os.environ.get(name) for name in env_names}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for module_name in module_names:
            module = sys.modules.get(module_name)
            if module is not None:
                importlib.reload(module)
