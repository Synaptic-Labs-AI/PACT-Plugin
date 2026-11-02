"""Pure core for the fast PR suite selection.

WHAT THIS OWNS
  Everything that decides WHICH test files the advisory fast lane runs. The
  decision is a whole-file selection over an explicit exclusion manifest: a
  new test file is included by default, and an excluded file comes back when
  it, one of its seams, or anything its static import closure can see
  changed: a module it loads, a file it names, a directory it builds or
  lists, or a conftest fixture it uses.

WHAT THIS MUST NEVER DO
  Narrow silently. Every unanswerable question -- a git failure, an invalid
  base, missing file contents, a process-global conftest change -- resolves
  to SELECT_ALL with a named reason. The fast lane may run slow; it may
  never run narrow beyond the bounded cases the closure cannot see (computed
  import names, computed fixture names, paths built only from computed
  segments), which the contract tests keep out of every excluded closure.

  No module-level I/O. discover(), load_manifest(), changed_paths(),
  ScanIndex, import_closure(), gather_facts() and select_for_change() touch
  the filesystem or git. select(), classify_conftest_change(),
  json_change_is_value_inert(), to_pytest_args(), build_argv() and
  parse_pytest_summary() are pure.

PATH DOMAINS (the single easiest thing to get wrong)
  Manifest paths, discovered corpus paths, and Selection members are
  PLUGIN-RELATIVE (relative to pact-plugin/).
  selection_machinery, value_inert_keys, constraints-ci.txt and pyproject.toml
  are REPO-RELATIVE, because git reports repo-relative paths.
  repo_to_plugin() is the only conversion, and it is table-tested.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PLUGIN_DIRNAME = "pact-plugin"
MANIFEST_SCHEMA_VERSION = 1
SUPPORTED_PATH_BASE = "plugin_root"
REJECTED_PATH_BASE_ALIAS = "paths_relative_to"

_REPO_TO_PLUGIN_PREFIX = PLUGIN_DIRNAME + "/"
_GLOB_CHARS = ("*", "?", "[", "]")

# The manifest is a reviewed data file, so its shape is pinned here as well as
# in the shipped JSON. A key added to one without the other is an error, never
# a silently ignored field.
_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "path_base", "baseline",
    "selection_machinery", "value_inert_keys", "guard_files", "exclusions",
})
_BASELINE_KEYS = frozenset({
    "measured_at_commit", "interpreter", "pytest", "platform",
    "collected_node_ids", "excluded_node_ids", "retained_node_ids",
    "retained_files", "excluded_files", "displayed_seconds_total",
    "wall_seconds_total",
})
_EXCLUSION_KEYS = frozenset({
    "path", "displayed_seconds", "tests", "mechanism", "production_seams",
})

# The ten discovery/isolation guards are ALWAYS retained, and the loader pins
# their IDENTITIES, not merely their number. A count-only check is a hole with
# exactly the shape it looks like it closes: a manifest could name ten other
# files as guards and move a real guard into `exclusions`, and the
# guard-in-exclusions rule -- which only tests membership against the
# substituted list -- would accept it. The fast lane would then silently skip
# the module that protects instruction-file content. Ten entries is not the
# property; the RIGHT ten is. Changing this set is a reviewed change to the
# manifest AND this constant, in the same commit.
EXPECTED_GUARD_FILES = frozenset({
    "tests/test_ci_collection_scope.py",
    "tests/test_import_hygiene.py",
    "tests/test_local_lint_parity.py",
    "tests/test_path_setup_pin.py",
    "tests/test_suite_home_isolation.py",
    "tests/test_conftest_config_root_isolation.py",
    "tests/test_audit_protocol.py",
    "tests/test_memory_store_isolation.py",
    "skills/pact-handoff-harvest/test_skill_loading_harvest.py",
    "skills/pact-agent-teams/test_skill_loading_agent_teams.py",
})

EXPECTED_EXCLUSION_COUNT = 24

_DEPENDENCY_PINS = ("constraints-ci.txt", "pyproject.toml")

_TEST_FILE_RE = re.compile(r"(?:^|/)test_[^/]*\.py$")
_FULL_SHA_RE = re.compile(r"[0-9a-fA-F]{7,40}")


class ManifestError(Exception):
    """The manifest is invalid. Carries every violation, one per list item."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class Exclusion:
    path: str
    displayed_seconds: float
    tests: int
    mechanism: str
    production_seams: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    path_base: str
    selection_machinery: tuple[str, ...]
    guard_files: tuple[str, ...]
    exclusions: tuple[Exclusion, ...]
    # Repo-relative JSON files whose change is ignored when only the named
    # top-level keys differ (the version bump every PR carries).
    value_inert_keys: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class AddBack:
    path: str
    reason: str
    matched_changed: str
    matched_seam: str | None
    # What in the exclusion's closure matched: a fixture name, a module, a
    # literal or a scanned directory. None for the two original reasons.
    evidence: str | None = None


@dataclass(frozen=True)
class Selection:
    status: str
    select_all_reason: str | None
    discovered: frozenset[str]
    included: frozenset[str]
    excluded: frozenset[str]
    added_back: tuple[AddBack, ...]


# --------------------------------------------------------------------------
# Manifest loading
# --------------------------------------------------------------------------


def _derive_repo_root(manifest_path: Path) -> Path:
    """The manifest lives in <repo_root>/scripts/; the repo root is its parent."""
    return manifest_path.resolve().parent.parent


def _derive_plugin_root(manifest_path: Path) -> Path:
    return _derive_repo_root(manifest_path) / PLUGIN_DIRNAME


def _check_relative(raw: object, field: str, errors: list[str]) -> str | None:
    """Reject a path that is not a plain, relative, glob-free string."""
    if not isinstance(raw, str) or not raw:
        errors.append(f"{field}: expected a non-empty string")
        return None
    if any(ch in raw for ch in _GLOB_CHARS):
        errors.append(
            f"{field}: {raw!r} contains a glob character "
            f"({', '.join(_GLOB_CHARS)}). The manifest uses explicit paths so "
            f"a rename fails loudly instead of quietly extending a pattern."
        )
        return None
    if raw.startswith("/") or "\\" in raw or ".." in Path(raw).parts:
        errors.append(
            f"{field}: {raw!r} must be a relative path with no '..' segment "
            f"and no backslash."
        )
        return None
    return raw


def _check_under(raw: str, field: str, base: Path, errors: list[str]) -> Path | None:
    """Resolve `raw` under `base`; reject anything that escapes it."""
    resolved = (base / raw).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        errors.append(
            f"{field}: {raw!r} resolves to {resolved}, which is outside the "
            f"path base {base}."
        )
        return None
    return resolved


def _validate_data_path(
    raw: object,
    field: str,
    plugin_root: Path,
    errors: list[str],
    *,
    require_py: bool = True,
) -> str | None:
    rel = _check_relative(raw, field, errors)
    if rel is None:
        return None
    if require_py and not rel.endswith(".py"):
        errors.append(
            f"{field}: {rel!r} must end in .py. Seams and corpus entries are "
            f"explicit Python files."
        )
        return None
    resolved = _check_under(rel, field, plugin_root, errors)
    if resolved is None:
        return None
    if not resolved.is_file():
        errors.append(
            f"{field}: {rel!r} resolves to {resolved}, which does not exist "
            f"under the path base ({SUPPORTED_PATH_BASE}). A manifest written "
            f"with repo-relative spellings lands here; rewrite the entry "
            f"relative to {PLUGIN_DIRNAME}/."
        )
        return None
    return rel


