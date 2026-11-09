"""
Presence pins for the import-bar cause in the memory-repair package.

Location: pact-plugin/tests/test_import_bar_cause_presence.py

WHAT THIS PROVES, AND IT IS NARROW ON PURPOSE. FOUR PROPERTIES, and the
heading below states what each one leaves open. Two are a PRESENCE, one is a
POPULATION RULE, and one is an ABSENCE.

  1. PRESENCE. Each module in the memory-repair package that states the import
     bar also carries the sentence that forbids an audit of which call is safe.
     That sentence is the only one that stops a reader who treats the bar as a
     DISTANCE to be measured rather than a rule to keep.
  2. PRESENCE. The package `__init__.py` carries the obligation that binds a
     later editor.
  3. POPULATION RULE. Each .py file in the package states the bar or takes a
     declared exemption, and each carrier known today stays in the derived
     population.
  4. ABSENCE. No file in the plugin tree carries one of the refuted spellings,
     apart from a declared exclusion.

WHAT A GREEN HERE DOES NOT MEAN. Read this before you trust it.

  * It does NOT check that the surrounding prose is true. No test can read a
    natural-language cause for truth.
  * It does NOT catch an incorrect cause in NEW WORDS. The refuted-spelling
    arms hold FIXED SPELLINGS, so they catch a revert, a bad merge, or an
    editor who restores a removed sentence from memory. A paraphrase passes.
    SOME ENTRIES SHIPPED AND SOME DID NOT, and the rule above
    `REFUTED_SPELLINGS` states the test that decides membership.
  * THE REFUTED-SPELLING ARMS REACH THE PLUGIN TREE AND NO FURTHER. They walk
    `PLUGIN_DIR`, so a refuted spelling written above that directory, at the
    repository root, is outside the scan.
  * A NEW TRACKED FILE THAT SAYS NOTHING IS REACHED INSIDE THE PACKAGE, AND NOT
    OUTSIDE IT. The population rule fails on a new tracked .py in the package that does
    not state the bar. The same file elsewhere in the tree is invisible here.

THE POPULATION HAS THREE PARTS, AND EACH COVERS A FAILURE THE OTHERS MISS.

  1. The DERIVATION reaches a carrier written later, on the day it arrives.
  2. `MINIMUM_CARRIERS` refuses SHRINKAGE for the carriers known today. It
     catches TWO conditions that no other arm catches: a carrier renamed out
     of the population AND declared exempt, and a carrier file deleted. A
     plain rename is NOT one of them, because part 3 catches that on its own.
  3. `EXEMPT_MODULES` extends part 2 to files nobody has written yet, which
     fixed paths cannot reach. AGAINST A RENAME, a module leaves the
     must-state-the-bar rule only by a declared line, and not by a rename that
     reads as tidying. A DELETION is a second exit, and part 2 covers it for
     the paths it holds.

EACH SET IN THIS FILE IS KEYED ON A RELATIVE PATH AND NOT ON A BASENAME. A
basename is not an identity: `__init__.py` occurs 9 times in the plugin tree,
so a basename key cannot separate a top-level file from one in a subdirectory,
and the most likely new file carries the most common basename.

An arm that asserts ONE member proves the set is not empty and proves nothing
about the rest. Non-emptiness and completeness are different properties.

THE FAILURE DIRECTION IS DELIBERATE. A legitimate reword trips these tests,
so they fail toward a FALSE ALARM and somebody must look. The pin to avoid is
the one that goes stale in silence. This is its opposite.

TO CHANGE ANY CONSTANT IN THIS FILE, STATE A CAUSE. The rule is stated over
the CLASS and not over a list of names, on purpose. Each module-level constant
here sets what the arms require, or which files the arms cover, so each one
weakens the guard if it changes for no reason. A list of names would go stale
the moment a constant is added, and a reader who trusts the list then learns a
rule narrower than the one that must hold.

If you update a constant to agree with new prose, or to quiet a red, and you
give no cause, this file becomes a MIRROR of the thing it guards, and its
green stops meaning anything.

MATCHING IGNORES WHITESPACE AND CASE, AND `carries` DOES THE TWO TOGETHER.
WHITESPACE: each guarded sentence wraps across lines in the file that carries
it, so a line-oriented search or a literal comparison cannot find it even when
it is present. Flatten first. CASE: this repository capitalises an
anti-permission rule for emphasis, so a strict comparison reddens on a
legitimate re-capitalisation. The reason for each sits above `carries`.

Used by: pytest.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "memory_repair"

# The anti-permission clause. Its absence is the defect this file exists for.
REQUIRED_CAUSE_CLAUSE = (
    "This rule holds so that nobody must audit which call is safe."
)

# The obligation that reaches an edit to an EXISTING module, not new ones only.
# THE CAUSE FOR THE CURRENT WORDING: the earlier form ended "the same way",
# which pointed back at a description naming two files of the fourteen in the
# guarded package. A reader could import a third module and stay inside the
# letter of the rule. The obligation now names the property it wants, so it
# leaves no population-shaped anchor for a successor phrase to point at.
REQUIRED_PACKAGE_OBLIGATION = (
    "Keep each module here, and each module added later, free of an import "
    "from that package."
)

# A module "states the import bar" when it names the guarded package. This
# derives the population from the property, so a third carrier file added
# later is covered on the day it arrives.
BAR_SUBJECT = "pact-memory scripts package"

# THE TWO SETS BELOW ARE KEYED ON POSIX PATHS RELATIVE TO `PACKAGE_DIR`, which
# is a DIFFERENT root from the one `SPELLING_EXCLUSIONS` uses. This file holds
# two roots, so each key set states the root it uses. A relative path without
# its base is a figure without its counting rule. A key written against the
# wrong root matches nothing, so the file loses its place in the set and an arm
# reddens, which is the safe direction.
#
# THE FLOOR. Its unique coverage is TWO conditions, and `TestTheFloorEarnsIts
# Place` below asserts each one by emptying this set and reading the result.
#   1. A carrier renamed out of the population AND declared exempt. The
#      exempt arm then passes, because the path is declared, and only this
#      floor holds.
#   2. A carrier file deleted. It leaves the disk and the population together,
#      so the exempt arm sees nothing missing.
# A PLAIN RENAME IS NOT UNIQUE TO THIS FLOOR. The exempt arm catches that on
# its own, measured with this set emptied.
#
# To remove a path from this floor, state a cause, as for the two sentence
# constants above. A path removed to quiet a red turns the floor into a report
# of the current population, which is the same mirror failure.
MINIMUM_CARRIERS = frozenset({"__init__.py", "shred_detect.py"})

# MODULES THAT DO NOT HAVE TO STATE THE BAR. Keys are relative to
# `PACKAGE_DIR`, as stated above `MINIMUM_CARRIERS`. Empty today, and that is
# the point: each .py file in this package must state the bar, or appear here.
#
# AN ENTRY HERE LIFTS ONE RULE AND NOT THE OTHER, AND THIS FILE USES THE WORD
# POPULATION FOR TWO SETS. An entry removes the file from the MUST-STATE-THE-BAR
# rule above. It does NOT remove the file from the DERIVED POPULATION, which is
# the set of files that DO state the bar and which
# `modules_missing_the_cause_clause` reads. So an exempt file that states the
# bar is under the cause-clause rule as before.
#
# The floor above protects the carriers known when it was written. It cannot
# protect a carrier written later, because its paths are fixed. This closes
# that gap from the other side, AGAINST A RENAME: a file leaves the
# must-state-the-bar rule by an entry here, which is a visible line in a diff,
# rather than by a rename of the subject phrase, which reads as tidying and is
# what defeated the derivation. DELETION IS A SECOND EXIT AND IT TAKES NO ENTRY.
# `MINIMUM_CARRIERS` covers a deletion for the paths it holds. A carrier added
# later and then deleted is covered by nothing here.
#
# To add a path here, state a cause. A path added to quiet a red is the mirror
# failure again. A KNOWN CARRIER CANNOT ESCAPE THIS WAY, AND THE REASON IS NOT
# THAT AN EXEMPTION ALONE GOES RED. An exemption alone changes nothing, because
# the file continues to state the bar and stays in the derived population. The
# escape asks for an exemption AND a rename together, and `MINIMUM_CARRIERS`
# reads the derived population, so it goes red on that pair.
EXEMPT_MODULES: frozenset = frozenset()

# The tree the refuted-spelling arms walk. A FILESYSTEM WALK and NOT a git
# query, on purpose: outside a git context, in a packaged install or a clean
# export, a git file listing returns nothing, the population is empty, and a
# negative arm reports a clean green over no files at all. That is a fail-open
# in the arm widened to close a fail-open.
PLUGIN_DIR = Path(__file__).resolve().parent.parent

SCANNED_SUFFIXES = (".py", ".md")

# Each claim below was measured incorrect and removed. A revert, a bad merge,
# or an editor who restores a removed sentence from memory reproduces the
# ORIGINAL BYTES, which is the condition this catches.
#
# 🔴 THE TEST FOR MEMBERSHIP, THREE PARTS, ALL MEASURED RATHER THAN ASSUMED.
# A spelling joins this set when the three hold TOGETHER:
#   1. THE CLAIM IS REFUTED AND SETTLED. A claim that an OPEN measurement can
#      move does not qualify.
#   2. THE RETURN OF THE SPELLING IS A LIVE RISK.
#   3. BANNING THE SPELLING BANS NO TRUE SENTENCE. Measure this. A zero-hit
#      search means nothing unless a control string that IS present returns
#      hits in the same walk.
#
# WHETHER THE SPELLING SHIPPED IS NOT THE TEST, and a reader who applies that
# rule will remove a correct member. Two entries here were drafted and removed
# BEFORE release. A criterion that excludes a member the set holds is not the
# criterion.
#
# PART 1 IS THE ONE THAT REFUSES AN ENTRY MOST OFTEN, and it refuses on a
# TIMING ground rather than a truth ground. While a measurement that could
# confirm a claim stays open, that claim cannot join, because A LIST THAT BANS
# A POSSIBLY-TRUE SENTENCE GETS DISABLED BY THE FIRST PERSON IT OBSTRUCTS, and
# the whole guard goes with it. Wait for the measurement. Then decide.
REFUTED_SPELLINGS = frozenset(
    {
        "an import is a write",
        "EXECUTES code that creates the live store",
        "runs code that creates the live store",
        # The refuted content is the word "before", which promises the package
        # becomes safe later. The bare noun phrase is TRUE in a package-wide
        # statement, so a wider entry bans a correct sentence.
        "before that package is safe to import",
        # The second corrected claim. It was written outside the memory-repair
        # package, which is why the population above covers the plugin tree.
        "an in-process HOME change is inert",
        # THE PERMISSIVE `--db-path` FRAMING. It was drafted, then removed
        # because it PERMITS the flag. The bar is a POLICY: an agent does not
        # select a store, and a path a caller chooses is not the store the
        # memory of the team lives in.
        #
        # 🔴 DO NOT RESTATE A MECHANISM HERE, AND DO NOT CORRECT ONE BACK IN.
        # This comment held a mechanism sentence, that the flag does not reach
        # the readiness path, and a measurement REFUTED it.
        #
        # NO TRUE MECHANISM SENTENCE IS AVAILABLE TO WRITE IN ITS PLACE, and
        # that is a MEASURED result rather than a caution. The verb sweep is
        # complete. The flag ISOLATES save, update and delete, each measured
        # with a live control. The remaining four verbs are UNMEASURED, because
        # their control is inert, so they are not clear and they are not
        # refuted. A sentence about the whole flag is therefore incorrect for
        # one part of the surface and unsupported for the rest.
        # A POLICY CAUSE HAS NO SUCH SURFACE, so no measurement can move it.
        #
        # WHY THE CAUSE MATTERS AS MUCH AS THE ENTRY. A guard justified by a
        # claim the repair removed is the defect this work closes, arriving in
        # the enforcement. A reader who probes an incorrect cause concludes the
        # entry is unfounded, and that is one probe from a disabled entry.
        #
        # THE REFUTED CONTENT IS THE WORD "only", which turns a description into
        # a PERMISSION. That is the same reasoning this file applies to the word
        # "before" in the entry above, so the entry is narrow for the same cause.
        # A wider entry such as "is for test isolation" would ban a TRUE future
        # sentence about the test-harness variable.
        #
        # MEASURED before it was added, across 519 .py and .md files in the
        # plugin tree: this spelling and two wider candidates each returned 0
        # FILES, so the entry bans no sentence that is present. A control
        # string that IS present returned 3 files in the same walk, so the zero
        # is a measured absence and not an empty read. This is PREVENTIVE, and
        # the warning above against an entry added to quiet a red does not
        # apply, because nothing is red.
        #
        # 🔴 THE ZERO ABOVE WAS TAKEN BEFORE THIS COMMENT EXISTED. A re-run
        # finds each of the three candidates INSIDE THIS FILE, because this
        # comment names them. NO OCCURRENCE OF ONE OF THEM SITS OUTSIDE THIS
        # FILE. That is the claim to check.
        #
        # STATE THE RULE AND NOT THE COUNT. A count breaks the moment a later
        # note names one of the phrases again, so the act of confirmation can
        # falsify the record it confirms. The rule above survives its own
        # confirmation, because one more self-reference leaves it true.
        #
        # THE CLASS, and it reaches past this comment. A CLAIM ABOUT A CORPUS
        # THAT THE CLAIM ITSELF JOINS MUST BE STATED SO THAT JOINING THE
        # CORPUS DOES NOT FALSIFY IT. An unreproducible correct claim and an
        # incorrect claim look the same to the next reader.
        "for test isolation only",
        # THE REFUTED CALIBRATION DEMAND. The secretary instruction asked the
        # store for a mean drift direction. The store cannot answer: the
        # `memories` table holds each calibration as prose, with no numeric
        # column, so no tool change closes the gap and the DEMAND had to go.
        #
        # WHY THIS SPELLING AND NOT A WIDER ONE. A wider entry would ban the
        # NAME of the statistic rather than the DEMAND for it, and an
        # instruction that forbids a statistic must be free to name the thing
        # it forbids. So a name-only entry bans correct sentences as readily as
        # incorrect ones. The spelling here carries the DEMAND, which no
        # correct sentence needs.
        #
        # 🔴 THE REASON ABOVE IS INTRINSIC TO THE SPELLING, AND THAT IS
        # DELIBERATE. An earlier wording rested on what another file says
        # today. A justification that names another file's CURRENT CONTENT
        # goes stale the moment that file changes, with no arm to report it,
        # and the comment then argues for the entry from a fact that is no
        # longer true. State a reason the spelling itself carries.
        #
        # ONE ENTRY COVERS THE REVERT. The two impossible demands, the mean and
        # the per-domain breakdown, shipped in ONE sentence, so a revert
        # restores the two together and this entry catches it. A second entry
        # would help only against a partial restore, and it would grow the list
        # past its purpose.
        #
        # MEASURED before it was added, across 519 .py and .md files: 0 hits
        # for this spelling and 0 for `summarize by domain`. A control string
        # that IS present returned 3 hits in the same walk, so the zero is a
        # measured absence and not an empty read.
        "mean drift direction",
    }
)

# DECLARED EXCLUSIONS, PER FILE AND PER SPELLING.
#
# KEYS ARE POSIX PATHS RELATIVE TO `PLUGIN_DIR`. The root is named here on
# purpose: A RELATIVE PATH WITHOUT ITS BASE IS A FIGURE WITHOUT ITS COUNTING
# RULE, and a reader who cannot tell which root a key uses cannot tell whether
# the key is correct. A key against the wrong root matches nothing, so the file
# loses its exclusion and reddens, which is the safe direction.
#
# WHY PER SPELLING RATHER THAN PER FILE. A document that QUOTES one refuted
# claim in order to explain the correction is legitimate prose, and this
# repository writes it. A whole-file exclusion would buy that document silence
# on the other four claims as well.
#
# THIS IS AN ENUMERATION, so it takes the same state-a-cause discipline as the
# other constants and it goes stale the same way. THE TWO DIRECTIONS ARE NOT
# SYMMETRIC: a stale key fails SAFE, because the file loses its exclusion and
# reddens. AN UNNECESSARY ENTRY FAILS OPEN, because the file keeps its silence
# on that one spelling and no arm reddens. SO THE MAINTENANCE BURDEN IS ON
# REMOVAL RATHER THAN ON ADDITION, which inverts the usual instinct.
# `test_each_exclusion_is_still_necessary` turns that fail-open into a red.
#
# Do NOT add an entry to quiet a red, and do NOT trim REFUTED_SPELLINGS to
# quiet a red. Either turns this arm into a mirror of the text that happens to
# be present, which is the failure this file warns about.
SPELLING_EXCLUSIONS: dict = {
    # This file holds each refuted spelling as a literal, so it is a GENUINE
    # MEMBER of the scanned population and must be named out. Compare
    # `SCANNED_SUFFIXES`, which excludes a `.pyc` by a correct predicate rather
    # than by a name: that is a NON-member and needs no entry here. A carve-out
    # for a non-member is the sign of a predicate aimed wrong.
    "tests/test_import_bar_cause_presence.py": REFUTED_SPELLINGS,
}


def files_scanned_for_refuted_spellings() -> list[Path]:
    """Return the plugin-tree files the refuted-spelling arms read.

    THIS WALK RETURNS EVERY FILE AND FILTERS NOTHING. The per-spelling
    exclusion runs at the arm instead. `test_each_exclusion_is_still_necessary`
    DEPENDS ON THAT PLACEMENT: it asks whether an exclusion key is in this
    population, and a key excluded HERE would fail that test while doing its
    job correctly. Move the filter into this walk and that arm breaks without
    anybody editing it.
    """
    found: list[Path] = []
    for suffix in SCANNED_SUFFIXES:
        found.extend(PLUGIN_DIR.rglob(f"*{suffix}"))
    return sorted(found)


def spelling_is_excluded_for(path: Path, spelling: str) -> bool:
    """True when `spelling` is declared out for `path` alone."""
    return spelling in SPELLING_EXCLUSIONS.get(relative_name(path, PLUGIN_DIR), ())


def flatten(text: str) -> str:
    """Collapse all whitespace runs to single spaces.

    A guarded sentence wraps across lines, so it is not a contiguous substring
    of any single line. This is what makes the comparison find it.
    """
    return re.sub(r"\s+", " ", text)


def carries(text: str, sentence: str) -> bool:
    """True when `text` holds `sentence`, apart from wraps and capitalisation.

    ONE COMPARISON FOR EACH SENTENCE CHECK IN THIS FILE, so no two arms can
    drift into opposite treatments of the same input.

    WHITESPACE: each guarded sentence wraps in the file that carries it, so a
    literal comparison misses a sentence that is present.

    CASE: this repository capitalises an anti-permission rule for emphasis. A
    strict comparison reddens on a legitimate re-capitalisation. That is a
    false alarm, the cheapest cure for a false alarm is a constant edit, and a
    constant edit with no cause is the mirror failure this file warns about.
    A strict comparison also FAILS OPEN in the other direction: a file that
    drops out of the population by a capitalisation change takes its required
    sentence out of reach in silence. Both directions point one way.
    """
    return sentence.casefold() in flatten(text).casefold()


def _tracked_package_python_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(PACKAGE_DIR.parents[2]), "ls-files", "-z", "--",
         "pact-plugin/scripts/memory_repair"], capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(f"git ls-files failed for {PACKAGE_DIR}: {result.stderr!r}")
    paths = [PACKAGE_DIR.parents[2] / raw.decode("utf-8")
             for raw in result.stdout.split(b"\0") if raw]
    return sorted(path for path in paths if path.suffix == ".py" and path.is_file())


def _python_files(package_dir: Path) -> list[Path]:
    if package_dir.resolve() == PACKAGE_DIR:
        return _tracked_package_python_files()
    return sorted(package_dir.rglob("*.py"))


def modules_stating_the_bar(package_dir: Path) -> list[Path]:
    """Return each tracked or synthetic .py file naming the guarded package."""
    return sorted(
        path
        for path in _python_files(package_dir)
        if carries(path.read_text(encoding="utf-8"), BAR_SUBJECT)
    )


def relative_name(path: Path, root: Path) -> str:
    """Return `path` as a POSIX path relative to `root`.

    A BASENAME IS NOT AN IDENTITY. `__init__.py` occurs 9 times in the plugin
    tree, so a set keyed on basenames cannot tell one from another: a silent
    `helpers/__init__.py` subtracts against the top-level entry and escapes,
    while the same silence at a unique basename reddens. The most likely new
    file in a package carries the most common basename, so a basename key
    misses the likely instance and catches the unlikely one.
    """
    return path.relative_to(root).as_posix()


# ---------------------------------------------------------------------------
# THE THREE POPULATION PREDICATES.
#
# ONE DEFINITION EACH, CALLED BY THE SHIPPED ARM AND BY THE FLOOR-CLASS MODEL.
# The model used to re-implement them, and this file was edited twice in one
# afternoon with the change carried into the model by hand each time. Both
# hand-carries were correct, which is what made the drift invisible rather
# than harmless. A shared definition makes the drift UNREPRESENTABLE, so the
# next edit cannot be carried incorrectly because it cannot be carried at all.
#
# Each returns a sorted list of relative paths, so an empty list means the
# property holds and a non-empty list names the files that break it.
# ---------------------------------------------------------------------------


def carriers_missing_from_the_floor(package_dir: Path, floor: frozenset) -> list[str]:
    """Known carriers that left the derived population."""
    present = {
        relative_name(p, package_dir) for p in modules_stating_the_bar(package_dir)
    }
    return sorted(floor - present)


def modules_neither_stating_nor_exempt(
    package_dir: Path, exempt: frozenset
) -> list[str]:
    """Files on disk that do not state the bar and are not declared exempt."""
    on_disk = {relative_name(p, package_dir) for p in _python_files(package_dir)}
    stating = {
        relative_name(p, package_dir) for p in modules_stating_the_bar(package_dir)
    }
    return sorted(on_disk - stating - exempt)


def modules_missing_the_cause_clause(package_dir: Path) -> list[str]:
    """Carriers that state the bar and omit the required cause clause."""
    return sorted(
        relative_name(p, package_dir)
        for p in modules_stating_the_bar(package_dir)
        if not carries(p.read_text(encoding="utf-8"), REQUIRED_CAUSE_CLAUSE)
    )


class TestThePopulationRules:
    """The two rules that decide WHICH files the sentence arms must cover.

    These are rules and not controls: each one can fail because the tree is
    wrong, rather than because the instrument is wrong. The controls sit in
    `TestTheInstrumentIsLive` below.
    """

    def test_each_known_carrier_stays_in_the_derived_population(self):
        """The floor. Non-emptiness is not enough, because COMPLETENESS is the
        property at issue.

        An arm that asserts one member proves the set is not empty and proves
        nothing about the rest. Drop a second carrier out of the derivation and
        such an arm keeps passing while coverage halves. This asserts each
        known carrier is IN the derived population, so a file cannot leave it
        in silence.
        """
        gone = carriers_missing_from_the_floor(PACKAGE_DIR, MINIMUM_CARRIERS)
        assert not gone, (
            f"{gone} are in MINIMUM_CARRIERS and are NOT in the derived "
            f"population. A path is in that population only while a file at "
            f"that path carries the subject phrase {BAR_SUBJECT!r}. A FAILURE "
            f"OF ONE HALF IS ENOUGH, and the causes named below are EXAMPLES "
            f"and not a closed set. HALF 1, no file is at that path, such as "
            f"after a deletion or a move. HALF 2, a file is at that path and "
            f"its text does not carry the phrase, such as after a docstring "
            f"rewrite or a change to BAR_SUBJECT. READ THE DISK FOR EACH PATH "
            f"BEFORE YOU REPAIR, because this result can hold paths that left "
            f"for different reasons at the same time. While a path is out, "
            f"the cause-clause arm covers nothing at that path. Do not repair "
            f"this by removal of a path from MINIMUM_CARRIERS without a "
            f"stated cause."
        )

    def test_each_module_states_the_bar_or_is_declared_exempt(self):
        """The population covers files nobody has written yet.

        MINIMUM_CARRIERS holds fixed paths, so it cannot reach a carrier added
        later. This reaches it: a new tracked .py file must state the bar, or take a
        line in EXEMPT_MODULES. AGAINST A RENAME, a file then leaves THIS RULE
        only by a visible declaration. A DELETED file leaves by a second exit
        and takes no declaration, and the floor above covers that for the paths
        it holds.
        """
        silent = modules_neither_stating_nor_exempt(PACKAGE_DIR, EXEMPT_MODULES)
        assert not silent, (
            f"{silent} do not name the guarded package. Add the import-bar "
            f"paragraph to each, or add the path to EXEMPT_MODULES with a "
            f"stated cause. AN EXEMPTION LIFTS THIS RULE ALONE: a module that "
            f"takes a line in EXEMPT_MODULES and states the bar anyway is "
            f"under the cause-clause rule as before."
        )


class TestTheInstrumentIsLive:
    """Controls on the instrument, not rules about the tree.

    Each arm here fails when the reader or the matcher breaks, and passes
    whatever the guarded prose says. An empty population agrees with every
    rule above, which is why the first control asserts the walk finds files.
    """

    def test_package_dir_holds_python_files(self):
        assert PACKAGE_DIR.is_dir(), f"package directory absent: {PACKAGE_DIR}"
        tracked = _tracked_package_python_files()
        assert tracked, (
            f"no tracked .py files below {PACKAGE_DIR}. The walk broke, and each "
            f"per-module assertion then passes over an empty set."
        )
        assert PACKAGE_DIR / "__init__.py" in tracked

    def test_tracked_package_population_controls_untracked_and_synthetic(self, tmp_path):
        before = _tracked_package_python_files()
        stray = PACKAGE_DIR / "000_backend_issue1714_probe.py"
        stray.write_text("", encoding="utf-8")
        try:
            assert _python_files(PACKAGE_DIR) == before
        finally:
            stray.unlink()
        synthetic = tmp_path / "package"
        synthetic.mkdir()
        member = synthetic / "member.py"
        member.write_text("", encoding="utf-8")
        assert member in _python_files(synthetic)

    def test_tracked_package_population_omits_a_deleted_member(self, monkeypatch):
        known = PACKAGE_DIR / "__init__.py"
        is_file = Path.is_file
        monkeypatch.setattr(
            Path, "is_file", lambda path: False if path == known else is_file(path))
        assert known not in _tracked_package_python_files()

    def test_the_comparison_survives_a_wrap_and_a_recapitalisation(self):
        """A control on the matcher itself, not on the files it reads.

        Each half must fail a literal comparison and pass `carries`. Without
        that pairing the control agrees with a matcher that ignores its input.
        """
        wrapped = "This rule holds so that\n    nobody must audit which\n    call is safe."
        assert REQUIRED_CAUSE_CLAUSE not in wrapped
        assert carries(wrapped, REQUIRED_CAUSE_CLAUSE)

        shouted = REQUIRED_CAUSE_CLAUSE.upper()
        assert REQUIRED_CAUSE_CLAUSE not in shouted
        assert carries(shouted, REQUIRED_CAUSE_CLAUSE)

    def test_the_comparison_rejects_a_sentence_that_is_absent(self):
        """The negative direction. A matcher that agrees with anything is useless."""
        assert not carries("nothing of the kind appears here", REQUIRED_CAUSE_CLAUSE)


class TestTheCauseClauseIsPresent:
    """The cause clause over the DERIVED population, and not over a listed one.

    The first two arms read the package on disk, so a carrier added later is
    covered on the day it arrives and no count in this class goes stale. The
    last arm reads a package built inside the test, because the boundary it
    pins asks for a module the shipped package does not hold.
    """

    def test_each_module_stating_the_bar_carries_the_cause_clause(self):
        missing = modules_missing_the_cause_clause(PACKAGE_DIR)
        assert not missing, (
            f"{missing} state the import bar but omit the clause that forbids "
            f"an audit of which call is safe. Without it, 'one call away' reads "
            f"as a DISTANCE, and a maintainer concludes they may import so long "
            f"as they audit which functions resolve a path. Required sentence: "
            f"{REQUIRED_CAUSE_CLAUSE!r}"
        )

    def test_the_package_docstring_carries_the_obligation(self):
        """The obligation is package-scoped, so its population is `__init__.py` alone."""
        text = (PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8")
        assert carries(text, REQUIRED_PACKAGE_OBLIGATION), (
            f"the package docstring lost the obligation that binds a future "
            f"editor. A description of an invariant does not command its "
            f"preservation. Required sentence: {REQUIRED_PACKAGE_OBLIGATION!r}"
        )

    def test_an_exemption_does_not_lift_the_cause_clause_requirement(
        self, tmp_path, monkeypatch
    ):
        """AN EXEMPTION LIFTS ONE RULE, and the arm message once said all of them.

        `modules_missing_the_cause_clause` derives from `modules_stating_the_bar`
        and reads no exemption at all. So a module that takes a line in
        `EXEMPT_MODULES` and states the bar anyway is under the cause-clause
        rule as before.

        THE EXEMPTION MUST DO WORK HERE, or this arm agrees with a mechanism
        that is broken. `silent.py` carries that weight: the population
        predicate reports it without the exemption and reports nothing with it.
        `helpers.py` then shows the cause arm reaches an EXEMPT module. The
        last pair is the control: it puts the clause into the same file and
        reads the predicate again, so the red above comes from the absent
        clause and not from the construction.

        WHY `EXEMPT_MODULES` IS PATCHED, AND THE ARM IS BLIND WITHOUT IT. The
        shipped set is EMPTY. An edit that teaches the cause arm to read that
        set changes nothing observable while it stays empty, so a local
        exemption alone cannot catch the edit that makes the message above
        true. This puts the same two paths into the shipped constant, so such
        an edit drops `helpers.py` and this arm fails.

        RESIDUAL, AND IT IS THE BOUND OF THE WHOLE ARM. This pins the
        BEHAVIOUR. It does not catch an edit to the arm message above that says
        something incorrect about behaviour that did not change.
        """
        package = tmp_path / "memory_repair"
        package.mkdir()
        silent = package / "silent.py"
        carrier = package / "helpers.py"
        silent.write_text('"""This module says nothing."""\n', encoding="utf-8")
        carrier.write_text(f'"""Names the {BAR_SUBJECT}."""\n', encoding="utf-8")
        exempt = frozenset({"silent.py", "helpers.py"})
        monkeypatch.setitem(globals(), "EXEMPT_MODULES", exempt)

        assert modules_neither_stating_nor_exempt(package, frozenset()) == ["silent.py"]
        assert modules_neither_stating_nor_exempt(package, exempt) == []

        assert modules_missing_the_cause_clause(package) == ["helpers.py"], (
            "the cause arm let an EXEMPT module through. An exemption lifts "
            "the must-state-the-bar rule alone, so the arm message above is "
            "correct only while this holds."
        )

        carrier.write_text(
            f'"""Names the {BAR_SUBJECT}. {REQUIRED_CAUSE_CLAUSE}"""\n',
            encoding="utf-8",
        )
        assert modules_missing_the_cause_clause(package) == []


class TestTheFloorEarnsItsPlace:
    """The stated cause for `MINIMUM_CARRIERS`, asserted rather than written.

    A GUARD'S UNIQUE COVERAGE IS VISIBLE ONLY WHEN THE GUARD COMES OUT. Each
    MUTATION arm here runs its mutation TWICE over a copy, once with the floor
    and once with it emptied, so a claim about what the floor catches ALONE has
    a failing thing behind it. Without this class the stated cause above is a
    second unchecked claim in the position of the first.
    """

    @staticmethod
    def _copy(tmp_path: Path) -> Path:
        target = tmp_path / "memory_repair"
        target.mkdir()
        for source in _tracked_package_python_files():
            destination = target / source.relative_to(PACKAGE_DIR)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        return target

    @staticmethod
    def _arms_pass(package: Path, floor: frozenset, exempt: frozenset) -> bool:
        """Evaluate the three population predicates against one copy.

        WHY THIS ORCHESTRATOR IS KEPT RATHER THAN DELETED, since a body of
        pure calls invites the question. The arms below must ask "would the
        shipped arms pass over THIS copy, under THIS floor", and a test cannot
        run pytest inside pytest to find out. This composes the answer from
        the SAME predicates the shipped arms call, so it holds no rule of its
        own. KEEP IT THAT WAY: one line of predicate logic here re-creates the
        drift this class exists to close.
        """
        return not (
            carriers_missing_from_the_floor(package, floor)
            or modules_neither_stating_nor_exempt(package, exempt)
            or modules_missing_the_cause_clause(package)
        )

    @staticmethod
    def _rename_the_subject(path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        renamed = text.replace(BAR_SUBJECT.upper(), "REDACTED").replace(
            BAR_SUBJECT, "REDACTED"
        )
        assert renamed != text, "the mutation changed nothing, so the arm proves nothing"
        path.write_text(renamed, encoding="utf-8")

    def test_the_copy_excludes_untracked_source_files(self, tmp_path):
        expected = {path.relative_to(PACKAGE_DIR)
                    for path in _tracked_package_python_files()}
        stray = PACKAGE_DIR / "000_backend_import_bar_copy_issue1714_probe.py"
        stray.write_text("", encoding="utf-8")
        try:
            package = self._copy(tmp_path)
            copied = {path.relative_to(package) for path in _python_files(package)}
            assert copied == expected
            assert self._arms_pass(package, MINIMUM_CARRIERS, EXEMPT_MODULES)
        finally:
            stray.unlink()

    def test_a_plain_rename_is_caught_without_the_floor(self, tmp_path):
        """The condition the OLD stated cause named, and it is not unique."""
        package = self._copy(tmp_path)
        self._rename_the_subject(package / "shred_detect.py")
        assert not self._arms_pass(package, MINIMUM_CARRIERS, EXEMPT_MODULES)
        assert not self._arms_pass(package, frozenset(), EXEMPT_MODULES), (
            "a plain rename passed with the floor emptied, so the floor would "
            "be its only guard. Measurement says the exempt arm catches it."
        )

    def test_only_the_floor_catches_a_rename_that_is_declared_exempt(self, tmp_path):
        """Unique coverage 1. The exemption escape."""
        package = self._copy(tmp_path)
        self._rename_the_subject(package / "shred_detect.py")
        exempt = frozenset({"shred_detect.py"})
        assert not self._arms_pass(package, MINIMUM_CARRIERS, exempt)
        assert self._arms_pass(package, frozenset(), exempt), (
            "another arm caught the exemption escape, so this is no longer "
            "unique to the floor. Correct the stated cause above."
        )

    def test_only_the_floor_catches_a_deleted_carrier_file(self, tmp_path):
        """Unique coverage 2. A carrier leaves the disk and the population together."""
        package = self._copy(tmp_path)
        (package / "shred_detect.py").unlink()
        assert not self._arms_pass(package, MINIMUM_CARRIERS, EXEMPT_MODULES)
        assert self._arms_pass(package, frozenset(), EXEMPT_MODULES), (
            "another arm caught the deletion, so this is no longer unique to "
            "the floor. Correct the stated cause above."
        )

    def test_the_unmutated_copy_passes_under_both_floors(self, tmp_path):
        """A landing control. Without it each arm above can pass on a broken copy."""
        package = self._copy(tmp_path)
        assert self._arms_pass(package, MINIMUM_CARRIERS, EXEMPT_MODULES)
        assert self._arms_pass(package, frozenset(), EXEMPT_MODULES)


class TestTheRefutedCauseStaysOut:
    """The negative arm, and its bound is the spellings named.

    This does NOT detect a new incorrect cause in new words. It detects the
    RETURN OF A NAMED SPELLING, which is worth catching because a revert, a bad
    merge, or an editor who restores a removed sentence from memory reproduces
    the original bytes rather than a paraphrase.

    A SPELLING HERE NEED NOT HAVE SHIPPED. Two entries were drafted and removed
    before release, and they are members for the same reason as the rest: the
    claim is settled, the return is a live risk, and the ban costs no true
    sentence. The rule above `REFUTED_SPELLINGS` states that test in full.

    ITS POPULATION IS THE PLUGIN TREE AND NOT THE PACKAGE. A revert lands where
    the claim was written, and one of the claims was written outside the
    memory-repair package. A population narrower than the thing it protects
    reports a clean green over the files it happens to hold.
    """

    def test_the_scanned_population_is_not_empty(self):
        """Non-vacuity ON THE WIDENED SET. An arm on the old set is not one on
        the new set, and each assertion below agrees with an empty population.
        """
        scanned = files_scanned_for_refuted_spellings()
        assert scanned, (
            f"the walk below {PLUGIN_DIR} returned nothing, so the refuted-"
            f"spelling arms pass over an empty set."
        )
        reached = {relative_name(path, PLUGIN_DIR) for path in scanned}
        # 🔴 ONE MEMBER OF EACH SCANNED SUFFIX, AND THE MARKDOWN ONE IS NOT
        # DECORATION. `SCANNED_SUFFIXES` carries `.py` and `.md`. MEASURED: with
        # the tuple narrowed to `.py` alone the walk drops 134 of its 519 files
        # and NOTHING REDS, because a control built from `.py` members cannot
        # see the markdown half go. AND THE LOSS IS NOT ABSTRACT: the spelling
        # `mean drift direction` guards a sentence in `agents/pact-secretary.md`,
        # which is markdown, so that narrowing disarms the entry in silence.
        # A CONTROL WHOSE POPULATION DOES NOT COVER WHAT IT CERTIFIES REPORTS
        # A CLEAN ANSWER OVER THE PART IT CANNOT REACH.
        for expected in (
            "scripts/memory_repair/__init__.py",
            "tests/test_archive_pin.py",
            "agents/pact-secretary.md",
        ):
            assert expected in reached, (
                f"{expected} is absent from the scanned population, so the walk "
                f"no longer reaches the files that carried the corrected claims. "
                f"Paths here are relative to {PLUGIN_DIR.name}."
            )

    def test_the_refused_set_is_not_empty(self):
        """Non-vacuity ON THE SET, which is a different population from the walk.

        THE WALK AND THE SET COLLAPSE SEPARATELY, and the arm above covers only
        the first. MEASURED: with `REFUTED_SPELLINGS` emptied, the parametrized
        arm below collects ZERO cases and the run reports 80 passed, 1 skipped,
        exit 0. A COLLAPSED ARM REPORTS A SKIP, NOT A FAILURE, and this suite
        already carries skips, so the loss hides in the noise of a green run.

        THE SIBLING GUARD APPLIES THIS RULE TO ITS OWN CONSTANTS, so this
        brings one file up to a standard the other holds rather than inventing
        one.
        """
        assert REFUTED_SPELLINGS, (
            "REFUTED_SPELLINGS is empty, so the arm that refuses a returned "
            "spelling collects no case and passes by collecting nothing. "
            "EITHER a correction removed the last entry, which must be a "
            "deliberate act with a stated cause, OR an edit emptied the set by "
            "accident."
        )
        for load_bearing in ("an import is a write", "mean drift direction"):
            assert load_bearing in REFUTED_SPELLINGS, (
                f"{load_bearing!r} left REFUTED_SPELLINGS. That is a control "
                f"on the SET rather than a rule about the spelling: these two "
                f"are load-bearing today, one from each repair this file "
                f"records. Remove one only with a stated cause."
            )

    def test_each_exclusion_is_still_necessary(self):
        """Closes TWO of the THREE ways an exclusion entry goes bad.

        A stale KEY fails safe, because the file loses its exclusion and
        reddens. AN ENTRY THAT DOES NO WORK FAILS OPEN: the file keeps its
        silence on that spelling and nothing reports it. So the maintenance
        burden sits on REMOVAL rather than on addition.

        WHAT THIS CLOSES, each measured:
          1. An entry whose file no longer carries the spelling.
          2. An entry that CANNOT do work: a key outside the scanned
             population, or a spelling outside REFUTED_SPELLINGS. Neither
             excludes anything from any arm, and `is_file()` cannot see it.

        WHAT THIS DOES NOT CLOSE, AND THE BOUND IS MEASURED RATHER THAN
        REASONED. An entry whose file continues to carry the spelling FOR A
        CHANGED REASON. A document that QUOTES a refuted claim earns its
        exclusion. The same document that later ASSERTS the claim does not,
        and the two are the SAME SUBSTRING. Measured: with the entry present
        each arm reports green, and with the entry removed the detection arm
        reddens, so the exclusion is what confers the silence. THIS ARM
        MEASURES THAT A FILE CARRIES A SPELLING. What earns an exclusion is
        WHY, and no substring comparison reaches a reason.
        """
        scanned = {
            relative_name(p, PLUGIN_DIR): p
            for p in files_scanned_for_refuted_spellings()
        }
        unreachable = []
        idle = []
        for rel, spellings in SPELLING_EXCLUSIONS.items():
            path = scanned.get(rel)
            if path is None:
                # MEMBERSHIP, NOT `is_file()`. A key can name a file that is
                # present and never scanned, because its suffix sits outside
                # SCANNED_SUFFIXES. `is_file()` says yes and the entry does
                # nothing.
                unreachable.append(f"{rel} (not in the scanned population)")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for spelling in spellings:
                if spelling not in REFUTED_SPELLINGS:
                    # A spelling no arm asks about. The entry excludes it from
                    # a question nobody puts.
                    unreachable.append(f"{rel} -> {spelling!r} (not a refuted spelling)")
                elif not carries(text, spelling):
                    idle.append(f"{rel} -> {spelling!r}")
        assert not unreachable, (
            f"these exclusion entries cannot do work: {sorted(unreachable)}. A "
            f"key outside the scanned population, or a spelling outside "
            f"REFUTED_SPELLINGS, excludes nothing from any arm. Correct the "
            f"key or the spelling. Paths are relative to {PLUGIN_DIR.name}."
        )
        assert not idle, (
            f"these exclusion entries do no work ANY ARM CAN SEE: "
            f"{sorted(idle)}. THAT IS NARROWER THAN UNNECESSARY: this arm "
            f"measures presence, and it does not measure why the file carries "
            f"the spelling. If the file dropped the spelling, remove the "
            f"entry. IF THE FILE MOVED, UPDATE THE KEY rather than remove the "
            f"entry. Paths are relative to {PLUGIN_DIR.name}."
        )

    @pytest.mark.parametrize("refuted", sorted(REFUTED_SPELLINGS))
    def test_a_refuted_spelling_does_not_return(self, refuted):
        for path in files_scanned_for_refuted_spellings():
            if spelling_is_excluded_for(path, refuted):
                continue
            if carries(path.read_text(encoding="utf-8", errors="replace"), refuted):
                raise AssertionError(
                    f"{relative_name(path, PLUGIN_DIR)} carries {refuted!r} "
                    f"again. Each spelling here was measured incorrect and "
                    f"removed. Do not clear this red by a spelling removed from "
                    f"REFUTED_SPELLINGS, and do not add an entry to "
                    f"SPELLING_EXCLUSIONS to quiet it."
                )
