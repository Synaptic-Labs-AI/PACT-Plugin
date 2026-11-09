"""The canary checklist states a check count that an instrument reads.

WHY THIS ARM IS THE REPAIR AND THE CORRECTED NUMBER IS NOT. The checklist said
the extract gate performs 16 checks. It performs 19. Correcting the digit fixes
today and repairs nothing: A PROSE CLAIM THAT NO INSTRUMENT READS GOES STALE IN
SILENCE, and the silence is the defect. Somebody adds a call to the script, no
test reads the checklist, and a reviewer follows a number that describes a
script from before.

THE POPULATION COMES FROM WHAT THE SCRIPT DID, NOT FROM ITS SOURCE TEXT, AND
THAT CHOICE IS THE LOAD-BEARING ONE. The obvious rule is to count the `verify`
call sites with a regular expression over the script. THAT RULE IS DEFECTIVE
AND IT WAS MEASURED DEFECTIVE ON THIS BRANCH, in two directions that a reader
of the rule cannot see:
  1. A line at COLUMN ZERO inside an ordinary heredoc, for example a usage
     message that shows a sample invocation, is counted as a call. The script
     is correct, and the count is one too high.
  2. Changing the first argument of each call from double to single quotes is
     identical to bash and makes the rule match NOTHING. The count falls to
     zero, which reads as a broken harness rather than as a defect in the rule.
  3. THE LARGEST OF THE THREE, AND IT IS RECORDED WITH ITS NUMBERS BECAUSE A
     READER WHO MEETS ONLY THE FIRST TWO CAN CONCLUDE THE SOURCE-TEXT RULE IS
     NEARLY CORRECT. It is not nearly correct. The counter increments at 5
     source sites in `verify-task-hierarchy.sh` and at 10 in
     `verify-worktree-protocol.sh`, and those two scripts emit 28 and 20. The
     increments sit inside helpers called many times, so a source-text count is
     wrong by more than five times on the first script and by two times on the
     second. Cases 1 and 2 are off by one and by everything. THIS ONE IS OFF BY
     A FACTOR, AND IT IS THE ORDINARY CASE RATHER THAN AN EDGE.
A shell script is not reliably parsed by a line-oriented pattern. NEITHER a
line at column zero, NOR a quote style, NOR a helper called many times can
change what a script EMITS when it runs, so the run is the sound source.

THE TWO SIDES ARE INDEPENDENT, WHICH IS WHAT MAKES THE COMPARISON EVIDENCE. One
side is a document a person maintains by hand. The other is the behaviour of a
script at run time. They can disagree, and the whole point is that they DO
disagree when somebody edits one alone. Nothing here derives both sides from
one source, which would make the comparison a tautology that always holds.

WHY `Passed + Failed` AND NOT `Passed`. The sum is the number of comparisons
the script performed. A genuinely failing extract moves a unit from one column
to the other and leaves the sum alone, so this arm stays on its own subject: it
counts CHECKS, and a byte difference between an extract and its section is the
subject of the extract-gate arms in `test_audit_protocol.py`.

SCOPE, STATED BECAUSE THE CHECKLIST CARRIES FOUR SUCH ROWS. ALL FOUR ARE ARMED
IN THIS MODULE, and the three classes below split on the RULE each row states,
not on the script it names:
  - THE EXTRACT-GATE ROW states a count plus a POSITIVE rule that gives the
    count, and that rule names a population in the SCRIPT SOURCE. First class.
  - THE SCOPE-INTEGRITY ROW states a count plus a FORMULA across a population
    on the FILESYSTEM, so its number moves and a third source is necessary.
    Second class.
  - THE TASK-HIERARCHY AND WORKTREE-PROTOCOL ROWS state a count plus a NEGATIVE
    DECLARATION OF FIXEDNESS. The rule names NO population. It asserts that no
    population is available, so no third source is necessary and the cheaper
    two-source comparison is sound. Third class, parametrized across the two.
A reader who expects one shape for each row will look for four classes and find
three. The count of classes follows the count of RULE SHAPES.

THE AXIS THESE ARMS CANNOT SEE. They count comparisons, not what each
comparison compares. A call that names the wrong heading pair is counted here
and caught by the byte comparison inside the script, which the exit-code arm in
`test_audit_protocol.py` reports.
"""
import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_CHECKLIST = _REPO_ROOT / "testing" / "canary-checklist.md"
_SCRIPT = _REPO_ROOT / "scripts" / "verify-protocol-extracts.sh"
_SCOPE_SCRIPT = _REPO_ROOT / "scripts" / "verify-scope-integrity.sh"
_AGENTS_DIR = _REPO_ROOT / "pact-plugin" / "agents"