def _validate_top_level_keys(data: dict, errors: list[str]) -> None:
    for key in sorted(set(data) - _TOP_LEVEL_KEYS):
        if key == "production_roots":
            # Named explicitly rather than folded into the generic message:
            # this field was load-bearing once, and a reader who finds it in
            # an old manifest must be told what replaced it, not just that it
            # is unwelcome.
            errors.append(
                "manifest: 'production_roots' was removed from the schema. "
                "Selection no longer uses an allowlist of production "
                "directories; it uses a plugin-wide denylist, where any "
                "changed path under pact-plugin/ that is neither a test "
                "module nor a mapped seam forces SELECT_ALL. Delete the field "
                "rather than replacing it with another allowlist."
            )
        else:
            errors.append(f"manifest: unknown key {key!r}")
    for key in sorted(_TOP_LEVEL_KEYS - set(data)):
        errors.append(f"manifest: missing required key {key!r}")


def _validate_baseline(baseline: object, errors: list[str]) -> None:
    if not isinstance(baseline, dict):
        errors.append("baseline: expected an object")
        return
    for key in sorted(set(baseline) - _BASELINE_KEYS):
        errors.append(f"baseline: unknown key {key!r}")
    for key in sorted(_BASELINE_KEYS - set(baseline)):
        errors.append(f"baseline: missing required key {key!r}")


def _validate_selection_machinery(entries: object, repo_root: Path,
                                  errors: list[str]) -> tuple[str, ...]:
    if not isinstance(entries, list) or not entries:
        errors.append("selection_machinery: expected a non-empty list")
        return ()
    validated: list[str] = []
    for index, raw in enumerate(entries):
        field = f"selection_machinery[{index}]"
        rel = _check_relative(raw, field, errors)
        if rel is None:
            continue
        resolved = _check_under(rel, field, repo_root, errors)
        if resolved is None:
            continue
        if not resolved.is_file():
            errors.append(
                f"{field}: {rel!r} resolves to {resolved}, which does not "
                f"exist. Selection machinery paths are repo-relative."
            )
            continue
        validated.append(rel)
    return tuple(validated)


def _validate_value_inert_keys(entries: object, repo_root: Path,
                               machinery: tuple[str, ...],
                               errors: list[str]
                               ) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """{repo-relative JSON file: [top-level keys whose change is ignored]}."""
    if not isinstance(entries, dict):
        errors.append("value_inert_keys: expected an object")
        return ()
    validated: list[tuple[str, tuple[str, ...]]] = []
    for raw, keys in sorted(entries.items()):
        field = f"value_inert_keys[{raw!r}]"
        rel = _check_relative(raw, field, errors)
        if rel is None:
            continue
        if rel in machinery or rel in _DEPENDENCY_PINS:
            errors.append(f"{field}: {rel!r} is selection machinery")
            continue
        if not rel.endswith(".json"):
            errors.append(f"{field}: {rel!r} must be a .json file")
            continue
        resolved = _check_under(rel, field, repo_root, errors)
        if resolved is None:
            continue
        try:
            document = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            errors.append(
                f"{field}: {rel!r} is not a readable JSON file. Paths are "
                f"repo-relative."
            )
            continue
        if not isinstance(document, dict):
            errors.append(f"{field}: {rel!r} is not a JSON object")
            continue
        if (not isinstance(keys, list) or not keys
                or not all(isinstance(key, str) and key for key in keys)):
            errors.append(f"{field}: expected a non-empty list of key names")
            continue
        validated.append((rel, tuple(keys)))
    return tuple(validated)


def _validate_guard_files(entries: object, plugin_root: Path,
                          errors: list[str]) -> tuple[str, ...]:
    if not isinstance(entries, list):
        errors.append("guard_files: expected a list")
        return ()
    validated: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(entries):
        field = f"guard_files[{index}]"
        rel = _validate_data_path(raw, field, plugin_root, errors)
        if rel is None:
            continue
        if not _TEST_FILE_RE.search(rel):
            errors.append(f"{field}: {rel!r} is not a test_*.py module")
            continue
        if rel in seen:
            errors.append(f"{field}: duplicate guard file {rel!r}")
            continue
        seen.add(rel)
        validated.append(rel)
    if len(entries) != len(EXPECTED_GUARD_FILES):
        errors.append(
            f"guard_files: expected exactly {len(EXPECTED_GUARD_FILES)} "
            f"entries, found {len(entries)}"
        )
    elif seen != EXPECTED_GUARD_FILES:
        missing = sorted(EXPECTED_GUARD_FILES - seen)
        unexpected = sorted(seen - EXPECTED_GUARD_FILES)
        errors.append(
            "guard_files: the guard set changed. Missing: "
            f"{missing}. Unexpected: {unexpected}. The ten discovery/"
            "isolation guards are always retained; substituting another file "
            "lets a real guard be moved into exclusions without tripping the "
            "membership rule. Update EXPECTED_GUARD_FILES only in a reviewed "
            "manifest change."
        )
    return tuple(validated)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_exclusions(entries: object, plugin_root: Path,
                         errors: list[str]) -> tuple[Exclusion, ...]:
    if not isinstance(entries, list):
        errors.append("exclusions: expected a list")
        return ()
    if len(entries) != EXPECTED_EXCLUSION_COUNT:
        errors.append(
            f"exclusions: expected exactly {EXPECTED_EXCLUSION_COUNT} "
            f"entries, found {len(entries)}. A different set needs a separate "
            f"reviewed change with fresh measurement."
        )
    validated: list[Exclusion] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        field = f"exclusions[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{field}: expected an object")
            continue
        for key in sorted(set(entry) - _EXCLUSION_KEYS):
            errors.append(f"{field}: unknown key {key!r}")
        for key in sorted(_EXCLUSION_KEYS - set(entry)):
            errors.append(f"{field}: missing required key {key!r}")
        rel = _validate_data_path(entry.get("path"), f"{field}.path",
                                  plugin_root, errors)
        if rel is not None:
            if not rel.startswith("tests/") or not _TEST_FILE_RE.search(rel):
                errors.append(
                    f"{field}.path: {rel!r} must be under tests/ and match "
                    f"tests/**/test_*.py"
                )
                rel = None
            elif rel in seen:
                errors.append(f"{field}.path: duplicate exclusion {rel!r}")
                rel = None
            else:
                seen.add(rel)

        seconds = entry.get("displayed_seconds")
        if not _is_number(seconds) or seconds <= 0:
            errors.append(f"{field}.displayed_seconds: expected a number > 0")
        tests = entry.get("tests")
        if not isinstance(tests, int) or isinstance(tests, bool) or tests <= 0:
            errors.append(f"{field}.tests: expected an integer > 0")
        mechanism = entry.get("mechanism")
        if not isinstance(mechanism, str) or not mechanism.strip():
            errors.append(f"{field}.mechanism: expected a non-empty string")

        # An EMPTY list is legal: an exclusion whose import closure reaches no
        # production module comes back only when its own module changes. The
        # import-closure contract test, not this loader, decides whether a
        # list is complete.
        seams_raw = entry.get("production_seams")
        seams: list[str] = []
        if not isinstance(seams_raw, list):
            errors.append(f"{field}.production_seams: expected a list")
        else:
            for seam_index, seam in enumerate(seams_raw):
                seam_field = f"{field}.production_seams[{seam_index}]"
                if isinstance(seam, str) and seam.startswith("tests/"):
                    # A changed path under tests/ is a test module or a
                    # test-support file; both are decided before seams are
                    # consulted, so a seam here could never fire.
                    errors.append(
                        f"{seam_field}: {seam!r} is under tests/. A seam "
                        f"there can never fire: a changed test-support file "
                        f"already selects everything."
                    )
                    continue
                seam_rel = _validate_data_path(seam, seam_field, plugin_root,
                                               errors)
                if seam_rel is not None:
                    seams.append(seam_rel)

        if rel is not None:
            validated.append(Exclusion(
                path=rel,
                displayed_seconds=float(seconds),
                tests=int(tests),
                mechanism=mechanism,
                production_seams=tuple(seams),
            ))
    return tuple(validated)


