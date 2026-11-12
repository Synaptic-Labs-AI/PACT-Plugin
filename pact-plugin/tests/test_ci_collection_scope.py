"""The CI test invocation must not be narrower than the test corpus on disk.

WHAT THIS EXISTS TO STOP, stated as the defect rather than the rule
-------------------------------------------------------------------
Test modules that live beside the skill they guard — rather than under
``tests/`` — are invisible to a path-scoped pytest invocation. A run scoped to
``tests/`` collects them nowhere, passes, and reports a five-digit total that
reads as the whole suite. Nothing in the output distinguishes a complete run
from one that silently dropped a directory: the count is the only tell, and no
reader has a reference value for it. An edit to those skills was then certified
by a gate that never loaded their guards.

The invocation itself is already correct — the workflow runs a bare
``python -m pytest`` from ``pact-plugin/``, which collects whatever is there.
This module is the half that keeps it correct. A comment saying "do not add
path arguments" is not a test, and the scope can re-narrow silently through
either of two doors:

  1. a path argument returning to the pytest command line;
  2. a ``testpaths`` entry in pytest config, which takes effect precisely
     BECAUSE the command line has no path arguments to override it.

MEASURED, so door 2 is not taken on faith (both restored afterward):

  * ``pact-plugin/pytest.ini`` with ``testpaths = tests`` -> collection drops
    13822 to 13774. That is the same 48-module gap, re-opened without touching
    the command line at all.
  * The repository-root ``pyproject.toml`` with the same key -> collection
    stays 13822. It does NOT narrow: rootdir stays at the repository root, so
    the entry resolves to a directory that does not exist and pytest ignores
    it in silence.

THAT SECOND ARM IS DORMANT, NOT HARMLESS, AND THE DIFFERENCE IS THE REASON THIS
CHECK SCANS A WHOLE FAMILY. It fails to narrow because ``<repo-root>/tests``
does not exist — which is a fact about the FILESYSTEM, not a property of the
placement. Anyone who creates a ``tests/`` directory at the repository root arms
it, and no promise anywhere forbids that.

Armed, it is WORSE than the other arm rather than equal to it. A ``pytest.ini``
in ``pact-plugin/`` collects the right tree 48 modules short — a narrowing. The
repository-root entry makes rootdir the repository root, so it collects
``<repo-root>/tests``, a DIFFERENT tree, while every module under
``pact-plugin/tests`` goes uncollected — a REDIRECTION. And pytest ignores the
missing path silently, so the transition from dormant to armed announces
nothing. That silence is the same signature as the original 48-module drop,
which is what makes it worth a guard instead of a comment.

So the config check is scoped to a FAMILY of filenames across both directories.
The one config file that exists today is the repository-root ``pyproject.toml``,
which is precisely the placement where the door is currently shut — a check
scoped to the config it can see would be a check aimed at the dormant arm.

WHY NO ASSERTION HERE COUNTS ANYTHING
-------------------------------------
A pinned collected-test total goes red on every legitimate test addition, which
trains its readers to bump the number rather than investigate — so the guard
that cried wolf is retired right before the run where it was right. The parity
check compares SETS OF NODE IDS and reports which tests went missing; the other
two are directional on SCOPE. None of them pins a number.

PARSE FAILURE AND DRIFT ARE REPORTED SEPARATELY, ALWAYS
-------------------------------------------------------
Every parse below asserts an anchor with ground truth INDEPENDENT of the parse,
before the comparison it feeds, and with a distinct message. An absence-shaped
assertion is satisfied trivially by a parser that read nothing — so "no path
arguments found" from a workflow this module failed to parse would be a false
green of exactly the genus the whole file is about. ``test_memory_init.py``
follows the same convention where it reads the same workflow, to check that a
runtime environment check brackets every test run.
"""

import ast
import dataclasses
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import ci_contract

# The two collection roots pytest resolves from `pact-plugin/`, and the pytest
# config filenames that can carry a `testpaths` entry. Both directories are
# scanned: rootdir resolution depends on which one holds a config, and that in
# turn decides whether a `testpaths` entry resolves to a real directory.
_PYTEST_CONFIG_NAMES = ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")


def _plugin_root():
    """The `pact-plugin/` directory — this file's grandparent."""
    return Path(__file__).resolve().parents[1]


def _source_repo_root():
    """Repo root by a POSITIVE marker, or None when this is not a checkout.

    Deliberately NOT "did I find the workflow file?". "I cannot find it" and
    "it is wrong" must never produce the same outcome — that equivalence is the
    silent-narrowing defect this module exists to prevent. The skip decision is
    made on an INDEPENDENT marker, which leaves the workflow's own absence free
    to be a FAILURE. Marker matches the convention in test_memory_init.py.
    """
    for base in Path(__file__).resolve().parents:
        if (base / ".claude-plugin" / "marketplace.json").exists():
            return base
    return None


def _require_checkout():
    """Return (repo_root, workflow_text), or skip when outside a source checkout."""
    root = _source_repo_root()
    if root is None:
        pytest.skip(
            "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json above "
            f"{Path(__file__).resolve()}, so this is not a PACT source checkout. "
            "The expected case is an INSTALLED PLUGIN CACHE, which has no "
            ".github/ tree by design. This skip is NO INFORMATION about the CI "
            "collection scope — never read it as a pass."
        )

    # INSIDE a checkout the workflow is required, so its absence is a FAILURE.
    workflow = root / ".github" / "workflows" / "tests.yml"
    assert workflow.exists(), (
        f"source checkout detected at {root} (marketplace.json present) but "
        f"{workflow} is missing. The collection-scope guard cannot run. This is "
        f"a FAILURE, not a skip: inside a checkout the workflow is required."
    )
    return root, workflow.read_text(encoding="utf-8")


def _require_fast_lane(repo_root):
    """LOUD skip when the whole fast lane is deleted; fail on a partial lane."""
    state, present, missing = ci_contract.fast_lane_state(repo_root)
    if state == "absent":
        pytest.skip(
            "SKIPPED, NOT PASSED — the WHOLE fast lane is absent (all "
            f"{len(ci_contract.FAST_LANE_FILES)} sentinel files missing): "
            f"{list(ci_contract.FAST_LANE_FILES)}. This arm checks the fast "
            "lane and cannot judge a lane that is not there; the full gate's "
            "independent guards still run. This skip is NO INFORMATION about "
            "the fast lane."
        )
    assert state == "present", (
        f"FAST LANE PARTIALLY PRESENT: present={present}, missing={missing}. "
        f"Deleting the whole lane is supported (this arm skips loudly); "
        f"deleting only part of it is a broken checkout and FAILS."
    )


def _pytest_run_commands(workflow_text):
    """Every non-comment `run:` command in the workflow that invokes pytest.

    Comment lines are dropped FIRST and that is load-bearing, not tidiness: the
    workflow discusses `python -m pytest` in roughly ten comment lines — several
    of them quoting the narrow `pytest tests/` form this module exists to keep
    out. A parser that read comments would find the forbidden shape in prose
    that argues against it, and report the file as already broken.

    The `pip install` line names pytest as a package rather than running it, so
    it is excluded on the same principle: it is not an invocation.
    """
    found = []
    for raw in workflow_text.splitlines():
        line = raw.strip()
        if line.startswith("#") or not line.startswith("run:"):
            continue
        command = line[len("run:"):].strip()
        if re.search(r"\bpytest\b", command) and "pip install" not in command:
            found.append(command)
    return found


