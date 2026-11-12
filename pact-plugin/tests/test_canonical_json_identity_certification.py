"""Certification of the SHARING invariant: both marker families hash through
ONE serializer object.

WHAT THIS FILE COVERS THAT test_canonical_json_contract.py CANNOT. That file
pins the BYTES `shared.canonical_json.canonical_bytes` produces, against
hand-typed expectations, and it is the correct protection for the three
serialization parameters. It exercises the SHARED OBJECT, so it stays GREEN
while the two families stop using that object. The header of canonical_json.py
enumerates the terms of the import contract and states that a change to the
module path or the exported name gives an ImportError. A FOURTH FAILURE MODE
IS NOT ON THAT LIST AND FAILS LOUDLY AT NO SITE:

  A SECOND DEFINITION. A re-added local `_canonical_bytes` in
  task_metadata_snapshot.py, or a second `json.dumps` in
  agent_handoff_marker.py, breaks NO import. The contract file keeps passing.
  The two families then compute DIFFERENT keys for one mapping, which is the
  divergence the extraction exists to prevent.

The protection against that was a prose line in the module header saying not
to re-add a local definition. A prose claim adjacent to a green suite is what
this arc measured as insufficient for the serialization parameters, and it is
the same shape here.

THE PROPERTY UNDER CERTIFICATION IS NOT THAT canonical_bytes SERIALIZES ONE
WAY. It is that BOTH FAMILIES HASH THROUGH THIS ONE OBJECT.

--------------------------------------------------------------------------
THE GUARD IS COPIED, NOT INVENTED, AND THE PRECEDENT IS NAMED
--------------------------------------------------------------------------
test_lock_identity_certification.py certifies shared-object identity for the
`file_lock` / `_atomic_write_text` pair through `__module__`, and its own
docstring records why `__module__` rather than `inspect.getsourcefile()`:
`functools.wraps` copies `__module__` through a decorator, so it stays
correct, while `getsourcefile()` resolves a decorated wrapper to
contextlib.py for every twin and fails in both directions. This file uses the
same instrument for the same class of claim.

--------------------------------------------------------------------------
TWO INSTRUMENTS, BECAUSE ONE OF THE TWO FAILURE MODES IS INVISIBLE TO THE
FIRST
--------------------------------------------------------------------------
1. OBJECT IDENTITY (`__module__` plus `is`). This catches a REBOUND name: a
   family that defines its own `_canonical_bytes` and shadows the import.
2. A SOURCE SCAN for a second serializer. `__module__` CANNOT see this one.
   A module can keep importing the shared object, keep the binding correct,
   and call `json.dumps` at a NEW site adjacent to it. The shared object stays
   right while the family stops going through it, so instrument 1 answers
   GREEN about a question it was not asked.

THE SOURCE SCAN PARSES, IT DOES NOT GREP, AND THAT IS LOAD-BEARING. Both
family modules discuss `json.dumps` IN PROSE, in the comments explaining why a
second serialization must not be added. A line-oriented search finds those
sentences and cannot separate them from a call. An AST walk sees calls and
does not see comments.
"""

import ast
import importlib
import subprocess
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_files as _tracked_files

import shared.canonical_json as canonical  # noqa: E402

CANONICAL_MODULE = "shared.canonical_json"

_SHARED_DIR = Path(canonical.__file__).parent
_DEFINING_MODULE_FILE = Path(canonical.__file__)


def _shared_module_paths() -> list[Path]:
    repo_root = _SHARED_DIR.parents[2]
    relative = _SHARED_DIR.relative_to(repo_root).as_posix()
    return sorted(path for path in _tracked_files(repo_root, relative)
                  if path.suffix == ".py")


