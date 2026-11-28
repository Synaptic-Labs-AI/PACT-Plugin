"""
Location: pact-plugin/tests/test_memory_module_boundaries.py
Summary: Pins the pact-memory module import boundaries with an AST scan: the
store layer (database.py) never imports the identity canonicaliser
(project_identity.py), and the canonicaliser never imports the API layer
(memory_api.py). Function-level (lazy) imports count, because a lazy import
crosses the boundary exactly as a top-level one does.
Used by: the test suite; no production code imports it.

database.py's pre-existing lazy import of memory_api (the CLAUDE.md sync
helper) is outside both rules and is not checked here.
"""

import ast
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "pact-memory" / "scripts"


def imported_modules(source: str) -> set:
    """Every module name `source` imports, at any depth, as the last dotted
    part: `from .project_identity import x`, `from scripts.project_identity
    import x`, `import project_identity` and `from . import project_identity`
    all give `project_identity`."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.rsplit(".", 1)[-1])
            else:
                names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize(
    "importer, forbidden",
    [
        ("database.py", "project_identity"),
        ("project_identity.py", "memory_api"),
    ],
)
def test_module_does_not_import_across_its_boundary(importer, forbidden):
    found = imported_modules((_SCRIPTS / importer).read_text(encoding="utf-8"))
    assert forbidden not in found, (
        f"{importer} imports {forbidden}; import the helper from its own "
        "layer (shared.git_helpers for git file readers) instead"
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .project_identity import gitfile_target\n",
        "def f():\n    from .project_identity import read_git_file\n",
        "from scripts.project_identity import x\n",
        "import project_identity\n",
        "from . import project_identity\n",
        "def f():\n    import scripts.project_identity as p\n",
    ],
)
def test_scanner_finds_every_import_form(source):
    """Positive control: each spelling of the import, top-level or lazy, is
    found, so a clean answer above is a scan that could have fired."""
    assert "project_identity" in imported_modules(source)


def test_scanner_reads_the_real_files():
    """Positive control on the real inputs: a module each file is known to
    import is found."""
    assert "config" in imported_modules((_SCRIPTS / "database.py").read_text(encoding="utf-8"))
    assert "contextvars" in imported_modules(
        (_SCRIPTS / "project_identity.py").read_text(encoding="utf-8")
    )