# The checklist row: a bullet naming the script, then its count in brackets.
_CHECKLIST_COUNT_RE = re.compile(
    r"`verify-protocol-extracts\.sh`\s+passes\s+\((\d+)\s+checks\)"
)

# The scope-integrity row states a RULE, not only a number. Four quantities are
# parsed out of it. Each pattern is anchored on wording that carries the
# quantity's MEANING, so a reworded row fails the non-vacuity assertions below
# LOUDLY rather than matching a digit that means something else.
_SCOPE_FIXED_RE = re.compile(r"It is (\d+) fixed checks")
_SCOPE_SKIP_RE = re.compile(
    r"skips the (\d+) agents named in the `case` statement"
)
_SCOPE_STATED_DIR_RE = re.compile(r"that directory holds (\d+) files")
_SCOPE_FORMULA_RE = re.compile(r"(\d+) \+ (\d+) \+ (\d+) = (\d+)")
_SCOPE_STATED_TOTAL_RE = re.compile(
    r"`verify-scope-integrity\.sh`\s+passes\s+\((\d+)\s+checks\)"
)


class TestCanaryChecklistCheckCount:
    """The stated number of checks must agree with the checks the gate runs."""

    def _stated_count(self):
        if not _CHECKLIST.exists():
            pytest.skip("testing/canary-checklist.md not present")
        text = _CHECKLIST.read_text(encoding="utf-8")
        match = _CHECKLIST_COUNT_RE.search(text)
        # NON-VACUITY ON THE PROSE SIDE. A row that was reworded out of this
        # shape gives `None`, and a comparison against `None` is not a check.
        # This fails LOUDLY rather than skipping, because a silent skip here
        # returns the file to the unread state this arm exists to end.
        assert match, (
            "no check count parsed from the canary checklist row for "
            "verify-protocol-extracts.sh. The row was reworded, and the count "
            "it states is now unread again."
        )
        return int(match.group(1))

    def _checks_the_gate_ran(self):
        """Return the number of comparisons the script performed.

        The summary line is the emitted evidence. A parse of it depends on the
        summary FORMAT, and a format change makes the assertion below fire
        rather than the count assertion, which is the safe direction and is a
        dependency a reader should know about.
        """
        if not _SCRIPT.exists():
            pytest.skip("scripts/verify-protocol-extracts.sh not present")
        result = subprocess.run(
            ["bash", str(_SCRIPT)], cwd=str(_REPO_ROOT),
            capture_output=True, text=True,
        )
        passed = re.search(r"^Passed:\s*(\d+)$", result.stdout, re.MULTILINE)
        failed = re.search(r"^Failed:\s*(\d+)$", result.stdout, re.MULTILINE)
        assert passed and failed, (
            "the script printed no Passed/Failed summary, so this arm has no "
            f"number to read.\nstdout:\n{result.stdout}"
        )
        return int(passed.group(1)) + int(failed.group(1))

    def test_control_the_gate_runs_and_compares_a_non_empty_population(self):
        """THE CONTROL, and it validates the INSTRUMENT rather than the claim.

        A gate that performed ZERO comparisons reports `Passed: 0, Failed: 0`
        and exits 0. Against an empty population the count comparison below
        would hold for any checklist number that happened to read 0, and a
        deleted script would skip. This arm asserts the gate did work.
        """
        assert self._checks_the_gate_ran() > 0, (
            "the extract gate performed no comparison, so the count arm below "
            "would describe an empty population"
        )

    def test_the_checklist_count_agrees_with_the_gate_it_describes(self):
        """The ruled property: the prose number equals the checks that ran.

        WHAT THIS PROVES AND WHAT IT DOES NOT. It proves the two sides agree.
        It does NOT prove that a reviewer who follows the checklist reaches a
        correct outcome, because no instrument drove a reviewer.
        """
        stated = self._stated_count()
        ran = self._checks_the_gate_ran()
        assert ran > 0, "the extract gate performed no comparison"

        assert stated == ran, (
            f"testing/canary-checklist.md states {stated} checks for "
            f"verify-protocol-extracts.sh and the script ran {ran}.\n"
            "One of the two moved without the other. Correct the checklist "
            "row, or restore the check the script lost."
        )