def _bound_serializer_name(source_path: Path) -> "str | None":
    """The local name a module binds canonical_bytes to, or None.

    Derives the BINDING NAME and the membership, so the snapshot family's
    historical `_canonical_bytes` alias is discovered rather than declared.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not node.module or not node.module.endswith("canonical_json"):
            continue
        for alias in node.names:
            if alias.name == "canonical_bytes":
                return alias.asname or alias.name
    return None


def _discover_family() -> tuple:
    """Every module in hooks/shared/ that imports the shared serializer.

    DERIVED AND NOT DECLARED, so a THIRD family joins this guard by
    construction rather than because somebody remembered to add it.

    🔴 IT PARSES AND DOES NOT GREP, AND THAT IS NOT A STYLE CHOICE. MEASURED
    on this tree: a search for the STRING `canonical_json` returns FOUR files
    and an import parse returns TWO. The two passengers are
    `canonical_json.py` itself, which is the DEFINING module, and
    `hook_infra_classifier.py`, which names `canonical_json` two times in a list
    of module-name strings and imports nothing. A file of which the whole
    subject is module names is the ideal false positive for a name search.
    THE DEFINING MODULE IS THE DANGEROUS ONE: a string derivation enrols it,
    and the import-absence assertion below then FAILS on it, because that
    module imports `json` by necessity. R4 would break R2 in one commit and
    the red would point at the wrong item. The explicit skip below is what
    keeps a member the derivation invented out of the family.
    """
    found = []
    for path in _shared_module_paths():
        if path == _DEFINING_MODULE_FILE:
            continue
        binding = _bound_serializer_name(path)
        if binding:
            found.append((path, binding))
    return tuple(found)


_FAMILY = _discover_family()

# (module object, bound name) for each discovered family member.
FAMILY_BINDINGS = tuple(
    (importlib.import_module(f"shared.{path.stem}"), binding)
    for path, binding in _FAMILY
)

_FAMILY_SOURCES = tuple(path for path, _ in _FAMILY)


def test_discover_family_ignores_an_untracked_serializer_member(tmp_path, monkeypatch):
    """The needle binds canonical_bytes; under the old glob it joins the family."""
    repo = tmp_path / "repo"
    shared = repo / "pact-plugin" / "hooks" / "shared"
    known = shared / "known.py"
    known.parent.mkdir(parents=True)
    known.write_text(
        "from shared.canonical_json import canonical_bytes\n", encoding="utf-8")
    defining = shared / "canonical_json.py"
    defining.write_text("def canonical_bytes(x):\n    return x\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", "pact-plugin"],
        check=True, capture_output=True)
    monkeypatch.setitem(globals(), "_SHARED_DIR", shared)
    monkeypatch.setitem(globals(), "_DEFINING_MODULE_FILE", defining)

    assert [path.name for path, _ in _discover_family()] == ["known.py"]

    stray = shared / "zzprobe.py"
    stray.write_text(
        "from shared.canonical_json import canonical_bytes\n", encoding="utf-8")
    try:
        assert [path.name for path, _ in _discover_family()] == ["known.py"]
    finally:
        stray.unlink()


def _serializer_calls(source_path: Path) -> list[str]:
    """Every `json.dumps` / `json.loads` call in one module, by dotted name.

    Parses rather than greps: the prose in both modules names `json.dumps`
    while calling it at no site, and a line search cannot tell those apart.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("dumps", "loads"):
            if isinstance(func.value, ast.Name) and func.value.id == "json":
                found.append(f"json.{func.attr}")
    return found


def _imported_top_level_modules(source_path: Path) -> list[str]:
    """Every top-level module name a file imports, in ANY spelling.

    THE CALL-SHAPE SCAN ABOVE CANNOT ANSWER THIS QUESTION AND THE TWO ARE
    KEPT SIDE BY SIDE FOR THAT CAUSE. It matches an Attribute call of which
    the value is the Name `json`, which is ONE spelling. MEASURED, three
    forms escape it while the guard stays green: `from json import dumps`
    then a bare `dumps(...)`, `import json as j` then `j.dumps(...)`, and
    `json.JSONEncoder(sort_keys=True).encode(...)`. MEASURED COST of that
    gap: two mutants that differ ONLY in the import line give 3 failed
    against 4 failed, so an alias in the import line costs the guard ONE
    KILL. An IMPORT-ABSENCE check covers the four shapes and any future one,
    because a module that imports `json` in no name form cannot call into it.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module.split(".")[0])
    return names


def _local_definitions(source_path: Path, names: tuple[str, ...]) -> list[str]:
    """Any module-level `def` in one module that shadows a shared name."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]


