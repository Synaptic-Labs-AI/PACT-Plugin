"""Session tripwire: report any CLAUDE.md this test run changed.

Location: pact-plugin/tests/claude_md_guard.py

The in-process half of this protection is tests/conftest.py's
`_refuse_claude_md_writes_outside_tmp`, which REFUSES a write it can see. This
is the other half and it is weaker on purpose: `monkeypatch` does not cross a
process boundary, so a child's write cannot be refused, only OBSERVED. This
module samples before the session and compares after it, and reports what
changed. It prevents nothing.

WHAT IT WATCHES. The paths this run's resolvers name: the pact-memory
resolver's own output, and `resolve_project_claude_md_path` evaluated for the
pytest rootdir and for that resolver's base. Asked at run time, never rebuilt
-- a path built from rootdir watches the worktree root, which is gitignored and
holds no CLAUDE.md, so it would stay green forever.

WHEN IT RUNS, AND WHEN IT DOES NOT. The comparison happens WHENEVER THE PYTEST
PROCESS EXITS THROUGH PYTHON. Measured on CPython 3.14.6 / pytest 9.1.1, one
binary: it runs on a clean pass, on failures, under -x, under a real SIGINT, on
a collection error where no test ran, and under --collect-only. It does NOT
run in these modes, which are NOT equally serious:

  1. A HARD KILL -- SIGKILL, os._exit, a segfault. THE ONE REAL GAP: tests were
     mid-execution, so a writer may already have fired and nothing reports it.
     Closing it needs a check outside this process, and none ships here.
  2. A mistyped CLI flag, and a nonexistent path argument. No test code runs in
     either, so there is nothing to have missed. Benign.

AN INSTRUMENT ERROR COSTS THAT PATH ITS EXIT CODE, NOT ITS REPORT. If a
sample carries `error` -- a failed stat or a failed read -- that path is
judged INSTRUMENT_ERROR before any content comparison, so a real change to it
in the same run does NOT raise and the process exits 0. The report still
prints and names the failure, under a "REPORT (no violation)" header, so this
is not a silent miss: of the two signals named below, one fires and one does
not. MEASURED by constructing that pair of samples directly and comparing
them; no run has been observed failing a read on a real CLAUDE.md.

COVERAGE SHRINKS SILENTLY WHERE THE DISPLAY RESOLVER FINDS NOTHING. The
watched set is built from three evaluations, and the first is skipped entirely
when the pact-memory resolver returns no path. MEASURED in THIS tree, by
stubbing that resolver to its no-CLAUDE.md return: the set falls from 2 paths
to 1, the survivor absent with no error, and the liveness test passes exactly
as it does with 2, because it asserts a non-empty set and no errors and
deliberately not that any file exists. So coverage halves with nothing
reporting it. NOT INERT there -- the rootdir-derived path is still watched, so
a creation at it still raises. That CI is such an environment is INFERRED from
the repo's CLAUDE.md being gitignored, not measured: no run of this guard in
CI has been observed.

UNMEASURED, AND LEFT OPEN. `resolve_project_claude_md_path` is total: handed
any project_dir it names a CLAUDE.md, existing or not, and its non-test callers
are the hooks that CREATE that file. A child handed a project_dir this run never
resolves can create a CLAUDE.md outside the watched set. The consequence was not
measured. Do not read this guard as closing it.

A VIOLATION IS NOT IN THE SUMMARY LINE. On violation this writes a report to
stderr and raises, which exits non-zero (measured, rc=1). pytest's own summary
still reads "N passed", so a reader or a CI step keying on that line sees green
on a failed run. The report and the exit code are the signals.

CI's other two interpreters are UNMEASURED, not passing: they carried no pytest
when these modes were measured.

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
# string constant. The hooks/ entry this module's resolver import needs is
# added by pact-plugin/conftest.py, which the pin exempts as the sanctioned
# mechanism. Note for whoever edits the prose above: the pin's string arm
# matches the parenthesised call forms and the augmented-assignment spellings,
# so naming the attribute in prose is safe and quoting a call is not.

import hashlib
import sys
from pathlib import Path

import pytest


class ClaudeMdGuardViolation(AssertionError):
    """Raised from `pytest_unconfigure` when a watched CLAUDE.md changed."""


# Config-scoped rather than a module global: a nested in-process pytester run
# gets its own Config, where a module global would be shared with the outer
# session and the inner run's before-sample would overwrite it.
_BEFORE: "pytest.StashKey[dict]" = pytest.StashKey()

# Verdicts that make the session a failure. Instrument failure is NOT here, by
# ruling: a resolver that cannot import on an unmeasured CI interpreter would
# otherwise fail every run there. Liveness is asserted in a test instead, which
# fails in the environment that actually has the problem.
_VIOLATIONS = frozenset(
    {"CREATED", "DELETED", "MODIFIED", "REWRITTEN", "NEW_TARGET_PRESENT"}
)

_OK_VERDICTS = frozenset({"OK_ABSENT", "OK_UNCHANGED"})


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
        "error": None,
    }


def _sample_one(path):
    """Sample one path. NEVER RAISES -- a failure lands in `error` instead.

    The `path` field is the RESOLVED path as a string, and it is produced here
    rather than by the caller so that both phases stringify the same file the
    same way. An inconsistent key between the phases would not error; it would
    read as a new target appearing, or hide one that did.
    """
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError) as exc:
        # `Path.resolve()` disagrees with itself across versions on a symlink
        # loop, so both types are caught and the guard reports rather than
        # deciding a containment question it could not answer.
        sample = _empty_sample(str(path))
        sample["error"] = f"resolve failed: {type(exc).__name__}: {exc}"
        return sample

    sample = _empty_sample(str(resolved))
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


def _watched_paths(rootpath):
    """The CLAUDE.md paths this run's resolvers name, plus any resolver failure.

    Returns `(paths, failures)`, where `failures` is a list of
    `(label, message)`. Three evaluations, deduped downstream by resolved path:

      1. the pact-memory display resolver's own output -- the symbol on the
         write path, which both writers reach;
      2. `resolve_project_claude_md_path` for the pytest rootdir;
      3. the same, for the base the display resolver reports.

    BOTH IMPORTS HAPPEN HERE, INSIDE THE BODY, each in its own `except
    Exception` recording a failure rather than raising. That is the root
    conftest's NO-IMPORT CHARTER: a missing optional dependency at conftest
    scope becomes a total suite collection failure, and `working_memory` lives
    under a skill's scripts dir. It is also why a sample carries an `error`
    field at all -- that field is the charter surfacing in the data model.

    A resolver that finds nothing is SKIPPED AT THE SOURCE, not carried as a
    None. The display resolver returns `(None, None)` when no CLAUDE.md
    exists -- the LIKELY case in CI, where the repo's own copy is gitignored --
    and a None reaching the sample map would key an entry on a non-path. The
    watched set legitimately shrinks instead, and the union sampling in
    `_sample_union` is what makes a path appearing later report as a new
    target rather than joining the set silently.
    """
    paths = []
    failures = []
    base = None

    try:
        import working_memory

        found, found_base = working_memory._resolve_display_claude_md_with_base()
        if found is not None:
            paths.append(Path(found))
        if found_base is not None:
            base = Path(found_base)
    except Exception as exc:  # noqa: BLE001 — report, never raise from a hook
        failures.append(
            ("pact-memory display resolver", f"{type(exc).__name__}: {exc}")
        )

    try:
        from shared.claude_md_manager import resolve_project_claude_md_path

        paths.append(Path(resolve_project_claude_md_path(rootpath)[0]))
        if base is not None:
            paths.append(Path(resolve_project_claude_md_path(base)[0]))
    except Exception as exc:  # noqa: BLE001 — report, never raise from a hook
        failures.append(
            ("claude_md_manager project resolver", f"{type(exc).__name__}: {exc}")
        )

    return paths, failures


def _sample(rootpath):
    """Map resolved-path-string -> sample, for every path the resolvers name.

    A resolver failure becomes its own entry under a bracketed pseudo-key, so
    an instrument that could not run is visible in the same structure as a
    file that could not be read, rather than as a silently shorter map.
    """
    samples = {}
    paths, failures = _watched_paths(rootpath)
    for path in paths:
        sample = _sample_one(path)
        samples.setdefault(sample["path"], sample)
    for label, message in failures:
        key = f"<unresolved: {label}>"
        sample = _empty_sample(key)
        sample["error"] = message
        samples.setdefault(key, sample)
    return samples


def _sample_union(rootpath, before):
    """Sample the paths resolving NOW, plus every path sampled THEN.

    `resolve_project_claude_md_path` is existence-dependent, so a child that
    creates `.claude/CLAUDE.md` where only `./CLAUDE.md` existed makes the
    after-phase resolve a path the before-phase never saw. Carrying the before
    keys forward makes every key comparable and leaves the appearance of a new
    key as its own signal.
    """
    after = _sample(rootpath)
    for path_text in before:
        if path_text not in after:
            after[path_text] = _sample_one(path_text)
    return after


def _verdict_for(path_text, before, after):
    """The verdict for one key. Pure; total over every before/after pair.

    THE COMPARED FIELDS ARE EXACTLY `exists`, `digest`, `st_dev` and `st_ino`,
    and they are compared BELOW rather than declared in a constant, because a
    list of field names that nothing reads is a claim about this function that
    this function does not have to honour -- add a field to the comparison and
    the list is silently false. `mtime_ns` is SAMPLED and deliberately NOT
    compared: it moves without content or identity moving (a touch, a metadata
    sync) and adds no detection power beyond `digest` and `st_ino`, which
    already catch both write routes, so including it would buy false positives
    only. `test_mtime_alone_never_moves_the_verdict` is what holds that.
    """
    if after is None:
        return {
            "path": path_text,
            "verdict": "INSTRUMENT_ERROR",
            "before": before,
            "after": None,
            "detail": "no after-sample for a path sampled before the session",
        }
    if (before is not None and before.get("error")) or after.get("error"):
        detail = (before or {}).get("error") or after.get("error")
        return {
            "path": path_text,
            "verdict": "INSTRUMENT_ERROR",
            "before": before,
            "after": after,
            "detail": detail,
        }

    verdict = None
    if before is None:
        verdict = "NEW_TARGET_PRESENT" if after["exists"] else "NEW_TARGET_ABSENT"
    elif not before["exists"] and not after["exists"]:
        verdict = "OK_ABSENT"
    elif not before["exists"]:
        verdict = "CREATED"
    elif not after["exists"]:
        verdict = "DELETED"
    elif before["digest"] != after["digest"]:
        verdict = "MODIFIED"
    elif (before["st_dev"], before["st_ino"]) != (after["st_dev"], after["st_ino"]):
        # Identical bytes, moved inode. `_atomic_write_text` renames a temp
        # file into place, so a write need not change the bytes -- but a child
        # that rewrote the operator's file still reached it, and the next
        # write may not be identical.
        verdict = "REWRITTEN"
    else:
        verdict = "OK_UNCHANGED"

    return {
        "path": path_text,
        "verdict": verdict,
        "before": before,
        "after": after,
        "detail": None,
    }


def _compare(before, after):
    """PURE function of two path->sample maps. No filesystem access."""
    return [
        _verdict_for(key, before.get(key), after.get(key))
        for key in sorted(set(before) | set(after))
    ]


def _short(digest):
    return "(absent)" if not digest else digest[:16] + "..."


def _describe(verdict):
    """The indented detail lines for one non-OK verdict."""
    before, after = verdict["before"], verdict["after"]
    name = verdict["verdict"]
    if name == "INSTRUMENT_ERROR":
        return [f"      {verdict['detail']}"]
    if name in ("CREATED", "NEW_TARGET_PRESENT"):
        return [
            f"      absent before; present after, {after['size']} B, "
            f"sha256 {_short(after['digest'])}"
        ]
    if name == "DELETED":
        return [
            f"      present before, {before['size']} B, "
            f"sha256 {_short(before['digest'])}; absent after"
        ]
    if name == "NEW_TARGET_ABSENT":
        return ["      a resolver named this path only after the session; absent"]
    if name == "REWRITTEN":
        return [
            f"      bytes identical (sha256 {_short(after['digest'])}), "
            "file identity changed",
            f"      st_dev/st_ino  {before['st_dev']}/{before['st_ino']} -> "
            f"{after['st_dev']}/{after['st_ino']}",
        ]
    return [
        f"      sha256   {_short(before['digest'])} -> {_short(after['digest'])}",
        f"      size     {before['size']} -> {after['size']}",
        f"      st_ino   {before['st_ino']} -> {after['st_ino']}",
    ]


def _format_report(verdicts):
    """The stderr report, or "" when every verdict is OK.

    Silent on a clean run by design: liveness is a test's job, not a line
    printed on every session. The header distinguishes a violation from a
    report-only outcome, because an instrument failure is explicitly not a
    violation and must not be announced as one.
    """
    notable = [v for v in verdicts if v["verdict"] not in _OK_VERDICTS]
    if not notable:
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
    errors = sum(1 for v in verdicts if v["verdict"] == "INSTRUMENT_ERROR")
    present = sum(
        1
        for v in verdicts
        if v["verdict"] != "INSTRUMENT_ERROR"
        and v["after"] is not None
        and v["after"]["exists"]
    )
    absent = sum(
        1
        for v in verdicts
        if v["verdict"] != "INSTRUMENT_ERROR"
        and v["after"] is not None
        and not v["after"]["exists"]
    )

    header = "VIOLATION" if violated else "REPORT (no violation)"
    lines = [
        f"=== PACT CLAUDE.md GUARD — {header} ===",
        f"watched {len(verdicts)} path(s): {present} present, {absent} absent, "
        f"{errors} instrument error(s)",
    ]
    for verdict in notable:
        lines.append(f"  {verdict['verdict']:<19}{verdict['path']}")
        lines.extend(_describe(verdict))
    if violated:
        lines.append(
            'A test session reached a real CLAUDE.md. Treat as HALT: do not merge, and\n'
            'find the writer before re-running. pytest\'s own summary line still says\n'
            '"passed" — this report and the non-zero exit code are the only signals.'
        )
    return "\n".join(lines)


def pytest_configure(config):
    """Take the before-sample. First thing the root conftest registers."""
    config.stash[_BEFORE] = _sample(config.rootpath)


def pytest_unconfigure(config):
    """Re-sample, report, and raise on any violation.

    ORDER IS LOAD-BEARING. The report goes out FIRST so it survives whatever a
    future pytest does with the exception, and the raise is what makes the
    process exit non-zero -- `pytest_unconfigure` receives only `config`, runs
    after the exit status is computed, and cannot change it any other way.
    """
    before = config.stash.get(_BEFORE, None)
    if before is None:
        return  # configure never ran; there is nothing to compare against

    verdicts = _compare(before, _sample_union(config.rootpath, before))
    report = _format_report(verdicts)
    if report:
        print(report, file=sys.stderr)

    violated = [v["path"] for v in verdicts if v["verdict"] in _VIOLATIONS]
    if violated:
        raise ClaudeMdGuardViolation(
            "this test session changed a real CLAUDE.md: "
            + ", ".join(violated)
            + " (see the guard report on stderr)"
        )
