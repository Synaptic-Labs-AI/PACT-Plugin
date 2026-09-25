"""Session tripwire: report any real CLAUDE.md this test run changed.

Location: pact-plugin/tests/claude_md_guard.py

The in-process half of this protection is tests/conftest.py's
`_refuse_claude_md_writes_outside_tmp`, which REFUSES a write it can see. This
is the other half and it is weaker on purpose: `monkeypatch` does not cross a
process boundary, so a child's write cannot be refused, only OBSERVED. This
module samples before the session and compares after it, and reports what
changed. It prevents nothing.

WHAT IT WATCHES, AND WHY IT DOES NOT ASK THE RESOLVERS. A writer resolves its
target in the environment it runs in -- a test body, a child that inherits
that body's environment, or a child given a literal one -- and none of those
is the environment this hook runs in: the suite deletes CLAUDE_PROJECT_DIR,
CLAUDE_CONFIG_DIR and the session id for each test and patches `Path.home()`
in-process only. A resolver asked here answers for this hook, not for a
writer. So `pytest_configure` reads this process's inputs ONCE and fixes the
watched set from them:

  * both project locations, `.claude/CLAUDE.md` and `CLAUDE.md`, under
    CLAUDE_PROJECT_DIR, the working directory, and the working directory's
    git worktree root and main-repository root;
  * `CLAUDE.md` under every config root a writer can reach: CLAUDE_CONFIG_DIR,
    `$HOME/.claude`, and the password database's home `.claude`, which is what
    a child started without HOME resolves.

`pytest_unconfigure` samples exactly those paths again. Nothing is resolved
twice, so the two samples cannot disagree about which files they describe,
and both locations are watched whether or not they exist, so a creation is
CREATED rather than a path that joins the set unseen.

EACH PATH IS KEYED AS WRITTEN, NOT AS RESOLVED. A PACT writer replaces a
CLAUDE.md by renaming a new file over it, so a CLAUDE.md that is a symlink --
a dotfiles-managed one, say -- becomes a regular file while the file it
pointed at is untouched. Keyed by the resolved target, that replacement read
as clean. So each sample records the path's own identity (`os.lstat`) beside
the identity and bytes of what it points at, and a change to either is
reported. The cost: two paths that are aliases of one file through a symlink
are two watched keys, not one. The coverage group in
test_claude_md_guard.py holds this set to the resolvers: it runs each CLAUDE.md
resolver a writer uses, under a test's environment and a child's, records
every directory it probes, and fails if one is not watched.

WHAT IT DOES NOT WATCH. A child that a test deliberately aims at a real path
these inputs do not name -- through the child's CLAUDE_PROJECT_DIR,
CLAUDE_CONFIG_DIR or HOME, or a `cwd=` outside this checkout -- writes outside
the watched set. The in-process half still refuses the same write made
in-process, because it refuses any target outside the tmp tree instead of
checking a list, so the two halves cover different populations. Also outside
it: a writer running outside every test (at collection or in a pytest hook)
in a session that exports a session id without CLAUDE_PROJECT_DIR, where the
pact-memory resolver's session-record rung can name another directory -- no
writer runs there today; a resolver rung that probes without the shared
helper, or a file name other than the two above; a relative
CLAUDE_CONFIG_DIR, which each writer resolves against its own working
directory; and a writer that replaces a DIFFERENT symlink to a watched file,
by a path none of these inputs names -- the watched path still points at the
untouched file.

WHEN IT RUNS, AND WHEN IT DOES NOT. The comparison runs whenever the pytest
process exits through Python. MEASURED on macOS with CPython 3.14.6 / pytest
9.1.1, 3.13.7 / pytest 8.3.0 and 3.9.6 / pytest 8.3.5, each mode alike on all
three: it runs on a clean pass, on failures, under -x, under a real SIGINT, on
a collection error where no test ran, under --collect-only, and with a
nonexistent path argument (exit 4). It does NOT run in these modes, which are
not equally serious:

  1. SIGTERM -- the default `kill`, a CI cancellation or timeout, `docker
     stop`. pytest installs no handler for it, so the process dies without
     comparing. MEASURED: exit 143, so CI still fails the job.
  2. A hard kill -- SIGKILL, a segfault, or os._exit from the pytest process.
     Tests were mid-execution, so a writer may already have fired. SIGKILL and
     a segfault never exit zero. os._exit(0) DOES: it is the silent case,
     exit 0 and no comparison. MEASURED: SIGKILL exit 137, a segfault exit
     139, os._exit(0) exit 0.
  3. A mistyped CLI flag. No test code runs, so there is nothing to miss.
  4. A --confcutdir that excludes pact-plugin/conftest.py, for example
     `--confcutdir=tests`. The guard is never registered and the run is green
     and inert. MEASURED. The summary line below is missing from such a run,
     and that absence is how to tell.

WHAT IT PRINTS. A clean run prints one line on stderr naming every watched
path and which of them exist, so a log shows what was watched rather than a
silence that reads the same as "not installed". Anything else prints a report
headed VIOLATION or REPORT (no violation).

A VIOLATION IS NOT IN THE SUMMARY LINE. On violation this writes the report
and raises, which exits non-zero (measured, rc=1). pytest's own summary still
reads "N passed", so a reader or a CI step keying on that line sees green on a
failed run. The report and the exit code are the signals. The unconfigure hook
is marked trylast, so no other plugin's unconfigure hook is skipped by the
raise unless that plugin also marks its hook trylast.

INSTRUMENT ERRORS. A path that sampled WITHOUT ERROR before the session -- an
absent file counts -- and cannot be sampled after it is a violation,
NOW_UNREADABLE: something replaced it with a directory, locked it, or removed
access to it during the run. A path that could not be sampled before the
session is reported and not raised: there is no baseline to compare. An input
this hook could not read at configure -- the working directory, git, the
password database, or the `shared` helpers -- is reported and not raised, and
the paths it would have named are not watched.

CI. CI exports no CLAUDE_* variable and its checkout carries no CLAUDE.md, which
is gitignored, so every watched path is ABSENT there and the guard can catch a
creation only. MEASURED in CI (ubuntu, CPython 3.9, 3.13 and 3.14): each cell's
summary line names 5 paths, 0 present -- both locations under the checkout and
under its pact-plugin/, and the runner's ~/.claude/CLAUDE.md. The exit modes
above are UNMEASURED in CI itself, on any interpreter.

Used by: pact-plugin/conftest.py, which re-exports `pytest_configure` and
`pytest_unconfigure` by name so pytest's hook discovery registers them
session-wide. The logic lives here rather than in that conftest because the
root conftest's NO-IMPORT CHARTER wants that file thin, and because keeping it
importable lets a nested `pytest -p claude_md_guard` run exercise it end to end
without any test-only disable seam.
"""