def load_manifest(path: Path, plugin_root: Path | None = None) -> Manifest:
    """Read and validate the exclusion manifest. Raises ManifestError.

    Every violation is reported, not just the first: an operator fixing a
    manifest should need one round trip, not one per line.
    """
    manifest_path = Path(path)
    errors: list[str] = []
    if not manifest_path.is_file():
        raise ManifestError([f"manifest: {manifest_path} does not exist"])
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(
            [f"manifest: {manifest_path} is not readable JSON ({exc})"]
        ) from exc
    if not isinstance(data, dict):
        raise ManifestError(["manifest: expected a JSON object at top level"])

    _validate_top_level_keys(data, errors)

    version = data.get("schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        errors.append(
            f"schema_version: expected {MANIFEST_SCHEMA_VERSION}, found "
            f"{version!r}"
        )

    path_base = data.get("path_base")
    if path_base != SUPPORTED_PATH_BASE:
        hint = ""
        if REJECTED_PATH_BASE_ALIAS in data:
            hint = (
                f" The key {REJECTED_PATH_BASE_ALIAS!r} is not accepted; the "
                f"canonical field is 'path_base'."
            )
        errors.append(
            f"path_base: expected {SUPPORTED_PATH_BASE!r}, found "
            f"{path_base!r}.{hint}"
        )

    repo_root = _derive_repo_root(manifest_path)
    plugin_root = Path(plugin_root) if plugin_root is not None else (
        repo_root / PLUGIN_DIRNAME
    )
    if not plugin_root.is_dir():
        errors.append(
            f"path_base: {plugin_root} is not a directory, so plugin-relative "
            f"paths cannot be validated"
        )
        raise ManifestError(errors)

    _validate_baseline(data.get("baseline"), errors)
    selection_machinery = _validate_selection_machinery(
        data.get("selection_machinery"), repo_root, errors)
    value_inert_keys = _validate_value_inert_keys(
        data.get("value_inert_keys"), repo_root, selection_machinery, errors)
    guard_files = _validate_guard_files(
        data.get("guard_files"), plugin_root, errors)
    exclusions = _validate_exclusions(
        data.get("exclusions"), plugin_root, errors)

    guard_set = set(guard_files)
    for exclusion in exclusions:
        if exclusion.path in guard_set:
            errors.append(
                f"exclusions: {exclusion.path!r} is also a guard file. The "
                f"ten guards are always retained."
            )

    if errors:
        raise ManifestError(errors)
    return Manifest(
        path_base=SUPPORTED_PATH_BASE,
        selection_machinery=selection_machinery,
        guard_files=guard_files,
        exclusions=exclusions,
        value_inert_keys=value_inert_keys,
    )


# --------------------------------------------------------------------------
# Discovery and changed paths
# --------------------------------------------------------------------------


def discover(repo_root: Path) -> frozenset[str]:
    """Every pact-plugin/**/test_*.py, plugin-relative and sorted.

    `.worktrees` is excluded on the path RELATIVE to repo_root, never on the
    absolute path: a worktree checkout carries `.worktrees` in every absolute
    path, so an absolute test would exclude the whole corpus.
    """
    root = Path(repo_root).resolve()
    plugin_root = root / PLUGIN_DIRNAME
    found: set[str] = set()
    for path in plugin_root.rglob("test_*.py"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if ".worktrees" in relative.parts:
            continue
        found.add(path.relative_to(plugin_root).as_posix())
    return frozenset(found)


def repo_to_plugin(path: str) -> str | None:
    """Repo-relative path -> plugin-relative, or None when outside the plugin.

    A file exactly at `pact-plugin` (not a directory) normalises to the empty
    string, which every caller treats as ignored.
    """
    if path == PLUGIN_DIRNAME:
        return ""
    if path.startswith(_REPO_TO_PLUGIN_PREFIX):
        return path[len(_REPO_TO_PLUGIN_PREFIX):]
    return None


def changed_paths(base_sha: str | None,
                  repo_root: Path) -> tuple[list[str], str]:
    """Changed repo-relative paths against `base_sha`; never raises.

    Statuses: ok | invalid-base | git-error | empty-diff. `--no-renames` is
    required so a rename shows both the old (deleted) and new (added) path.
    `-z` is required so a non-ASCII path comes back verbatim: without it git
    C-quotes the path, the quoted spelling matches no plugin prefix, and the
    change is silently dropped. `core.quotePath=false` changes nothing while
    `-z` is present; it is a second guard on the same property.
    """
    # An all-zeros SHA is a sentinel, not a commit: git would answer
    # "unknown revision" and the caller would read `git-error`, which says the
    # repository failed rather than that the caller supplied no usable base.
    # Both are non-ok and both SELECT_ALL, but the status is part of the
    # contract and drives the recorded reason.
    stripped = base_sha.strip() if isinstance(base_sha, str) else ""
    if (not stripped or not _FULL_SHA_RE.fullmatch(stripped)
            or set(stripped) == {"0"}):
        return [], "invalid-base"
    try:
        completed = subprocess.run(
            ["git", "-c", "core.quotePath=false", "diff", "--name-only",
             "-z", "--no-renames", f"{stripped}...HEAD"],
            cwd=str(repo_root), capture_output=True, timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return [], "git-error"
    if completed.returncode != 0:
        return [], "git-error"
    try:
        stdout = completed.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return [], "git-error"
    paths = [entry for entry in stdout.split("\0") if entry]
    if not paths:
        return [], "empty-diff"
    return paths, "ok"


# --------------------------------------------------------------------------
# Per-file scan index (filesystem reads, cached per file)
# --------------------------------------------------------------------------

# Calls whose string arguments name a filesystem path. A literal in one of
# these positions, or an operand of `/`, is a PATH-CONSTRUCTION literal: only
# those can name a directory a test reads. A bare word elsewhere ('commands'
# as a dict key) is not a path, and matching it re-created A6.
_PATH_CALLS = frozenset({
    "join", "joinpath", "Path", "PurePath", "glob", "rglob", "iglob", "open",
    "iterdir", "listdir", "walk", "scandir", "exists", "is_file", "is_dir",
    "read_text", "read_bytes",
})
# Directory scans. A scope that calls one of these and is anchored to the
# real checkout through __file__ is a SCANNER.
_SCAN_CALLS = frozenset({"glob", "rglob", "iterdir", "listdir", "walk",
                         "scandir"})
_DYNAMIC_IMPORT_CALLS = frozenset({
    "import_module", "__import__", "run_path", "run_module",
})
_SEMVER_RE = re.compile(r"v?\d+\.\d+\.\d+")
_MAX_LITERAL = 300


@dataclass(frozen=True)
class FileScan:
    edges: tuple[str, ...]
    import_targets: frozenset[str]
    literals: frozenset[str]
    names: frozenset[str]
    path_literals: frozenset[str]
    # Plugin directories this module scans from the real checkout, or None
    # when it scans none. "." is the whole plugin.
    scanner_roots: frozenset[str] | None
    dynamic_imports: tuple[str, ...]
    dynamic_fixtures: tuple[str, ...]


@dataclass(frozen=True)
class ExclusionView:
    """What an exclusion's import closure can see, unioned over its modules."""
    closure: frozenset[str]
    import_targets: frozenset[str]
    literals: frozenset[str]
    test_names: frozenset[str]
    path_literals: frozenset[str]
    scan_roots: frozenset[str] = frozenset()


_EMPTY_SCAN = FileScan((), frozenset(), frozenset(), frozenset(), frozenset(),
                       None, (), ())

# The sys.path entries the two conftests insert, plugin-relative. A test's
# bare `import x` resolves against these, so the closure does too.
_IMPORT_ROOTS = (
    "",
    "tests",
    "hooks",
    "skills/pact-memory",
    "skills/pact-memory/scripts",
    "skills/pact-coding-standards/scripts",
)


def _import_roots(plugin_root: Path) -> list[Path]:
    roots = [plugin_root / rel for rel in _IMPORT_ROOTS]
    roots.extend(sorted(plugin_root.glob("skills/*/scripts")))
    return [root for root in dict.fromkeys(roots) if root.is_dir()]


def _module_candidates(name: str, roots: list[Path]) -> list[Path]:
    """Every file `import name` could load under any root, existing or not."""
    parts = name.split(".")
    found: list[Path] = []
    for root in roots:
        target = root.joinpath(*parts)
        found.append(target.with_suffix(".py"))
        found.append(target / "__init__.py")
    return found


def _resolve_module(name: str, roots: list[Path]) -> list[Path]:
    """Every file `import name` can load under any root: its packages too.

    Resolution is a UNION over roots, not first-match: an over-approximated
    closure costs an extra add-back; an under-approximated one drops a test.
    """
    parts = name.split(".")
    found: list[Path] = []
    for root in roots:
        target = root.joinpath(*parts)
        if (target / "__init__.py").is_file():
            leaf = target / "__init__.py"
        elif target.with_suffix(".py").is_file():
            leaf = target.with_suffix(".py")
        else:
            continue
        for depth in range(1, len(parts)):
            init = root.joinpath(*parts[:depth], "__init__.py")
            if init.is_file():
                found.append(init)
        found.append(leaf)
    return found


def _literal_script(value: str, plugin_root: Path,
                    roots: list[Path]) -> list[Path]:
    """A string literal naming a production .py file, e.g. a hook run by path.

    Tests that run a hook as a subprocess name it as a string, which no
    import edge sees. Test-tree files and conftests are skipped: a literal
    there is a fixture file name, not a dependency.
    """
    if not value.endswith(".py") or "\n" in value or len(value) > 200:
        return []
    hits: list[Path] = []
    for base in (plugin_root, *roots):
        candidate = base / value
        if not candidate.is_file() or candidate.name == "conftest.py":
            continue
        try:
            relative = candidate.resolve().relative_to(plugin_root.resolve())
        except ValueError:
            continue
        if relative.parts and relative.parts[0] == "tests":
            continue
        hits.append(candidate)
    return hits


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _str_constant(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_loop_names(tree: ast.AST) -> set[str]:
    """Names a `for` binds only to string literals: `for a, b in (("x", "y"),)`.

    An import or fixture request through such a name is a literal one.
    """
    bindings: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            bindings.setdefault(node.targets[0].id, []).append(node.value)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.comprehension)):
            continue
        rows = node.iter
        if isinstance(rows, ast.Name) and len(bindings.get(rows.id, ())) == 1:
            rows = bindings[rows.id][0]  # `packages = [...]; for x in packages`
        if not isinstance(rows, (ast.Tuple, ast.List)) or not rows.elts:
            continue
        targets = (node.target.elts if isinstance(node.target, ast.Tuple)
                   else [node.target])
        literal = all(
            (_str_constant(row) is not None) if len(targets) == 1 else (
                isinstance(row, (ast.Tuple, ast.List))
                and all(_str_constant(cell) is not None for cell in row.elts))
            for row in rows.elts)
        if literal:
            names.update(target.id for target in targets
                         if isinstance(target, ast.Name))
    return names


def _module_rows(tree: ast.Module) -> dict[str, ast.AST]:
    """Module-level names bound to a value (rows of a literal table too)."""
    bound: dict[str, ast.AST] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = statement
        elif (isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)):
            bound[statement.target.id] = statement
    return bound


