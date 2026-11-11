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
import enum
import fnmatch
import functools
import hashlib
import importlib
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
    "reach_reviews",
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
# The files every PR's version bump touches. A broad exclusion ignores a
# change to them unless its closure names one or scans a tree.
BUMP_TRIO = ("CHANGELOG.md", "README.md", ".claude-plugin/marketplace.json")
# The bump-trio file stems, matched case-insensitively inside any string, so
# a name built in pieces or formatted ('READ' + 'ME.md', f'{stem}.md' with
# stem = 'README') still counts.
_TRIO_STEMS = ("changelog", "readme", "marketplace")
_REVIEW_KEYS = frozenset({"sha256", "reason", "extra_reads"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

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
    # Reviewed test-side modules or fixtures ('module::function'):
    # (key, sha256, reason, repo-relative extra_reads globs). A review counts
    # only while the module's bytes, or the fixture's source with every
    # same-file name it uses, still hash to sha256.
    reach_reviews: tuple[tuple[str, str, str, tuple[str, ...]], ...] = ()


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


def _validate_reach_reviews(entries: object, plugin_root: Path,
                            errors: list[str]
                            ) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    """{test-side module: {sha256, reason, extra_reads}}, every field
    checked; an unknown key is an error."""
    if not isinstance(entries, dict):
        errors.append("reach_reviews: expected an object")
        return ()
    validated: list[tuple[str, str, str, tuple[str, ...]]] = []
    for raw, review in sorted(entries.items()):
        field = f"reach_reviews[{raw!r}]"
        path_part, separator, function = (raw.partition("::")
                                          if isinstance(raw, str)
                                          else (raw, "", ""))
        # A reviewed module or fixture that was deleted or renamed is NOT an
        # error here: like a stale hash, the review simply stops matching at
        # selection, so its exclusion turns broad. The review contract
        # reports it.
        rel = _check_relative(path_part, field, errors)
        if rel is None:
            continue
        if not rel.endswith(".py"):
            errors.append(f"{field}: {rel!r} must end in .py")
            continue
        if _check_under(rel, field, plugin_root, errors) is None:
            continue
        if separator:
            if not function or not function.isidentifier():
                errors.append(f"{field}: {function!r} is not a function name")
                continue
            rel = f"{rel}::{function}"
        if not (rel.startswith("tests/") or _is_test_file(rel.split("::")[0])):
            errors.append(f"{field}: {rel!r} is not test-side code")
            continue
        if not isinstance(review, dict):
            errors.append(f"{field}: expected an object")
            continue
        for key in sorted(set(review) - _REVIEW_KEYS):
            errors.append(f"{field}: unknown key {key!r}")
        for key in sorted(_REVIEW_KEYS - set(review)):
            errors.append(f"{field}: missing required key {key!r}")
        sha = review.get("sha256")
        reason = review.get("reason")
        reads = review.get("extra_reads")
        if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
            errors.append(f"{field}.sha256: expected 64 lowercase hex digits")
            continue
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{field}.reason: expected a non-empty string")
            continue
        if not isinstance(reads, list) or not all(
                isinstance(glob, str) and glob and not glob.startswith("/")
                and ".." not in glob.split("/") for glob in reads):
            errors.append(f"{field}.extra_reads: expected a list of "
                          f"repo-relative globs")
            continue
        validated.append((rel, sha, reason, tuple(reads)))
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
    reach_reviews = _validate_reach_reviews(
        data.get("reach_reviews"), plugin_root, errors)
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
        reach_reviews=reach_reviews,
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
# as a dict key) is not a path; matching it adds back most exclusions on
# every instruction-file change.
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
    # 'line:why' evidence that a production module climbs out of the plugin.
    repo_reach: tuple[str, ...]
    # 'line:why' for every site that keeps a TEST-SIDE module unproven.
    unproven: tuple[str, ...]
    dynamic_imports: tuple[str, ...]
    dynamic_fixtures: tuple[str, ...]
    # Whether the module calls a directory scan, glob or walk.
    scans: bool = False
    # Whether any non-docstring string, or literal concatenation, contains
    # a bump-trio stem.
    trio_stem: bool = False


@dataclass(frozen=True)
class ExclusionView:
    """What an exclusion's import closure can see, unioned over its modules."""
    closure: frozenset[str]
    import_targets: frozenset[str]
    literals: frozenset[str]
    test_names: frozenset[str]
    path_literals: frozenset[str]
    scan_roots: frozenset[str] = frozenset()
    # Production climbs out of the plugin ('module:line:why').
    repo_reach: tuple[str, ...] = ()
    # (module, reasons) for each unproven test-side module or requested
    # conftest fixture ('tests/conftest.py::name'). An exclusion with any
    # entry not covered by a matching review is BROAD.
    unproven: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Whether any string in the closure contains a bump-trio stem; if so the
    # trio is not carved out for this exclusion.
    trio_named: bool = False
    # Whether any module in the closure scans, globs or walks a directory;
    # such a scan can reach a trio file no literal names, so the trio is not
    # carved out either.
    scans_tree: bool = False


_EMPTY_SCAN = FileScan((), frozenset(), frozenset(), frozenset(), frozenset(),
                       None, (), (), (), ())

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


def _conftest_path_entries(plugin_root: Path) -> list[str]:
    """Plugin directories the conftests put on sys.path, read from their
    sys.path.insert / append calls, so a new entry cannot be missed by a
    hand-kept list. Entries whose location cannot be resolved are skipped;
    _IMPORT_ROOTS and the skills/*/scripts glob cover the known ones."""
    entries: list[str] = []
    for rel in ("conftest.py", "tests/conftest.py"):
        try:
            tree = ast.parse((plugin_root / rel).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            continue
        _, _, resolved = _module_anchors(tree, rel, {})
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("insert", "append")
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "path"
                    and node.args):
                continue
            anchor = _anchor_of(node.args[-1], resolved)
            if anchor is not None:
                directory = _anchor_directory(anchor, Path(rel).parts)
                if directory is not None:
                    entries.append("" if directory == "." else directory)
    return entries


def _anchor_directory(anchor: tuple[int, tuple[str, ...]],
                      module_parts: tuple[str, ...]) -> str | None:
    """The plugin directory a resolved anchor names, or None above it."""
    level, parts = anchor
    if level > len(module_parts):
        return None
    kept = module_parts[:len(module_parts) - max(level, 1)]
    directory = "/".join((*kept, *parts)) if level else "/".join(
        (*module_parts[:-1], *parts))
    return directory or "."


def _import_roots(plugin_root: Path) -> list[Path]:
    roots = [plugin_root / rel for rel in _IMPORT_ROOTS]
    roots.extend(plugin_root / rel for rel in _conftest_path_entries(
        plugin_root))
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


# The shared tracked-enumeration fixture scans the directory it is given, but
# module-local wrappers are named freely (`_tracked_glob`, `_shared_tracked_glob`,
# `_tracked_agent_files`, ...), so a closed name list cannot recognise them.
# The convention is the contract: `tracked_*`, `_tracked_*`, `shared_tracked_*`.
_TRACKED_SCAN_NAME = re.compile(r"^_?(?:shared_)?tracked_[a-z0-9_]+$")


def _is_scan_name(name: str) -> bool:
    return name in _SCAN_CALLS or bool(_TRACKED_SCAN_NAME.match(name))


def _is_scan_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _is_scan_name(_call_name(node))


def _scan_wrapper_names(tree: ast.Module) -> frozenset[str]:
    """Module-local functions that reach a scan, whatever they are named.

    A wrapper may take the scanned directory as a PARAMETER, so the wrapper
    body cannot be anchored; it is the call site that carries the root. The
    name is free (`_shipped_shell_scripts`, `_population`), so the convention
    cannot see it; the call graph can. Transitive, because a wrapper may call
    another wrapper. A false positive only widens reach.
    """
    functions = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    wrappers: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, node in functions.items():
            if name in wrappers:
                continue
            if any(isinstance(call, ast.Call)
                   and (_is_scan_name(_call_name(call))
                        or _call_name(call) in wrappers)
                   for call in ast.walk(node)):
                wrappers.add(name)
                changed = True
    return frozenset(wrappers)


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
        return None if inner is None else _up(*inner)
    if (isinstance(node, ast.Attribute) and node.attr == "__file__"
            and isinstance(node.value, ast.Name)):
        # Another module's file, placed through this module's imports.
        return anchors.get(f"{node.value.id}.__file__")
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)):
        inner = _anchor_of(node.value.value, anchors)
        if inner is None:
            return None
        level, parts = inner
        for _ in range(node.slice.value + 1):
            level, parts = _up(level, parts)
        return level, parts
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        inner = _anchor_of(node.left, anchors)
        text = _str_constant(node.right)
        if inner is None or text is None:
            return None
        return _join(inner, text.split("/"))
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
            return None if inner is None else _up(*inner)
        if name in ("joinpath", "join") and node.args:
            if name == "joinpath" and isinstance(node.func, ast.Attribute):
                head, rest = _anchor_of(node.func.value, anchors), node.args
            else:
                head, rest = _anchor_of(node.args[0], anchors), node.args[1:]
            texts = [_str_constant(part) for part in rest]
            if head is None or any(text is None for text in texts):
                return None
            return _join(head, [piece for text in texts
                                for piece in text.split("/")])
    return None


def _up(level: int, parts: tuple[str, ...]) -> tuple[int, tuple[str, ...]]:
    """One directory up: drop a joined part, else climb a level."""
    return (level, parts[:-1]) if parts else (level + 1, ())


def _join(anchor: tuple[int, tuple[str, ...]],
          pieces) -> tuple[int, tuple[str, ...]]:
    """Append path pieces; '..' climbs and '.' or '' is skipped."""
    level, parts = anchor
    for piece in pieces:
        if piece in ("", "."):
            continue
        if piece == "..":
            level, parts = _up(level, parts)
        else:
            parts = parts + (piece,)
    return level, parts


