"""Tests for the CLAUDE.md session tripwire.

Location: pact-plugin/tests/test_claude_md_guard.py

Covers `tests/claude_md_guard.py` in four groups:

1. the comparator table -- one arm per verdict, against handmade sample dicts,
   because `_compare` is the only non-trivial logic in the guard and it is a
   pure function of two maps;
2. instrument liveness -- that the inputs read cleanly on this machine and name
   candidates, and that the modules the guard imports come from this tree;
3. hook collision and registration -- that no conftest rebinds a pytest hook
   name over itself, and that the root conftest really does register this
   guard's two hooks;
4. coverage certification -- that every directory a writer's resolver probes,
   and every config root a writer can reach, is in the watched set.

None of these writes a real CLAUDE.md. Group 2 reads the real tree and asserts
nothing about what it finds there; the end-to-end arms that watch a real write
run a nested pytest under tmp_path, in test_claude_md_guard_end_to_end.py.
"""

import ast
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

import claude_md_guard as guard

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Group 1 -- the comparator table
# --------------------------------------------------------------------------

def _present(digest="aa", dev=1, ino=10, size=5, leaf_dev=1, leaf_ino=10):
    return {
        "path": "/watched/CLAUDE.md",
        "exists": True,
        "digest": digest,
        "size": size,
        "st_dev": dev,
        "st_ino": ino,
        "mtime_ns": 123,
        "leaf_dev": leaf_dev,
        "leaf_ino": leaf_ino,
        "error": None,
    }


