"""Tests for the CLAUDE.md session tripwire.

Location: pact-plugin/tests/test_claude_md_guard.py

Covers `tests/claude_md_guard.py` in three groups:

1. the comparator table -- one arm per verdict, against handmade sample dicts,
   because `_compare` is the only non-trivial logic in the guard and it is a
   pure function of two maps;
2. instrument liveness -- that the sampler RAN and could resolve, which is the
   check that would fail if the guard were aimed at nothing;
3. hook collision and registration -- that no conftest rebinds a pytest hook
   name over itself, and that the root conftest really does register this
   guard's two hooks.

None of these writes a CLAUDE.md anywhere. Group 2 reads the real tree and
asserts nothing about what it finds there; the end-to-end arm that watches a
real write belongs in a nested run under tmp_path, and lives with the TEST
phase's work rather than here.
"""

import ast
from pathlib import Path

import claude_md_guard as guard

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Group 1 -- the comparator table
# --------------------------------------------------------------------------

def _present(digest="aa", dev=1, ino=10, size=5):
    return {
        "path": "/watched/CLAUDE.md",
        "exists": True,
        "digest": digest,
        "size": size,
        "st_dev": dev,
        "st_ino": ino,
        "mtime_ns": 123,
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
        "error": None,
    }


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


def test_a_key_only_the_after_phase_resolved_is_a_new_target():
    """`resolve_project_claude_md_path` is existence-dependent, so a child
    creating `.claude/CLAUDE.md` where only `./CLAUDE.md` existed makes the
    after-phase name a path the before-phase never sampled."""
    assert _only({}, {KEY: _present()}) == "NEW_TARGET_PRESENT"
    assert _only({}, {KEY: _absent()}) == "NEW_TARGET_ABSENT"


def test_mtime_alone_never_moves_the_verdict():
    """`mtime_ns` is SAMPLED and NOT COMPARED. It moves without content or
    identity moving, and adds no detection power beyond digest and st_ino --
    so including it in the verdict would buy false positives only. This arm
    fails if someone adds it to the comparison."""
    before = {KEY: _present()}
    after = {KEY: dict(_present(), mtime_ns=999_999)}
    assert _only(before, after) == "OK_UNCHANGED"


def test_an_error_on_either_side_is_an_instrument_error():
    erred = dict(_absent(), error="stat failed: OSError: boom")
    assert _only({KEY: erred}, {KEY: _present()}) == "INSTRUMENT_ERROR"
    assert _only({KEY: _present()}, {KEY: erred}) == "INSTRUMENT_ERROR"


def test_an_instrument_error_outranks_a_content_change():
    """Ordering matters: a sample that could not be read cannot also be
    trusted to say the file changed. Reporting MODIFIED off a failed read
    would assert a difference the instrument never observed."""
    before = {KEY: _present(digest="aa")}
    after = {KEY: dict(_present(digest="bb"), error="read failed: OSError: boom")}
    assert _only(before, after) == "INSTRUMENT_ERROR"


def test_the_violation_set_is_exactly_the_five_raising_verdicts():
    """Pins WHICH verdicts end the session non-zero. An instrument error and a
    newly-named absent path are reported and NOT raised, by ruling: a resolver
    that cannot import on an unmeasured CI interpreter would otherwise fail
    every run there, an over-block on a guard whose job is a rare catch."""
    assert guard._VIOLATIONS == frozenset(
        {"CREATED", "DELETED", "MODIFIED", "REWRITTEN", "NEW_TARGET_PRESENT"}
    )
    assert "INSTRUMENT_ERROR" not in guard._VIOLATIONS
    assert "NEW_TARGET_ABSENT" not in guard._VIOLATIONS


def test_the_report_is_silent_on_a_clean_comparison():
    """Silence on a clean run is deliberate -- liveness is this file's job,
    not a line printed on every session."""
    clean = guard._compare({KEY: _present()}, {KEY: _present()})
    assert guard._format_report(clean) == ""


def test_the_report_names_the_verdict_the_path_and_the_summary_line_caveat():
    verdicts = guard._compare({KEY: _absent()}, {KEY: _present()})
    report = guard._format_report(verdicts)
    assert "VIOLATION" in report
    assert KEY in report
    assert "CREATED" in report
    # The caveat is the whole point of the report existing: a violating run
    # still prints "N passed", so the report and the exit code are the only
    # signals a reader gets.
    assert "passed" in report


def test_an_instrument_error_is_not_reported_as_a_violation():
    """The header must not cry violation for a failure of the instrument --
    the same distinction the raising set makes, at the place a human reads."""
    erred = dict(_absent(), error="stat failed: OSError: boom")
    report = guard._format_report(guard._compare({KEY: erred}, {KEY: erred}))
    assert report, "an instrument error must still be reported"
    assert "no violation" in report
    assert "stat failed" in report


# --------------------------------------------------------------------------
# Group 2 -- instrument liveness
# --------------------------------------------------------------------------

def test_the_sampler_runs_and_every_resolver_resolves():
    """The instrument RAN and could resolve, against the real tree.

    DELIBERATELY NOT an assertion that any file EXISTS. In CI the repo's own
    CLAUDE.md is gitignored and absent, so an `exists=True` assertion would
    fail there for the right reason at the wrong time. What is asserted is
    that at least one path was named and that no evaluation recorded an
    error -- which is what fails if a resolver cannot import, the failure this
    guard reports rather than raises at run time.
    """
    samples = guard._sample(PLUGIN_ROOT)
    assert samples, "the sampler named no paths at all"
    erred = {k: v["error"] for k, v in samples.items() if v["error"]}
    assert not erred, f"resolver or stat failure: {erred}"


def test_a_sample_key_is_its_own_resolved_path():
    """Both phases must stringify the same file the same way. An inconsistent
    key would not error -- it would read as a target appearing, or hide one
    that did."""
    for key, sample in guard._sample(PLUGIN_ROOT).items():
        assert key == sample["path"]
        if not key.startswith("<"):
            assert key == str(Path(key).resolve())


def test_the_union_phase_carries_every_before_key_forward():
    """Every key in before ∪ after gets an after-sample, which is what makes
    the appearance of a new key a signal rather than a comparison hole."""
    before = dict(guard._sample(PLUGIN_ROOT))
    before["/nonexistent/vanished/CLAUDE.md"] = _absent()
    after = guard._sample_union(PLUGIN_ROOT, before)
    assert set(before) <= set(after)


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


def test_the_root_conftest_registers_the_guards_two_hooks():
    """Registration liveness: deleting the re-export reddens here.

    Without this arm the guard could be removed from the conftest and every
    other test in this file would still pass, because they all exercise the
    module directly rather than through pytest's hook discovery.
    """
    bindings = _module_level_bindings(PLUGIN_ROOT / "conftest.py")
    assert bindings.get("pytest_configure") == 1
    assert bindings.get("pytest_unconfigure") == 1


def test_the_guard_module_exposes_both_hooks():
    assert callable(guard.pytest_configure)
    assert callable(guard.pytest_unconfigure)