class TestCanaryChecklistScopeRowFormula:
    """The scope-integrity row states a FORMULA, and a run must satisfy it.

    WHY THIS ROW COULD NOT BE ARMED THE WAY THE ROW ABOVE IS. That row states a
    fixed number, so a run alone settles it. This one states a count that MOVES
    with the number of files in `pact-plugin/agents/`, so a fixed number would
    be incorrect the next time somebody adds an agent, and nothing would say so.

    THREE INDEPENDENT SOURCES, AND NO TWO OF THEM READ FROM ONE PLACE. That
    independence is the whole argument, because two shapes that look like this
    arm are UNSOUND and were rejected before it was written:
      SOURCE A, THE THING UNDER TEST: the checklist's stated RULE, which is
        hand-maintained prose. Two quantities come from it, the fixed-check
        count and the size of the skip set. Nothing derives them.
      SOURCE B: the number of `*.md` files in `pact-plugin/agents/`, read from
        the filesystem at run time.
      SOURCE C: the comparisons `verify-scope-integrity.sh` performed, read out
        of its own summary block after a real run.
    The arm predicts C from A and B, and compares. A never reads the script and
    never reads the directory. C never reads the checklist. So the two sides of
    the comparison have no common ancestor and CAN disagree.

    THE TWO UNSOUND SHAPES, RECORDED SO NEITHER IS RE-PROPOSED AS A SIMPLER
    ALTERNATIVE:
      1. Deriving the formula from the SCRIPT SOURCE puts both sides of the
         comparison on one source, which is a tautology that always holds. It
         is also defective for the second cause the module docstring records: a
         shell script is not reliably parsed by a line-oriented pattern.
      2. Hardcoding the fixed-check count in this file mints a STALE TWIN. The
         number would then live in two places and a later editor would correct
         one of them.

    WHAT THIS ARM DELIBERATELY DOES NOT ASSERT, AND THE REASON IS AN OVER-BLOCK
    IT WOULD OTHERWISE BECOME. The row's bracketed total and its "that directory
    holds N files" are a WORKED EXAMPLE, a snapshot of one day. The row itself
    tells a reader who sees a different number to count the agent files before
    reporting a defect. If this arm compared the stated TOTAL against the run,
    then ADDING AN AGENT FILE would redden it, and the arm would fight correct
    growth rather than catch a defect. So the snapshot numbers are checked only
    for internal arithmetic, by the separate arm at the end of this class, and
    the two hand-maintained quantities are the ones checked against the world.

    THE SENSITIVITY THIS ARM CARRIES ON PURPOSE. The predicted term subtracts
    the STATED skip count from the REAL directory count, which assumes each
    skipped agent file is present in that directory. Removing one of the skipped
    agent files reddens this arm. That is the correct direction: the stated skip
    claim would then describe a set the directory no longer holds. Reading the
    `case` statement instead would remove the sensitivity and reintroduce
    unsound shape 1, so the sensitivity is kept and stated rather than fixed.
    """

    def _checklist_text(self):
        if not _CHECKLIST.exists():
            pytest.skip("testing/canary-checklist.md not present")
        return _CHECKLIST.read_text(encoding="utf-8")

    def _stated_rule(self):
        """Return the two hand-maintained quantities the row states.

        NON-VACUITY ON THE PROSE SIDE. A row reworded out of either shape gives
        `None`, and a comparison against `None` is not a check. Each failure is
        LOUD rather than a skip, because a silent skip returns the row to the
        unread state this arm exists to end.
        """
        text = self._checklist_text()
        fixed = _SCOPE_FIXED_RE.search(text)
        skip = _SCOPE_SKIP_RE.search(text)
        assert fixed, (
            "no fixed-check count parsed from the canary checklist row for "
            "verify-scope-integrity.sh. The row was reworded, and the count "
            "it states is now unread again."
        )
        assert skip, (
            "no skip-set size parsed from the canary checklist row for "
            "verify-scope-integrity.sh. The row was reworded, and the skip "
            "count it states is now unread again."
        )
        return int(fixed.group(1)), int(skip.group(1))

    def _agent_file_count(self):
        if not _AGENTS_DIR.is_dir():
            pytest.skip("pact-plugin/agents/ not present")
        return len(list(_AGENTS_DIR.glob("*.md")))

    def _checks_the_scope_gate_ran(self):
        """Return the number of comparisons `verify-scope-integrity.sh` made.

        `Passed + Failed` and not `Passed`, for the reason the module docstring
        gives: a genuinely failing check moves a unit between the two columns
        and leaves the sum alone, which keeps this arm on its own subject.
        """
        if not _SCOPE_SCRIPT.exists():
            pytest.skip("scripts/verify-scope-integrity.sh not present")
        result = subprocess.run(
            ["bash", str(_SCOPE_SCRIPT)], cwd=str(_REPO_ROOT),
            capture_output=True, text=True,
        )
        passed = re.search(r"^Passed:\s*(\d+)$", result.stdout, re.MULTILINE)
        failed = re.search(r"^Failed:\s*(\d+)$", result.stdout, re.MULTILINE)
        assert passed and failed, (
            "the scope-integrity script printed no Passed/Failed summary, so "
            f"this arm has no number to read.\nstdout:\n{result.stdout}"
        )
        return int(passed.group(1)) + int(failed.group(1))

    def test_control_the_scope_gate_runs_and_reads_a_non_empty_directory(self):
        """THE CONTROL, and it validates the two INSTRUMENTS, not the claim.

        Against an empty agent directory the formula below collapses to the
        fixed count, and against zero comparisons the prediction would describe
        an empty population. Either state makes the arm below meaningless while
        it can still report a pass, so both are asserted here.
        """
        assert self._checks_the_scope_gate_ran() > 0, (
            "the scope-integrity gate performed no comparison, so the "
            "prediction below would describe an empty population"
        )
        assert self._agent_file_count() > 0, (
            "pact-plugin/agents/ holds no .md file, so the directory term of "
            "the formula below carries no information"
        )

    def test_the_stated_rule_predicts_the_checks_the_scope_gate_ran(self):
        """The ruled property: the row's RULE predicts what the script did.

        WHAT THIS PROVES AND WHAT IT DOES NOT. It proves the hand-maintained
        rule still describes the script. It does NOT prove the row's stated
        total is current, which is deliberate and is explained in the class
        docstring.
        """
        fixed, skip = self._stated_rule()
        agents = self._agent_file_count()
        ran = self._checks_the_scope_gate_ran()

        # The row's rule: fixed checks, plus a nesting-limit loop over the
        # agent files that are not skipped, plus a `memory: user` loop over
        # every agent file.
        predicted = fixed + (agents - skip) + agents

        assert predicted == ran, (
            f"testing/canary-checklist.md states a rule that predicts "
            f"{predicted} checks for verify-scope-integrity.sh "
            f"({fixed} fixed + ({agents} agent files - {skip} skipped) + "
            f"{agents} agent files) and the script ran {ran}.\n"
            "The stated rule and the script disagree. Either a check was "
            "added to or removed from the script, or the skip set changed, "
            "or the row's fixed-check count went stale. Adding an agent file "
            "alone does NOT cause this failure, because the rule tracks the "
            "directory."
        )

    def test_the_worked_example_in_the_row_is_internally_consistent(self):
        """A DIFFERENT SUBJECT: the row's own arithmetic, not the script.

        This arm reads NOTHING outside the checklist. It exists because the
        worked example is what a reviewer copies when the numbers disagree, and
        a garbled example sends that reviewer to an incorrect expected value.
        A stale example is NOT the subject: the numbers here are allowed to
        describe an earlier directory, so long as they agree with each other.
        """
        text = self._checklist_text()
        fixed, skip = self._stated_rule()
        stated_dir = _SCOPE_STATED_DIR_RE.search(text)
        formula = _SCOPE_FORMULA_RE.search(text)
        stated_total = _SCOPE_STATED_TOTAL_RE.search(text)
        assert stated_dir and formula and stated_total, (
            "the worked example in the verify-scope-integrity.sh row lost one "
            "of its three parts (the file count, the three-term sum, or the "
            "bracketed total). The example is now unreadable to this arm."
        )

        directory = int(stated_dir.group(1))
        term_fixed, term_loop, term_dir, term_total = (
            int(g) for g in formula.groups()
        )
        total = int(stated_total.group(1))

        assert (term_fixed, term_loop, term_dir) == (
            fixed, directory - skip, directory
        ), (
            f"the three-term sum in the row reads "
            f"{term_fixed} + {term_loop} + {term_dir}, and the prose around "
            f"it states {fixed} fixed checks, a skip set of {skip}, and "
            f"{directory} agent files, which give "
            f"{fixed} + {directory - skip} + {directory}.\n"
            "The worked example contradicts the rule stated beside it."
        )
        assert term_fixed + term_loop + term_dir == term_total == total, (
            f"the row's sum {term_fixed} + {term_loop} + {term_dir} gives "
            f"{term_fixed + term_loop + term_dir}, the sum states "
            f"{term_total}, and the bracketed total states {total}.\n"
            "The worked example does not add up."
        )