def _module_anchors(tree: ast.Module, rel: str,
                    module_files: dict[str, str]):
    """(constants, anchor names, resolved anchors) for one module.

    Anchors are module-level names bound, transitively, from __file__ or
    another module's __file__. An imported plugin module's file is placed
    through `module_files` ({bound name: plugin-relative file}), so
    `Path(archive_pin.__file__).parent.parent` is the plugin root rather
    than an unknown.
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
    depth = len(Path(rel).parts)
    resolved: dict[str, tuple[int, tuple[str, ...]]] = {
        f"{name}.__file__": (depth, tuple(Path(target).parts))
        for name, target in module_files.items()}
    for _ in range(len(constants)):
        for name in anchors:
            value = getattr(constants[name], "value", None)
            if value is not None and name not in resolved:
                found_anchor = _anchor_of(value, resolved)
                if found_anchor is not None:
                    resolved[name] = found_anchor
    return constants, anchors, resolved


def _repo_reach(tree: ast.Module, rel: str, plugin_root: Path,
                module_files: dict[str, str],
                docstrings: set[int]) -> tuple[str, ...]:
    """Evidence that a module climbs out of the plugin.

    Recorded as 'line:why': an anchored path that climbs above the plugin;
    an anchored climb (.parent, .parents[n], dirname) whose depth cannot be
    resolved; an anchored `.parents` used other than with a literal index
    (a walk up to a marker). Wherever it appears, assignments included, any
    of these makes an exclusion broad: in production code as a climb, in
    test-side code as an unproven site.
    """
    _, anchors, resolved = _module_anchors(tree, rel, module_files)
    depth = len(Path(rel).parts)
    evidence: list[str] = []
    # `.parents` with a literal index is a fixed climb, resolved below. Any
    # other use (iteration, `in`, list(...), an argument) walks every
    # ancestor, which is how a test finds the repository root by marker.
    indexed = {id(node.value) for node in ast.walk(tree)
               if isinstance(node, ast.Subscript)
               and isinstance(node.slice, ast.Constant)
               and isinstance(node.slice.value, int)}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "parents"
                and id(node) not in indexed
                and (_uses_file(node) or any(
                    isinstance(inner, ast.Name) and inner.id in anchors
                    for inner in ast.walk(node)))):
            evidence.append(f"{node.lineno}:walks every ancestor")
            continue
        climb = (
            (isinstance(node, ast.Attribute) and node.attr == "parent")
            or (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "parents")
            or (isinstance(node, ast.Call) and _call_name(node) == "dirname"))
        joined = isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
        if not (climb or joined):
            continue
        mentions = _uses_file(node) or any(
            isinstance(inner, ast.Name) and inner.id in anchors
            for inner in ast.walk(node))
        if not mentions:
            continue
        found = _anchor_of(node, resolved)
        if found is None:
            if climb:
                evidence.append(f"{node.lineno}:unresolved climb")
            continue
        if found[0] > depth:
            evidence.append(f"{node.lineno}:climbs above the plugin")
    return tuple(dict.fromkeys(evidence))


def _scanner_roots(tree: ast.Module, rel: str, is_tracked_dir,
                   module_files: dict[str, str] | None = None
                   ) -> frozenset[str] | None:
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
    constants, anchors, resolved = _module_anchors(
        tree, rel, module_files or {})
    module_parts = Path(rel).parts
    wrappers = _scan_wrapper_names(tree)

    def is_scan(node: ast.AST) -> bool:
        return (isinstance(node, ast.Call)
                and (_is_scan_name(_call_name(node))
                     or _call_name(node) in wrappers))

    def directory_at(anchor: tuple[int, tuple[str, ...]]) -> str:
        """The plugin directory a resolved anchor names. One that climbs
        above the plugin is the PLUGIN ROOT: scanning wider costs time,
        scanning narrower drops a test."""
        return _anchor_directory(anchor, module_parts) or "."

    scopes = [node for node in ast.walk(tree)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    scopes.append(ast.Module(body=[
        statement for statement in tree.body
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef))], type_ignores=[]))
    roots: set[str] = set()
    found = False
    for scope in scopes:
        if not any(is_scan(inner) for inner in ast.walk(scope)):
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
            if not is_scan(call):
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
            if base is None or base[0] > len(module_parts):
                # No anchor resolves (an unknown depth, or one above the
                # plugin): the scan may start anywhere, so it is the whole
                # plugin. A literal in the scope must not narrow a root it
                # cannot be placed under.
                starts.add(".")
                continue
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


# --------------------------------------------------------------------------
# Confinement: is a test-side module PROVEN to touch only safe locations?
# --------------------------------------------------------------------------
#
# CLOSED BY DEFAULT. Every rule below is an ALLOWLIST: an import, a call or a
# value the rules do not know is UNPROVEN, and one unproven site makes the
# whole exclusion BROAD. A new kind of access therefore defaults to broad
# instead of slipping past a list of known shapes.

# Modules PROVEN pure: none of their public names reaches a file, a process,
# the network, the environment or an import. A module that cannot meet that
# proof is I/O (every use checked) or unlisted (an unclassified import, so
# the exclusion is broad).
_COMPUTE_MODULES = frozenset({
    "__future__", "abc", "array", "ast", "base64", "binascii", "bisect",
    "calendar", "collections", "copy", "dataclasses", "datetime", "decimal",
    "difflib", "enum", "errno", "fnmatch", "fractions", "functools",
    "hashlib", "heapq", "hmac", "html", "itertools", "json", "keyword",
    "math", "numbers", "operator", "pprint", "queue", "random", "re",
    "secrets", "shlex", "stat", "statistics", "string", "struct",
    "textwrap", "threading", "time", "types", "typing", "typing_extensions",
    "unicodedata", "weakref", "zlib",
})
# Modules with at least one name that can touch files, processes, the
# environment or imports. Allowed, but every call needs confined values or a
# place on _PURE_CALLABLES, and every other reference must be a constant, an
# exception class or a listed safe name.
_IO_MODULES = frozenset({
    "asyncio", "codecs", "concurrent", "contextlib", "csv", "dis", "email",
    "glob", "hypothesis", "importlib", "inspect", "io", "locale", "logging",
    "os", "pathlib", "posixpath", "pytest", "runpy", "shutil", "signal",
    "sqlite3", "subprocess", "sys", "tempfile", "traceback", "unittest",
    "uuid", "warnings",
})
# Names in I/O modules proven to reach no location, by canonical dotted name.
# A call to one passes whatever arguments it likes; a reference to one is
# fine. A name ending in '.' covers everything under it.
_PURE_CALLABLES = frozenset({
    "pytest.raises", "pytest.skip", "pytest.fail", "pytest.xfail",
    "pytest.param", "pytest.approx", "pytest.fixture", "pytest.warns",
    "pytest.deprecated_call", "pytest.mark.", "pytest.MonkeyPatch.context",
    "pytest.MonkeyPatch",
    "unittest.TestCase", "unittest.IsolatedAsyncioTestCase", "unittest.skip",
    "unittest.skipIf", "unittest.skipUnless", "unittest.expectedFailure",
    "unittest.mock.Mock", "unittest.mock.MagicMock", "unittest.mock.AsyncMock",
    "unittest.mock.NonCallableMock", "unittest.mock.NonCallableMagicMock",
    "unittest.mock.PropertyMock", "unittest.mock.create_autospec",
    "unittest.mock.call", "unittest.mock.sentinel", "unittest.mock.ANY",
    "unittest.mock.DEFAULT", "unittest.mock.seal", "unittest.mock.mock_open",
    "contextlib.contextmanager", "contextlib.asynccontextmanager",
    "contextlib.suppress", "contextlib.ExitStack", "contextlib.AsyncExitStack",
    "contextlib.nullcontext", "contextlib.closing",
    "contextlib.redirect_stdout", "contextlib.redirect_stderr",
    "asyncio.run", "asyncio.gather", "asyncio.sleep", "asyncio.wait_for",
    "asyncio.wait", "asyncio.get_event_loop", "asyncio.new_event_loop",
    "asyncio.set_event_loop", "asyncio.get_running_loop", "asyncio.Event",
    "asyncio.Future", "asyncio.Queue", "asyncio.Lock", "asyncio.Semaphore",
    "asyncio.create_task", "asyncio.ensure_future", "asyncio.iscoroutine",
    "asyncio.iscoroutinefunction", "asyncio.timeout", "asyncio.shield",
    "concurrent.futures.ThreadPoolExecutor", "concurrent.futures.wait",
    "concurrent.futures.as_completed",
    "inspect.signature", "inspect.Parameter", "inspect.Signature",
    "inspect.isfunction", "inspect.iscoroutinefunction", "inspect.isclass",
    "inspect.ismethod", "inspect.isgeneratorfunction", "inspect.unwrap",
    "inspect.getattr_static", "inspect.getmembers", "inspect.isabstract",
    "logging.getLogger", "logging.debug", "logging.info", "logging.warning",
    "logging.error", "logging.exception", "logging.critical",
    "logging.Logger", "logging.LogRecord", "logging.Handler",
    "traceback.format_exc", "traceback.format_exception",
    "traceback.format_exception_only",
    "warnings.warn", "warnings.catch_warnings", "warnings.simplefilter",
    "warnings.filterwarnings", "warnings.resetwarnings",
    "uuid.uuid4", "uuid.UUID", "uuid.uuid3", "uuid.uuid5",
    "codecs.encode", "codecs.decode", "codecs.lookup", "codecs.BOM_UTF8",
    "signal.Signals", "signal.getsignal",
    "subprocess.CompletedProcess", "subprocess.PIPE", "subprocess.DEVNULL",
    "subprocess.STDOUT",
    "csv.reader", "csv.writer", "csv.DictReader", "csv.DictWriter",
    "pathlib.Path", "pathlib.PurePath", "pathlib.PurePosixPath",
    "pathlib.PureWindowsPath",
    "os.environ", "sys.path", "sys.argv", "sys.modules", "sys.flags",
})
# Names that load or run code by name or by path: unproven whatever their
# arguments.
_RUNS_CODE = frozenset({
    "pytest.main", "unittest.main", "runpy.run_path", "runpy.run_module",
    "importlib.reload", "importlib.__import__", "code.interact",
})
# Calls that import a module by its name: the name must be a literal that
# resolves to a classified or plugin module.
_IMPORT_BY_NAME = frozenset({
    "importlib.import_module", "pytest.importorskip",
})
# Attribute-name routes to a method no rule sees.
_ATTRIBUTE_GETTERS = frozenset({
    "operator.methodcaller", "operator.attrgetter",
})
# Mock constructors: a value at an injection site when its own arguments are.
_INJECTION_SAFE_CONSTRUCTORS = frozenset({
    "unittest.mock.Mock", "unittest.mock.MagicMock", "unittest.mock.AsyncMock",
    "unittest.mock.NonCallableMock", "unittest.mock.NonCallableMagicMock",
    "unittest.mock.PropertyMock", "unittest.mock.create_autospec",
    "unittest.mock.mock_open",
})
# In-memory stream constructors: their argument is data, not a location.
_IN_MEMORY_STREAMS = frozenset({"io.StringIO", "io.BytesIO"})
# Builtins that build or run code or read input: unproven whatever their
# arguments.
_ALWAYS_UNPROVEN_BUILTINS = frozenset({
    "exec", "eval", "compile", "__import__", "breakpoint", "input",
})
_IO_BUILTINS = frozenset({
    "open", "exec", "eval", "compile", "__import__", "breakpoint", "input",
})
_TMP_FIXTURES = frozenset({
    "tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory",
})
# Attribute reads and calls on I/O modules that pass no location.
_ZERO_ARG_CONFINED = frozenset({
    "os.sep", "os.pathsep", "os.linesep", "os.name", "os.environ.copy",
    "os.environ", "os.devnull", "os.curdir",
    "sys.executable", "sys.platform", "sys.version_info", "sys.modules",
    "sys.maxsize", "sys.stdout", "sys.stderr", "sys.stdin",
    "tempfile.mkdtemp", "tempfile.mkstemp", "tempfile.TemporaryDirectory",
    "tempfile.NamedTemporaryFile", "tempfile.TemporaryFile",
    "tempfile.gettempdir", "io.StringIO", "io.BytesIO",
})
_TEMPFILE_RESULTS = frozenset({
    "mkdtemp", "mkstemp", "TemporaryDirectory", "NamedTemporaryFile",
    "TemporaryFile", "gettempdir",
})
_LOCATION_SOURCES = frozenset({
    "pathlib.Path.cwd", "pathlib.Path.home", "os.getcwd", "os.getcwdb",
    "os.path.expanduser", "os.path.expandvars", "os.getenv", "os.getenvb",
    "os.environ.get", "posixpath.expanduser", "posixpath.expandvars",
})
# Environment calls that only remove state.
_ENV_SCRUBS = frozenset({
    "os.environ.pop", "os.environ.clear", "os.unsetenv",
})
# Environment calls that write state: their values are injected.
_ENV_WRITES = frozenset({
    "os.environ.update", "os.environ.setdefault", "os.putenv",
})
_SUBPROCESS_CALLS = frozenset({
    "run", "check_output", "Popen", "check_call", "call",
})
# Process launches with no argv/cwd discipline: always unproven.
_SHELL_CALL_PREFIXES = ("os.system", "os.popen", "os.exec", "os.spawn",
                        "os.startfile", "os.posix_spawn", "os.fork",
                        "asyncio.create_subprocess", "pty.")
# Keyword arguments of a subprocess call that carry data, not a location.
# cwd= and env= are injection sites; shell=True is unproven; any other
# keyword must be confined.
_SUBPROCESS_DATA_KEYWORDS = frozenset({
    "input", "capture_output", "text", "timeout", "check", "encoding",
    "errors", "stdout", "stderr", "universal_newlines", "stdin",
})
# monkeypatch methods that only remove state.
_MONKEYPATCH_SCRUBS = frozenset({"delenv", "delattr", "delitem", "undo",
                                 "context"})
# mock.patch keywords that name a spec, not a value.
_PATCH_SPEC_KEYWORDS = frozenset({"spec", "spec_set", "autospec", "create",
                                  "clear"})
# Builtins and constructors a confined value may pass through.
_PASSTHROUGH_NAMES = frozenset({
    "str", "Path", "PurePath", "len", "sorted", "list", "tuple", "dict",
    "set", "int", "bool", "repr", "range", "bytes", "frozenset", "enumerate",
    "zip", "min", "max", "any", "all", "isinstance", "True", "False", "None",
})
_TOKEN_SPLIT = re.compile(r"[\s=,;]")


def _path_methods() -> frozenset[str]:
    """pathlib.Path and io.IOBase method names, computed rather than listed,
    minus pure attributes that read no file."""
    import io as _io
    import pathlib as _pathlib
    names = {name for name in dir(_pathlib.Path) if not name.startswith("_")}
    names |= {name for name in dir(_io.IOBase) if not name.startswith("_")}
    pure = {"name", "parts", "suffix", "stem", "parent", "parents", "anchor",
            "drive", "root", "suffixes", "with_name", "with_suffix",
            "with_stem", "joinpath", "relative_to", "is_relative_to",
            "as_posix", "match", "full_match", "with_segments",
            "is_absolute", "is_reserved", "as_uri", "parser"}
    return frozenset(names - pure)


_PATH_METHODS = _path_methods()
# String methods that share a name with a Path method; on a literal or a
# str they read nothing.
_PURE_STRING_METHODS = frozenset({
    "split", "join", "strip", "replace", "format", "startswith", "endswith",
    "count", "index", "find", "lower", "upper", "encode", "decode", "sort",
})
# Receivers whose methods are never file reads.
_NON_FILE_RECEIVERS = frozenset({
    "monkeypatch", "self", "cls", "mock", "re", "json", "capsys",
    "caplog", "request",
})
_MISSING = object()


def _is_pure(canonical: str | None) -> bool:
    return bool(canonical) and (canonical in _PURE_CALLABLES or any(
        entry.endswith(".") and canonical.startswith(entry)
        for entry in _PURE_CALLABLES))


@functools.lru_cache(maxsize=None)
def _io_object(canonical: str) -> object:
    """The object a canonical name in an I/O module names, or _MISSING."""
    parts = canonical.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            found: object = importlib.import_module(".".join(parts[:cut]))
        except Exception:  # noqa: BLE001 - any import failure is "unknown"
            continue
        for attr in parts[cut:]:
            found = getattr(found, attr, _MISSING)
            if found is _MISSING:
                return _MISSING
        return found
    return _MISSING


def _inert_reference(canonical: str) -> bool:
    """A reference (not a call) to an I/O-module name that reaches nothing:
    a listed pure name, a plain constant or an exception class."""
    if _is_pure(canonical) or canonical in _ZERO_ARG_CONFINED:
        return True
    found = _io_object(canonical)
    if found is _MISSING:
        return False
    if isinstance(found, type) and issubclass(found, BaseException):
        return True
    return isinstance(found, (int, float, str, bytes, bool, type(None),
                              tuple, frozenset, enum.Enum))


# I/O-module names that are references to a location, not inert data.
_LOCATION_DATA = frozenset({"sys.path", "sys.argv"})


def _value_reference(canonical: str) -> bool:
    """An I/O-module name a confined VALUE may carry: a listed constant, or
    an inert reference that is not a string (sys.prefix, tempfile.tempdir
    and their kind name real locations)."""
    if canonical in _ZERO_ARG_CONFINED:
        return True
    if canonical in _LOCATION_DATA or not _inert_reference(canonical):
        return False
    return not isinstance(_io_object(canonical), (str, bytes, list))


def _builtin_exception(name: str) -> bool:
    import builtins
    found = getattr(builtins, name, None)
    return isinstance(found, type) and issubclass(found, BaseException)


def _dotted(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError, TypeError):
        return ""


def _literal_string(node: ast.AST) -> str | None:
    """A string built only from literals: a constant, '/' or '+' of
    literals, os.path.join / joinpath of literals, or an f-string with no
    expressions."""
    text = _str_constant(node)
    if text is not None:
        return text
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        left, right = _literal_string(node.left), _literal_string(node.right)
        if left is None or right is None:
            return None
        return (left.rstrip("/") + "/" + right.lstrip("/")
                if isinstance(node.op, ast.Div) else left + right)
    if isinstance(node, ast.JoinedStr):
        parts = [_str_constant(value) for value in node.values]
        return None if any(part is None for part in parts) else "".join(parts)
    if isinstance(node, ast.Call) and _call_name(node) in ("join", "joinpath",
                                                           "Path", "PurePath"):
        pieces = [_literal_string(arg) for arg in node.args]
        if not pieces or any(piece is None for piece in pieces):
            return None
        if (_call_name(node) == "joinpath"
                and isinstance(node.func, ast.Attribute)):
            head = _literal_string(node.func.value)
            if head is None:
                return None
            pieces = [head, *pieces]
        return "/".join(piece.strip("/") for piece in pieces if piece)
    return None


def _names_repo_path(text: str, repo_root: Path, plugin_root: Path) -> bool:
    """True when `text` names an existing repository path outside the
    plugin (and not the same relative path inside it)."""
    if not text or len(text) > _MAX_LITERAL or any(ch.isspace() for ch in text):
        return False
    candidate = text
    while candidate.startswith("../"):
        candidate = candidate[3:]
    candidate = candidate.strip("/")
    first = candidate.split("/", 1)[0]
    if not candidate or first in ("", ".", "..", PLUGIN_DIRNAME) or "*" in candidate:
        return False
    target = repo_root / candidate
    try:
        return (("/" in candidate or target.is_file()) and target.exists()
                and not (plugin_root / candidate).exists())
    except (OSError, ValueError):
        return False


def _confined_string(value: str) -> bool:
    """A string that can name only a relative location with no escape: no
    leading '/' or '~' on any token, no '..' segment, no ':' and no
    whitespace."""
    if any(ch.isspace() for ch in value) or ":" in value:
        return False
    if ".." in value.split("/"):
        return False
    return not any(token.startswith(("/", "~"))
                   for token in _TOKEN_SPLIT.split(value))


def _confinement_reasons(tree: ast.Module, rel: str, roots: list[Path],
                         plugin_root: Path,
                         docstrings: set[int]) -> tuple[str, ...]:
    """'line:why' for every site that keeps this test-side module unproven.

    (1) every import is COMPUTE, I/O or a plugin module, and every import by
        name resolves to one;
    (2) every call into an I/O module is a pure name or passes only confined
        values; a subprocess needs a confined argv with no '-c' and no
        shell, and a cwd= in a temporary folder unless it is a python run
        with no operand;
    (3) every Path or file method, called or referenced, has a confined
        receiver, and no getattr or attribute getter names one by a value;
    (4) every other reference to an I/O builtin or I/O-module name is inert;
    (5) every value injected into process state (monkeypatch, mock.patch,
        os.environ, sys.*, a module attribute, subprocess env= and cwd=)
        is built from temporary folders and plain literals, or is a mock,
        a lambda or a module-defined function or class whose results are;
    (6) no literal, literal concatenation or '<rev>:<path>' names a
        repository path outside the plugin.
    Confined values: tmp fixtures and tempfile results, __file__ anchors
    inside the plugin (not at an injection site), confined strings,
    sys.executable, and values built only from those.
    """
    repo_root = plugin_root.parent
    reasons: list[str] = []
    io_names = set(_IO_BUILTINS)
    subprocess_names: set[str] = set()
    other_modules: set[str] = set()
    canonical_of: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                bound = (alias.asname or alias.name).split(".")[0]
                canonical_of[bound] = alias.name if alias.asname else top
                if top in _COMPUTE_MODULES:
                    other_modules.add(bound)
                elif top in _IO_MODULES:
                    io_names.add(bound)
                    if top == "subprocess":
                        subprocess_names.add(bound)
                elif _resolve_module(alias.name, roots):
                    other_modules.add(bound)
                else:
                    reasons.append(
                        f"{node.lineno}:unclassified import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                other_modules.update(alias.asname or alias.name
                                     for alias in node.names)
                continue  # a relative import is a plugin module
            module = node.module or ""
            top = module.split(".")[0]
            for alias in node.names:
                canonical_of[alias.asname or alias.name] = (
                    f"{module}.{alias.name}")
            if top in _COMPUTE_MODULES:
                other_modules.update(alias.asname or alias.name
                                     for alias in node.names)
            elif top in _IO_MODULES:
                io_names.update(alias.asname or alias.name
                                for alias in node.names)
                if top == "subprocess":
                    subprocess_names.update(alias.asname or alias.name
                                            for alias in node.names)
            elif _resolve_module(module, roots):
                other_modules.update(alias.asname or alias.name
                                     for alias in node.names)
            else:
                reasons.append(f"{node.lineno}:unclassified import {module}")
    module_bound = (io_names - _IO_BUILTINS) | other_modules
    compute_names = {name for name in other_modules
                     if canonical_of.get(name, name).split(".")[0]
                     in _COMPUTE_MODULES}

    def canonical(node: ast.AST) -> str | None:
        """'os.path.join' for os.path.join, 'pathlib.Path.cwd' for Path.cwd
        after `from pathlib import Path`; None when not rooted at a name."""
        attrs: list[str] = []
        while isinstance(node, ast.Attribute):
            attrs.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        head = canonical_of.get(node.id, node.id)
        return ".".join([head, *reversed(attrs)])

    def root_name(node: ast.AST) -> ast.AST:
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        return node

    def io_rooted(node: ast.AST) -> bool:
        root = root_name(node)
        return isinstance(root, ast.Name) and root.id in io_names

    _, _, resolved = _module_anchors(tree, rel, {})
    depth = len(Path(rel).parts)
    anchored_inside = {name for name, (level, _parts) in resolved.items()
                       if "." not in name and level <= depth}

    def climbs_out(expr: ast.AST) -> bool:
        """An anchored climb in `expr` that leaves, or may leave, the plugin."""
        for sub in ast.walk(expr):
            is_climb = (
                (isinstance(sub, ast.Attribute) and sub.attr in ("parent",
                                                                 "parents"))
                or (isinstance(sub, ast.Subscript)
                    and isinstance(sub.value, ast.Attribute)
                    and sub.value.attr == "parents")
                or (isinstance(sub, ast.Call) and _call_name(sub) == "dirname")
                or (isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Div)))
            if not is_climb or not (_uses_file(sub) or any(
                    isinstance(name, ast.Name) and name.id in resolved
                    for name in ast.walk(sub))):
                continue
            if (isinstance(sub, ast.Attribute) and sub.attr == "parents"
                    and not any(isinstance(parent, ast.Subscript)
                                and parent.value is sub
                                for parent in ast.walk(expr))):
                return True  # a walk over every ancestor
            if isinstance(sub, ast.Attribute) and sub.attr == "parents":
                continue  # judged as its subscript
            found = _anchor_of(sub, resolved)
            if found is None:
                if not isinstance(sub, ast.BinOp):
                    return True  # an unresolvable climb
                continue
            if found[0] > depth:
                return True
        return False

    def confined(expr: ast.AST, local: set[str], anchors: bool = True) -> bool:
        """Built only from confined names and strings. With anchors=False a
        __file__ anchor is not confined: an injected checkout path lets the
        code under test read whatever it likes below it."""
        if climbs_out(expr):
            return False
        inner_parents = {id(child): node for node in ast.walk(expr)
                         for child in ast.iter_child_nodes(node)}
        for sub in ast.walk(expr):
            if isinstance(sub, (ast.Name, ast.Attribute)) and io_rooted(sub):
                parent = inner_parents.get(id(sub))
                if not ((isinstance(parent, ast.Attribute)
                         and parent.value is sub)
                        or (isinstance(parent, ast.Call)
                            and parent.func is sub)):
                    # A value taken from an I/O module, not called: only a
                    # name proven to carry no location.
                    canon = canonical(sub)
                    if canon is None or not _value_reference(canon):
                        return False
            if isinstance(sub, ast.Name):
                if sub.id in local or sub.id in _PASSTHROUGH_NAMES:
                    continue
                if anchors and (sub.id in anchored_inside
                                or sub.id == "__file__"):
                    continue
                if sub.id in io_names:
                    continue  # judged where it is called or referenced
                if sub.id in compute_names:
                    parent = inner_parents.get(id(sub))
                    if (isinstance(parent, ast.Attribute)
                            and parent.value is sub) or (
                            isinstance(parent, ast.Call)
                            and parent.func is sub):
                        continue  # json.dumps(...): data from data
                return False  # a bare module object is not a location
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if not _confined_string(sub.value):
                    return False
            if isinstance(sub, ast.Attribute) and sub.attr == "__file__":
                return False
        if not anchors:
            return True
        scope = scope_of.get(id(expr), tree)
        tmp = tmp_locals.get(id(scope), set())
        if any(isinstance(sub, ast.Name) and (
                sub.id in anchored_inside or sub.id == "__file__"
                or (sub.id in local and sub.id not in tmp))
               for sub in ast.walk(expr)):
            # A checkout path: the selector finds the file it names only from
            # the literals it is built from, so every part must be one.
            return resolvable(expr, local, scope)
        return True

    def resolvable(expr: ast.AST, local: set[str], scope: ast.AST,
                   depth: int = 0) -> bool:
        """A checkout path built from the anchor and literal parts only: no
        part computed by concatenation, formatting or a call."""
        if depth > 24:
            return False
        tmp = tmp_locals.get(id(scope), set())
        if isinstance(expr, ast.Constant):
            return isinstance(expr.value, (str, int))
        if isinstance(expr, ast.Name):
            if (expr.id == "__file__" or expr.id in anchored_inside
                    or (expr.id in local and expr.id not in tmp)):
                return True  # the anchor, or a name whose binding was judged
            return literal_strings(expr, scope) is not None
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
            return (resolvable(expr.left, local, scope, depth + 1)
                    and resolvable(expr.right, local, scope, depth + 1))
        if isinstance(expr, (ast.BinOp, ast.JoinedStr)):
            return _literal_string(expr) is not None
        if isinstance(expr, ast.Subscript) and isinstance(
                expr.value, ast.Attribute) and expr.value.attr == "parents":
            return resolvable(expr.value.value, local, scope, depth + 1)
        if isinstance(expr, ast.Attribute) and expr.attr in ("parent",
                                                              "parents"):
            return resolvable(expr.value, local, scope, depth + 1)
        if isinstance(expr, ast.Call):
            func = expr.func
            canon = canonical(func) or ""
            arguments = [*expr.args, *(k.value for k in expr.keywords)]
            if _is_scan_call(expr):
                # What a scan of a checkout directory yields is under a
                # scanner root, which the scanned-tree rule already covers.
                receiver = (expr.args[0] if canon.startswith(("os.", "glob."))
                            and expr.args else func.value
                            if isinstance(func, ast.Attribute) else None)
                return receiver is not None and resolvable(
                    receiver, local, scope, depth + 1)
            if isinstance(func, ast.Name) and func.id in (
                    "sorted", "list", "tuple", "set", "reversed",
                    "frozenset") and len(expr.args) == 1:
                return resolvable(expr.args[0], local, scope, depth + 1)
            if (isinstance(func, ast.Name) and func.id in ("str", "Path",
                                                           "PurePath")) or (
                    canon in ("pathlib.Path", "pathlib.PurePath",
                              "os.path.join", "os.path.dirname",
                              "os.path.abspath", "os.path.realpath",
                              "os.path.normpath", "os.fspath")):
                return all(resolvable(argument, local, scope, depth + 1)
                           for argument in arguments)
            if isinstance(func, ast.Attribute) and func.attr in (
                    "resolve", "absolute", "joinpath", "with_name",
                    "with_suffix", "as_posix"):
                return resolvable(func.value, local, scope, depth + 1) and all(
                    resolvable(argument, local, scope, depth + 1)
                    for argument in arguments)
        return False

    # Every function in the module, by name, for injected callables.
    defs: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            defs.setdefault(node.name, node)
    fixture_defs = {name for name, node in defs.items()
                    if isinstance(node, (ast.FunctionDef,
                                         ast.AsyncFunctionDef))
                    and any(_dotted(d.func if isinstance(d, ast.Call) else d)
                            .endswith("fixture")
                            for d in node.decorator_list)}

    # Scopes: the module (everything outside a function, class bodies
    # included) and each function. One pass records each node's innermost
    # scope and each function's enclosing function.
    scope_of: dict[int, ast.AST] = {}
    nodes_by_scope: dict[int, list[ast.AST]] = {id(tree): []}
    enclosing: dict[int, ast.AST] = {}
    scopes: list[ast.AST] = [tree]
    stack: list[tuple[ast.AST, ast.AST]] = [(tree, tree)]
    while stack:
        node, scope = stack.pop()
        scope_of[id(node)] = scope
        nodes_by_scope[id(scope)].append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if scope is not tree:
                    enclosing[id(child)] = scope
                scopes.append(child)
                nodes_by_scope[id(child)] = []
                stack.append((child, child))
            else:
                stack.append((child, scope))
    bindings_cache: dict[int, dict[str, list[ast.AST | None]]] = {}

    def bindings_of(scope: ast.AST) -> dict[str, list[ast.AST | None]]:
        if id(scope) not in bindings_cache:
            bindings_cache[id(scope)] = bindings(nodes_by_scope[id(scope)])
        return bindings_cache[id(scope)]

    module_scope = tree

    def sequence_strings(expr: ast.AST) -> list[str] | None:
        """The strings a literal tuple or list (or a module-level name bound
        only to one) holds."""
        if isinstance(expr, ast.Name):
            values = bindings_of(module_scope).get(expr.id, ())
            if len(values) != 1 or values[0] is None:
                return None
            expr = values[0]
        if not isinstance(expr, (ast.Tuple, ast.List)):
            return None
        items = [_str_constant(element) for element in expr.elts]
        return None if any(item is None for item in items) else items

    def literal_strings(expr: ast.AST, scope: ast.AST) -> list[str] | None:
        """Every string `expr` can be: a literal, or a name bound only by a
        loop over a literal sequence. None when any value is computed."""
        text = _str_constant(expr)
        if text is not None:
            return [text]
        if not isinstance(expr, ast.Name):
            return None
        owner = scope if expr.id in bindings_of(scope) else module_scope
        values = bindings_of(owner).get(expr.id, ())
        loops = [inner for inner in nodes_by_scope[id(owner)]
                 if isinstance(inner, (ast.For, ast.comprehension))
                 and isinstance(inner.target, ast.Name)
                 and inner.target.id == expr.id]
        if not values:
            return None
        iterators = {id(loop.iter) for loop in loops}
        found: list[str] = []
        for value in values:
            items = (sequence_strings(value) if value is not None
                     and id(value) in iterators
                     else [_str_constant(value)] if value is not None
                     and _str_constant(value) is not None else None)
            if items is None:
                return None
            found += items
        return found

    def bindings(nodes: list[ast.AST]) -> dict[str, list[ast.AST | None]]:
        """Every value bound to, or written into, each local name. None is a
        value no rule can judge (an augmented or unpacked write)."""
        found: dict[str, list[ast.AST | None]] = {}

        def bind(target: ast.AST, value: ast.AST | None) -> None:
            if isinstance(target, ast.Name):
                found.setdefault(target.id, []).append(value)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for element in target.elts:
                    bind(element, None)
            elif isinstance(target, ast.Starred):
                bind(target.value, None)
            elif isinstance(target, ast.Subscript) and isinstance(
                    target.value, ast.Name):
                # env["X"] = value writes into the name's value.
                found.setdefault(target.value.id, []).append(value)

        for node in nodes:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(
                            node.value, (ast.Tuple, ast.List)) and len(
                            target.elts) == len(node.value.elts):
                        for element, value in zip(target.elts,
                                                  node.value.elts):
                            bind(element, value)
                    elif isinstance(target, (ast.Tuple, ast.List)):
                        for element in ast.walk(target):
                            if isinstance(element, ast.Name):
                                found.setdefault(element.id, []).append(
                                    node.value)
                    else:
                        bind(target, node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                bind(node.target, node.value)
            elif isinstance(node, ast.AugAssign):
                bind(node.target, node.value)
            elif isinstance(node, ast.NamedExpr):
                bind(node.target, node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                for element in ast.walk(node.target):
                    if isinstance(element, ast.Name):
                        found.setdefault(element.id, []).append(node.iter)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        for element in ast.walk(item.optional_vars):
                            if isinstance(element, ast.Name):
                                found.setdefault(element.id, []).append(
                                    item.context_expr)
            elif (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.attr in ("update", "setdefault", "append",
                                           "extend", "insert", "add")):
                # env.update({...}) writes into the name's value.
                for value in [*node.args, *(k.value for k in node.keywords)]:
                    found.setdefault(node.func.value.id, []).append(value)
        return found

    def solve(scope: ast.AST, seed: set[str], anchors: bool,
              inherited: set[str]) -> set[str]:
        """The names every binding of which is confined: a greatest fixed
        point, so a name built from itself (p = p / 'x') stays confined."""
        bound = {name: list(values)
                 for name, values in bindings_of(scope).items()}
        for name in params(scope):
            if name not in seed and name in bound:
                bound[name].append(None)  # the caller's value
        candidates = set(bound) | seed
        while True:
            names = candidates | inherited
            drop = {name for name in candidates - seed
                    if any(value is None or not confined(value, names,
                                                         anchors)
                           for value in bound.get(name, ()))}
            # A seeded tmp name rebound to something else is not confined.
            drop |= {name for name in seed & set(bound)
                     if any(value is None or not confined(value, names,
                                                          anchors)
                            for value in bound[name])}
            if not drop:
                return names
            candidates -= drop

    final_locals: dict[int, set[str]] = {}
    tmp_locals: dict[int, set[str]] = {}

    def params(scope: ast.AST) -> list[str]:
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda)):
            return []
        arguments = scope.args
        names = [arg.arg for arg in (*arguments.posonlyargs, *arguments.args,
                                     *arguments.kwonlyargs)]
        names += [arg.arg for arg in (arguments.vararg, arguments.kwarg)
                  if arg is not None]
        return names

    tmp_fixture_cache: dict[str, bool] = {}

    for scope in scopes:
        parent = enclosing.get(id(scope))
        while parent is not None and id(parent) not in final_locals:
            parent = enclosing.get(id(parent))
        if parent is None and scope is not module_scope:
            parent = module_scope  # a module-level name every binding of
            # which is confined is confined inside functions too
        inherited = final_locals[id(parent)] if parent is not None else set()
        inherited_tmp = tmp_locals[id(parent)] if parent is not None else set()
        seed = {name for name in params(scope) if name in _TMP_FIXTURES}
        tmp_locals[id(scope)] = solve(scope, seed, False, inherited_tmp)
        final_locals[id(scope)] = solve(scope, seed, True, inherited)


    def returns_injectable(name: str, depth: int) -> bool:
        """A module-defined function or class whose results (returns,
        yields, class-level values) are injectable."""
        node = defs.get(name)
        if node is None or depth > 4:
            return False
        if isinstance(node, ast.ClassDef):
            values = [statement.value for statement in node.body
                      if isinstance(statement, (ast.Assign, ast.AnnAssign))
                      and statement.value is not None]
            methods = [statement.name for statement in node.body
                       if isinstance(statement, (ast.FunctionDef,
                                                 ast.AsyncFunctionDef))]
            local = set()
            return all(injectable(value, local, depth + 1) for value in values
                       ) and all(returns_injectable_def(defs_in(node, method),
                                                        depth + 1)
                                 for method in methods)
        return returns_injectable_def(node, depth)

    def defs_in(cls: ast.ClassDef, name: str) -> ast.AST:
        for statement in cls.body:
            if getattr(statement, "name", None) == name:
                return statement
        return cls

    def returns_injectable_def(node: ast.AST, depth: int) -> bool:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        # The function's own arguments are the caller's values, passed
        # through; anything it builds itself must be injectable.
        local = tmp_locals.get(id(node), set()) | set(params(node))
        for inner in ast.walk(node):
            if isinstance(inner, (ast.Return, ast.Yield, ast.YieldFrom)) and (
                    inner.value is not None
                    and not injectable(inner.value, local, depth + 1)):
                return False
        return True

    def tmp_fixture(name: str) -> bool:
        """A fixture defined in this module whose results are injectable."""
        if name not in tmp_fixture_cache:
            tmp_fixture_cache[name] = False
            tmp_fixture_cache[name] = (name in fixture_defs
                                       and returns_injectable(name, 0))
        return tmp_fixture_cache[name]

    def injectable(expr: ast.AST, local: set[str], depth: int = 0) -> bool:
        """A value that cannot hand the code under test a real location."""
        if depth > 6:
            return False
        if isinstance(expr, ast.Lambda):
            return injectable(expr.body, local | set(params(expr)), depth + 1)
        if isinstance(expr, ast.Name):
            if expr.id in local or expr.id in ("True", "False", "None"):
                return True
            if expr.id in defs:
                return returns_injectable(expr.id, depth + 1)
            if _builtin_exception(expr.id) or expr.id in _PASSTHROUGH_NAMES:
                return True
            canon = canonical(expr)
            return (expr.id in io_names and canon is not None
                    and (_is_pure(canon) or _inert_reference(canon)))
        if isinstance(expr, ast.Call):
            canon = canonical(expr.func)
            arguments = [*expr.args, *(keyword.value
                                       for keyword in expr.keywords)]
            if canon in _IN_MEMORY_STREAMS:
                return True
            if (canon in _INJECTION_SAFE_CONSTRUCTORS
                    or (isinstance(expr.func, ast.Name)
                        and (expr.func.id in defs
                             or _builtin_exception(expr.func.id)))):
                if (isinstance(expr.func, ast.Name) and expr.func.id in defs
                        and not returns_injectable(expr.func.id, depth + 1)):
                    return False
                return all(injectable(argument, local, depth + 1)
                           for argument in arguments)
        if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
            return all(injectable(element, local, depth + 1)
                       for element in expr.elts)
        if isinstance(expr, ast.Dict):
            return all(injectable(part, local, depth + 1)
                       for part in [*expr.keys, *expr.values]
                       if part is not None)
        if isinstance(expr, ast.Starred):
            return injectable(expr.value, local, depth + 1)
        return confined(expr, local, anchors=False)

    def judge_injection(node: ast.AST, values: list[ast.AST], what: str,
                        local: set[str], anchors_ok: bool = False) -> None:
        for value in values:
            ok = (confined(value, local_any(node), True) if anchors_ok
                  else injectable(value, local))
            if not ok:
                reasons.append(f"{node.lineno}:injects an unproven value "
                               f"into {what}")
                return

    def local_any(node: ast.AST) -> set[str]:
        return final_locals.get(id(scope_of.get(id(node))), set())

    def local_tmp(node: ast.AST) -> set[str]:
        scope = scope_of.get(id(node))
        names = set(tmp_locals.get(id(scope), set()))
        names |= {name for name in params(scope) if tmp_fixture(name)}
        return names

    def monkeypatch_receiver(func: ast.AST) -> bool:
        if not isinstance(func, ast.Attribute):
            return False
        base = root_name(func.value)
        if isinstance(base, ast.Name) and base.id == "monkeypatch":
            return True
        return monkeypatch_value(base, scope_of.get(id(func), tree))

    def monkeypatch_value(value: ast.AST | None, scope: ast.AST,
                          depth: int = 0) -> bool:
        """A pytest.MonkeyPatch() or MonkeyPatch.context() call, or a name
        bound to one (in the scope or the module), followed through
        `with CTX as mp` and plain rebinding."""
        if value is None or depth > 4:
            return False
        if isinstance(value, ast.Call):
            return (canonical(value.func) or "").endswith(
                ("MonkeyPatch.context", "MonkeyPatch"))
        if isinstance(value, ast.Name):
            return any(
                monkeypatch_value(bound, scope, depth + 1)
                for bound in [*bindings_of(scope).get(value.id, ()),
                              *bindings_of(module_scope).get(value.id, ())])
        return False

    def injection_site(node: ast.Call) -> tuple[list[ast.AST], str,
                                                bool] | None:
        """(values, what, anchors_ok) when this call writes process state."""
        func = node.func
        canon = canonical(func) or ""
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        if monkeypatch_receiver(func):
            method = func.attr
            if method in _MONKEYPATCH_SCRUBS:
                return [], "", False
            if method == "setattr":
                positional = (node.args[1:2] if node.args and _str_constant(
                    node.args[0]) is not None else node.args[2:3])
                return ([*positional, *([keywords["value"]]
                                        if "value" in keywords else [])],
                        "monkeypatch.setattr", False)
            if method == "setenv":
                return ([*node.args[1:2], *[v for k, v in keywords.items()
                                            if k == "value"]],
                        "monkeypatch.setenv", False)
            if method == "setitem":
                return ([*node.args[2:3], *[v for k, v in keywords.items()
                                            if k == "value"]],
                        "monkeypatch.setitem", False)
            if method == "syspath_prepend":
                return list(node.args), "sys.path", True
            if method == "chdir":
                return None  # judged by the working-directory rule
            return ([*node.args, *keywords.values()],
                    f"monkeypatch.{method}", False)
        if canon == "unittest.mock.patch" or canon.startswith(
                "unittest.mock.patch."):
            kind = canon.rsplit(".", 1)[-1]
            skip = 2 if kind == "object" else 1
            if kind in ("stopall", "stop"):
                return [], "", False
            return ([*node.args[skip:],
                     *[value for key, value in keywords.items()
                       if key not in _PATCH_SPEC_KEYWORDS]],
                    f"mock.{kind if kind != 'patch' else 'patch'}", False)
        if canon in _ENV_SCRUBS:
            return [], "", False
        if canon in _ENV_WRITES:
            positional = (list(node.args) if canon == "os.environ.update"
                          else list(node.args[1:]))
            return [*positional, *keywords.values()], "os.environ", False
        if canon.startswith("sys.path."):
            return [*node.args, *keywords.values()], "sys.path", True
        if (isinstance(func, ast.Name) and func.id == "setattr"
                and len(node.args) >= 3
                and isinstance(root_name(node.args[0]), ast.Name)
                and root_name(node.args[0]).id in module_bound):
            return [node.args[2]], "a module attribute", False
        return None

    # Where a reference to an I/O name is not a use: annotations, the type
    # positions of isinstance/issubclass and except, and the module or
    # object that a patch or setattr targets.
    exempt: set[int] = set()
    for node in ast.walk(tree):
        subtrees: list[ast.AST] = []
        if isinstance(node, ast.arg) and node.annotation is not None:
            subtrees.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.returns is not None):
            subtrees.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            subtrees.append(node.annotation)
        elif isinstance(node, ast.ExceptHandler) and node.type is not None:
            subtrees.append(node.type)
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if name in ("isinstance", "issubclass") and len(node.args) > 1:
                subtrees.append(node.args[1])
            if name in ("setattr", "delattr", "hasattr") and node.args:
                subtrees.append(node.args[0])
            canon = canonical(node.func) or ""
            if (monkeypatch_receiver(node.func)
                    and node.func.attr in ("setattr", "delattr", "setitem",
                                           "delitem") and node.args):
                subtrees.append(node.args[0])
            if canon in ("unittest.mock.patch.object",
                         "unittest.mock.patch.dict",
                         "unittest.mock.patch.multiple") and node.args:
                subtrees.append(node.args[0])
        for subtree in subtrees:
            exempt.update(id(inner) for inner in ast.walk(subtree))
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    bound_anywhere = {node.id for node in ast.walk(tree)
                      if isinstance(node, ast.Name)
                      and isinstance(node.ctx, (ast.Store, ast.Del))}
    bound_anywhere |= {node.arg for node in ast.walk(tree)
                       if isinstance(node, ast.arg)}
    bound_anywhere |= set(defs)

    def receiver_proven(receiver: ast.AST, local: set[str]) -> bool:
        base = receiver
        while isinstance(base, (ast.Attribute, ast.Subscript, ast.Call)):
            base = base.func if isinstance(base, ast.Call) else base.value
        if isinstance(base, ast.Name) and (
                base.id in other_modules or base.id in _NON_FILE_RECEIVERS):
            return True
        if isinstance(base, ast.Constant):
            return True  # a literal string's method
        return confined(receiver, local)

    # (4) and (3): references that are not calls.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(
                node.ctx, ast.Load) or id(node) in exempt:
            continue
        parent = parents.get(id(node))
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue  # part of a longer name
        if isinstance(parent, ast.Call) and parent.func is node:
            continue  # a call, judged below
        if isinstance(node, ast.Name):
            if node.id in _IO_BUILTINS and node.id not in bound_anywhere:
                reasons.append(f"{node.lineno}:references the I/O builtin "
                               f"{node.id}")
            elif node.id == "__builtins__":
                reasons.append(f"{node.lineno}:references __builtins__")
            elif node.id in io_names and node.id not in _IO_BUILTINS:
                canon = canonical(node) or node.id
                if not _inert_reference(canon):
                    reasons.append(f"{node.lineno}:references the I/O name "
                                   f"{canon}")
            continue
        if io_rooted(node):
            canon = canonical(node)
            if canon is None or not _inert_reference(canon):
                reasons.append(f"{node.lineno}:references the I/O name "
                               f"{canon or _dotted(node)}")
            continue
        if node.attr in _PATH_METHODS and node.attr not in _PURE_STRING_METHODS:
            if not receiver_proven(node.value, local_any(node)):
                reasons.append(f"{node.lineno}:references a path method on an "
                               f"unproven receiver .{node.attr}")

    # (5) assignments that write process state.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            continue
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target])
        if node.value is None:
            continue
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Subscript) and canonical(
                        sub.value) == "os.environ":
                    judge_injection(node, [node.value], "os.environ",
                                    local_tmp(node))
                elif isinstance(sub, ast.Attribute) and isinstance(
                        root_name(sub), ast.Name) and root_name(
                        sub).id in module_bound:
                    canon = canonical(sub) or ""
                    judge_injection(node, [node.value],
                                    f"the module attribute {canon}",
                                    local_tmp(node),
                                    anchors_ok=canon.startswith("sys.path"))
                elif sub is target and isinstance(sub, ast.Attribute):
                    # fake.return_value = REPO: a mock's attributes reach
                    # the code under test once it is patched in.
                    judge_injection(node, [node.value],
                                    f"the attribute .{sub.attr}",
                                    local_tmp(node))

    def rig_path(expr: ast.AST, tmp: set[str], depth: int = 0) -> bool:
        """A working directory proven to be a temporary folder: injectable
        AND rooted at a tmp fixture, a tempfile result or a name bound only
        to one. None (inherit the checkout's cwd), a bare literal or any
        other value is not a rig."""
        return injectable(expr, tmp) and tmp_rooted(expr, depth)

    def tmp_rooted(expr: ast.AST, depth: int = 0) -> bool:
        if depth > 8:
            return False
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Name):
                if sub.id in _TMP_FIXTURES:
                    return True
                if tmp_fixture(sub.id) and all(
                        inner.value is not None
                        and tmp_rooted(inner.value, depth + 1)
                        for inner in ast.walk(defs[sub.id])
                        if isinstance(inner, (ast.Return, ast.Yield))):
                    return True  # a module fixture that returns a tmp path
                scope = scope_of.get(id(sub), tree)
                owner = (scope if sub.id in bindings_of(scope)
                         else module_scope)
                values = bindings_of(owner).get(sub.id, ())
                if values and all(value is not None
                                  and tmp_rooted(value, depth + 1)
                                  for value in values):
                    return True
            if isinstance(sub, (ast.Call, ast.Attribute)):
                target = sub.func if isinstance(sub, ast.Call) else sub
                if (canonical(target) or "").startswith("tempfile."):
                    return True
        return False

    def judge_subprocess(node: ast.Call, dotted: str, local: set[str],
                         tmp: set[str]) -> None:
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        shell = keywords.get("shell")
        if shell is not None and not (isinstance(shell, ast.Constant)
                                      and shell.value is False):
            reasons.append(f"{node.lineno}:shell launch {dotted}()")
            return
        argv = node.args[0] if node.args else keywords.get("args")
        elements = (argv.elts if isinstance(argv, (ast.List, ast.Tuple))
                    else None)
        if elements is not None and any(
                re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", _str_constant(item) or "")
                for item in elements):
            reasons.append(f"{node.lineno}:inline code (-c) {dotted}()")
            return
        argv_ok = argv is not None and (
            all(confined(item, local) for item in elements)
            if elements is not None else confined(argv, local))
        if not argv_ok:
            reasons.append(f"{node.lineno}:unproven argv {dotted}()")
            return
        if "env" in keywords and not injectable(keywords["env"], tmp):
            reasons.append(f"{node.lineno}:injects an unproven env= "
                           f"{dotted}()")
            return
        for key, value in keywords.items():
            if key in _SUBPROCESS_DATA_KEYWORDS or key in ("env", "cwd",
                                                           "shell", "args"):
                continue
            if key is None or not confined(value, local):
                reasons.append(f"{node.lineno}:unproven keyword {key}= "
                               f"{dotted}()")
                return
        python = bool(elements) and canonical(elements[0]) == "sys.executable"
        # A python run with no script or module operand (only options such as
        # --version) reads nothing relative to the cwd.
        python_without_operand = python and all(
            (_str_constant(item) or "").startswith("-")
            for item in elements[1:])
        cwd_ok = "cwd" in keywords and rig_path(keywords["cwd"], tmp)
        if elements and not cwd_ok:
            # `git -C <rig>` runs in the rig just like cwd=.
            for flag, value in zip(elements, elements[1:]):
                if (_str_constant(flag) == "-C"
                        and rig_path(value, tmp)):
                    cwd_ok = True
        if not (cwd_ok or python_without_operand):
            reasons.append(f"{node.lineno}:program without a rig cwd "
                           f"{dotted}()")

    for scope in scopes:
        local = final_locals[id(scope)]
        for node in nodes_by_scope[id(scope)]:
            if isinstance(node, ast.Subscript) and canonical(
                    node.value) == "os.environ" and isinstance(
                    node.ctx, ast.Load):
                reasons.append(f"{node.lineno}:reads os.environ[]")
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            canon = canonical(func)
            dotted = _dotted(func)
            if isinstance(func, ast.Name) and func.id in _ALWAYS_UNPROVEN_BUILTINS:
                reasons.append(f"{node.lineno}:runs code or reads input "
                               f"{func.id}()")
                continue
            if isinstance(func, ast.Name) and func.id == "getattr":
                attributes = (literal_strings(node.args[1], scope)
                              if len(node.args) > 1 else None)
                if attributes is None:
                    reasons.append(f"{node.lineno}:getattr by a computed name")
                    continue
                named = [attribute for attribute in attributes
                         if attribute in _PATH_METHODS
                         and attribute not in _PURE_STRING_METHODS]
                if named:
                    reasons.append(f"{node.lineno}:getattr of a path method "
                                   f"{named[0]!r}")
                    continue
                if isinstance(root_name(node.args[0]), ast.Name) and root_name(
                        node.args[0]).id in io_names:
                    reasons.append(f"{node.lineno}:dynamic attribute of an I/O "
                                   f"module getattr({dotted}, ...)")
                    continue
            if (isinstance(func, ast.Name) and func.id == "vars"
                    and func.id not in bound_anywhere and node.args
                    and isinstance(root_name(node.args[0]), ast.Name)
                    and root_name(node.args[0]).id in module_bound):
                # vars(Path)['read_text'] reaches a method no rule sees,
                # like .__dict__.
                reasons.append(f"{node.lineno}:namespace of an imported "
                               f"name vars({_dotted(node.args[0])})")
                continue
            if canon in _ATTRIBUTE_GETTERS:
                names = [_str_constant(argument) for argument in node.args]
                if not names or any(
                        name is None or any(
                            part in _PATH_METHODS
                            and part not in _PURE_STRING_METHODS
                            for part in name.split("."))
                        for name in names):
                    reasons.append(f"{node.lineno}:attribute getter that may "
                                   f"reach a path method {dotted}()")
                continue
            if canon in _RUNS_CODE:
                reasons.append(f"{node.lineno}:loads or runs code {dotted}()")
                continue
            if canon in _IMPORT_BY_NAME:
                name = _str_constant(node.args[0]) if node.args else None
                top = (name or "").split(".")[0]
                if name is None or not (top in _COMPUTE_MODULES
                                        or top in _IO_MODULES
                                        or _resolve_module(name, roots)):
                    reasons.append(f"{node.lineno}:imports an unresolved "
                                   f"module by name {dotted}()")
                continue
            site = injection_site(node)
            if site is not None:
                values, what, anchors_ok = site
                judge_injection(node, values, what, local_tmp(node),
                                anchors_ok)
                continue
            if isinstance(func, ast.Attribute) and func.attr == "chdir":
                if not (node.args and all(
                        injectable(argument, local_tmp(node))
                        for argument in node.args)):
                    reasons.append(f"{node.lineno}:changes directory to an "
                                   f"unproven location {dotted}()")
                continue
            if canon in _IN_MEMORY_STREAMS:
                continue  # StringIO(text) / BytesIO(data): no location
            if io_rooted(func) or (isinstance(func, ast.Name)
                                   and func.id in io_names):
                if _is_pure(canon):
                    continue
                if canon in _LOCATION_SOURCES or (canon or "").startswith(
                        "os.environ."):
                    if canon not in _ZERO_ARG_CONFINED:
                        reasons.append(f"{node.lineno}:location source "
                                       f"{dotted}()")
                    continue
                name = _call_name(node)
                if (canon or "").startswith(_SHELL_CALL_PREFIXES):
                    reasons.append(f"{node.lineno}:shell or exec launch "
                                   f"{dotted}()")
                    continue
                if (canon or "").startswith("subprocess."):
                    if name not in _SUBPROCESS_CALLS:
                        reasons.append(f"{node.lineno}:unproven subprocess "
                                       f"{dotted}()")
                        continue
                    judge_subprocess(node, dotted, local, local_tmp(node))
                    continue
                arguments = list(node.args) + [keyword.value
                                               for keyword in node.keywords]
                if not arguments:
                    if canon not in _ZERO_ARG_CONFINED:
                        reasons.append(f"{node.lineno}:unproven zero-argument "
                                       f"{dotted}()")
                    continue
                if not all(confined(argument, local) for argument in arguments):
                    reasons.append(f"{node.lineno}:unproven {dotted}()")
                continue
            # (3) a Path or file method needs a confined receiver.
            if (isinstance(func, ast.Attribute) and func.attr in _PATH_METHODS
                    and func.attr not in _PURE_STRING_METHODS):
                if not receiver_proven(func.value, local):
                    reasons.append(f"{node.lineno}:path method on an unproven "
                                   f"receiver .{func.attr}()")

    # (6) literals naming repository paths outside the plugin.
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        text = _literal_string(node) if isinstance(
            node, (ast.Constant, ast.BinOp, ast.JoinedStr, ast.Call)) else None
        if text is None:
            continue
        candidates = [text]
        if ":" in text and not text.startswith("/"):
            candidates.append(text.split(":", 1)[1])  # "<rev>:<path>"
        if any(_names_repo_path(candidate, repo_root, plugin_root)
               for candidate in candidates):
            reasons.append(f"{node.lineno}:names a repository path {text!r}")
    return tuple(dict.fromkeys(sorted(
        reasons, key=lambda reason: int(reason.split(":", 1)[0]))))


def _fixture_spans(tree: ast.Module) -> dict[str, tuple[int, int, bool]]:
    """Fixture functions in a conftest: name -> (first line, last line,
    autouse)."""
    spans: dict[str, tuple[int, int, bool]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if _call_name(ast.Call(func=target, args=[], keywords=[])) == "fixture":
                spans[node.name] = (node.lineno, node.end_lineno or node.lineno,
                                    _is_autouse(node))
    return spans


def _test_side_reasons(tree: ast.Module, rel: str, roots: list[Path],
                       plugin_root: Path, docstrings: set[int],
                       module_files: dict[str, str]) -> tuple[str, ...]:
    """Every unproven site in a test-side module: the confinement rules plus
    each anchored climb out of the plugin."""
    reasons = (*_confinement_reasons(tree, rel, roots, plugin_root,
                                     docstrings),
               *(f"{where.split(':', 1)[0]}:climb {where.split(':', 1)[1]}"
                 for where in _repo_reach(tree, rel, plugin_root,
                                          module_files, docstrings)))
    return tuple(dict.fromkeys(sorted(
        reasons, key=lambda reason: int(reason.split(":", 1)[0]))))


def _names_a_trio_stem(tree: ast.AST, docstrings: set[int]) -> bool:
    """A string, a literal concatenation or an f-string part that contains a
    bump-trio stem, in any case."""
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        text = (_str_constant(node) if isinstance(node, ast.Constant)
                else _literal_string(node)
                if isinstance(node, (ast.BinOp, ast.JoinedStr)) else None)
        if text and any(stem in text.lower() for stem in _TRIO_STEMS):
            return True
    return False


def _scan_file(path: Path, plugin_root: Path, roots: list[Path],
               is_tracked_dir) -> FileScan:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        # Nothing about an unparseable module can be proven: its imports,
        # literals and calls are all unknown.
        try:
            rel = path.resolve().relative_to(plugin_root.resolve()).as_posix()
        except ValueError:
            rel = path.name
        unparsed = ("0:unparseable module",)
        test_side = rel.startswith("tests/") or _is_test_file(rel)
        return FileScan((), frozenset(), frozenset(), frozenset(),
                        frozenset(), None, () if test_side else unparsed,
                        unparsed if test_side else (), (), (), scans=True,
                        trio_stem=True)
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
    scans = False
    scan_wrappers = _scan_wrapper_names(tree)

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
        if _is_scan_name(call) or call in scan_wrappers:
            scans = True
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
    # Bound names of imported plugin modules -> their one file, so another
    # module's __file__ can be placed. Ambiguous names are left out.
    module_files: dict[str, str] = {}
    for node in ast.walk(tree):
        pairs: list[tuple[str, str]] = []
        if isinstance(node, ast.Import):
            pairs = [(alias.asname or alias.name, alias.name)
                     for alias in node.names
                     if alias.asname or "." not in alias.name]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            pairs = [(alias.asname or alias.name, f"{node.module}.{alias.name}")
                     for alias in node.names]
        for bound, dotted in pairs:
            leaves = {found.resolve() for found in _resolve_module(dotted, roots)
                      if found.name != "__init__.py"
                      or found.parent.name == dotted.rsplit(".", 1)[-1]}
            leaves = {leaf for leaf in leaves
                      if leaf.stem == dotted.rsplit(".", 1)[-1]
                      or leaf.name == "__init__.py"}
            if len(leaves) == 1:
                try:
                    module_files[bound] = next(iter(leaves)).relative_to(
                        root).as_posix()
                except ValueError:
                    pass
    test_side = module_rel.startswith("tests/") or _is_test_file(module_rel)
    return FileScan(
        edges=tuple(dict.fromkeys(to_rel(edges))),
        import_targets=frozenset(to_rel(targets)),
        literals=frozenset(literals),
        names=frozenset(names),
        path_literals=frozenset(path_literals),
        scanner_roots=_scanner_roots(tree, module_rel, is_tracked_dir,
                                     module_files),
        repo_reach=(() if test_side else _repo_reach(
            tree, module_rel, root, module_files, docstrings)),
        unproven=(_test_side_reasons(tree, module_rel, roots, root,
                                     docstrings, module_files)
                  if test_side else ()),
        dynamic_imports=tuple(dynamic_imports),
        dynamic_fixtures=tuple(dynamic_fixtures),
        scans=scans,
        trio_stem=_names_a_trio_stem(tree, docstrings),
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
        self._fixtures: dict[str, dict[str, tuple[str, ...]]] | None = None
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

    def fixture_reasons(self) -> dict[str, dict[str, tuple[str, ...]]]:
        """Unproven sites inside each requestable (non-autouse) conftest
        fixture. Autouse fixtures are the isolation layer, pinned by name in
        a contract; a change to them selects everything."""
        if self._fixtures is None:
            self._fixtures = {}
            for conftest in _CONFTEST_NAMES:
                path = self.plugin_root / conftest
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except OSError:
                    continue  # no such conftest
                except (UnicodeDecodeError, SyntaxError, ValueError):
                    # Its fixtures cannot be named, so any request of any
                    # fixture is unproven.
                    self._fixtures[conftest] = {
                        "*": ("0:unparseable conftest",)}
                    continue
                reasons = _test_side_reasons(
                    tree, conftest, self.roots, self.plugin_root,
                    _docstring_nodes(tree), {})
                found: dict[str, tuple[str, ...]] = {}
                for name, (first, last, autouse) in _fixture_spans(tree).items():
                    if autouse:
                        continue
                    found[name] = tuple(
                        reason for reason in reasons
                        if first <= int(reason.split(":", 1)[0]) <= last)
                self._fixtures[conftest] = found
        return self._fixtures

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
            repo_reach: list[str] = []
            unproven: list[tuple[str, tuple[str, ...]]] = []
            trio_named = False
            scans_tree = False
            for rel in sorted(closure):
                scan = self.scan(rel)
                scans_tree = scans_tree or scan.scans
                targets |= scan.import_targets
                literals |= scan.literals
                path_literals |= scan.path_literals
                scan_roots |= scan.scanner_roots or set()
                repo_reach.extend(f"{rel}:{where}" for where in scan.repo_reach)
                if scan.unproven:
                    unproven.append((rel, scan.unproven))
                trio_named = trio_named or scan.trio_stem
                if rel.startswith("tests/"):
                    # Test-side code is what requests fixtures: by parameter
                    # name, attribute or a usefixtures string.
                    test_names |= scan.names | scan.literals
            for conftest, fixtures in self.fixture_reasons().items():
                for name, reasons in sorted(fixtures.items()):
                    if (name in test_names or name == "*") and reasons:
                        unproven.append((f"{conftest}::{name}", reasons))
            self._views[exclusion_path] = ExclusionView(
                closure=closure,
                import_targets=frozenset(targets),
                literals=frozenset(literals),
                test_names=frozenset(test_names),
                path_literals=frozenset(path_literals),
                scan_roots=frozenset(scan_roots),
                repo_reach=tuple(repo_reach),
                unproven=tuple(unproven),
                trio_named=trio_named,
                scans_tree=scans_tree,
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
    # Reviewed modules whose bytes still match their review, and the
    # extra_reads globs those reviews declare.
    reviewed: frozenset[str] = frozenset()
    review_reads: tuple[tuple[str, tuple[str, ...]], ...] = ()


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
    reviewed, review_reads = current_reviews(index.plugin_root, manifest)
    return ChangeFacts(
        views=views,
        value_inert=frozenset(value_inert),
        conftest_verdicts=verdicts,
        conftest_imported=frozenset(conftest_imported),
        reviewed=reviewed,
        review_reads=review_reads,
    )


def fixture_digest(source: str, name: str) -> str | None:
    """sha256 of a module-level function's source (decorators included)
    plus every same-file top-level function, class, assignment or import
    it reads, transitively. None when the function does not exist.

    So an edit to the fixture, or to a helper or constant it uses, voids a
    review of it, while an unrelated edit elsewhere in the file does not.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    top: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            top[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            for target in targets:
                for bound in ast.walk(target):
                    if isinstance(bound, ast.Name):
                        top[bound.id] = node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                top[(alias.asname or alias.name).split(".")[0]] = node
    target = top.get(name)
    if not isinstance(target, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    lines = source.splitlines(keepends=True)

    def segment(node: ast.AST) -> str:
        first = min([node.lineno] + [decorator.lineno for decorator in
                                     getattr(node, "decorator_list", [])])
        return "".join(lines[first - 1:node.end_lineno or node.lineno])

    seen: dict[int, ast.AST] = {}
    queue = [target]
    while queue:
        node = queue.pop()
        if id(node) in seen:
            continue
        seen[id(node)] = node
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and inner.id in top:
                queue.append(top[inner.id])
    parts = sorted(segment(node) for node in seen.values())
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def current_reviews(plugin_root: Path, manifest: Manifest
                    ) -> tuple[frozenset[str],
                               tuple[tuple[str, tuple[str, ...]], ...]]:
    """Reviews whose module still hashes to the reviewed sha256.

    Hashed at RUNTIME from the bytes the selector reads, so an edit to a
    reviewed module makes it unproven again without any contract running.
    """
    reviewed: set[str] = set()
    reads: list[tuple[str, tuple[str, ...]]] = []
    for module, sha, _reason, extra in manifest.reach_reviews:
        path_part, _, function = module.partition("::")
        try:
            raw = (Path(plugin_root) / path_part).read_bytes()
        except OSError:
            continue
        if function:
            try:
                digest = fixture_digest(raw.decode("utf-8"), function)
            except UnicodeDecodeError:
                continue
        else:
            digest = hashlib.sha256(raw).hexdigest()
        if digest == sha:
            reviewed.add(module)
            reads.append((module, extra))
    return frozenset(reviewed), tuple(reads)


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


def _review_applies(key: str, view: ExclusionView) -> bool:
    """A module review applies to an exclusion whose closure loads the
    module; a 'conftest::fixture' review applies to one that requests the
    fixture."""
    path, _, function = key.partition("::")
    if function:
        return path in _CONFTEST_NAMES and function in view.test_names
    return key in view.closure


def broad_reasons(view: ExclusionView, facts: ChangeFacts) -> tuple[str, ...]:
    """Why an exclusion is BROAD: each unreviewed unproven test-side module
    or requested fixture, and each production climb out of the plugin.
    Empty means CONFINED."""
    reasons: list[str] = []
    for module, sites in view.unproven:
        if module in facts.reviewed or module.split("::", 1)[0] in facts.reviewed:
            continue
        reasons.extend(f"{module}:{site}" for site in sites)
    reasons.extend(f"production climb {where}" for where in view.repo_reach)
    return tuple(reasons)


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
    # Changed paths outside the plugin (machinery and pins already selected
    # everything above). Only a BROAD exclusion can see them.
    outside = [path for path in changed if repo_to_plugin(path) is None]
    if outside and facts is None:
        return _select_all(discovered, status, "change-facts-unavailable")
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
            if plugin_changed or outside:
                # An exclusion with no view cannot be cleared for any change.
                return _select_all(discovered, status,
                                   f"no-closure-view:{exclusion.path}")
            continue
        broad = broad_reasons(view, facts)
        if broad:
            # Not proven confined: back on ANY change, except value-inert
            # keys (already dropped above) and the bump trio, unless its own
            # test-side code names a trio file or its closure scans a tree.
            trigger = next(
                (path for path in changed
                 if view.trio_named or view.scans_tree
                 or path not in BUMP_TRIO), None)
            if trigger is not None:
                added[exclusion.path] = AddBack(
                    path=exclusion.path, reason="unproven_reach",
                    matched_changed=trigger, matched_seam=None,
                    evidence=broad[0])
            continue
        extra = next(
            ((module, path) for module, globs in facts.review_reads
             if _review_applies(module, view) for path in changed
             for glob in globs if fnmatch.fnmatchcase(path, glob)), None)
        if extra is not None:
            added[exclusion.path] = AddBack(
                path=exclusion.path, reason="review_extra_read",
                matched_changed=extra[1], matched_seam=None,
                evidence=f"{extra[0]} declares this read")
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