# THIS FILE MUST NEVER MUTATE THE MODULE SEARCH PATH, IN ANY FORM. It sits in
# tests/**/*.py, which is test_path_setup_pin.py's population, and that pin
# fails on a mutation at module level, inside a function, or embedded in a
# string constant. The hooks/ entry this module's `shared` imports need is
# added by pact-plugin/conftest.py, which the pin exempts as the sanctioned
# mechanism. Note for whoever edits the prose above: the pin's string arm
# matches the parenthesised call forms and the augmented-assignment spellings,
# so naming the attribute in prose is safe and quoting a call is not.

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


class ClaudeMdGuardViolation(AssertionError):
    """Raised from `pytest_unconfigure` when a watched CLAUDE.md changed."""


# Config-scoped rather than module globals: a nested in-process pytester run
# gets its own Config, where a module global would be shared with the outer
# session and the inner run's before-sample would overwrite it.
_BEFORE: "pytest.StashKey[dict]" = pytest.StashKey()
_INPUTS: "pytest.StashKey[dict]" = pytest.StashKey()

# Verdicts that fail the session. NOW_UNREADABLE is here and INSTRUMENT_ERROR
# is not, and the before-sample draws the line: a path that sampled without
# error before the session and cannot be sampled after it lost something
# during the run, while a path that already failed before has no baseline to
# compare. An input this hook could not read at configure is not a verdict; it
# is reported, and the paths it would have named are not watched.
_VIOLATIONS = frozenset(
    {"CREATED", "DELETED", "MODIFIED", "REWRITTEN", "NOW_UNREADABLE"}
)

