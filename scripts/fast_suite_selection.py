"""Pure core for the fast PR suite selection.

WHAT THIS OWNS
  Everything that decides WHICH test files the advisory fast lane runs. The
  decision is a whole-file selection over an explicit exclusion manifest: a
  new test file is included by default, and an excluded file comes back when
  its own test module or one of its production seams changed.

WHAT THIS MUST NEVER DO
  Narrow silently. Every unanswerable question -- a git failure, an invalid
  base, a changed production file nobody mapped -- resolves to SELECT_ALL.
  The fast lane may run slow; it may never run narrow.

  No module-level I/O. Only discover(), load_manifest() and changed_paths()
  touch the filesystem or git. select(), to_pytest_args(), build_argv() and
  parse_pytest_summary() are pure.

PATH DOMAINS (the single easiest thing to get wrong)
  Manifest paths, discovered corpus paths, and Selection members are
  PLUGIN-RELATIVE (relative to pact-plugin/).
  selection_machinery, constraints-ci.txt and pyproject.toml are
  REPO-RELATIVE, because git reports repo-relative paths.
  repo_to_plugin() is the only conversion, and it is table-tested.
"""

from __future__ import annotations

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
    "selection_machinery", "guard_files", "exclusions",
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


@dataclass(frozen=True)
class AddBack:
    path: str
    reason: str
    matched_changed: str
    matched_seam: str | None


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

        seams_raw = entry.get("production_seams")
        seams: list[str] = []
        if not isinstance(seams_raw, list) or not seams_raw:
            errors.append(f"{field}.production_seams: expected a non-empty list")
        else:
            for seam_index, seam in enumerate(seams_raw):
                seam_field = f"{field}.production_seams[{seam_index}]"
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
            ["git", "diff", "--name-only", "--no-renames",
             f"{stripped}...HEAD"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return [], "git-error"
    if completed.returncode != 0:
        return [], "git-error"
    paths = [line for line in completed.stdout.splitlines() if line.strip()]
    if not paths:
        return [], "empty-diff"
    return paths, "ok"


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


def select(discovered: frozenset[str], manifest: Manifest,
           changed: list[str], status: str) -> Selection:
    """Apply the ten precedence rules; the first match decides."""
    if status != "ok":
        return _select_all(discovered, status, f"changed-paths:{status}")

    machinery = set(manifest.selection_machinery)
    for changed_path in changed:
        if changed_path in machinery:
            return _select_all(discovered, status, "selection-machinery")
    for changed_path in changed:
        if changed_path in ("constraints-ci.txt", "pyproject.toml"):
            return _select_all(discovered, status, "selection-machinery")

    plugin_changed = [
        rel for rel in (repo_to_plugin(path) for path in changed)
        if rel is not None and rel != ""
    ]
    changed_set = set(plugin_changed)
    # AddBack.matched_changed is REPO-relative -- exactly the path
    # changed_paths returned -- while matched_seam is plugin-relative. The
    # comparison domains stay plugin-relative; only the recorded cause is in
    # the spelling a reader sees in the diff.
    repo_spelling: dict[str, str] = {}
    for repo_path in changed:
        plugin_path = repo_to_plugin(repo_path)
        if plugin_path:
            repo_spelling.setdefault(plugin_path, repo_path)

    for rel in plugin_changed:
        if Path(rel).name == "conftest.py":
            return _select_all(discovered, status, "shared-conftest")
    for rel in plugin_changed:
        if rel.startswith("tests/fixtures/"):
            return _select_all(discovered, status, "shared-fixture")
    for rel in plugin_changed:
        if rel.startswith("tests/") and not _is_test_file(rel):
            return _select_all(discovered, status, "test-support-file")
    for rel in plugin_changed:
        if "/" not in rel:
            return _select_all(discovered, status, "plugin-root-file")

    added_back: list[AddBack] = []
    for exclusion in manifest.exclusions:
        if exclusion.path in changed_set:
            added_back.append(AddBack(
                path=exclusion.path,
                reason="changed_test",
                matched_changed=repo_spelling.get(exclusion.path,
                                                  exclusion.path),
                matched_seam=None,
            ))
            continue
        matched_seam = next(
            (seam for seam in exclusion.production_seams if seam in changed_set),
            None,
        )
        if matched_seam is not None:
            added_back.append(AddBack(
                path=exclusion.path,
                reason="changed_seam",
                matched_changed=repo_spelling.get(matched_seam, matched_seam),
                matched_seam=matched_seam,
            ))

    known_seams = {
        seam for exclusion in manifest.exclusions
        for seam in exclusion.production_seams
    }
    # A DENYLIST OVER THE WHOLE PLUGIN TREE, not an allowlist of "production"
    # roots. Any changed path under the plugin that is neither a test module
    # nor a mapped seam can affect what the fast lane runs, so it selects
    # everything. The allowlist this replaced named hooks, scripts, skills,
    # telegram and bin, and silently omitted agents, commands, protocols,
    # templates, reference and the plugin manifest: a change to the repo's own
    # instruction surfaces applied all 24 exclusions with no add-back, which
    # is the narrow-the-lane outcome the design forbids. An allowlist drifts
    # on the next directory; this cannot.
    for rel in plugin_changed:
        if _is_test_file(rel) or rel in known_seams:
            continue
        return _select_all(discovered, status, f"unmapped-production:{rel}")

    added_back_paths = {entry.path for entry in added_back}
    applied = {
        exclusion.path for exclusion in manifest.exclusions
        if exclusion.path not in added_back_paths
    }
    included = frozenset(
        path for path in discovered
        if path not in {exclusion.path for exclusion in manifest.exclusions}
        or path in added_back_paths
    )
    return Selection(
        status=status,
        select_all_reason=None,
        discovered=discovered,
        included=included,
        excluded=frozenset(applied),
        added_back=tuple(sorted(added_back, key=lambda entry: entry.path)),
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

    The decoration is not cosmetic to a parser: the SAME message is bare
    under `-q` and `=`-decorated without it, so a pattern pinned to one form
    matches nothing under the other and reads identically to a run that never
    produced a summary.
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