def _absent():
    return {
        "path": "/watched/CLAUDE.md",
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


def _erred(sample, message="stat failed: OSError: boom"):
    return dict(sample, error=message)


def _only(before, after):
    """The single verdict for one watched key."""
    verdicts = guard._compare(before, after)
    assert len(verdicts) == 1, verdicts
    return verdicts[0]["verdict"]


KEY = "/watched/CLAUDE.md"


def test_absent_then_absent_is_ok():
    assert _only({KEY: _absent()}, {KEY: _absent()}) == "OK_ABSENT"


def test_unchanged_is_ok():
    assert _only({KEY: _present()}, {KEY: _present()}) == "OK_UNCHANGED"


def test_appearing_is_created():
    assert _only({KEY: _absent()}, {KEY: _present()}) == "CREATED"


def test_disappearing_is_deleted():
    assert _only({KEY: _present()}, {KEY: _absent()}) == "DELETED"


def test_different_digest_is_modified():
    before = {KEY: _present(digest="aa")}
    after = {KEY: _present(digest="bb")}
    assert _only(before, after) == "MODIFIED"


def test_same_bytes_new_inode_is_rewritten():
    """The identical-bytes rewrite IS a violation, and reports as its own
    verdict rather than as MODIFIED. `_atomic_write_text` renames a temp file
    into place, so a write moves the inode and need not change the bytes -- a
    child that rewrote the operator's file with identical content still
    reached it, and the next write may not be identical."""
    before = {KEY: _present(digest="aa", ino=10)}
    after = {KEY: _present(digest="aa", ino=11)}
    assert _only(before, after) == "REWRITTEN"


def test_a_moved_device_alone_is_rewritten():
    before = {KEY: _present(dev=1)}
    after = {KEY: _present(dev=2)}
    assert _only(before, after) == "REWRITTEN"


def test_a_replaced_path_alone_is_rewritten():
    """The path's own identity moved while the file it names did not: a
    symlink replaced by a rename, whose target is untouched."""
    before = {KEY: _present(leaf_ino=20)}
    after = {KEY: _present(leaf_ino=21)}
    assert _only(before, after) == "REWRITTEN"


def test_a_writer_replacing_a_symlinked_claude_md_is_seen(tmp_path):
    """From real samples: the path is a symlink to a file elsewhere, and a
    rename puts a regular file with the SAME bytes in its place. The file it
    pointed at is untouched, so only the path's own identity moves. Keyed by
    the resolved target, this read as unchanged."""
    target = tmp_path / "dotfiles" / "CLAUDE.md"
    target.parent.mkdir()
    target.write_text("same bytes\n")
    path = tmp_path / "CLAUDE.md"
    path.symlink_to(target)
    before = guard._sample_one(path)
    replacement = tmp_path / "replacement"
    replacement.write_text("same bytes\n")
    os.replace(replacement, path)
    after = guard._sample_one(path)

    assert before["path"] == after["path"] == str(path)
    assert _only({before["path"]: before}, {after["path"]: after}) == "REWRITTEN"
    assert target.read_text() == "same bytes\n"


def test_a_dangling_symlink_created_at_an_absent_path_is_created(tmp_path):
    """Neither sample reaches a file, but the path itself changed: a symlink
    to a missing file appeared where nothing was. That is a creation."""
    path = tmp_path / "CLAUDE.md"
    before = guard._sample_one(path)
    path.symlink_to(tmp_path / "missing.md")
    after = guard._sample_one(path)

    assert not before["exists"] and not after["exists"]
    verdict = _only({before["path"]: before}, {after["path"]: after})
    assert verdict == "CREATED" and verdict in guard._VIOLATIONS


def test_a_key_through_a_symlink_and_dotdot_names_the_file_the_kernel_reaches(
    tmp_path,
):
    """`link/..` goes to the link TARGET's parent, not the link's own parent.
    The key keeps the path as written, and the sample describes the file the
    kernel reaches; collapsing `..` lexically would watch a different file."""
    (tmp_path / "deep" / "dir").mkdir(parents=True)
    (tmp_path / "deep" / "CLAUDE.md").write_text("deep\n")
    (tmp_path / "CLAUDE.md").write_text("top\n")
    (tmp_path / "link").symlink_to(tmp_path / "deep" / "dir")
    path = tmp_path / "link" / ".." / "CLAUDE.md"

    sample = guard._sample_one(path)

    assert sample["path"] == str(path)
    assert sample["digest"] == guard._sample_one(tmp_path / "deep" / "CLAUDE.md")["digest"]
    assert sample["digest"] != guard._sample_one(tmp_path / "CLAUDE.md")["digest"]


def test_mtime_alone_never_moves_the_verdict():
    """`mtime_ns` is SAMPLED and NOT COMPARED. It moves without content or
    identity moving, and adds no detection power beyond digest and st_ino --
    so including it in the verdict would buy false positives only. This arm
    fails if someone adds it to the comparison."""
    before = {KEY: _present()}
    after = {KEY: dict(_present(), mtime_ns=999_999)}
    assert _only(before, after) == "OK_UNCHANGED"


def test_an_error_before_the_session_is_report_only():
    """No baseline, nothing to compare: reported, never raised, whatever the
    after-sample says."""
    for after in (_present(), _absent(), _erred(_present())):
        verdict = _only({KEY: _erred(_absent())}, {KEY: after})
        assert verdict == "INSTRUMENT_ERROR"
        assert verdict not in guard._VIOLATIONS


@pytest.mark.parametrize("before", [_present(), _absent()], ids=["present", "absent"])
def test_an_error_after_a_clean_sample_fails_closed(before):
    """A path that sampled cleanly before the session -- an absent file counts
    -- and cannot be sampled after it lost something during the run."""
    verdict = _only({KEY: before}, {KEY: _erred(before)})
    assert verdict == "NOW_UNREADABLE"
    assert verdict in guard._VIOLATIONS


def test_an_error_after_a_clean_sample_outranks_a_content_change():
    """A sample that could not be read cannot also be trusted to say how the
    file changed, so the error decides the verdict -- and it still fails."""
    before = {KEY: _present(digest="aa")}
    after = {KEY: _erred(_present(digest="bb"), "read failed: OSError: boom")}
    assert _only(before, after) == "NOW_UNREADABLE"


def test_a_directory_created_at_a_watched_absent_path_fails_closed(tmp_path):
    """The destructive case, from real samples: the path is absent before, and
    a directory stands there after. `stat()` succeeds and the read raises."""
    target = tmp_path / "CLAUDE.md"
    before = guard._sample_one(target)
    target.mkdir()
    after = guard._sample_one(target)
    key = before["path"]
    assert _only({key: before}, {key: after}) == "NOW_UNREADABLE"


def test_the_verdict_reads_exactly_the_fields_its_docstring_names():
    """Flip each sampled field of an unchanged present sample, one at a time,
    and collect the fields whose flip moves the verdict. That set is what
    `_verdict_for`'s docstring says the verdict reads."""
    base = _present()
    moved = set()
    for field in guard._empty_sample(KEY):
        value = base[field]
        if value is None:
            flipped = "flipped"
        elif isinstance(value, bool):
            flipped = not value
        elif isinstance(value, int):
            flipped = value + 1
        else:
            flipped = f"{value}-flipped"
        after = dict(base, **{field: flipped})
        if _only({KEY: base}, {KEY: after}) != "OK_UNCHANGED":
            moved.add(field)
    assert moved == {"error", "exists", "digest", "st_dev", "st_ino", "leaf_dev", "leaf_ino"}


def test_the_violation_set_is_exactly_the_five_raising_verdicts():
    """Pins WHICH verdicts end the session non-zero. NOW_UNREADABLE raises
    because the path sampled cleanly before; INSTRUMENT_ERROR does not, because
    a path that failed before the session has no baseline to compare."""
    assert guard._VIOLATIONS == frozenset(
        {"CREATED", "DELETED", "MODIFIED", "REWRITTEN", "NOW_UNREADABLE"}
    )
    assert "INSTRUMENT_ERROR" not in guard._VIOLATIONS


def test_a_sample_that_is_both_present_and_erred_keeps_the_census_honest():
    """THE CENSUS BUCKETS MUST BE COUNTED DISJOINTLY, NOT DERIVED BY SUBTRACTION.

    `exists` and `error` are not mutually exclusive: `_sample_one` sets
    `exists=True` from a successful `stat()` and only then reads, so a read
    failure leaves BOTH set. Counting `present` and `errors` over those
    overlapping populations and subtracting for `absent` printed a NEGATIVE
    count -- `1 present, -1 absent, 1 instrument error(s)` on one watched path.

    Fed the OVERLAP deliberately, because that is the only input that separates
    the two arithmetics; a sample that is present-or-erred but not both passes
    under either. The sibling below pins that this shape is REACHABLE at all.
    """
    both = dict(_present(), digest=None, error="read failed: IsADirectoryError")
    report = guard._format_report(guard._compare({KEY: both}, {KEY: both}), [])
    census = report.splitlines()[1]
    assert "-" not in census, f"negative count in the census line: {census}"
    assert census.endswith("0 present, 0 absent, 1 instrument error(s)")


def test_the_present_and_erred_overlap_is_reachable_from_a_real_file(tmp_path):
    """Pins that the arm above tests a shape the sampler can actually produce.

    A DIRECTORY at a watched path is the deterministic route: `stat()` succeeds,
    so `exists` is set, and `read_bytes()` then raises `IsADirectoryError`,
    which is an `OSError`. No race is needed. Without this arm the sibling
    above could be pinning an input that only a test can construct.
    """
    target = tmp_path / "CLAUDE.md"
    target.mkdir()
    sample = guard._sample_one(target)
    assert sample["exists"] is True
    assert sample["error"] and "IsADirectoryError" in sample["error"]


def test_the_report_is_silent_on_a_clean_comparison():
    """A clean comparison produces no REPORT. The one-line summary is a
    separate function, pinned below."""
    clean = guard._compare({KEY: _present()}, {KEY: _present()})
    assert guard._format_report(clean, []) == ""


def test_the_report_names_the_verdict_the_path_and_the_summary_line_caveat():
    verdicts = guard._compare({KEY: _absent()}, {KEY: _present()})
    report = guard._format_report(verdicts, [])
    assert "VIOLATION" in report
    assert KEY in report
    assert "CREATED" in report
    # The caveat is the whole point of the report existing: a violating run
    # still prints "N passed", so the report and the exit code are the only
    # signals a reader gets.
    assert "passed" in report


def test_the_violation_report_names_the_causes_outside_the_suite_first():
    """An edit, a Claude Code session in this project, and another session
    are each named BEFORE the report sends the reader after a test."""
    report = guard._format_report(guard._compare({KEY: _present()}, {KEY: _absent()}), [])
    writer = report.index("find the writer")
    for marker in ("editor", "session in this project", "another Claude Code session"):
        assert marker in report, marker
        assert report.index(marker) < writer, marker


def test_a_now_unreadable_report_names_its_causes():
    report = guard._format_report(
        guard._compare({KEY: _absent()}, {KEY: _erred(_absent())}), []
    )
    assert "NOW_UNREADABLE" in report and "VIOLATION" in report
    assert "sampled cleanly before the session" in report
    for marker in ("permissions", "replaced by a directory", "volume went away"):
        assert marker in report, marker


def test_an_instrument_error_is_not_reported_as_a_violation():
    """The header must not cry violation for a failure of the instrument --
    the same distinction the raising set makes, at the place a human reads."""
    erred = _erred(_absent())
    report = guard._format_report(guard._compare({KEY: erred}, {KEY: erred}), [])
    assert report, "an instrument error must still be reported"
    assert "no violation" in report
    assert "stat failed" in report


def _fake_config(before, input_errors=()):
    config = types.SimpleNamespace(stash=pytest.Stash())
    config.stash[guard._BEFORE] = before
    config.stash[guard._INPUTS] = {"inputs": {}, "errors": list(input_errors)}
    return config


def test_an_unreadable_input_is_reported_and_not_raised(tmp_path, capsys):
    """An input the guard could not read is named in a REPORT, never raised."""
    ok = guard._compare({KEY: _absent()}, {KEY: _absent()})
    errors = [("git", "FileNotFoundError: [Errno 2] No such file: 'git'")]
    report = guard._format_report(ok, errors)
    assert "REPORT (no violation)" in report
    assert "INPUT UNAVAILABLE  git:" in report
    assert "1 input(s) unavailable" in report

    absent = guard._sample_one(tmp_path / "CLAUDE.md")
    guard.pytest_unconfigure(_fake_config({absent["path"]: absent}, errors))
    assert "INPUT UNAVAILABLE  git:" in capsys.readouterr().err


def test_the_clean_summary_is_one_line_naming_every_watched_path(tmp_path, capsys):
    """A clean run prints ONE stderr line naming every watched path and which
    exist, so a log shows what was watched rather than a silence."""
    present = tmp_path / "present" / "CLAUDE.md"
    present.parent.mkdir()
    present.write_text("x")
    samples = [guard._sample_one(present), guard._sample_one(tmp_path / "absent.md")]
    before = {s["path"]: s for s in samples}

    guard.pytest_unconfigure(_fake_config(before))

    err = capsys.readouterr().err
    assert err.count("\n") == 1 and err.startswith("[PACT CLAUDE.md guard] clean:"), err
    assert "watched 2 path(s), 1 present" in err
    assert f"{samples[0]['path']} (present)" in err
    assert samples[1]["path"] in err and f"{samples[1]['path']} (present)" not in err
    assert "PACT CLAUDE.md GUARD" not in err


# --------------------------------------------------------------------------
# Group 2 -- instrument liveness
# --------------------------------------------------------------------------

def _guard_imports():
    """Every module name an import statement in claude_md_guard.py names."""
    tree = ast.parse(Path(guard.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_inputs_read_cleanly_on_this_machine():
    """The instrument's inputs read without error here and name candidates.

    DELIBERATELY NOT an assertion that any file EXISTS. In CI the repo's own
    CLAUDE.md is gitignored and absent. What is asserted is that no input was
    unavailable -- the failure this guard reports rather than raises at run
    time -- and that the working directory's own location is watched.
    """
    inputs, errors = guard._pin_inputs()
    assert errors == []
    candidates = guard._candidates(inputs)
    assert candidates
    assert Path(os.getcwd()) / "CLAUDE.md" in candidates

    # The `shared` modules the guard imports come from THIS tree. A plugin
    # that pre-populated `shared` from elsewhere would otherwise be silent.
    for name in ("shared.paths", "shared.project_scope"):
        origin = Path(sys.modules[name].__file__).resolve()
        assert PLUGIN_ROOT in origin.parents, (name, origin)

    imported = _guard_imports()
    assert not {n for n in imported if n.split(".")[-1] in ("working_memory", "pact_session")}, (
        imported
    )


def test_a_sample_key_is_its_own_absolute_path(tmp_path, monkeypatch):
    """Both phases must stringify the same path the same way: absolute, as
    written, never resolved through a symlink. The real candidates alone
    cannot show either half on a machine whose inputs cross no symlink and are
    all absolute, so a relative path and a path through a symlinked directory
    are added."""
    for path in guard._candidates(guard._pin_inputs()[0]):
        sample = guard._sample_one(path)
        assert sample["path"] == str(Path(path).absolute())

    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    monkeypatch.chdir(tmp_path)
    assert guard._sample_one(Path("CLAUDE.md"))["path"] == str(tmp_path / "CLAUDE.md")
    through = tmp_path / "link" / "CLAUDE.md"
    assert guard._sample_one(through)["path"] == str(through)


_NEEDS_GIT = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the guard's inputs run git; without it every run reports an input error",
)


@_NEEDS_GIT
def test_unconfigure_samples_exactly_the_keys_configure_fixed(
    tmp_path, monkeypatch, capsys
):
    """Every input the guard reads changes between the two phases -- the
    project directory is deleted, a session id appears, the config root and
    the working directory move -- and unconfigure still samples exactly the
    keys configure fixed, and nothing the new inputs would have named."""
    for name in ("h", "c", "p/.claude", "w", "pw", "other", "elsewhere"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "p" / ".claude" / "CLAUDE.md").write_text("x")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "p"))
    monkeypatch.chdir(tmp_path / "w")
    monkeypatch.setattr(guard, "_passwd_home", lambda: str(tmp_path / "pw"))
    config = types.SimpleNamespace(stash=pytest.Stash())

    guard.pytest_configure(config)
    keys = sorted(config.stash[guard._BEFORE])

    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "fake")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "other"))
    monkeypatch.chdir(tmp_path / "elsewhere")
    guard.pytest_unconfigure(config)

    err = capsys.readouterr().err
    assert err.startswith("[PACT CLAUDE.md guard] clean:"), err
    assert f"watched {len(keys)} path(s)" in err
    for key in keys:
        assert key in err
    for moved in ("other", "elsewhere"):
        assert str((tmp_path / moved).resolve()) not in err


