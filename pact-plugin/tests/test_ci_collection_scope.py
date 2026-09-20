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
green of exactly the genus the whole file is about. Convention followed from
``test_memory_init.py``, which parses the same workflow for its install line.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

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


class TestMatrixAndProseAgreeOnTheVersionSet:
    """The matrix and the prose naming its check names must desync together."""

    def test_check_names_passage_enumerates_exactly_the_matrix_versions(self):
        """Keep the check-names passage equal to the matrix it describes.

        Editing `python-version` means editing the passage that names the
        reported checks, and the reverse. Both operands are read from the
        workflow, so neither is a literal this module owns.

        Extraction is over the WHOLE file, never per line: the enumeration
        spans a line break, so a line-scoped read sees a subset of it and
        reports a mismatch that is an artifact of wrapping.
        """
        _, workflow_text = _require_checkout()

        declared = re.search(r"python-version:\s*\[([^\]]*)\]", workflow_text)
        assert declared is not None, (
            "no `python-version: [...]` literal list found in the workflow. "
            "The matrix side of this comparison is missing, so the check "
            "below would compare against nothing. Report this as a parse "
            "failure, not as agreement."
        )
        matrix = set(re.findall(r"[\"']([^\"']+)[\"']", declared.group(1)))

        reported = set(re.findall(r"pytest \(([^)]+)\)", workflow_text))
        assert reported, (
            "the workflow names no `pytest (<version>)` check anywhere. That "
            "passage is the prose side of this comparison; with it gone the "
            "set equality below would hold vacuously against an empty read. "
            "Either restore the enumeration or replace this arm with one "
            "asserting no enumeration exists."
        )

        assert matrix == reported, (
            f"THE MATRIX AND THE CHECK-NAMES PROSE DISAGREE.\n"
            f"  python-version declares: {sorted(matrix)}\n"
            f"  prose reports as checks: {sorted(reported)}\n"
            f"  only in the matrix: {sorted(matrix - reported)}\n"
            f"  only in the prose:  {sorted(reported - matrix)}\n"
            f"A matrix job is named `pytest (<value>)` per cell, so these two "
            f"sets are the same set by construction. Update whichever side "
            f"you did not just edit."
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