def _uses_file(node: ast.AST) -> bool:
    """`__file__`, or another module's `x.__file__` (a checkout path whose
    depth this resolver does not follow, so it resolves to the plugin root)."""
    return any((isinstance(inner, ast.Name) and inner.id == "__file__")
               or (isinstance(inner, ast.Attribute)
                   and inner.attr == "__file__")
               for inner in ast.walk(node))


def _is_scan_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _call_name(node) in _SCAN_CALLS


def _anchor_of(node: ast.AST,
               anchors: dict[str, tuple[int, tuple[str, ...]]]
               ) -> tuple[int, tuple[str, ...]] | None:
    """Where an anchored expression points: (levels above its own FILE,
    literal subfolders joined after that).

    Path(__file__) is (0, ()); .parent adds a level; .parents[n] adds n + 1;
    os.path.dirname adds one; `/ "x"`, joinpath("x") and os.path.join(.., "x")
    append "x"; .resolve() and Path()/str() wrappers change nothing; a name
    bound to another anchor continues from it. None for anything else.
    """
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return 0, ()
        return anchors.get(node.id)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        inner = _anchor_of(node.value, anchors)
        if inner is None:
            return None
        level, parts = inner
        return (level + 1, ()) if not parts else (level, parts[:-1])
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)):
        inner = _anchor_of(node.value.value, anchors)
        if inner is None or inner[1]:
            return None
        return inner[0] + node.slice.value + 1, ()
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        inner = _anchor_of(node.left, anchors)
        text = _str_constant(node.right)
        if inner is None or text is None:
            return None
        return inner[0], inner[1] + tuple(part for part in text.split("/")
                                           if part)
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in ("resolve", "absolute") and isinstance(node.func,
                                                         ast.Attribute):
            return _anchor_of(node.func.value, anchors)
        if name in ("Path", "PurePath", "str", "abspath", "realpath",
                    "fspath") and len(node.args) == 1:
            return _anchor_of(node.args[0], anchors)
        if name == "dirname" and len(node.args) == 1:
            inner = _anchor_of(node.args[0], anchors)
            if inner is None:
                return None
            level, parts = inner
            return (level + 1, ()) if not parts else (level, parts[:-1])
        if name in ("joinpath", "join") and node.args:
            if name == "joinpath" and isinstance(node.func, ast.Attribute):
                head, rest = _anchor_of(node.func.value, anchors), node.args
            else:
                head, rest = _anchor_of(node.args[0], anchors), node.args[1:]
            texts = [_str_constant(part) for part in rest]
            if head is None or any(text is None for text in texts):
                return None
            return head[0], head[1] + tuple(
                piece for text in texts for piece in text.split("/") if piece)
    return None