_OK_VERDICTS = frozenset({"OK_ABSENT", "OK_UNCHANGED"})

_PROJECT_SHAPES = (Path(".claude") / "CLAUDE.md", Path("CLAUDE.md"))

_SUMMARY_PREFIX = "[PACT CLAUDE.md guard] clean:"


def _passwd_home():
    """The password database's home for this user: where a child started
    without HOME resolves `~`. A function so a test can replace it."""
    import pwd

    return pwd.getpwuid(os.getuid()).pw_dir


def _git_path(cwd, flag, env):
    """`git -C <cwd> rev-parse <flag>` as a path, or None when the cwd is not
    a repository. Raises when git itself cannot run; the caller records it."""
    result = subprocess.run(
        ["git", "-C", cwd, "rev-parse", flag],
        env=env,
        timeout=5,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip())


def _pin_inputs():
    """Read this process's inputs ONCE. Returns `(inputs, errors)`.

    NEVER RAISES. An input it cannot read becomes a `(label, message)` entry
    in `errors`, and the candidates that input would have named are absent.
    """
    errors = []

    def unavailable(label, exc):
        errors.append((label, f"{type(exc).__name__}: {exc}"))

    inputs = {
        "project_dir": os.environ.get("CLAUDE_PROJECT_DIR") or None,
        "cwd": None,
        "git_toplevel": None,
        "git_common_parent": None,
        "config_roots": [],
    }

    try:
        inputs["cwd"] = os.getcwd()
    except OSError as exc:
        unavailable("working directory", exc)

    if inputs["cwd"] is not None:
        try:
            from shared.project_scope import git_env_without_location

            env = git_env_without_location()
            inputs["git_toplevel"] = _git_path(inputs["cwd"], "--show-toplevel", env)
            common = _git_path(inputs["cwd"], "--git-common-dir", env)
            if common is not None:
                if not common.is_absolute():
                    common = Path(inputs["cwd"]) / common
                # realpath, as the resolvers use for the same rung.
                inputs["git_common_parent"] = Path(os.path.realpath(common)).parent
        except Exception as exc:  # noqa: BLE001 — report, never raise from a hook
            unavailable("git", exc)

    homes = []
    if os.environ.get("HOME"):
        homes.append(os.environ["HOME"])
    try:
        homes.append(_passwd_home())
    except Exception as exc:  # noqa: BLE001 — no `pwd` module, or no entry
        unavailable("password database home", exc)

    try:
        from shared.paths import get_claude_config_dir

        declared = os.environ.get("CLAUDE_CONFIG_DIR")
        roots = []
        for home in homes:
            roots.append(get_claude_config_dir(env={}, home=Path(home)))
            if declared:
                roots.append(
                    get_claude_config_dir(
                        env={"CLAUDE_CONFIG_DIR": declared}, home=Path(home)
                    )
                )
        if not homes and declared and Path(declared).is_absolute():
            roots.append(Path(declared))
        inputs["config_roots"] = roots
    except Exception as exc:  # noqa: BLE001 — report, never raise from a hook
        unavailable("shared.paths", exc)

    return inputs, errors


def _candidates(inputs):
    """PURE. Every CLAUDE.md path a writer can reach from these inputs, whether
    or not it exists. See the module docstring for the list and for what it
    leaves out."""
    project_dirs = [
        inputs.get(name)
        for name in ("project_dir", "cwd", "git_toplevel", "git_common_parent")
    ]
    paths = [
        Path(directory) / shape
        for directory in project_dirs
        if directory is not None
        for shape in _PROJECT_SHAPES
    ]
    paths.extend(Path(root) / "CLAUDE.md" for root in inputs.get("config_roots", ()))
    return paths


def _empty_sample(path_text):
    """The absent-file sample. Every field explicit; no None sentinel stands
    in for absence, because a bare hash collapses (absent, absent) with
    (present, unchanged) and cannot represent creation at all."""
    return {
        "path": path_text,
        "exists": False,
        "digest": None,
        "size": None,
        "st_dev": None,
        "st_ino": None,
        "mtime_ns": None,
        "leaf_dev": None,
        "leaf_ino": None,
        "error": None,
    }