def _the_pytest_command(workflow_text):
    """The single CI pytest invocation, with its parse anchored before use.

    NON-VACUITY, with ground truth independent of the parse: this assertion is
    EXECUTING under pytest, and it runs in CI, so CI demonstrably invokes
    pytest. A correct parse of the workflow therefore cannot yield nothing. If
    it does, the parser is wrong — not the workflow — and that must be reported
    as a parse failure rather than as a clean scope check over an empty read.
    """
    commands = _pytest_run_commands(workflow_text)
    assert commands, (
        "PARSE FAILURE, not a scope regression: no `run:` line invoking pytest "
        "could be read from the workflow, yet this assertion is running under "
        "pytest in that same workflow, so one exists. The parser models a "
        "single-line `run: <command>`; a refactor to a block scalar (`run: |`) "
        "or a composite action defeats it. RE-POINT THE PARSER — do NOT relax "
        "the assertions below, which pass vacuously over an empty command list."
    )
    assert len(commands) == 1, (
        f"PARSE AMBIGUITY, not a scope regression: {len(commands)} pytest "
        f"invocations were read from the workflow ({commands}). Every check "
        f"below reasons about THE CI invocation, singular. If the workflow "
        f"genuinely gained a second pytest step, these checks must be re-pointed "
        f"to cover both — a guard that silently examines only the first one is "
        f"the narrowing defect wearing a different hat."
    )
    return commands[0]


def _pytest_args(command):
    """Effective default arguments after pytest, never expression fragments."""
    words = ci_contract.normalized_workflow_test_argv(command)
    if "pytest" not in words:
        return None
    return words[words.index("pytest") + 1:]


def _step_blocks(workflow_text):
    """The workflow's step blocks, one per `- ` list item, as stripped lines.

    Line-oriented on purpose, matching `_pytest_run_commands` above: this
    module parses the workflow as lines rather than as YAML, and two parsers
    that disagree about what a line is would be worse than one. Comments are
    dropped FIRST for the reason that helper documents — the workflow discusses
    these very keys in prose, at length.

    OVER-SPLITTING IS SAFE IN THE DIRECTION THAT MATTERS, and that is why a
    crude `- ` boundary is enough. Any `- ` starts a new block, so a nested
    list item inside a step would end that step's block early. The consequence
    is a SMALLER block, which can only make the assertion below harder to
    satisfy — a missing `working-directory` still reddens. It cannot fail open.
    Step keys precede any nested list a step carries, so the real case does not
    arise today.
    """
    blocks, current = [], None
    for raw in workflow_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current is not None:
                blocks.append(current)
            current = []
        if current is not None:
            current.append(stripped)
    if current is not None:
        blocks.append(current)
    return blocks


def _assert_ci_runs_from_plugin_root(workflow_text):
    """The PYTEST STEP must declare `working-directory: pact-plugin`.

    The parity comparison runs both arms from that directory, so if CI ever
    stops doing the same, the comparison stops describing CI — it would still
    pass, over an invocation nobody runs. Pinned rather than assumed.

    THIS KEYS ON THE STEP THAT RUNS PYTEST. It used to assert that the whole
    FILE contained exactly one `working-directory:` line, which was sound only
    while the workflow had exactly one such step. It no longer does — a
    model-cache warm step legitimately declares the same directory — and the
    count form became UNSOUND rather than merely noisy at that moment:

      remove `working-directory` from the PYTEST step, leave the warm step's,
      and the count is STILL EXACTLY ONE, so the old assertion PASSES while
      CI runs pytest from the repository root.

    That is verbatim the disaster the paragraph above describes, reported
    green. A count cannot distinguish WHICH step declares the directory, and
    once two steps can declare it the count stops carrying the claim at all.

    The file already knows this. `test_ci_invocation_and_full_corpus_collect_the_same_tests`
    compares SETS rather than counts, and says why: a pinned total goes red on
    every legitimate addition, which teaches its readers to bump the number
    instead of investigating, so the guard is relaxed exactly before the run
    where it was right. The principle was applied to the parity check and not
    to this precondition; it is applied here now.
    """
    owning = [
        block for block in _step_blocks(workflow_text)
        if any(
            line.startswith("run:")
            and re.search(r"\bpytest\b", line)
            and "pip install" not in line
            for line in block
        )
    ]
    assert len(owning) == 1, (
        f"PARSE FAILURE, not a CI relocation: expected exactly one step whose "
        f"`run:` invokes pytest, found {len(owning)}. This assertion is itself "
        f"executing under pytest in that workflow, so one exists — the parser "
        f"is wrong, not the workflow. RE-POINT IT rather than relaxing the "
        f"check below, which would otherwise pass over an empty read."
    )
    assert "working-directory: pact-plugin" in owning[0], (
        f"CI RELOCATION: the step that runs pytest does not declare "
        f"`working-directory: pact-plugin`. Its lines are {owning[0]}. The "
        f"parity check below runs both arms from pact-plugin/ on the strength "
        f"of that declaration — without it, this test compares two invocations "
        f"that CI does not perform. Re-point it, or restore the declaration.\n"
        f"NOTE: a file-wide COUNT of `working-directory:` lines cannot catch "
        f"this once more than one step declares one, which is why this keys on "
        f"the pytest step itself."
    )


def _path_operands(command, working_dir):
    """Path operands in a pytest command — non-flag words naming a real path.

    Models pytest's argv: operands are the non-flag words after the `pytest`
    token. The extra requirement that the word NAME AN EXISTING PATH is what
    keeps a separated flag value (`--maxfail 3`, `-p no:cacheprovider`) from
    reading as a path and producing a false red. It cannot fail open in the
    direction that matters: a path argument that does not exist collects
    nothing, so it is not a silent narrowing — it is a broken CI run.
    """
    words = ci_contract.normalized_workflow_test_argv(command)
    if "pytest" not in words:
        return None
    after = words[words.index("pytest") + 1:]
    return [w for w in after if not w.startswith("-") and (working_dir / w).exists()]


def _discovered_test_roots(plugin_root):
    """First path component of every test module under `pact-plugin/`.

    The nested-worktree exclusion is tested on the RELATIVE path, and that is
    the whole point rather than a detail: a checkout INSIDE a worktree has
    `.worktrees` in the absolute path of every file it contains, so an absolute
    test excludes the entire tree and returns an empty set. Empty is the one
    result this scan must never return quietly — the comparison it feeds is a
    set difference, which is satisfied trivially by nothing. Only a worktree
    nested BELOW pact-plugin/ is a real duplicate worth skipping.
    """
    roots = set()
    for path in plugin_root.rglob("test_*.py"):
        relative = path.relative_to(plugin_root)
        if ".worktrees" in relative.parts:
            continue
        roots.add(relative.parts[0])
    return roots