def _scanner_roots(tree: ast.Module, rel: str,
                   is_tracked_dir) -> frozenset[str] | None:
    """Plugin directories this module scans from the real checkout.

    A scope is each function, or the module body. It is a scanner scope when
    it calls a directory scan AND is anchored to the real checkout: it uses
    __file__, or a module-level name assigned (transitively) from __file__.
    Its roots are the tracked plugin directories named by string literals in
    the scope or in the module-level constants it uses, from the plugin root
    or joined after the anchor. An anchored scope naming none scans from its
    anchor's directory, at the anchor's depth; a depth that cannot be
    resolved is the plugin root. None when no scope scans.
    """
    constants = _module_rows(tree)
    anchors: set[str] = set()
    changed = True
    while changed:  # transitive: HERE = Path(__file__); ROOT = HERE.parent
        changed = False
        for name, statement in constants.items():
            if name in anchors:
                continue
            if _uses_file(statement) or any(
                    isinstance(inner, ast.Name) and inner.id in anchors
                    for inner in ast.walk(statement)):
                anchors.add(name)
                changed = True
    # Where each anchor points. One resolver serves every spelling:
    # .parent chains, .parents[N] with a literal N, nested
    # os.path.dirname(__file__), and literal joins after them.
    resolved: dict[str, tuple[int, tuple[str, ...]]] = {}
    for _ in range(len(constants)):
        for name in anchors:
            value = getattr(constants[name], "value", None)
            if value is not None and name not in resolved:
                found_anchor = _anchor_of(value, resolved)
                if found_anchor is not None:
                    resolved[name] = found_anchor
    module_parts = Path(rel).parts

    def directory_at(anchor: tuple[int, tuple[str, ...]] | None) -> str:
        """The plugin directory an anchor names. One that cannot be
        resolved, or that climbs above the plugin, is the PLUGIN ROOT:
        scanning wider costs time, scanning narrower drops a test."""
        if anchor is None:
            return "."
        level, parts = anchor
        if level > len(module_parts) - 1:
            return "."
        kept = module_parts[:len(module_parts) - max(level, 1)]
        directory = "/".join((*kept, *parts)) if level else "/".join(
            (*module_parts[:-1], *parts))
        return directory or "."

    scopes = [node for node in ast.walk(tree)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    scopes.append(ast.Module(body=[
        statement for statement in tree.body
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef))], type_ignores=[]))
    roots: set[str] = set()
    found = False
    for scope in scopes:
        if not any(_is_scan_call(inner) for inner in ast.walk(scope)):
            continue
        used = {inner.id for inner in ast.walk(scope)
                if isinstance(inner, ast.Name)}
        # The directories this scope's scans start from, read off each scan
        # call's receiver (root.rglob(...), os.listdir(root)). Local names
        # are followed through the scope's own assignments. A receiver that
        # never touches __file__ or an anchor (ast.walk(tree), a tmp_path)
        # is not a scan of the checkout. A receiver that does, but whose
        # location cannot be resolved, starts at the plugin root.
        local: dict[str, ast.AST] = {}
        for inner in ast.walk(scope):
            if (isinstance(inner, ast.Assign) and len(inner.targets) == 1
                    and isinstance(inner.targets[0], ast.Name)):
                local.setdefault(inner.targets[0].id, inner.value)

        def anchored(node: ast.AST, depth: int = 0) -> bool:
            if _uses_file(node):
                return True
            for name_node in ast.walk(node):
                if not isinstance(name_node, ast.Name):
                    continue
                if name_node.id == "__file__" or name_node.id in anchors:
                    return True
                if (name_node.id in local and depth < 8
                        and local[name_node.id] is not node
                        and anchored(local[name_node.id], depth + 1)):
                    return True
            return False

        scope_anchors = dict(resolved)
        for name, value in local.items():
            if name not in scope_anchors and anchored(value):
                found_local = _anchor_of(value, scope_anchors)
                if found_local is not None:
                    scope_anchors[name] = found_local
        literals = [_str_constant(inner) for inner in ast.walk(scope)]
        for name in used & set(constants):
            literals += [_str_constant(inner)
                         for inner in ast.walk(constants[name])]
        starts: set[str] = set()
        for call in ast.walk(scope):
            if not _is_scan_call(call):
                continue
            func = call.func
            on_module = (isinstance(func, ast.Attribute)
                         and isinstance(func.value, ast.Name)
                         and func.value.id in ("os", "glob"))
            if isinstance(func, ast.Attribute) and not on_module:
                receiver = func.value
            elif call.args:
                receiver = call.args[0]
            else:
                continue
            if not anchored(receiver):
                continue
            exact = _anchor_of(receiver, scope_anchors)
            if exact is not None:
                starts.add(directory_at(exact))
                continue
            # Part of the path is computed (PLUGIN / top). Start from the
            # longest resolvable prefix; literal directories under it, from
            # the scope or the constants it uses, narrow it.
            prefix = receiver
            base = None
            while base is None:
                if isinstance(prefix, ast.BinOp):
                    prefix = prefix.left
                elif isinstance(prefix, ast.Attribute):
                    prefix = prefix.value
                elif isinstance(prefix, ast.Call) and isinstance(
                        prefix.func, ast.Attribute):
                    prefix = prefix.func.value
                elif isinstance(prefix, ast.Call) and prefix.args:
                    prefix = prefix.args[0]
                else:
                    break
                base = _anchor_of(prefix, scope_anchors)
            start = directory_at(base)
            narrowed = set()
            for literal in literals:
                literal = (literal or "").strip("/")
                if (not literal or literal in (".", "..")
                        or any(ch.isspace() for ch in literal)):
                    continue
                candidate = literal if start == "." else f"{start}/{literal}"
                if is_tracked_dir(candidate):
                    narrowed.add(candidate)
            starts |= narrowed or {start}
        if not starts:
            continue  # anchored scope, but no scan of the checkout
        found = True
        roots |= starts
    return frozenset(roots) if found else None


def _scan_file(path: Path, plugin_root: Path, roots: list[Path],
               is_tracked_dir) -> FileScan:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return _EMPTY_SCAN
    rel_name = path.name
    docstrings = _docstring_nodes(tree)
    literal_names = _literal_loop_names(tree)
    edges: list[Path] = []
    targets: set[Path] = set()
    literals: set[str] = set()
    names: set[str] = set()
    path_literals: set[str] = set()
    dynamic_imports: list[str] = []
    dynamic_fixtures: list[str] = []

    def is_literal(node: ast.AST) -> bool:
        return _str_constant(node) is not None or (
            isinstance(node, ast.Name) and node.id in literal_names)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                edges.extend(_resolve_module(alias.name, roots))
                targets.update(_module_candidates(alias.name, roots))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                package = (base.joinpath(*node.module.split("."))
                           if node.module else base)
                candidates = [base / "__init__.py",
                              package / "__init__.py",
                              package.with_suffix(".py")]
                for alias in node.names:
                    candidates.append(package / f"{alias.name}.py")
                    candidates.append(package / alias.name / "__init__.py")
                targets.update(candidates)
                edges.extend(c for c in candidates if c.is_file())
            elif node.module:
                edges.extend(_resolve_module(node.module, roots))
                targets.update(_module_candidates(node.module, roots))
                # `from pkg import submodule` loads pkg/submodule.py.
                for alias in node.names:
                    dotted = f"{node.module}.{alias.name}"
                    edges.extend(_resolve_module(dotted, roots))
                    targets.update(_module_candidates(dotted, roots))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            edges.extend(_literal_script(node.value, plugin_root, roots))
            value = node.value
            if (id(node) not in docstrings and value
                    and len(value) <= _MAX_LITERAL
                    and not any(ch.isspace() for ch in value)):
                literals.add(value)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            for side in (node.left, node.right):
                text = _str_constant(side)
                if text is not None:
                    path_literals.add(text)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names.add(node.name)
        if not isinstance(node, ast.Call):
            continue
        call = _call_name(node)
        if call in _PATH_CALLS:
            for argument in node.args:
                text = _str_constant(argument)
                if text is not None:
                    path_literals.add(text)
        if (call in _DYNAMIC_IMPORT_CALLS and node.args
                and not is_literal(node.args[0])):
            dynamic_imports.append(f"{rel_name}:{node.lineno}:{call}")
        if (call == "getfixturevalue" and node.args
                and not is_literal(node.args[0])):
            dynamic_fixtures.append(f"{rel_name}:{node.lineno}")
    root = plugin_root.resolve()

    def to_rel(items) -> list[str]:
        out = []
        for item in items:
            try:
                out.append(item.resolve().relative_to(root).as_posix())
            except (ValueError, OSError):
                continue
        return out

    try:
        module_rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        module_rel = path.name
    return FileScan(
        edges=tuple(dict.fromkeys(to_rel(edges))),
        import_targets=frozenset(to_rel(targets)),
        literals=frozenset(literals),
        names=frozenset(names),
        path_literals=frozenset(path_literals),
        scanner_roots=_scanner_roots(tree, module_rel, is_tracked_dir),
        dynamic_imports=tuple(dynamic_imports),
        dynamic_fixtures=tuple(dynamic_fixtures),
    )