class TestBothFamiliesHashThroughOneObject:
    """The sharing invariant, certified rather than asserted in prose."""

    @pytest.mark.parametrize("module,binding", FAMILY_BINDINGS)
    def test_family_binding_comes_from_the_canonical_module(
        self, module, binding
    ):
        obtained = getattr(module, binding)
        assert obtained.__module__ == CANONICAL_MODULE, (
            f"{module.__name__}.{binding} came from "
            f"{obtained.__module__!r}, expected {CANONICAL_MODULE!r}. A "
            f"family that defines its own serializer breaks no import and "
            f"keeps the byte-contract test green while the two families "
            f"compute different keys for one mapping."
        )

    def test_the_two_families_bind_the_same_object(self):
        obtained = [getattr(mod, name) for mod, name in FAMILY_BINDINGS]
        assert obtained[0] is obtained[1] is canonical.canonical_bytes, (
            "the two marker families must hash through ONE object. Two "
            "objects that agree today are two serializations with nothing "
            "that makes them agree tomorrow."
        )

    @pytest.mark.parametrize("source_path", _FAMILY_SOURCES, ids=lambda p: p.name)
    def test_no_family_module_calls_a_second_serializer(self, source_path):
        """`__module__` cannot see this failure mode. See the header.

        A module can keep the shared import, keep the binding correct, and
        call json.dumps at a new site beside it.
        """
        calls = _serializer_calls(source_path)
        assert calls == [], (
            f"{source_path.name} calls {calls} directly. Every size "
            f"measurement, truncation head and content key in both families "
            f"must go through shared.canonical_json.canonical_bytes, or the "
            f"two families serialize one mapping two ways."
        )

    @pytest.mark.parametrize("source_path", _FAMILY_SOURCES, ids=lambda p: p.name)
    def test_no_family_module_imports_json_under_any_name(self, source_path):
        """FULLY STRONGER THAN THE CALL-SHAPE SCAN, AND CHEAPER THAN
        WIDENING IT. A module that imports `json` in no name form cannot reach
        `json.dumps` by any spelling, aliased or not."""
        imported = _imported_top_level_modules(source_path)
        assert "json" not in imported, (
            f"{source_path.name} imports `json`. Every serialization in both "
            f"marker families must go through "
            f"shared.canonical_json.canonical_bytes. An import here is the "
            f"precondition for a second serializer, and an ALIAS on that "
            f"import defeats the call-shape scan adjacent to this one. Imports "
            f"found: {sorted(set(imported))}"
        )

    @pytest.mark.parametrize("source_path", _FAMILY_SOURCES, ids=lambda p: p.name)
    def test_no_family_module_redefines_the_serializer(self, source_path):
        shadowed = _local_definitions(
            source_path, ("canonical_bytes", "_canonical_bytes")
        )
        assert shadowed == [], (
            f"{source_path.name} defines {shadowed} locally, which shadows "
            f"the shared import without breaking it."
        )


class TestTheGuardIsNotVacuous:
    """A guard against a silent divergence that could not itself fail would
    RETIRE the concern while covering nothing. Each instrument is shown able
    to answer the other way."""

    def test_the_derived_family_is_not_empty_and_excludes_the_definer(self):
        """🔴 THE DERIVATION ADDED A VACUITY ROUTE THE DECLARATION DID NOT
        HAVE, AND THIS ARM IS THE PRICE OF DERIVING.

        A DECLARED tuple cannot become empty. A DERIVED one can, and an
        empty one makes every parametrized assertion above COLLECT ZERO
        CASES and vanish in silence, which reports as a clean green rather
        than as a broken guard. So the population is asserted here, and it
        is asserted by PROPERTY rather than by a member list, because a
        member list would re-declare the thing the derivation exists to
        stop declaring.
        """
        assert _FAMILY, (
            "the family derivation returned NOTHING. Every parametrized "
            "assertion in this file collects zero cases and the guard is "
            "inert while the suite stays green."
        )
        assert _DEFINING_MODULE_FILE not in _FAMILY_SOURCES, (
            "the DEFINING module entered its own family. It imports `json` "
            "by necessity, so the import-absence assertion would fail on a "
            "member the derivation invented."
        )
        for _, binding in _FAMILY:
            assert binding, "a discovered member carries no bound name"

    def test_module_attribute_discriminates_a_local_definition(self):
        """The mechanism works, recorded as an arm rather than a comment so
        that a Python change making `__module__` non-discriminating fails
        HERE, loudly, rather than turning each assertion above into a
        tautology."""

        def _canonical_bytes(value):  # a stand-in for a re-added local
            return b""

        assert _canonical_bytes.__module__ != CANONICAL_MODULE
        assert canonical.canonical_bytes.__module__ == CANONICAL_MODULE
        assert _canonical_bytes is not canonical.canonical_bytes

    def test_the_source_scan_finds_a_call_and_ignores_the_prose(self, tmp_path):
        """The scan must separate a CALL from a SENTENCE, because both family
        modules carry the sentence and neither carries the call."""
        carries_a_call = tmp_path / "with_call.py"
        carries_a_call.write_text(
            "import json\n"
            "def f(v):\n"
            "    return json.dumps(v).encode()\n",
            encoding="utf-8",
        )
        prose_only = tmp_path / "prose_only.py"
        prose_only.write_text(
            '"""A second json.dumps here would be two serializations."""\n'
            "# Do not add a json.dumps below this line.\n"
            "VALUE = 1\n",
            encoding="utf-8",
        )
        assert _serializer_calls(carries_a_call) == ["json.dumps"]
        assert _serializer_calls(prose_only) == [], (
            "the scan matched a comment or a docstring. A line-oriented "
            "search does exactly that, which is why this one parses."
        )

    def test_the_definition_scan_finds_a_shadowing_def(self, tmp_path):
        shadowing = tmp_path / "shadowing.py"
        shadowing.write_text(
            "from .canonical_json import canonical_bytes\n"
            "def _canonical_bytes(v):\n"
            "    return b''\n",
            encoding="utf-8",
        )
        assert _local_definitions(
            shadowing, ("canonical_bytes", "_canonical_bytes")
        ) == ["_canonical_bytes"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