def _sample_one(path):
    """Sample one path. NEVER RAISES -- a failure lands in `error` instead.

    The `path` field is the ABSOLUTE path as written, not resolved, and it is
    produced here rather than by the caller so that both phases stringify the
    same path the same way. `Path.absolute()`, never `os.path.abspath`: the
    latter collapses `link/..` lexically, where the kernel follows the link
    first, so it could name a different file. `leaf_dev`/`leaf_ino` are the
    path's own identity (`os.lstat`, which does not follow a symlink); the
    other fields describe what it points at, re-followed at every sample.
    """
    key = str(Path(path).absolute())
    sample = _empty_sample(key)
    try:
        leaf = os.lstat(key)
        sample["leaf_dev"], sample["leaf_ino"] = leaf.st_dev, leaf.st_ino
    except OSError:
        pass
    try:
        resolved = Path(key).resolve()
    except (OSError, RuntimeError) as exc:
        # `Path.resolve()` disagrees with itself across versions on a symlink
        # loop, so both types are caught and the guard reports rather than
        # deciding a containment question it could not answer.
        sample["error"] = f"resolve failed: {type(exc).__name__}: {exc}"
        return sample
    try:
        stat = resolved.stat()
    except FileNotFoundError:
        return sample
    except OSError as exc:
        sample["error"] = f"stat failed: {type(exc).__name__}: {exc}"
        return sample

    sample["exists"] = True
    sample["size"] = stat.st_size
    sample["st_dev"] = stat.st_dev
    sample["st_ino"] = stat.st_ino
    sample["mtime_ns"] = stat.st_mtime_ns
    try:
        sample["digest"] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        sample["error"] = f"read failed: {type(exc).__name__}: {exc}"
    return sample


def _take_before(config):
    """Fix the watched set from this process's inputs and sample it."""
    inputs, errors = _pin_inputs()
    config.stash[_INPUTS] = {"inputs": inputs, "errors": errors}
    before = {}
    for path in _candidates(inputs):
        sample = _sample_one(path)
        # Exact lexical key only, never realpath: with CLAUDE_CONFIG_DIR at
        # ~/dotfiles/.claude and $HOME/.claude linked to it, both paths stay
        # watched, because a writer can replace either one's leaf.
        before.setdefault(sample["path"], sample)
    config.stash[_BEFORE] = before


def _take_after(before):
    """Sample EXACTLY the keys configure fixed, and re-derive nothing. Keyed by
    the before key even when the sample's own `path` differs, because a
    symlink appeared at it during the run."""
    return {key: _sample_one(key) for key in before}


def _verdict_for(path_text, before, after):
    """The verdict for one key. Pure; both samples are present by construction.

    The verdict reads `error` first -- an error before the session is
    INSTRUMENT_ERROR, an error after a clean one is NOW_UNREADABLE -- and then
    compares exactly `exists`, `digest`, `st_dev`, `st_ino`, `leaf_dev` and
    `leaf_ino`.
    `test_the_verdict_reads_exactly_the_fields_its_docstring_names` flips each
    sampled field in turn and fails if a field outside that list moves the
    verdict or one inside it does not. `mtime_ns` and `size` are SAMPLED and
    deliberately NOT compared: `mtime_ns` moves without content or identity
    moving (a touch, a metadata sync) and adds no detection power beyond
    `digest` and `st_ino`, which already catch both write routes, so including
    it would buy false positives only; size cannot change without digest
    changing.
    """
    detail = None
    if before["error"]:
        verdict, detail = "INSTRUMENT_ERROR", before["error"]
    elif after["error"]:
        verdict = "NOW_UNREADABLE"
        detail = f"sampled cleanly before the session; after it: {after['error']}"
    elif not before["exists"] and not after["exists"]:
        # Neither sample reaches a file, but the path itself can still have
        # changed: a dangling symlink appearing, vanishing or being replaced.
        leaf_before = (before["leaf_dev"], before["leaf_ino"])
        leaf_after = (after["leaf_dev"], after["leaf_ino"])
        if leaf_before == leaf_after:
            verdict = "OK_ABSENT"
        elif before["leaf_ino"] is None:
            verdict = "CREATED"
        elif after["leaf_ino"] is None:
            verdict = "DELETED"
        else:
            verdict = "REWRITTEN"
    elif not before["exists"]:
        verdict = "CREATED"
    elif not after["exists"]:
        verdict = "DELETED"
    elif before["digest"] != after["digest"]:
        verdict = "MODIFIED"
    elif _identity(before) != _identity(after):
        # Identical bytes, moved inode -- of the file, or of the path itself
        # when it was a symlink. `_atomic_write_text` renames a temp file into
        # place, so a write need not change the bytes -- but a child that
        # rewrote the operator's file still reached it, and the next write may
        # not be identical.
        verdict = "REWRITTEN"
    else:
        verdict = "OK_UNCHANGED"
    return {
        "path": path_text,
        "verdict": verdict,
        "before": before,
        "after": after,
        "detail": detail,
    }


