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
    """Everything after the `pytest` token, or None when it cannot be found."""
    words = command.split()
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
    words = command.split()
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
         "-p", "no:cacheprovider", *args],
        cwd=str(cwd), capture_output=True, text=True,
    )
    ids = {line.strip() for line in result.stdout.splitlines() if "::" in line}
    return ids, result


class TestCiInvocationCollectsTheWholeCorpus:
    """The CI pytest step must reach every test root, through either door."""

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
      'hooks/track_files.py'),
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
      'hooks/track_files.py'),
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


def _shipped_manifest():
    repo_root, _ = _require_checkout()
    _require_fast_lane(repo_root)
    core = _load_selector(repo_root)
    manifest = core.load_manifest(
        repo_root / "scripts" / "fast_suite_exclusions.json")
    return repo_root, core, manifest


class TestTheSeamListsCoverEveryExclusionsImportClosure:
    """A changed seam must add back EVERY exclusion that depends on it.

    The seam lists are hand-written data; the import closure is computed. A
    known seam that an exclusion reaches but does not list is a change that
    leaves a dependent test excluded. Reddens when a test or a production
    module gains an import of another exclusion's seam.
    """

    def test_every_known_seam_an_exclusion_reaches_is_one_of_its_seams(self):
        repo_root, core, manifest = _shipped_manifest()
        plugin_root = repo_root / "pact-plugin"
        known = {seam for exclusion in manifest.exclusions
                 for seam in exclusion.production_seams}
        assert known, "the manifest maps no seams; the check would be vacuous"
        gaps = []
        reached_any = False
        for exclusion in manifest.exclusions:
            closure = core.import_closure(plugin_root, [exclusion.path])
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
        repo_root, core, manifest = _shipped_manifest()
        selection = core.select(
            core.discover(repo_root), manifest,
            ["pact-plugin/hooks/shared/gh_helpers.py"], "ok")
        assert selection.select_all_reason is None, selection.select_all_reason
        added = {entry.path for entry in selection.added_back}
        assert "tests/test_merge_guard_retirement_gaps.py" in added, added


def _string_literals(path):
    """Every string constant in a module, f-string fragments included."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


class TestNoExcludedTestReadsAnInertPath:
    """An inert path neither selects everything nor adds anything back.

    That is safe only while no excluded test can read it, so this scans every
    module in every exclusion's import closure for a string naming an inert
    path, by repo path, plugin path or bare filename. Reddens when an
    excluded test, or a module it imports, starts naming one.
    """

    def test_no_closure_module_names_an_inert_path(self):
        repo_root, core, manifest = _shipped_manifest()
        plugin_root = repo_root / "pact-plugin"
        assert manifest.inert_paths, (
            "the manifest lists no inert paths; this contract has no subject")
        tokens = {}
        for inert in manifest.inert_paths:
            names = {inert, Path(inert).name}
            plugin_relative = core.repo_to_plugin(inert)
            if plugin_relative:
                names.add(plugin_relative)
            tokens[inert] = names
        hits = []
        scanned = 0
        for exclusion in manifest.exclusions:
            for rel in sorted(core.import_closure(plugin_root, [exclusion.path])):
                literals = _string_literals(plugin_root / rel)
                assert literals is not None, (
                    f"SCAN FAILURE: {rel} in the closure of {exclusion.path} "
                    f"did not parse, so it was not scanned.")
                scanned += 1
                for inert, names in tokens.items():
                    for literal in literals:
                        if any(name in literal for name in names):
                            hits.append(
                                f"{exclusion.path} -> {rel}: "
                                f"{literal[:80]!r} names {inert}")
        assert scanned >= len(manifest.exclusions), (
            "SCAN FAILURE: fewer modules scanned than there are exclusions.")
        assert not hits, (
            "An excluded test can read an inert path, so a change to it must "
            "not leave that test excluded:\n  " + "\n  ".join(sorted(set(hits)))
            + "\nRemove the path from inert_paths."
        )

    def test_the_scan_sees_a_literal_naming_an_inert_path(self, tmp_path):
        """Non-vacuity: the token match fires on the spelling it looks for."""
        probe = tmp_path / "probe.py"
        probe.write_text(
            'MANIFEST = Path(root) / ".claude-plugin" / "plugin.json"\n'
            'DOC = f"{root}/README.md"\n', encoding="utf-8")
        literals = _string_literals(probe)
        assert any("plugin.json" in literal for literal in literals)
        assert any("README.md" in literal for literal in literals)
