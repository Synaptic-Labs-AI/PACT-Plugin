"""Child process for the zero-CREATE read arm.

ONE VERB PER PROCESS, because `memory_init._initialized` is per-process: in a
single process the first read initialises the schema and every later read
measures a warmed store. The parent test asserts the store's shape, so this
worker only has to run the verb.

Usage:
  python zero_create_worker.py <verb> <store-path>             # caller-named
  python zero_create_worker.py <verb> <derived-root> derived   # derived route
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
for entry in (
    PLUGIN_ROOT,
    PLUGIN_ROOT / "hooks",
    PLUGIN_ROOT / "skills" / "pact-memory",
    PLUGIN_ROOT / "skills" / "pact-memory" / "scripts",
):
    sys.path.insert(0, str(entry))


def main() -> int:
    verb = sys.argv[1]
    target = Path(sys.argv[2])
    derived = len(sys.argv) > 3 and sys.argv[3] == "derived"
    if derived:
        # No explicit path: the instance resolves the DERIVED store, which is
        # the route that bootstraps an absent store on the first operation.
        os.environ["PACT_TEST_MEMORY_DIR"] = str(target)
        db_path = None
    else:
        db_path = target
    from scripts.memory_api import PACTMemory

    memory = PACTMemory(session_id="s", db_path=db_path)
    if verb == "list":
        memory.list(limit=5)
    elif verb == "search":
        memory.search("needle", sync_to_claude=False)
    elif verb == "search_by_file":
        memory.search_by_file("src/x.py", limit=5)
    elif verb == "sync":
        memory.sync()
    elif verb == "get_status":
        memory.get_status()
    elif verb == "_resolve_id_or_full":
        # THROUGH THE PRODUCTION SURFACE, NOT A HAND-OPENED CONNECTION.
        # `get()` is the public path that resolves a prefix via
        # `_resolve_id_or_full`; opening `db_connection` here would create
        # the file itself and the arm would measure the fixture, not the
        # read path's own guard.
        memory.get("abcd1234")
    else:
        raise SystemExit(f"unknown verb {verb!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())