def _identity(sample):
    """What a rename over the path moves: the file's and the path's own."""
    return (sample["st_dev"], sample["st_ino"], sample["leaf_dev"], sample["leaf_ino"])


def _compare(before, after):
    """PURE function of two path->sample maps with the same keys."""
    return [_verdict_for(key, before[key], after[key]) for key in sorted(before)]


def _short(digest):
    return "(absent)" if not digest else digest[:16] + "..."


def _describe(verdict):
    """The indented detail lines for one non-OK verdict."""
    before, after = verdict["before"], verdict["after"]
    name = verdict["verdict"]
    if name in ("INSTRUMENT_ERROR", "NOW_UNREADABLE"):
        return [f"      {verdict['detail']}"]
    if name == "CREATED" and not after["exists"]:
        return ["      absent before; a symlink to a missing file after"]
    if name == "CREATED":
        return [
            f"      absent before; present after, {after['size']} B, "
            f"sha256 {_short(after['digest'])}"
        ]
    if name == "DELETED" and not before["exists"]:
        return ["      a symlink to a missing file before; absent after"]
    if name == "DELETED":
        return [
            f"      present before, {before['size']} B, "
            f"sha256 {_short(before['digest'])}; absent after"
        ]
    if name == "REWRITTEN":
        return [
            f"      bytes identical (sha256 {_short(after['digest'])}), "
            "file identity changed",
            f"      st_dev/st_ino  {before['st_dev']}/{before['st_ino']} -> "
            f"{after['st_dev']}/{after['st_ino']}",
            f"      path itself    {before['leaf_dev']}/{before['leaf_ino']} -> "
            f"{after['leaf_dev']}/{after['leaf_ino']}",
        ]
    return [
        f"      sha256   {_short(before['digest'])} -> {_short(after['digest'])}",
        f"      size     {before['size']} -> {after['size']}",
        f"      st_ino   {before['st_ino']} -> {after['st_ino']}",
    ]


_VIOLATION_CAUSES = """\
A watched CLAUDE.md changed while this test session ran. Rule out the causes
outside the suite FIRST -- each produces exactly this report:
  * you or an editor changed the file during the run;
  * a Claude Code session in this project started, resumed or compacted, or
    ran a PACT command that writes it (a memory save, a pin, bootstrap):
    PACT rewrites the managed block of the project CLAUDE.md, often with
    identical bytes, which reports as REWRITTEN;
  * another Claude Code session wrote it.
If none of these happened during the run, a test or a process it spawned
wrote the file: find the writer before re-running, and do not merge.
pytest's summary line still says "passed" -- this report and the non-zero
exit code are the signals."""

_NOW_UNREADABLE_CAUSES = """\
A watched path that sampled cleanly before the run could not be sampled after
it. Rule out the causes outside the suite first: its permissions or a parent
directory's changed, it was replaced by a directory, or its volume went away.
If none of these happened during the run, a test or a process it spawned did
it: find it before re-running, and do not merge."""