class TestCanaryChecklistFixedCountRows:
    """The two rows that DECLARE their count fixed, checked against a run.

    THE RULE THESE TWO ROWS STATE IS A THIRD SHAPE, AND NAMING IT IS WHAT MAKES
    THE CHEAP COMPARISON SOUND. Each row states a count and then declares "FIXED
    count: no check comes from the contents of a directory". That is not the
    positive rule the extract-gate row states, and it is not the formula the
    scope-integrity row states. IT NAMES NO POPULATION AT ALL. It asserts that
    none is available. A number that its own rule says does not move needs no
    third source, so these two rows take the two-source comparison: the stated
    count against `Passed + Failed` from a real run.

    THE DECLARATION WAS VERIFIED BY RUN, NOT BY READING THE ROW. An unverified
    absence is the weakest claim a document can carry, so it was driven: a
    command file and a skill directory were added in an isolated copy of the
    tree, and the two scripts emitted 28 and 20 both before and after. Neither
    count tracks a directory. A static read agreed, and the run is what settles
    it.

    WHAT THIS CLASS DOES NOT CHECK, AND THE LIMIT IS DELIBERATE. It checks THE
    NUMBER. It does NOT check the negative declaration itself. To check the
    declaration means searching the script source for a glob, which is the
    source-text instrument the module docstring records as defective in three
    directions, and a false negative there is SILENT. A silent miss on a guard
    is worse than a documented gap, so the gap is documented here and the guard
    is not built.

    THAT LIMIT HAS A CONSEQUENCE FOR THE READER, AND THE FAILURE MESSAGE CARRIES
    IT. If a script later gains a directory-derived population, this arm reds,
    which is correct. But the row it sends the reader to says the count is
    fixed, and that sentence is incorrect at that moment. A reader who trusts
    the row diagnoses "a check was lost" and hunts for a deletion that never
    happened. So the message names BOTH causes and lets the reader take the
    fork. It cannot decide between them, and it does not pretend to.

    THESE ARMS CANNOT FLAKE, AND THAT WAS MEASURED RATHER THAN ASSUMED. Neither
    script reads a clock, sleeps, uses a random value, or calls the network.
    Neither writes to disk, so parallel execution cannot corrupt the input of
    another test. The working directory is passed explicitly. Measured run
    times are about 0.32 s and 0.08 s. The one way these arms red without a
    defect is a concurrent edit to a file the scripts read, which is a true
    report about the tree rather than an unstable test.
    """

    # (script filename, the wording that anchors the row's count).
    # Each pattern is anchored on the script NAME plus the word `passes`, so a
    # digit somewhere else in the row cannot be read as this count.
    FIXED_COUNT_ROWS = [
        ("verify-task-hierarchy.sh",
         r"`verify-task-hierarchy\.sh`\s+passes\s+\((\d+)\s+checks\)"),
        ("verify-worktree-protocol.sh",
         r"`verify-worktree-protocol\.sh`\s+passes\s+\((\d+)\s+checks\)"),
    ]

    def _stated_count(self, pattern, script_name):
        if not _CHECKLIST.exists():
            pytest.skip("testing/canary-checklist.md not present")
        text = _CHECKLIST.read_text(encoding="utf-8")
        match = re.search(pattern, text)
        # NON-VACUITY ON THE PROSE SIDE. A row reworded out of this shape gives
        # `None`, and a comparison against `None` is not a check. This fails
        # LOUDLY rather than skipping, because a silent skip returns the row to
        # the unread state this arm exists to end.
        assert match, (
            f"no check count parsed from the canary checklist row for "
            f"{script_name}. The row was reworded, and the count it states is "
            f"now unread again."
        )
        return int(match.group(1))

    def _checks_the_gate_ran(self, script_name):
        """Return the comparisons the named script performed.

        `Passed + Failed` and not `Passed`, for the cause the module docstring
        gives: a genuinely failing check moves a unit between the two columns
        and leaves the sum alone, which keeps this arm on its own subject.
        """
        script = _REPO_ROOT / "scripts" / script_name
        if not script.exists():
            pytest.skip(f"scripts/{script_name} not present")
        result = subprocess.run(
            ["bash", str(script)], cwd=str(_REPO_ROOT),
            capture_output=True, text=True,
        )
        passed = re.search(r"^Passed:\s*(\d+)$", result.stdout, re.MULTILINE)
        failed = re.search(r"^Failed:\s*(\d+)$", result.stdout, re.MULTILINE)
        assert passed and failed, (
            f"{script_name} printed no Passed/Failed summary, so this arm has "
            f"no number to read.\nstdout:\n{result.stdout}"
        )
        return int(passed.group(1)) + int(failed.group(1))

    @pytest.mark.parametrize("script_name,pattern", FIXED_COUNT_ROWS)
    def test_control_the_gate_ran_a_non_empty_population(
        self, script_name, pattern
    ):
        """THE CONTROL, and it validates the INSTRUMENT rather than the claim.

        A script that performed ZERO comparisons prints `Passed: 0, Failed: 0`
        and exits 0. Against an empty population the comparison below holds for
        any row that happens to state 0, and a deleted script skips. This arm
        asserts the script did work.
        """
        assert self._checks_the_gate_ran(script_name) > 0, (
            f"{script_name} performed no comparison, so the count arm below "
            f"would describe an empty population"
        )

    @pytest.mark.parametrize("script_name,pattern", FIXED_COUNT_ROWS)
    def test_the_stated_count_agrees_with_the_gate_it_describes(
        self, script_name, pattern
    ):
        """The ruled property: the stated number equals the checks that ran."""
        stated = self._stated_count(pattern, script_name)
        ran = self._checks_the_gate_ran(script_name)
        assert ran > 0, f"{script_name} performed no comparison"

        assert stated == ran, (
            f"testing/canary-checklist.md states {stated} checks for "
            f"{script_name} and the script ran {ran}.\n"
            "TWO CAUSES PRODUCE THIS, AND THE ROW CANNOT TELL YOU WHICH. "
            "Separate them before you repair anything:\n"
            "  (a) A CHECK WAS LOST OR GAINED. The count is still fixed and "
            "the row's digit is stale. Correct the digit, or restore the "
            "check the script lost.\n"
            "  (b) THE ROW'S FIXEDNESS DECLARATION IS NO LONGER TRUE. The "
            "script gained a population it derives from the contents of a "
            "directory, so the count now MOVES and the row's rule describes a "
            "script from before. Correcting the digit alone leaves the row "
            "incorrect again at the next directory change. Give the row a "
            "formula, as the scope-integrity row carries, and arm it with the "
            "three-source shape rather than this two-source one.\n"
            "Read the script for a loop across a directory. That is what "
            "separates (a) from (b), and no arm here can do it for you."
        )