# --------------------------------------------------------------------------
# Group 3 -- hook collision and registration
# --------------------------------------------------------------------------

def _module_level_bindings(path):
    """Module-level binding names -> count, for a conftest."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    counts = {}
    for node in tree.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.asname or a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _conftests():
    return sorted(PLUGIN_ROOT.glob("**/conftest.py"))


def test_no_conftest_rebinds_a_pytest_hook_name_over_itself():
    """Counted PER MODULE, and restricted to `pytest_*` names. BOTH
    restrictions are load-bearing, and each has a counterexample in the tree
    right now -- this pair has been conflated twice already, so it is written
    down rather than left to be re-derived.

    PER MODULE, not across them. `pytest_sessionfinish` is legitimately bound
    in BOTH conftests today (by def in tests/conftest.py, by import in the
    root one). Inter-module co-registration is how pytest is designed to work
    and both hooks run; a rule counting across modules would fail on the
    existing tree on its first run. The hazard is INTRA-module rebinding,
    where Python silently keeps the last binding and a live guard is shadowed
    with no error anywhere.

    RESTRICTED to `pytest_*`. A rule flagging any duplicate module-level name
    would fail today on `shared`, imported three times inside
    tests/conftest.py -- a legitimate submodule import, not a hook.
    """
    collisions = []
    for conftest in _conftests():
        for name, count in _module_level_bindings(conftest).items():
            if name.startswith("pytest_") and count > 1:
                rel = conftest.resolve().relative_to(PLUGIN_ROOT)
                collisions.append(f"{rel}: {name} bound {count}x")
    assert not collisions, (
        "a conftest rebinds a pytest hook name over itself, which silently "
        "keeps only the last binding: " + "; ".join(collisions)
    )


def test_the_root_conftest_source_re_exports_the_guards_two_hooks():
    """The re-export is PRESENT IN THE SOURCE of the root conftest.

    WHAT THIS DOES NOT ESTABLISH, and the earlier name for it claimed
    otherwise: it does not show that pytest registered either hook, and it
    does not show that either one ran. It is an AST assertion over source
    text, so it would pass unchanged against a conftest whose hooks pytest
    never discovered. Its sibling below is the arm that watches them run.
    Together they cover the route and the execution; neither does both, and
    a name promising liveness from this one alone was the over-claim.
    """
    bindings = _module_level_bindings(PLUGIN_ROOT / "conftest.py")
    assert bindings.get("pytest_configure") == 1
    assert bindings.get("pytest_unconfigure") == 1


def test_the_guard_hook_actually_ran_in_this_session(pytestconfig):
    """RUNTIME liveness: pytest discovered the hook AND invoked it, here, now.

    `pytest_configure` stashes the before-sample on the `Config`, so a
    populated stash is proof the hook executed in THIS session rather than
    merely being spelled correctly in a file. Deleting the re-export empties
    it and reddens this arm; so does a hook that registers but never runs.

    WHAT THIS DOES NOT ESTABLISH: the ROUTE. Loading the module directly with
    `-p claude_md_guard` populates the same stash, so this cannot tell a
    conftest re-export from a direct plugin load. The sibling above is what
    pins the re-export itself.

    Reading the stash by its own key is deliberate -- nothing else writes it,
    so this cannot pass on some other plugin's state.
    """
    before = pytestconfig.stash.get(guard._BEFORE, None)
    assert before is not None, (
        "the guard's pytest_configure did not run in this session: the root "
        "conftest's re-export is the registration route"
    )
    assert before, "the hook ran but named no paths to watch"


def test_the_guard_module_exposes_both_hooks():
    assert callable(guard.pytest_configure)
    assert callable(guard.pytest_unconfigure)


# --------------------------------------------------------------------------
# Group 4 -- coverage certification
# --------------------------------------------------------------------------

def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=T",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, check=True, timeout=30,
    )


def _umbrella(tmp_path):
    """umbrella/main is a repository with one commit; main/.worktrees/wt is a
    linked worktree; the returned cwd is wt/sub."""
    main = tmp_path / "umbrella" / "main"
    main.mkdir(parents=True)
    _git("init", cwd=main)
    (main / "README").write_text("seed")
    _git("add", "README", cwd=main)
    _git("commit", "-m", "seed", cwd=main)
    _git("worktree", "add", ".worktrees/wt", cwd=main)
    sub = main / ".worktrees" / "wt" / "sub"
    sub.mkdir()
    return tmp_path / "umbrella", main, sub


def _watched(inputs):
    return {str(Path(p).resolve()) for p in guard._candidates(inputs)}


def _display_resolver():
    import scripts.working_memory as module

    return module, module._resolve_display_claude_md_with_base


def _staleness_resolver():
    import staleness as module

    return module, module._resolve_project_claude_md_with_base


_RESOLVERS = {"display": _display_resolver, "staleness": _staleness_resolver}


@_NEEDS_GIT
@pytest.mark.parametrize("shape", ["undeclared", "declared"])
@pytest.mark.parametrize("resolver", sorted(_RESOLVERS))
def test_every_directory_a_writer_resolver_probes_is_watched(
    resolver, shape, tmp_path, monkeypatch
):
    """Run each writer's resolver with its probe replaced by a recorder that
    finds nothing, so every rung runs, and require each directory it probed to
    be watched in both shapes. The census needs no knowledge of which rungs
    exist, so a new rung fails it the day it lands.

    The guard's inputs are B3's configuration: CLAUDE_PROJECT_DIR is an
    umbrella directory above the repository, the cwd is inside a linked
    worktree. The writer runs either with the declaration deleted (a test body,
    or a child inheriting one) or keeping it.
    """
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)
    umbrella, main, sub = _umbrella(tmp_path)
    monkeypatch.chdir(sub)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(umbrella))
    monkeypatch.setattr(guard, "_passwd_home", lambda: str(tmp_path / "pw"))
    inputs, errors = guard._pin_inputs()
    assert errors == []
    watched = _watched(inputs)

    if shape == "undeclared":
        monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    module, resolve = _RESOLVERS[resolver]()
    probed = []

    def record(base):
        probed.append(Path(base).resolve())
        return None

    monkeypatch.setattr(module, "_find_existing_claude_md", record)
    resolve()

    assert probed, "the resolver probed nothing"
    assert main.resolve() in probed, "the main checkout was never probed"
    if shape == "declared":
        assert umbrella.resolve() in probed
    for base in probed:
        for location in (base / ".claude" / "CLAUDE.md", base / "CLAUDE.md"):
            assert str(location) in watched, (location, sorted(watched))


@_NEEDS_GIT
@pytest.mark.parametrize("location", [".claude/CLAUDE.md", "CLAUDE.md"])
@pytest.mark.parametrize("resolver", sorted(_RESOLVERS))
def test_the_file_a_writer_resolver_finds_is_watched(
    resolver, location, tmp_path, monkeypatch
):
    """The same layout with a real CLAUDE.md at the main checkout, in each
    location, and the real probe: the file the resolver returns is watched."""
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)
    umbrella, main, sub = _umbrella(tmp_path)
    planted = main / location
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("main\n")
    monkeypatch.chdir(sub)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(umbrella))
    monkeypatch.setattr(guard, "_passwd_home", lambda: str(tmp_path / "pw"))
    watched = _watched(guard._pin_inputs()[0])

    _module, resolve = _RESOLVERS[resolver]()
    found, _base = resolve()

    assert found is not None and str(Path(found).resolve()) == str(planted.resolve())
    assert str(Path(found).resolve()) in watched


@pytest.mark.parametrize("config_dir", [None, "abs", "~/x", "~"])
def test_every_config_root_a_writer_can_reach_is_watched(
    config_dir, tmp_path, monkeypatch
):
    """The config roots a writer resolves: a child inheriting HOME, a child
    started with a literal environment and no HOME (the password database's
    home), and CLAUDE_CONFIG_DIR itself outside every test's window."""
    from shared.paths import get_claude_config_dir

    home, pw = tmp_path / "h", tmp_path / "pw"
    value = str(tmp_path / "abs") if config_dir == "abs" else config_dir
    monkeypatch.setenv("HOME", str(home))
    if value is None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", value)
    monkeypatch.setattr(guard, "_passwd_home", lambda: str(pw))

    watched = _watched(guard._pin_inputs()[0])

    reachable = [
        get_claude_config_dir(env={}, home=home) / "CLAUDE.md",
        get_claude_config_dir(env={}, home=pw) / "CLAUDE.md",
    ]
    if value is not None:
        reachable.append(
            get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": value}, home=home) / "CLAUDE.md"
        )
    for path in reachable:
        assert str(path.resolve()) in watched, (path, sorted(watched))