def _collect_node_ids(args, cwd):
    """Node IDs pytest actually collects, by running it. Returns (ids, result).

    A real collection rather than a prediction: the whole defect is that a
    narrowed invocation LOOKS correct, so the only claim worth making is about
    what pytest DOES, not about what an argument list should imply.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "-p", "no:randomly", *args],
        cwd=str(cwd), capture_output=True, text=True,
    )
    ids = {line.strip() for line in result.stdout.splitlines() if "::" in line}
    return ids, result


class TestCiInvocationCollectsTheWholeCorpus:
    """The CI pytest step must reach every test root, through either door."""

    def test_dynamic_command_normalizes_to_the_static_default(self):
        normalized = ci_contract.normalized_workflow_test_argv()
        assert normalized == ci_contract.TEST_CMD.split()
        assert _pytest_args(ci_contract.WORKFLOW_TEST_CMD) == [
            "-ra", "-n", "4", "--dist", "loadfile"
        ]

    def test_dynamic_command_rejects_expression_and_argument_drift(self):
        junit = '--junitxml="$GITHUB_WORKSPACE/.full-suite-results.xml"'
        for command in (
            # An expression reintroduced into the run text is drift: the
            # workflow must pass the dispatch value through `DIST`, never
            # inline as shell text.
            ci_contract.WORKFLOW_TEST_CMD.replace(
                '"$DIST"', '"${{ inputs.dist || \'load\' }}"', 1),
            ci_contract.WORKFLOW_TEST_CMD + " tests",
            ci_contract.WORKFLOW_TEST_CMD.replace(" " + junit, "", 1),
            ci_contract.WORKFLOW_TEST_CMD + " --deselect tests/test_smoke.py",
        ):
            with pytest.raises(AssertionError, match="PARSE FAILURE"):
                ci_contract.normalized_workflow_test_argv(command)

    def test_unbalanced_quote_is_a_named_parse_failure(self):
        with pytest.raises(AssertionError,
                           match="PARSE FAILURE.*does not tokenize"):
            ci_contract.normalized_workflow_test_argv(
                'python -m pytest -ra -n 4 --dist "loadfile')

    def test_ci_invocation_and_full_corpus_collect_the_same_tests(self):
        """COLLECTION PARITY: what CI collects must equal the whole corpus.

        This runs pytest twice and compares the two sets of node IDs. It does
        NOT compare counts, and the distinction is the issue's own: a pinned
        total goes red on every legitimate test addition, which teaches its
        readers to bump the number instead of investigating, so the guard is
        relaxed exactly before the run where it was right. A SET comparison
        fires only when the membership differs, and it names the tests that
        went missing rather than a delta a reader has to interpret.

        Both arms run from the CI working directory, so rootdir resolves
        identically and the node IDs are directly comparable — verified: the
        two arms produce byte-identical sets today, with no normalisation.

        This measures the property directly instead of predicting it from the
        argument list. A structural check on the arguments is a proxy: it
        reasons about what an invocation OUGHT to collect, and every silent
        narrowing in this repo's history happened because what an invocation
        ought to collect and what it did collect had quietly diverged.
        """
        _, workflow_text = _require_checkout()
        plugin_root = _plugin_root()
        command = _the_pytest_command(workflow_text)
        _assert_ci_runs_from_plugin_root(workflow_text)

        discovered = _discovered_test_roots(plugin_root)

        # NON-VACUITY, ground truth independent of the scan: this file is
        # `tests/test_ci_collection_scope.py` and it is executing, so `tests`
        # is a test root whether or not the scan can see it.
        assert "tests" in discovered, (
            f"SCAN FAILURE, not a scope regression: the test-root scan of "
            f"{plugin_root} returned {sorted(discovered)}, which omits 'tests' "
            f"— yet this module lives in tests/ and is running. The scan is "
            f"broken, and the corpus arm below would be built from a set that "
            f"does not describe the tree."
        )
        assert discovered - {"tests"}, (
            "THIS GUARD IS NOW VACUOUS — read the message before changing "
            "anything. Every test module under pact-plugin/ lives in tests/, so "
            "the two arms below name the same tree and cannot disagree. That is "
            "a legitimate state: it is what moving the skill-adjacent test "
            "modules into tests/ would produce, which was one of the proposed "
            "fixes. If that move was deliberate, RETIRE this test or re-point "
            "it. Do not delete this assertion and leave the comparison "
            "standing — a guard that cannot fail is worse than none, because it "
            "is counted as coverage."
        )

        ci_args = _pytest_args(command)
        assert ci_args is not None, (
            f"PARSE FAILURE, not a scope regression: no `pytest` token in the "
            f"CI command {command!r}, so its arguments cannot be read."
        )

        ci_ids, ci_run = _collect_node_ids(ci_args, plugin_root)
        corpus_ids, corpus_run = _collect_node_ids(sorted(discovered), plugin_root)

        # Both collections must have SUCCEEDED and be non-empty before their
        # difference means anything: two failed collections agree perfectly.
        for label, ids, run in (("CI", ci_ids, ci_run), ("corpus", corpus_ids, corpus_run)):
            assert run.returncode == 0, (
                f"COLLECTION FAILURE, not a scope regression: the {label} arm "
                f"exited {run.returncode}. Comparing a failed collection would "
                f"be meaningless. stderr tail:\n{run.stderr[-600:]}"
            )
            assert ids, (
                f"COLLECTION FAILURE, not a scope regression: the {label} arm "
                f"collected NOTHING, so the comparison below would pass "
                f"vacuously — two empty sets are equal. stdout tail:\n"
                f"{run.stdout[-600:]}"
            )

        # Ground truth independent of both collections: this module is running,
        # so it is collectable, so a correct collection contains it.
        this_module = f"{Path(__file__).parent.name}/{Path(__file__).name}"
        for label, ids in (("CI", ci_ids), ("corpus", corpus_ids)):
            assert any(this_module in node for node in ids), (
                f"COLLECTION FAILURE, not a scope regression: the {label} arm "
                f"does not contain {this_module}, yet that module is executing "
                f"right now. The node-ID parse is wrong, not the scope."
            )

        missing = corpus_ids - ci_ids
        extra = ci_ids - corpus_ids
        assert not missing and not extra, (
            f"CI COLLECTION DIVERGES FROM THE CORPUS.\n"
            f"  {len(missing)} test(s) exist but CI does NOT collect them — "
            f"they pass locally and NEVER RUN IN CI, over a green run whose "
            f"output says nothing about it.\n"
            f"  {len(extra)} test(s) collected by CI but not by the corpus arm "
            f"(usually means a test root moved).\n"
            f"  BEFORE reading this as a scope regression: the two arms are "
            f"SEPARATE subprocesses, so a collectable file (test_*.py) "
            f"created or deleted BETWEEN them lands in one set only, "
            f"shifting it by the node IDs that file contributes. If "
            f"anything else was running in this worktree, re-run on a quiet "
            f"tree first — isolation discriminates, repetition does not.\n"
            f"  CI command: {command!r} (args {ci_args})\n"
            f"  corpus roots: {sorted(discovered)}\n"
            f"  missing sample: {sorted(missing)[:5]}\n"
            f"  extra sample: {sorted(extra)[:5]}"
        )

    def test_the_ci_invocation_carries_no_path_arguments(self):
        """The pytest step must carry no path arguments at all.

        Stricter than the reachability check above, and deliberately so: a bare
        invocation is SELF-MAINTAINING, because a test root added later is
        collected without anyone remembering this decision. An explicit list
        that names every root passes the check above while re-introducing the
        requirement to maintain it — which is the discipline that already
        failed once.

        These two checks overlap today: with no path arguments, the reachability
        comparison cannot fail. They are both kept because they fail for
        DIFFERENT reasons and survive different futures — if a path argument
        ever becomes genuinely necessary, this check is the one to revisit, and
        the reachability check above is what must keep holding.
        """
        _, workflow_text = _require_checkout()
        command = _the_pytest_command(workflow_text)

        operands = _path_operands(command, _plugin_root())
        assert operands is not None, (
            f"PARSE FAILURE, not a scope regression: no `pytest` token in the "
            f"CI command {command!r}, so its path operands cannot be read."
        )
        assert not operands, (
            f"THE CI PYTEST STEP RE-ACQUIRED PATH ARGUMENTS: {operands}. A path "
            f"argument collects ONLY what it names, so every test directory not "
            f"listed is dropped SILENTLY — an edit is then certified by a gate "
            f"that never ran the tests written for it. The command is "
            f"{command!r}. Remove the path arguments; a bare `python -m pytest` "
            f"from pact-plugin/ collects the whole tree."
        )

    def test_no_pytest_config_declares_testpaths(self):
        """`testpaths` re-narrows collection without touching the command line.

        The second door, and the one a command-line check cannot see. It is
        armed BY the fix: `testpaths` applies only when no path arguments are
        given, so removing them is what gives such an entry its effect.

        Measured (see the module docstring): a `pytest.ini` in pact-plugin/
        declaring `testpaths = tests` drops collection by exactly the gap this
        module exists to keep closed.
        """
        repo_root, _ = _require_checkout()
        plugin_root = _plugin_root()

        checked = []
        declaring = []
        for directory in (repo_root, plugin_root):
            for name in _PYTEST_CONFIG_NAMES:
                config = directory / name
                if not config.exists():
                    continue
                checked.append(config)
                body = config.read_text(encoding="utf-8")
                # Ignore commented-out entries: the workflow's own prose warns
                # against testpaths, and a doc line is not a declaration.
                for raw in body.splitlines():
                    line = raw.strip()
                    if line.startswith("#") or line.startswith(";"):
                        continue
                    if re.match(r"^testpaths\s*=", line):
                        declaring.append(f"{config}: {line}")

        assert not declaring, (
            f"A PYTEST CONFIG DECLARES testpaths: {declaring}. That re-narrows "
            f"collection through a route the CI command line does not show — "
            f"the command stays a bare `python -m pytest` and looks correct "
            f"while the corpus shrinks. Remove the entry. If a narrower default "
            f"is genuinely wanted for local runs, it must not be the thing CI "
            f"inherits. (Config files examined: "
            f"{[str(c) for c in checked] or 'none'}.)"
        )


def test_no_second_checkout_sits_inside_the_collection_root():
    """A checkout under `pact-plugin/` makes pytest collect a SECOND copy.

    Same defect as the rest of this module and the opposite direction: there a
    scoped invocation collected too little, here an extra checkout collects too
    much. Both move the only number a reader has, and this one moves it the way
    that looks like good news — every total roughly DOUBLES, and a doubled
    green reads as a bigger, healthier suite rather than an error.

    NOTHING ELSE WE RUN CATCHES IT. Count-versus-expectation does not fire,
    because nobody carries a reference value precise enough. Collecting twice
    and comparing scopes does not fire either: the extra copy inflates both
    sides identically.

    A `.git` entry is the tell for both shapes that cause it — a linked
    worktree writes a `.git` FILE, a clone a `.git` DIRECTORY — and neither
    belongs under the package. Near-missed for real: a `git worktree add` run
    from the wrong directory landed one here.

    CEILING, and it is why this reads the filesystem rather than
    `git worktree list`: that command knows only worktrees of THIS repository,
    so it is blind to a stray clone. It also cannot run where git is absent.
    This check fires mostly on a developer's machine — CI checks out fresh and
    has nothing nested — which is the right place, because that is where the
    mistake is made and where the misleading total gets believed.

    RED WHEN any second checkout appears under the plugin root.
    """
    root = _plugin_root()
    nested = sorted(str(path.relative_to(root)) for path in root.rglob(".git"))
    assert nested == [], (
        f"a second checkout sits inside the collection root: {nested}. pytest "
        f"collects it as well, so every total below is roughly doubled and the "
        f"run looks healthier than the real suite. Move or remove it, then "
        f"re-run before trusting any count from this session."
    )


class TestTheFastManifestNamesRealCorpusFiles:
    """The fast manifest may only name files the full corpus can collect.

    This is the FILE-LEVEL COMPLETENESS arm, and the only arm this module
    gains for the fast lane. The fast-workflow SHAPE lives in
    ``test_fast_workflow_contract.py``: one module owning two contradictory
    subjects — "what is the full corpus" and "is the fast workflow shaped
    right" — is how a guard starts lying.

    Membership and existence only; never a count. The manifest's cardinality
    and schema rules are validated by ``scripts/fast_suite_args.py --check``,
    which this arm runs as the entry point rather than reimplementing.
    """

    def test_manifest_paths_exist_and_appear_in_the_discovered_corpus(self):
        repo_root, _ = _require_checkout()
        _require_fast_lane(repo_root)
        plugin_root = _plugin_root()
        manifest = repo_root / "scripts" / "fast_suite_exclusions.json"
        cli = repo_root / "scripts" / "fast_suite_args.py"
        assert manifest.is_file(), (
            f"source checkout detected at {repo_root} but {manifest} is "
            f"missing. The fast selection manifest is part of the reviewed "
            f"contract; its absence is a FAILURE, not a skip."
        )
        assert cli.is_file(), (
            f"source checkout detected at {repo_root} but {cli} is missing; "
            f"the manifest cannot be validated."
        )
        document = json.loads(manifest.read_text(encoding="utf-8"))
        guard_files = list(document.get("guard_files") or [])
        exclusions = [entry.get("path") for entry in document.get("exclusions") or []]
        named = guard_files + exclusions
        assert guard_files, "the manifest names no guard files to retain"
        assert exclusions, "the manifest names no exclusions"
        assert all(isinstance(path, str) and path for path in named), (
            f"the manifest carries a non-string path: {named!r}"
        )
        duplicates = sorted({path for path in named if named.count(path) > 1})
        assert not duplicates, f"the manifest names a path twice: {duplicates}"

        corpus = {
            path.relative_to(plugin_root).as_posix()
            for path in plugin_root.rglob("test_*.py")
            if ".worktrees" not in path.relative_to(plugin_root).parts
        }
        # NON-VACUITY, ground truth independent of the scan: this module is
        # executing, so it is on disk and must be in the corpus the scan built.
        this_module = f"{Path(__file__).parent.name}/{Path(__file__).name}"
        assert corpus, (
            "SCAN FAILURE, not a manifest regression: the rglob over "
            f"{plugin_root} returned nothing, so the comparison below would "
            "pass vacuously."
        )
        assert this_module in corpus, (
            f"SCAN FAILURE, not a manifest regression: {this_module} is "
            f"executing yet absent from the discovered corpus."
        )

        missing_on_disk = sorted(
            path for path in named if not (plugin_root / path).is_file())
        not_test_modules = sorted(
            path for path in named
            if not (path.endswith(".py") and Path(path).name.startswith("test_")))
        not_in_corpus = sorted(path for path in named if path not in corpus)
        assert not missing_on_disk, (
            f"the manifest names paths that do not exist under {plugin_root}: "
            f"{missing_on_disk}. A moved or renamed file must fail loudly; a "
            f"glob or a stale path would silently stop covering its tests."
        )
        assert not not_test_modules, (
            f"the manifest names non-test modules: {not_test_modules}. Only "
            f"test_*.py files may be excluded or retained."
        )
        assert not not_in_corpus, (
            f"the manifest names files the discovered corpus does not see: "
            f"{not_in_corpus}. Every named file must be collectable from the "
            f"bare invocation."
        )

        proc = subprocess.run(
            [sys.executable, str(cli), "--check"], cwd=str(repo_root),
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, (
            f"scripts/fast_suite_args.py --check exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


# The reviewed exclusion identities: path, production_seams, mechanism.
# Changing any entry is a reviewed manifest change, not a code tweak. This
# table is a DRIFT GUARD, not an adversarial barrier: a PR can edit this pin
# and the manifest in the same commit, so review remains the real control.
EXPECTED_EXCLUSIONS = (
    ('tests/test_merge_guard_refused_is_mintable.py',
     ('hooks/merge_guard_post.py',
      'hooks/merge_guard_pre.py',
      'hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'mint matrix over nested refusal shapes'),
    ('tests/test_snapshot_provenance_semantics.py',
     ('hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/shared/task_metadata_snapshot.py',
      'hooks/staleness.py'),
     'deep-pressure totality over lookalike provenance values'),
    ('tests/test_merge_guard_substitution_mint.py',
     ('hooks/merge_guard_post.py',
      'hooks/merge_guard_pre.py',
      'hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'large substitution-mint matrix'),
    ('tests/test_task_metadata_snapshot.py',
     ('hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/shared/task_metadata_snapshot.py',
      'hooks/staleness.py'),
     'deep-pressure totality over metadata payloads'),
    ('tests/test_snapshot_key_count_discriminator.py',
     ('hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/shared/task_metadata_snapshot.py',
      'hooks/staleness.py'),
     'key-count discriminator totality matrix'),
    ('tests/test_merge_guard_review_fixes.py',
     ('hooks/merge_guard_post.py',
      'hooks/merge_guard_pre.py',
      'hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'large review-fix regression matrix'),
    ('tests/test_memory_cli.py',
     ('hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py',
      'skills/pact-memory/scripts/__init__.py',
      'skills/pact-memory/scripts/cli.py',
      'skills/pact-memory/scripts/config.py',
      'skills/pact-memory/scripts/database.py',
      'skills/pact-memory/scripts/embeddings.py',
      'skills/pact-memory/scripts/memory_api.py',
      'skills/pact-memory/scripts/search.py',
      'skills/pact-memory/scripts/working_memory.py'),
     'real CLI subprocess and database work'),
    ('tests/test_pin_growth_populations.py',
     ('hooks/pin_caps.py',
      'hooks/pin_caps_gate.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_drift.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/pin_growth.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'pin-growth population matrices'),
    ('tests/test_merge_guard_1140_carrier5_cert.py',
     ('hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'carrier certification matrix over nested compounds'),
    ('tests/test_claude_md_locator_census.py',
     ('hooks/bootstrap_prompt_gate.py',
      'hooks/pin_caps.py',
      'hooks/pin_caps_gate.py',
      'hooks/session_init.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_drift.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/pin_growth.py',
      'hooks/shared/pin_markers.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py',
      'skills/pact-memory/scripts/__init__.py',
      'skills/pact-memory/scripts/cli.py',
      'skills/pact-memory/scripts/config.py',
      'skills/pact-memory/scripts/database.py',
      'skills/pact-memory/scripts/embeddings.py',
      'skills/pact-memory/scripts/memory_api.py',
      'skills/pact-memory/scripts/search.py',
      'skills/pact-memory/scripts/working_memory.py'),
     'whole-tree AST census of shipped CLAUDE.md parse sites; its '
     'true subject is every production module, so an unmapped '
     'production change forces SELECT_ALL'),
    ('tests/test_working_memory_fidelity.py',
     ('hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py',
      'skills/pact-memory/scripts/__init__.py',
      'skills/pact-memory/scripts/cli.py',
      'skills/pact-memory/scripts/config.py',
      'skills/pact-memory/scripts/database.py',
      'skills/pact-memory/scripts/embeddings.py',
      'skills/pact-memory/scripts/memory_api.py',
      'skills/pact-memory/scripts/search.py',
      'skills/pact-memory/scripts/working_memory.py'),
     'working-memory fidelity over many project populations'),
    ('tests/test_merge_guard_perf.py',
     ('hooks/merge_guard_pre.py',
      'hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'parser performance and deep tokenization cases'),
    ('tests/test_claude_md_corpus_writers.py',
     ('hooks/pin_caps.py',
      'hooks/session_init.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/pin_markers.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py',
      'skills/pact-memory/scripts/__init__.py',
      'skills/pact-memory/scripts/cli.py',
      'skills/pact-memory/scripts/config.py',
      'skills/pact-memory/scripts/database.py',
      'skills/pact-memory/scripts/embeddings.py',
      'skills/pact-memory/scripts/memory_api.py',
      'skills/pact-memory/scripts/search.py',
      'skills/pact-memory/scripts/working_memory.py'),
     'large corpus-writer matrix'),
    ('tests/test_bootstrap_prompt_gate_unrecorded_lead.py',
     ('hooks/bootstrap_marker_writer.py',
      'hooks/bootstrap_prompt_gate.py',
      'hooks/pin_caps.py',
      'hooks/session_init.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'subprocess hook runs per case'),
    ('tests/test_merge_guard_fan_out.py',
     ('hooks/merge_guard_post.py',
      'hooks/merge_guard_pre.py',
      'hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'merge-guard fan-out matrix'),
    ('tests/test_merge_guard_retirement_gaps.py',
     ('hooks/merge_guard_post.py',
      'hooks/merge_guard_pre.py',
      'hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'retirement-gap regression matrix'),
    ('tests/test_telegram_tools.py',
     ('telegram/telegram_client.py',
      'telegram/tools.py'),
     'telegram tool rate-limit and reply-queue timing arms'),
    ('tests/test_claude_md_drift.py',
     ('hooks/missed_wake_scan.py',
      'hooks/pin_caps.py',
      'hooks/pin_caps_gate.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_drift.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/pin_growth.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py',
      'hooks/track_files.py',
      'skills/pact-memory/scripts/__init__.py',
      'skills/pact-memory/scripts/cli.py',
      'skills/pact-memory/scripts/config.py',
      'skills/pact-memory/scripts/database.py',
      'skills/pact-memory/scripts/embeddings.py',
      'skills/pact-memory/scripts/memory_api.py',
      'skills/pact-memory/scripts/search.py',
      'skills/pact-memory/scripts/working_memory.py'),
     'clock-shift subprocess matrix over the drift writers'),
    ('tests/test_embedding_model_load_is_cache_first.py',
     ('hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py',
      'skills/pact-memory/scripts/__init__.py',
      'skills/pact-memory/scripts/config.py',
      'skills/pact-memory/scripts/database.py',
      'skills/pact-memory/scripts/embeddings.py',
      'skills/pact-memory/scripts/memory_api.py',
      'skills/pact-memory/scripts/search.py',
      'skills/pact-memory/scripts/working_memory.py'),
     'embedding model load and cache arms'),
    ('tests/test_merge_guard_pr_state_retirement.py',
     ('hooks/merge_guard_post.py',
      'hooks/merge_guard_pre.py',
      'hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'PR-state retirement regression matrix'),
    ('tests/test_archive_pin.py',
     ('hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py',
      'skills/pact-memory/scripts/__init__.py',
      'skills/pact-memory/scripts/cli.py',
      'skills/pact-memory/scripts/config.py',
      'skills/pact-memory/scripts/database.py',
      'skills/pact-memory/scripts/embeddings.py',
      'skills/pact-memory/scripts/memory_api.py',
      'skills/pact-memory/scripts/search.py',
      'skills/pact-memory/scripts/working_memory.py'),
     'archive-pin corpus census over CLAUDE.md fixtures'),
    ('tests/test_clock_shift_instrument.py',
     (),
     'clock-shift instrument subprocess probes'),
    ('tests/test_pin_caps_gate_credit.py',
     ('hooks/missed_wake_scan.py',
      'hooks/pin_caps.py',
      'hooks/pin_caps_gate.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_drift.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/pin_growth.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py',
      'hooks/track_files.py',
      'skills/pact-memory/scripts/__init__.py',
      'skills/pact-memory/scripts/cli.py',
      'skills/pact-memory/scripts/config.py',
      'skills/pact-memory/scripts/database.py',
      'skills/pact-memory/scripts/embeddings.py',
      'skills/pact-memory/scripts/memory_api.py',
      'skills/pact-memory/scripts/search.py',
      'skills/pact-memory/scripts/working_memory.py'),
     'pin-caps credit matrix'),
    ('tests/test_merge_guard_nested_compound.py',
     ('hooks/merge_guard_post.py',
      'hooks/merge_guard_pre.py',
      'hooks/pin_caps.py',
      'hooks/shared/background_work.py',
      'hooks/shared/claude_md_manager.py',
      'hooks/shared/claude_md_markers.py',
      'hooks/shared/constants.py',
      'hooks/shared/gh_helpers.py',
      'hooks/shared/merge_guard_common.py',
      'hooks/shared/pact_context.py',
      'hooks/shared/project_scope.py',
      'hooks/shared/session_journal.py',
      'hooks/shared/session_resume.py',
      'hooks/shared/stale_session.py',
      'hooks/staleness.py'),
     'nested compound detection matrix'),
)


class TestTheShippedManifestIsTheReviewedManifest:
    """WHICH 24 exclusions, their seams, and their named reasons.

    A drift guard, not an adversarial barrier: a PR edits this table and the
    manifest in the same commit, so review remains the real control. Impact
    today is the advisory lane only. Reddens on: a swapped path (missing +
    unexpected), an altered seam tuple, or a changed mechanism.
    """

    def test_the_manifest_identities_and_seams_are_the_reviewed_set(self):
        repo_root, _ = _require_checkout()
        _require_fast_lane(repo_root)
        manifest = json.loads(
            (repo_root / "scripts" / "fast_suite_exclusions.json").read_text(
                encoding="utf-8"))
        actual = {
            entry["path"]: (
                tuple(entry.get("production_seams") or ()),
                entry.get("mechanism"),
            )
            for entry in manifest.get("exclusions") or []
        }
        expected = {
            path: (seams, mechanism)
            for path, seams, mechanism in EXPECTED_EXCLUSIONS
        }
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        changed = []
        for path in sorted(set(expected) & set(actual)):
            expected_seams, expected_mechanism = expected[path]
            actual_seams, actual_mechanism = actual[path]
            if actual_seams != expected_seams:
                changed.append(
                    f"{path}: production_seams {actual_seams!r} != "
                    f"{expected_seams!r}")
            if actual_mechanism != expected_mechanism:
                changed.append(
                    f"{path}: mechanism {actual_mechanism!r} != "
                    f"{expected_mechanism!r}")
        assert not missing and not unexpected and not changed, (
            "THE MANIFEST IS NOT THE REVIEWED MANIFEST.\n"
            f"  missing paths: {missing}\n"
            f"  unexpected paths: {unexpected}\n"
            f"  changed entries:\n"
            + "\n".join(f"    {line}" for line in changed)
            + "\nEvery exclusion, its production_seams tuple and its "
            "mechanism are reviewed data. If the change is intended, update "
            "EXPECTED_EXCLUSIONS in the same commit and say why."
        )


def _load_selector(repo_root):
    """The selection core, loaded by path from the checkout under test."""
    path = repo_root / "scripts" / "fast_suite_selection.py"
    assert path.is_file(), f"{path} is missing; the fast lane is partial"
    spec = importlib.util.spec_from_file_location(
        "_pact_fast_suite_selection_contract", path)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: dataclass annotations resolve through
    # sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


_SHIPPED = {}


def _shipped_manifest():
    """(repo_root, core, manifest, index), built once per session.

    The scan index parses every module in every exclusion's closure; the
    contracts below share one copy instead of each paying for it.
    """
    repo_root, _ = _require_checkout()
    _require_fast_lane(repo_root)
    key = str(repo_root)
    if key not in _SHIPPED:
        core = _load_selector(repo_root)
        manifest = core.load_manifest(
            repo_root / "scripts" / "fast_suite_exclusions.json")
        index = core.ScanIndex(repo_root / "pact-plugin")
        _SHIPPED[key] = (repo_root, core, manifest, index)
    return _SHIPPED[key]


def _select_real(changed, value_inert=()):
    """select() over the real tree for `changed`, with real closure views."""
    repo_root, core, manifest, index = _shipped_manifest()
    facts = core.gather_facts(repo_root, manifest, changed, None, index=index)
    if value_inert:
        facts = dataclasses.replace(facts, value_inert=frozenset(value_inert))
    return core.select(core.discover(repo_root), manifest, changed, "ok",
                       facts)


def _closure_modules():
    """(exclusion, module, FileScan) for every module of every closure."""
    _, _, manifest, index = _shipped_manifest()
    rows = []
    for exclusion in manifest.exclusions:
        for rel in sorted(index.view(exclusion.path).closure):
            rows.append((exclusion.path, rel, index.scan(rel)))
    assert len({row[0] for row in rows}) == len(manifest.exclusions), (
        "SCAN FAILURE: some exclusion has an empty closure.")
    return rows


class TestTheSeamListsCoverEveryExclusionsImportClosure:
    """A changed seam must add back EVERY exclusion that depends on it.

    The seam lists are hand-written data; the import closure is computed. A
    known seam that an exclusion reaches but does not list is a change that
    leaves a dependent test excluded. Reddens when a test or a production
    module gains an import of another exclusion's seam.
    """

    def test_every_known_seam_an_exclusion_reaches_is_one_of_its_seams(self):
        _, _, manifest, index = _shipped_manifest()
        known = {seam for exclusion in manifest.exclusions
                 for seam in exclusion.production_seams}
        assert known, "the manifest maps no seams; the check would be vacuous"
        gaps = []
        reached_any = False
        for exclusion in manifest.exclusions:
            closure = index.closure([exclusion.path])
            # Non-vacuity: the closure starts at the module itself.
            assert exclusion.path in closure, (
                f"CLOSURE FAILURE, not a manifest regression: "
                f"{exclusion.path} is absent from its own closure.")
            reached = closure & known
            reached_any = reached_any or bool(reached)
            missing = sorted(reached - set(exclusion.production_seams))
            if missing:
                gaps.append(f"{exclusion.path}: {missing}")
        assert reached_any, (
            "CLOSURE FAILURE: no exclusion reaches any known seam, so the "
            "import resolution found nothing.")
        assert not gaps, (
            "These exclusions reach a known seam they do not list, so a "
            "change to that seam leaves them excluded:\n  "
            + "\n  ".join(gaps)
            + "\nAdd each seam to that exclusion's production_seams and to "
            "EXPECTED_EXCLUSIONS."
        )

    def test_a_gh_helpers_change_adds_back_the_retirement_gaps_test(self):
        """Behavioural proof: a gh_helpers mutant fails this excluded test."""
        selection = _select_real(["pact-plugin/hooks/shared/gh_helpers.py"])
        assert selection.select_all_reason is None, selection.select_all_reason
        added = {entry.path for entry in selection.added_back}
        assert "tests/test_merge_guard_retirement_gaps.py" in added, added


class TestTheVersionBumpCannotReachAnExcludedTest:
    """A version-only plugin.json change is ignored (value_inert_keys).

    Safe while no excluded test can compare against a pinned version, so no
    module in any exclusion's closure may hold a version-shaped literal.
    """

    def test_no_closure_module_holds_a_version_literal(self):
        _, core, _, _ = _shipped_manifest()
        hits = sorted({f"{rel}: {literal!r}"
                       for _, rel, scan in _closure_modules()
                       for literal in scan.literals
                       if core._SEMVER_RE.fullmatch(literal)})
        assert not hits, (
            "A module an excluded test loads holds a version literal, so a "
            "version bump could change its result:\n  " + "\n  ".join(hits)
            + "\nRemove 'version' from value_inert_keys or the literal.")

    def test_the_detector_sees_a_version_literal(self, tmp_path):
        """Non-vacuity: the same scan and pattern fire on a planted literal."""
        repo_root, core, _, _ = _shipped_manifest()
        plugin = tmp_path / "pact-plugin"
        (plugin / "tests").mkdir(parents=True)
        (plugin / "tests" / "test_pinned.py").write_text(
            'EXPECTED = "4.8.18"\n', encoding="utf-8")
        scan = core.ScanIndex(plugin).scan("tests/test_pinned.py")
        assert any(core._SEMVER_RE.fullmatch(literal)
                   for literal in scan.literals)

    def test_a_version_only_bump_adds_nothing_back(self):
        path = "pact-plugin/.claude-plugin/plugin.json"
        selection = _select_real([path], value_inert=[path])
        assert selection.select_all_reason is None
        assert selection.added_back == ()

    def test_any_other_plugin_json_change_reaches_its_readers(self):
        """Control: plugin.json is read (actor_identity, bootstrap_gate)."""
        selection = _select_real(["pact-plugin/.claude-plugin/plugin.json"])
        assert selection.select_all_reason is None
        assert selection.added_back, "no reader of plugin.json was found"


class TestTheClosureCanSeeEveryDependency:
    """The add-back rules see imports, fixture names and listed directories
    statically. These are the dependency forms they cannot see; none may
    appear in a module an excluded test loads."""

    def test_no_closure_module_imports_by_a_computed_name(self):
        hits = sorted({f"{rel}: {where}" for _, rel, scan in _closure_modules()
                       for where in scan.dynamic_imports})
        assert not hits, (
            "Computed imports hide a dependency from the closure:\n  "
            + "\n  ".join(hits))

    def test_no_closure_module_requests_a_fixture_by_a_computed_name(self):
        hits = sorted({f"{rel}: {where}" for _, rel, scan in _closure_modules()
                       for where in scan.dynamic_fixtures})
        assert not hits, (
            "A computed getfixturevalue hides a conftest dependency:\n  "
            + "\n  ".join(hits))


class TestScanningExclusionsAreAddedBackForTheirTrees:
    """An excluded test that scans the real checkout comes back when any
    file under a scanned directory changes. The scanning set is DETECTED from
    each closure (a scan call anchored to __file__), never written down."""

    def _scanners(self):
        _, _, manifest, index = _shipped_manifest()
        return {exclusion.path: index.view(exclusion.path).scan_roots
                for exclusion in manifest.exclusions
                if index.view(exclusion.path).scan_roots}

    def test_every_anchored_scan_yields_roots(self):
        empty = sorted({rel for _, rel, scan in _closure_modules()
                        if scan.scanner_roots is not None
                        and not scan.scanner_roots})
        assert not empty, f"an anchored scan with no root: {empty}"

    def test_the_detector_finds_the_known_tree_scanners(self):
        """Positive controls, computed by the detector itself."""
        scanners = self._scanners()
        census = scanners.get("tests/test_claude_md_locator_census.py", set())
        mint = scanners.get("tests/test_merge_guard_refused_is_mintable.py",
                            set())
        assert {"hooks", "telegram"} <= census, sorted(census)
        assert "tests" in mint, sorted(mint)

    def test_every_detected_root_adds_its_exclusion_back(self):
        misses = []
        for path, roots in sorted(self._scanners().items()):
            for root in sorted(roots):
                probe = "zz_probe.txt" if root == "." else f"{root}/zz_probe.txt"
                selection = _select_real([f"pact-plugin/{probe}"])
                if path not in {entry.path for entry in selection.added_back}:
                    misses.append(f"{path}: root {root}")
        assert not misses, "\n".join(misses)

    @pytest.mark.parametrize("changed", [
        "pact-plugin/hooks/track_files.py",
        "pact-plugin/hooks/merge_guard_pre.py",
    ])
    def test_a_hook_change_adds_back_the_locator_census(self, changed):
        selection = _select_real([changed])
        reasons = {entry.path: entry.reason for entry in selection.added_back}
        assert "tests/test_claude_md_locator_census.py" in reasons, reasons

    @pytest.mark.parametrize("scanner", [
        "tests/test_clock_shift_instrument.py",
        "tests/test_embedding_model_load_is_cache_first.py",
    ])
    def test_a_new_hook_file_adds_back_the_whole_plugin_scanners(self, scanner):
        """Both rglob the whole plugin from Path(__file__).parents[1]. A hook
        that starts naming the census exemption variable fails the
        clock-shift guard, so any hooks/*.py change must bring them back."""
        selection = _select_real(["pact-plugin/hooks/names_the_exemption.py"])
        reasons = {entry.path: entry.reason for entry in selection.added_back}
        # A broad exclusion comes back as unproven_reach before the scanner
        # rule is consulted; either reason brings it back.
        assert reasons.get(scanner) in ("scanned_tree", "unproven_reach"), reasons

    def test_an_unmapped_hook_reaches_the_census_through_the_scanner(self):
        """track_files.py is in no census seam list: only the scanner rule
        can add it."""
        selection = _select_real(["pact-plugin/hooks/track_files.py"])
        reasons = {entry.path: entry.reason for entry in selection.added_back}
        assert reasons.get(
            "tests/test_claude_md_locator_census.py") == "scanned_tree"

    def test_a_changed_test_module_adds_back_the_mint_matrix(self):
        """refused_is_mintable rglobs tests/ for test modules."""
        selection = _select_real(["pact-plugin/tests/test_import_hygiene.py"])
        assert selection.select_all_reason is None
        reasons = {entry.path: entry.reason for entry in selection.added_back}
        assert reasons.get(
            "tests/test_merge_guard_refused_is_mintable.py") == "scanned_tree"

    def test_the_plugin_readme_adds_back_only_broad_and_whole_tree_scanners(
            self):
        """No excluded test reads pact-plugin/README.md by name. Only broad
        exclusions (back on any non-trio change) and confined scanners of the
        whole plugin can see it."""
        repo_root, core, manifest, index = _shipped_manifest()
        selection = _select_real(["pact-plugin/README.md"])
        assert selection.select_all_reason is None
        facts = core.gather_facts(repo_root, manifest, [], None, index=index)
        broad = {exclusion.path for exclusion in manifest.exclusions
                 if core.broad_reasons(index.view(exclusion.path), facts)}
        whole = {path for path, roots in self._scanners().items()
                 if "." in roots}
        reasons = {entry.path: entry.reason for entry in selection.added_back}
        assert set(reasons) == broad | whole
        assert all(reasons[path] == "unproven_reach" for path in broad)


class TestProcessGlobalConftestFixturesSelectEverything:
    """A change to a process-global autouse fixture in tests/conftest.py
    reaches every test, so it must select everything, never a subset."""

    @pytest.mark.parametrize("fixture", [
        "_isolate_merge_token_dir_to_tmp",
        "_refuse_claude_md_writes_outside_tmp",
        "_isolate_memory_store_to_tmp",
    ])
    def test_a_change_to_the_fixture_selects_all(self, fixture):
        repo_root, core, manifest, index = _shipped_manifest()
        conftest = repo_root / "pact-plugin" / "tests" / "conftest.py"
        source = conftest.read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            (item for item in tree.body
             if isinstance(item, ast.FunctionDef) and item.name == fixture),
            None)
        assert node is not None, f"{fixture} is no longer in {conftest}"
        lines = source.splitlines(keepends=True)
        changed = "".join(lines[:node.end_lineno]) + (
            "    _probe_change = None\n") + "".join(lines[node.end_lineno:])
        verdict = core.classify_conftest_change(
            source, changed, True,
            resolve=lambda dotted: core._production_files(dotted, index))
        assert verdict.select_all == f"autouse-unscoped:{fixture}", verdict
        facts = core.ChangeFacts(
            views={exclusion.path: index.view(exclusion.path)
                   for exclusion in manifest.exclusions},
            value_inert=frozenset(),
            conftest_verdicts={"tests/conftest.py": verdict},
            conftest_imported=frozenset())
        selection = core.select(core.discover(repo_root), manifest,
                                ["pact-plugin/tests/conftest.py"], "ok", facts)
        assert selection.select_all_reason == (
            f"conftest:tests/conftest.py:autouse-unscoped:{fixture}")


class TestClosedByDefaultReach:
    """Contracts for the confined/broad split over the real exclusions."""

    def _split(self):
        repo_root, core, manifest, index = _shipped_manifest()
        facts = core.gather_facts(repo_root, manifest, [], None, index=index)
        return core, manifest, index, {
            exclusion.path: core.broad_reasons(index.view(exclusion.path),
                                               facts)
            for exclusion in manifest.exclusions}

    def test_the_split_is_not_vacuous(self):
        """C9: computed, not listed: some exclusion of each kind exists."""
        _, _, _, split = self._split()
        assert any(split.values()) and not all(split.values()), split

    def test_the_bump_trio_carve_out_holds_for_every_exclusion(self):
        """C4: the trio is ignored for an exclusion only while no literal in
        its test-side closure names a trio file. Pinned to hold today."""
        _, manifest, index, _ = self._split()
        named = [exclusion.path for exclusion in manifest.exclusions
                 if index.view(exclusion.path).trio_named]
        assert named == [], named

    def test_a_trio_only_change_adds_back_only_broad_scanners(self):
        """The trio is ignored for a broad exclusion only while nothing in
        its closure names a trio file or scans a tree; a confined one never
        reads outside the plugin."""
        _, manifest, index, split = self._split()
        selection = _select_real(["CHANGELOG.md", "README.md",
                                  ".claude-plugin/marketplace.json"])
        assert selection.select_all_reason is None
        expected = {path for path, reasons in split.items() if reasons and (
            index.view(path).trio_named or index.view(path).scans_tree)}
        assert {entry.path for entry in selection.added_back} == expected

    def test_an_outside_change_adds_back_exactly_the_broad_exclusions(self):
        _, _, _, split = self._split()
        selection = _select_real([".github/zz_probe.yml"])
        assert {entry.path for entry in selection.added_back} == {
            path for path, reasons in split.items() if reasons}

    def test_every_review_is_current_and_used(self):
        """Reports stale and unused reviews. Safety does not depend on it:
        a stale review is already ignored at runtime."""
        repo_root, core, manifest, index = _shipped_manifest()
        reviewed, _ = core.current_reviews(index.plugin_root, manifest)
        stale = sorted(module for module, *_ in manifest.reach_reviews
                       if module not in reviewed)
        unproven_anywhere = {
            module for exclusion in manifest.exclusions
            for module, _ in index.view(exclusion.path).unproven}
        unproven_anywhere |= {key.split("::", 1)[0] for key in unproven_anywhere}
        unused = sorted(key for key, *_ in manifest.reach_reviews
                        if key not in unproven_anywhere)
        assert not stale, f"reviews whose module changed since review: {stale}"
        assert not unused, f"reviews of modules nothing flags: {unused}"

    def test_every_review_clears_an_exclusion(self):
        """A review that turns no exclusion from broad to confined vouches
        for nothing the selection uses: drop it rather than keep it."""
        repo_root, core, manifest, index = _shipped_manifest()
        reviewed, reads = core.current_reviews(index.plugin_root, manifest)

        def confined(reviews):
            facts = core.ChangeFacts(
                views={}, value_inert=frozenset(), conftest_verdicts={},
                conftest_imported=frozenset(), reviewed=frozenset(reviews),
                review_reads=reads)
            return {exclusion.path for exclusion in manifest.exclusions
                    if not core.broad_reasons(index.view(exclusion.path),
                                              facts)}

        everything = confined(reviewed)
        idle = sorted(key for key, *_ in manifest.reach_reviews
                      if confined(reviewed - {key}) == everything)
        assert manifest.reach_reviews, "no reviews: the check read nothing"
        assert not idle, f"reviews that clear no exclusion: {idle}"

    def test_a_review_covers_only_the_bytes_it_hashed(self, tmp_path):
        """A genuinely new unproven site still turns its exclusion broad.

        The reviews are safe only because `current_reviews` hashes the bytes:
        a review whose module changed is ignored, so the module is unproven
        again. This arm pins both halves — the hash binding, and that the
        confinement rules still FLAG a new unproven site rather than having
        been swallowed by the review.
        """
        _, core, manifest, index = _shipped_manifest()
        plugin = index.plugin_root
        module = "tests/test_claude_md_locator_census.py"

        # 1. A new unproven site is still flagged by the rules.
        source = (plugin / module).read_text(encoding="utf-8")
        original = tmp_path / "original" / module
        mutated = tmp_path / "mutated" / module
        for path, text in (
                (original, source),
                (mutated, source + "\n\ndef test_zz_new_unproven_site():\n"
                                   "    subprocess.run(['true'])\n")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        before = set(core._scan_file(
            original, index.plugin_root, index.roots,
            index.is_tracked_dir).unproven)
        after = set(core._scan_file(
            mutated, index.plugin_root, index.roots,
            index.is_tracked_dir).unproven)
        new_sites = sorted(after - before)
        assert any("subprocess.run" in site for site in new_sites), new_sites

        # 2. That same review entry no longer matches those bytes, so the
        # exclusion is broad instead of reachable as `scanned_tree`.
        stale = dataclasses.replace(
            manifest,
            reach_reviews=tuple(
                (key, "0" * 64 if key == module else sha, reason, extra)
                for key, sha, reason, extra in manifest.reach_reviews))
        reviewed_stale, reads_stale = core.current_reviews(plugin, stale)
        assert module not in reviewed_stale, "a stale hash still reviewed the module"
        facts = core.ChangeFacts(
            views={}, value_inert=frozenset(), conftest_verdicts={},
            conftest_imported=frozenset(),
            reviewed=frozenset(reviewed_stale),
            review_reads=reads_stale)
        assert core.broad_reasons(index.view(module), facts), (
            "an unreviewed unproven module did not turn the exclusion broad")

    PINNED_ISOLATION = (
        "_isolate_config_root_to_tmp", "_scrub_claude_project_dir_env",
        "_scrub_claude_code_remote_env", "_scrub_claude_plugin_root_env",
        "_scrub_claude_env_file_env", "_scrub_session_id_from_test_env",
        "_isolate_claude_md_target", "_isolate_merge_token_dir_to_tmp",
        "_refuse_claude_md_writes_outside_tmp", "_isolate_memory_store_to_tmp",
        "_confine_agent_definition_scan")

    def test_the_conftest_isolation_fixtures_stay_autouse(self):
        """C5: confinement trusts these to redirect HOME, the config root,
        CLAUDE_PROJECT_DIR and the session env to tmp for every test. Their
        CHANGE already selects everything as process-global; this pins that
        they exist and stay autouse."""
        repo_root, core, _, _ = _shipped_manifest()
        conftest = repo_root / "pact-plugin" / "tests" / "conftest.py"
        spans = core._fixture_spans(ast.parse(conftest.read_text(
            encoding="utf-8")))
        required = self.PINNED_ISOLATION
        missing = [name for name in required if name not in spans]
        not_autouse = [name for name in required
                       if name in spans and not spans[name][2]]
        assert not missing and not not_autouse, (missing, not_autouse)
        # The session-wide HOME redirect at import time.
        assert 'os.environ["HOME"] = _SESSION_HOME_DIR' in conftest.read_text(
            encoding="utf-8")

    def test_the_repository_root_finder_is_unproven(self):
        """The repo's own marker walk (for base in Path(__file__).parents)
        cannot be proven confined."""
        _, _, index, _ = self._split()
        sites = index.scan("tests/test_local_lint_parity.py").unproven
        assert sites, "the marker walk in test_local_lint_parity passed"

    def test_conftest_sys_path_entries_are_import_roots(self):
        repo_root, core, _, index = _shipped_manifest()
        plugin_root = repo_root / "pact-plugin"
        entries = core._conftest_path_entries(plugin_root.resolve())
        assert "scripts" in entries, entries
        assert {(plugin_root / entry).resolve() for entry in entries} <= set(
            index.roots)
        assert "scripts/archive_pin.py" in index.closure(
            ["tests/test_archive_pin.py"])

    def test_every_autouse_fixture_is_pinned_or_confined(self):
        """Autouse fixtures are exempt from the confinement rules only
        because the isolation set is pinned by name. Every other autouse
        fixture must come out confined under the rules."""
        repo_root, core, _, index = _shipped_manifest()
        pinned = set(self.PINNED_ISOLATION)
        unpinned_unproven = []
        examined = 0
        for conftest in ("conftest.py", "tests/conftest.py"):
            path = repo_root / "pact-plugin" / conftest
            tree = ast.parse(path.read_text(encoding="utf-8"))
            reasons = core._test_side_reasons(
                tree, conftest, index.roots, index.plugin_root,
                core._docstring_nodes(tree), {})
            for name, (first, last, autouse) in core._fixture_spans(
                    tree).items():
                if not autouse or name in pinned:
                    continue
                examined += 1
                sites = [reason for reason in reasons
                         if first <= int(reason.split(":", 1)[0]) <= last]
                if sites:
                    unpinned_unproven.append(f"{conftest}::{name}: {sites[:2]}")
        # The rule must have something to judge, or it proves nothing.
        assert examined > 0, "no unpinned autouse fixture was examined"
        assert not unpinned_unproven, unpinned_unproven