class TestCanaryTreeAttributionContract:
    """The suite row consumes every after-run attribution stamp."""

    @staticmethod
    def _text():
        if not _CHECKLIST.exists():
            pytest.skip("testing/canary-checklist.md not present")
        return _CHECKLIST.read_text(encoding="utf-8")

    def test_all_after_stamps_are_named(self):
        text = self._text()
        for stamp in (
            "GATE-HEAD-AFTER",
            "GATE-DIRTY-PATHS-AFTER",
            "GATE-TREE-ATTRIBUTION",
        ):
            assert f"`{stamp}`" in text, f"canary lost {stamp}"

    def test_after_head_must_match_both_external_sha_and_before_head(self):
        text = self._text()
        assert "after-HEAD must equal both `GATE-HEAD` and the externally recorded PR SHA" in text

    def test_stable_requires_full_porcelain_equality_not_count_equality(self):
        text = self._text()
        assert "`GATE-TREE-ATTRIBUTION`** is `stable`" in text
        assert "complete before/after `git status --porcelain` text is identical" in text
        assert "not merely that the two dirty counts match" in text

    def test_exit_stamp_is_last_and_green_movement_is_not_a_pass(self):
        text = self._text()
        assert "`GATE-PYTEST-EXIT`** is `0` and is the last stamp" in text
        assert "after all three after-run stamps" in text
        assert "A green pytest run whose tree moved returns 1" in text
        assert "detached clean worktree at the named SHA" in text