def _tracked_directories(plugin_root: Path) -> frozenset[str] | None:
    """Plugin-relative directories holding a git-tracked file, or None.

    Scanner roots are restricted to these, so a literal that happens to
    name an untracked directory (a __pycache__, a model cache) is not one.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--", "."], cwd=str(plugin_root),
            capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    directories: set[str] = set()
    for entry in completed.stdout.decode("utf-8", "replace").split("\0"):
        parts = entry.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            directories.add("/".join(parts[:depth]))
    return frozenset(directories)


class ScanIndex:
    """Per-file scans of one plugin tree, each file parsed at most once."""

    def __init__(self, plugin_root: Path):
        self.plugin_root = Path(plugin_root).resolve()
        self.roots = [root.resolve() for root in _import_roots(self.plugin_root)]
        self._scans: dict[str, FileScan] = {}
        self._views: dict[str, ExclusionView] = {}
        tracked = _tracked_directories(self.plugin_root)
        if tracked:
            self.is_tracked_dir = tracked.__contains__
        else:
            # Outside a checkout: any directory except caches.
            self.is_tracked_dir = lambda rel: (
                "__pycache__" not in rel.split("/")
                and (self.plugin_root / rel).is_dir())

    def scan(self, rel: str) -> FileScan:
        if rel not in self._scans:
            path = self.plugin_root / rel
            self._scans[rel] = (
                _scan_file(path, self.plugin_root, self.roots,
                           self.is_tracked_dir)
                if path.is_file() else _EMPTY_SCAN)
        return self._scans[rel]

    def closure(self, start: list[str]) -> frozenset[str]:
        seen: set[str] = set()
        stack = [rel for rel in start if (self.plugin_root / rel).is_file()]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self.scan(current).edges)
        return frozenset(seen)

    def view(self, exclusion_path: str) -> ExclusionView:
        if exclusion_path not in self._views:
            closure = self.closure([exclusion_path])
            targets: set[str] = set()
            literals: set[str] = set()
            test_names: set[str] = set()
            path_literals: set[str] = set()
            scan_roots: set[str] = set()
            for rel in sorted(closure):
                scan = self.scan(rel)
                targets |= scan.import_targets
                literals |= scan.literals
                path_literals |= scan.path_literals
                scan_roots |= scan.scanner_roots or set()
                if rel.startswith("tests/"):
                    # Test-side code is what requests fixtures: by parameter
                    # name, attribute or a usefixtures string.
                    test_names |= scan.names | scan.literals
            self._views[exclusion_path] = ExclusionView(
                closure=closure,
                import_targets=frozenset(targets),
                literals=frozenset(literals),
                test_names=frozenset(test_names),
                path_literals=frozenset(path_literals),
                scan_roots=frozenset(scan_roots),
            )
        return self._views[exclusion_path]


def import_closure(plugin_root: Path, start: list[str]) -> frozenset[str]:
    """Plugin-relative files statically reachable from `start` (plugin-relative).

    Follows every import statement (module level and function local, so a
    package's eager `__init__` imports are followed too), relative imports,
    and string literals naming a production .py file. Over-approximates by
    design: it is the floor a manifest's seam lists must cover.
    """
    return ScanIndex(plugin_root).closure(list(start))


# --------------------------------------------------------------------------
# Conftest change classification (pure, from two source texts)
# --------------------------------------------------------------------------


def _is_autouse(node: ast.AST) -> bool:
    for decorator in getattr(node, "decorator_list", []):
        for keyword in getattr(decorator, "keywords", []):
            if (keyword.arg == "autouse"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True):
                return True
    return False


def _top_level_nodes(source: str) -> dict[tuple[str, str], tuple[str, ast.AST]]:
    """Top-level statements keyed by kind and name. Unknown kinds are effects.

    The kinds are total: anything that is not a def, a class, an import, a
    plain name assignment or the module docstring is keyed as an `effect`,
    and a changed effect selects everything.
    """
    tree = ast.parse(source)
    out: dict[tuple[str, str], tuple[str, ast.AST]] = {}
    for index, node in enumerate(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            key = ("def", node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            key = ("import", ",".join(
                (alias.asname or alias.name).split(".")[0]
                for alias in node.names))
        elif isinstance(node, ast.Assign) and all(
                isinstance(target, ast.Name) for target in node.targets):
            key = ("assign", ",".join(target.id for target in node.targets))
        elif (isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)):
            key = ("assign", node.target.id)
        elif (index == 0 and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            key = ("doc", "")
        else:
            key = ("effect", ast.dump(node))
        out[key] = (ast.dump(node), node)
    return out


def _referenced_modules(node: ast.AST, tree: ast.Module) -> set[str]:
    """Dotted module names a changed node uses: imports it makes, names it
    reads that the module imported, and dotted monkeypatch-target strings."""
    bound: dict[str, str] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                bound[(alias.asname or alias.name).split(".")[0]] = alias.name
        elif (isinstance(statement, ast.ImportFrom) and statement.module
                and not statement.level):
            for alias in statement.names:
                bound[alias.asname or alias.name] = statement.module
    modules: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Import):
            modules.update(alias.name for alias in inner.names)
        elif (isinstance(inner, ast.ImportFrom) and inner.module
                and not inner.level):
            modules.add(inner.module)
        elif isinstance(inner, ast.Name) and inner.id in bound:
            modules.add(bound[inner.id])
        elif (isinstance(inner, ast.Constant) and isinstance(inner.value, str)
                and _names_a_module(inner.value)):
            # A string names a module only when it has two or more identifier
            # parts and does not end in a file extension: a patch target
            # "pkg.mod.attr" names "pkg.mod" (both readings are kept), while
            # ".claude", "a-b.json" and "config.json" name nothing.
            modules.add(inner.value)
            modules.add(inner.value.rsplit(".", 1)[0])
    return modules


# Extensions of the files this repository's code names in string literals.
# A dotted string ending in one is a file name, not a module path.
_FILE_EXTENSIONS = frozenset({
    "bak", "cfg", "consumed", "csv", "db", "gitignore", "html", "ini",
    "json", "jsonl", "lock", "log", "md", "ogg", "pid", "py", "pyc", "sh",
    "sqlite", "tmp", "toml", "ts", "txt", "yaml", "yml",
})


def _names_a_module(text: str) -> bool:
    parts = text.split(".")
    return (len(parts) >= 2 and _is_dotted_name(text)
            and parts[-1].lower() not in _FILE_EXTENSIONS)


def _is_dotted_name(name: str) -> bool:
    return bool(name) and all(part.isidentifier() for part in name.split("."))


@dataclass(frozen=True)
class ConftestVerdict:
    """`select_all` names a process-global change; otherwise the changed
    names and the plugin production files changed autouse fixtures
    reference."""
    select_all: str | None
    names: frozenset[str] = frozenset()
    modules: frozenset[str] = frozenset()


def classify_conftest_change(old: str | None, new: str | None,
                             is_conftest: bool,
                             resolve=lambda dotted: frozenset()
                             ) -> ConftestVerdict:
    """Classify one changed conftest (or conftest-imported module).

    `resolve` maps a dotted module name to the plugin production files it
    loads. A changed autouse fixture that resolves to none of them is
    process-global (an env scrub, HOME isolation) and selects everything.
    """
    if is_conftest and (old is None or new is None):
        return ConftestVerdict("conftest-added-or-removed")
    try:
        before = _top_level_nodes(old or "")
        after = _top_level_nodes(new or "")
        tree = ast.parse(new or old or "")
    except SyntaxError:
        return ConftestVerdict("conftest-unparseable")
    names: set[str] = set()
    modules: set[str] = set()
    for key in sorted(set(before) | set(after)):
        old_entry, new_entry = before.get(key), after.get(key)
        if old_entry and new_entry and old_entry[0] == new_entry[0]:
            continue
        kind, name = key
        if kind == "doc":
            continue
        if kind == "effect":
            return ConftestVerdict("module-level-effect")
        node = (new_entry or old_entry)[1]
        if kind in ("def", "assign") and (name.startswith("pytest_")
                                          or "pytest_plugins" in name):
            return ConftestVerdict(f"hook:{name}")
        if kind == "def" and _is_autouse(node):
            files: set[str] = set()
            for dotted in _referenced_modules(node, tree):
                files |= set(resolve(dotted))
            if not files:
                return ConftestVerdict(f"autouse-unscoped:{name}")
            modules |= files
            continue
        names |= set(name.split(","))
    return ConftestVerdict(None, frozenset(names), frozenset(modules))


def json_change_is_value_inert(old: str | None, new: str | None,
                               keys: tuple[str, ...]) -> bool:
    """True when both versions parse to objects equal without `keys`."""
    try:
        before, after = json.loads(old), json.loads(new)
    except (TypeError, ValueError):
        return False
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    for key in keys:
        before.pop(key, None)
        after.pop(key, None)
    return before == after


# --------------------------------------------------------------------------
# Change facts: everything select() needs beyond the changed paths
# --------------------------------------------------------------------------

_CONFTEST_NAMES = ("conftest.py", "tests/conftest.py")


@dataclass(frozen=True)
class ChangeFacts:
    """Inputs select() cannot compute purely: file contents and closures.

    `views` maps each exclusion to what its closure sees; `value_inert` holds
    repo paths whose change only touched ignored keys; `conftest_verdicts`
    maps changed conftests and conftest-imported modules (plugin-relative)
    to their classification; `import_roots` are plugin-relative.
    """
    views: dict[str, ExclusionView]
    value_inert: frozenset[str]
    conftest_verdicts: dict[str, ConftestVerdict]
    conftest_imported: frozenset[str]


def _git_blob(repo_root: Path, rev: str, path: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "show", f"{rev}:{path}"], cwd=str(repo_root),
            capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _merge_base(repo_root: Path, base_sha: str, head: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "merge-base", base_sha, head], cwd=str(repo_root),
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = completed.stdout.strip()
    return sha if completed.returncode == 0 and sha else None


def _production_files(dotted: str, index: ScanIndex) -> frozenset[str]:
    if not _is_dotted_name(dotted):
        return frozenset()
    files: set[str] = set()
    for found in _resolve_module(dotted, index.roots):
        try:
            relative = found.resolve().relative_to(index.plugin_root).as_posix()
        except ValueError:
            continue
        if not relative.startswith("tests/"):
            files.add(relative)
    return frozenset(files)


def gather_facts(repo_root: Path, manifest: Manifest, changed: list[str],
                 base_sha: str | None,
                 index: ScanIndex | None = None,
                 head: str = "HEAD") -> ChangeFacts:
    """Compute ChangeFacts for `changed`. Never raises on git trouble.

    Contents come from the merge base and HEAD, matching the three-dot diff.
    Without a usable base the content checks cannot run: a value-inert file
    then counts as an ordinary data change, and a changed conftest selects
    everything.
    """
    repo_root = Path(repo_root).resolve()
    plugin_root = repo_root / PLUGIN_DIRNAME
    index = index or ScanIndex(plugin_root)
    views = {exclusion.path: index.view(exclusion.path)
             for exclusion in manifest.exclusions}
    merge_base = _merge_base(repo_root, base_sha, head) if base_sha else None

    value_inert: set[str] = set()
    for path, keys in manifest.value_inert_keys:
        if path in changed and merge_base is not None:
            if json_change_is_value_inert(
                    _git_blob(repo_root, merge_base, path),
                    _git_blob(repo_root, head, path), keys):
                value_inert.add(path)

    conftest_imported: set[str] = set()
    for conftest in _CONFTEST_NAMES:
        for edge in index.scan(conftest).edges:
            if edge.startswith("tests/") and Path(edge).name != "conftest.py":
                conftest_imported.add(edge)

    verdicts: dict[str, ConftestVerdict] = {}
    for repo_path in changed:
        rel = repo_to_plugin(repo_path)
        if not rel:
            continue
        is_conftest = Path(rel).name == "conftest.py"
        if not is_conftest and rel not in conftest_imported:
            continue
        if merge_base is None:
            verdicts[rel] = ConftestVerdict("conftest-content-unavailable")
            continue
        verdict = classify_conftest_change(
            _git_blob(repo_root, merge_base, repo_path),
            _git_blob(repo_root, head, repo_path), is_conftest,
            resolve=lambda dotted: _production_files(dotted, index))
        verdicts[rel] = verdict
    return ChangeFacts(
        views=views,
        value_inert=frozenset(value_inert),
        conftest_verdicts=verdicts,
        conftest_imported=frozenset(conftest_imported),
    )


def select_for_change(repo_root: Path, manifest: Manifest,
                      base_sha: str | None) -> tuple[Selection, list[str], str]:
    """The one entry point the runner, the CLI and the verifier share."""
    changed, status = changed_paths(base_sha, repo_root)
    facts = gather_facts(repo_root, manifest, changed,
                         base_sha if status == "ok" else None)
    return select(discover(repo_root), manifest, changed, status, facts), \
        changed, status


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def _select_all(discovered: frozenset[str], status: str,
                reason: str) -> Selection:
    return Selection(
        status=status,
        select_all_reason=reason,
        discovered=discovered,
        included=discovered,
        excluded=frozenset(),
        added_back=(),
    )


def _is_test_file(plugin_relative: str) -> bool:
    return bool(_TEST_FILE_RE.search(plugin_relative))


def _ancestors(rel: str) -> set[str]:
    parts = rel.split("/")
    return {"/".join(parts[:depth]) for depth in range(1, len(parts))}


def _under(rel: str, root: str) -> bool:
    return root == "." or rel == root or rel.startswith(root + "/")


def _content_evidence(rel: str, view: ExclusionView,
                      facts: ChangeFacts) -> tuple[str, str] | None:
    """(reason, evidence) when a changed plugin file can reach the exclusion.

    The rules, in order:
      scanned_tree       any file, whatever its type, under a directory a
                         closure module scans from the real checkout;
      fixture_name / autouse_module  a changed conftest or conftest-imported
                                     module: a changed name the exclusion's
                                     test-side code uses, or a module a
                                     changed autouse fixture references that
                                     the exclusion also loads;
      closure            a .py file the exclusion's import closure loads or
                         names as an import target, a deletion included;
      data_reference     a non-.py file whose basename a closure string
                         literal contains;
      constructed_path   a non-.py file under a directory a closure module
                         builds with `/` or a path call.
    """
    roots = sorted(root for root in view.scan_roots if _under(rel, root))
    if roots:
        return "scanned_tree", roots[0]
    verdict = facts.conftest_verdicts.get(rel)
    if verdict is not None:
        used = sorted(verdict.names & view.test_names)
        if used:
            return "fixture_name", used[0]
        shared = sorted(verdict.modules & view.closure)
        if shared:
            return "autouse_module", shared[0]
    if rel.endswith(".py"):
        if rel in view.closure or rel in view.import_targets:
            return "closure", rel
        return None
    basename = rel.rsplit("/", 1)[-1]
    named = [literal for literal in view.literals if basename in literal]
    if named:
        return "data_reference", min(named)
    ancestors = _ancestors(rel)
    built = [literal for literal in view.path_literals
             if literal.strip("/") in ancestors]
    if built:
        return "constructed_path", min(built)
    return None


def select(discovered: frozenset[str], manifest: Manifest,
           changed: list[str], status: str,
           facts: ChangeFacts | None = None) -> Selection:
    """Pure: decide the run from the changed paths and their facts.

    Select-all fires only on named shapes: an unusable diff, selection
    machinery, a process-global conftest change, or missing facts. Every
    other change adds back exactly the exclusions that can see it.
    """
    if status != "ok":
        return _select_all(discovered, status, f"changed-paths:{status}")

    machinery = set(manifest.selection_machinery)
    for changed_path in changed:
        if changed_path in machinery or changed_path in _DEPENDENCY_PINS:
            return _select_all(discovered, status, "selection-machinery")

    if facts is not None:
        changed = [path for path in changed if path not in facts.value_inert]
    plugin_changed = [
        rel for rel in (repo_to_plugin(path) for path in changed)
        if rel is not None and rel != ""
    ]
    if plugin_changed and facts is None:
        return _select_all(discovered, status, "change-facts-unavailable")
    # AddBack.matched_changed is REPO-relative -- exactly the path
    # changed_paths returned -- while matched_seam is plugin-relative.
    repo_spelling: dict[str, str] = {}
    for repo_path in changed:
        plugin_path = repo_to_plugin(repo_path)
        if plugin_path:
            repo_spelling.setdefault(plugin_path, repo_path)

    for rel in plugin_changed:
        is_conftest = rel.rsplit("/", 1)[-1] == "conftest.py"
        if is_conftest or rel in facts.conftest_imported:
            verdict = facts.conftest_verdicts.get(rel)
            if verdict is None:
                return _select_all(discovered, status,
                                   f"conftest:{rel}:no-verdict")
            if verdict.select_all is not None:
                return _select_all(discovered, status,
                                   f"conftest:{rel}:{verdict.select_all}")

    changed_set = set(plugin_changed)
    added: dict[str, AddBack] = {}
    for exclusion in manifest.exclusions:
        if exclusion.path in changed_set:
            added[exclusion.path] = AddBack(
                path=exclusion.path, reason="changed_test",
                matched_changed=repo_spelling.get(exclusion.path,
                                                  exclusion.path),
                matched_seam=None)
            continue
        matched_seam = next(
            (seam for seam in exclusion.production_seams if seam in changed_set),
            None)
        if matched_seam is not None:
            added[exclusion.path] = AddBack(
                path=exclusion.path, reason="changed_seam",
                matched_changed=repo_spelling.get(matched_seam, matched_seam),
                matched_seam=matched_seam)
            continue
        view = facts.views.get(exclusion.path) if facts else None
        if view is None:
            if plugin_changed:
                # An exclusion with no view cannot be cleared for any change.
                return _select_all(discovered, status,
                                   f"no-closure-view:{exclusion.path}")
            continue
        for rel in plugin_changed:
            hit = _content_evidence(rel, view, facts)
            if hit is not None:
                added[exclusion.path] = AddBack(
                    path=exclusion.path, reason=hit[0],
                    matched_changed=repo_spelling.get(rel, rel),
                    matched_seam=None, evidence=hit[1])
                break

    excluded_paths = {exclusion.path for exclusion in manifest.exclusions}
    applied = frozenset(excluded_paths - set(added))
    included = frozenset(
        path for path in discovered
        if path not in excluded_paths or path in added)
    return Selection(
        status=status,
        select_all_reason=None,
        discovered=discovered,
        included=included,
        excluded=applied,
        added_back=tuple(sorted(added.values(), key=lambda entry: entry.path)),
    )


# --------------------------------------------------------------------------
# argv construction
# --------------------------------------------------------------------------


def to_pytest_args(selection: Selection, cwd: Path) -> list[str]:
    """Applied exclusions as ABSOLUTE --ignore arguments.

    Measured: `--ignore` resolves against the invocation CWD, and a
    rootdir-relative spelling collects the file anyway with no warning. A
    caller therefore cannot be trusted to pass a relative path, so this
    emits absolute ones and refuses any path that does not exist or escapes
    `cwd`.
    """
    base = Path(cwd).resolve()
    arguments: list[str] = []
    for relative in sorted(selection.excluded):
        resolved = (base / relative).resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise ValueError(
                f"exclusion {relative!r} resolves to {resolved}, which is "
                f"outside the pytest working directory {base}"
            ) from exc
        if not resolved.is_file():
            raise ValueError(
                f"exclusion {relative!r} resolves to {resolved}, which does "
                f"not exist. pytest silently ignores a nonexistent --ignore, "
                f"so emitting this would run the file it claims to exclude."
            )
        arguments.append(f"--ignore={resolved}")
    return arguments


def build_argv(selection: Selection, cwd: Path, mode: str,
               workers: int | None = None) -> list[str]:
    """The exact pytest argv for a mode. Pure and subprocess-free."""
    if mode == "collect-only":
        if workers is not None:
            raise ValueError("workers is not supported in collect-only mode")
        return [
            sys.executable, "-m", "pytest", "-ra", "--collect-only", "-q",
            "-p", "no:cacheprovider", *to_pytest_args(selection, cwd),
        ]
    if mode == "run":
        argv = [sys.executable, "-m", "pytest", "-ra",
                *to_pytest_args(selection, cwd)]
        if workers is not None:
            argv.extend(["-n", str(workers), "--dist", "loadfile"])
        return argv
    raise ValueError(f"unknown mode {mode!r}")


# --------------------------------------------------------------------------
# pytest summary parsing (pure)
# --------------------------------------------------------------------------

_TIMING_RE = re.compile(r"\bin \d+(?:\.\d+)?s\b")
# `no tests ran` carries no leading count, so it cannot sit inside the
# digit-prefixed alternation: required here, or a zero-collection run never
# parses and gets reported as "no summary at all", naming the wrong cause.
_OUTCOME_RE = re.compile(
    r"(?:\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed"
    r"|warnings?|tests? collected|test collected)|no tests ran)"
)
_COUNT_PATTERNS = (
    ("passed", re.compile(r"(\d+) passed")),
    ("failed", re.compile(r"(\d+) failed")),
    ("errors", re.compile(r"(\d+) errors?")),
    ("skipped", re.compile(r"(\d+) skipped")),
    ("deselected", re.compile(r"(\d+) deselected")),
    ("xfailed", re.compile(r"(\d+) xfailed")),
    ("xpassed", re.compile(r"(\d+) xpassed")),
)
_COLLECTED_RE = re.compile(r"(\d+) tests? collected")
_ITEMS_RE = re.compile(r"^collected (\d+) items?\b")
_NO_TESTS_RE = re.compile(r"\bno tests ran\b")


def _undecorate(line: str) -> str:
    """Strip pytest's `=====` decoration and surrounding whitespace.

    This normalises the RECORDED summary line, so the same run records the
    same `summary_line` with or without `-q`. Finding the line does not
    depend on it: _TIMING_RE and _OUTCOME_RE are unanchored and match the
    decorated form too.
    """
    return line.strip().strip("=").strip()


def _counts_from_line(line: str) -> dict[str, int]:
    counts = {name: 0 for name in (
        "passed", "failed", "errors", "skipped", "deselected",
        "xfailed", "xpassed")}
    for name, pattern in _COUNT_PATTERNS:
        match = pattern.search(line)
        if match:
            counts[name] = int(match.group(1))
    return counts


def parse_pytest_summary(text: str) -> dict[str, object] | None:
    """Parse a pytest summary from captured output or a single summary line.

    Returns None when no summary line exists. The caller MUST treat None as a
    failure, never as zero: an absent summary and a zero count produce the
    same digits, and only one of them means the suite ran.

    Keys: summary_line, collected (int | None), passed, failed, errors,
    skipped, deselected, xfailed, xpassed.
    """
    lines = text.splitlines()
    summary_line: str | None = None
    for raw in reversed(lines):
        candidate = _undecorate(raw)
        if _TIMING_RE.search(candidate) and _OUTCOME_RE.search(candidate):
            summary_line = candidate
            break
    if summary_line is None:
        return None

    result: dict[str, object] = _counts_from_line(summary_line)
    result["summary_line"] = summary_line

    collected_match = _COLLECTED_RE.search(summary_line)
    collected: int | None = (
        int(collected_match.group(1)) if collected_match else None
    )
    if collected is None:
        for raw in lines:
            items_match = _ITEMS_RE.match(_undecorate(raw))
            if items_match:
                collected = int(items_match.group(1))
                break
    if collected is None and _NO_TESTS_RE.search(summary_line):
        collected = 0
    if collected is None:
        collected = sum(int(result[name]) for name in (
            "passed", "failed", "errors", "skipped", "xfailed", "xpassed",
            "deselected"))
    result["collected"] = collected
    return result