def _format_report(verdicts, input_errors):
    """The stderr report, or "" when every verdict is OK and every input was
    read.

    Returns "" on a clean run; the caller prints the one-line summary instead.
    The header distinguishes a violation from a report-only outcome, because
    an instrument failure is explicitly not a violation and must not be
    announced as one.
    """
    notable = [v for v in verdicts if v["verdict"] not in _OK_VERDICTS]
    if not notable and not input_errors:
        return ""

    violated = [v for v in notable if v["verdict"] in _VIOLATIONS]

    # THE THREE CENSUS BUCKETS ARE COUNTED DISJOINTLY, AND DERIVING ANY ONE OF
    # THEM BY SUBTRACTION IS THE BUG THIS SHAPE REPLACES. `exists` and `error`
    # are NOT mutually exclusive: `_sample_one` sets `exists=True` from a
    # successful `stat()` and only then reads, so an `OSError` from the read
    # leaves both set. Counting `present` and `errors` over those overlapping
    # populations and subtracting for `absent` printed `-1 absent` on a single
    # watched path. Reachable deterministically, with no race: a DIRECTORY at
    # the watched path stats cleanly and raises `IsADirectoryError` on read.
    erred = ("INSTRUMENT_ERROR", "NOW_UNREADABLE")
    errors = sum(1 for v in verdicts if v["verdict"] in erred)
    present = sum(
        1 for v in verdicts if v["verdict"] not in erred and v["after"]["exists"]
    )
    absent = sum(
        1 for v in verdicts if v["verdict"] not in erred and not v["after"]["exists"]
    )

    header = "VIOLATION" if violated else "REPORT (no violation)"
    census = (
        f"watched {len(verdicts)} path(s): {present} present, {absent} absent, "
        f"{errors} instrument error(s)"
    )
    if input_errors:
        census += f"; {len(input_errors)} input(s) unavailable"
    lines = [f"=== PACT CLAUDE.md GUARD — {header} ===", census]
    for verdict in notable:
        lines.append(f"  {verdict['verdict']:<19}{verdict['path']}")
        lines.extend(_describe(verdict))
    for label, message in input_errors:
        lines.append(
            f"  INPUT UNAVAILABLE  {label}: {message} -- the paths it names are "
            "not watched"
        )
    if violated:
        lines.append(_VIOLATION_CAUSES)
    if any(v["verdict"] == "NOW_UNREADABLE" for v in verdicts):
        lines.append(_NOW_UNREADABLE_CAUSES)
    return "\n".join(lines)


def _format_summary(verdicts):
    """The one line a clean run prints: every watched path, and which exist."""
    named = ", ".join(
        f"{v['path']} (present)" if v["after"]["exists"] else v["path"]
        for v in sorted(verdicts, key=lambda v: v["path"])
    )
    present = sum(1 for v in verdicts if v["after"]["exists"])
    return (
        f"{_SUMMARY_PREFIX} watched {len(verdicts)} path(s), {present} present: "
        f"{named}"
    )


def pytest_configure(config):
    """Take the before-sample. First thing the root conftest registers."""
    _take_before(config)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """Re-sample, report, and raise on any violation.

    ORDER IS LOAD-BEARING. The report goes out FIRST so it survives whatever a
    future pytest does with the exception, and the raise is what makes the
    process exit non-zero -- `pytest_unconfigure` receives only `config`, runs
    after the exit status is computed, and cannot change it any other way.
    TRYLAST, so the raise skips no other plugin's unconfigure hook.
    """
    before = config.stash.get(_BEFORE, None)
    if before is None:
        return  # configure never ran; there is nothing to compare against

    verdicts = _compare(before, _take_after(before))
    input_errors = config.stash.get(_INPUTS, {}).get("errors", [])
    report = _format_report(verdicts, input_errors)
    print(report or _format_summary(verdicts), file=sys.stderr)

    violated = [v["path"] for v in verdicts if v["verdict"] in _VIOLATIONS]
    if violated:
        raise ClaudeMdGuardViolation(
            "a watched CLAUDE.md changed during this test session: "
            + ", ".join(violated)
            + " (the guard report on stderr lists the causes to rule out first)"
        )
