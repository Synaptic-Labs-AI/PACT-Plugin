"""
Cross-file copy fidelity for the store-access bar.

Location: pact-plugin/tests/test_store_access_bar_presence.py

WHAT THIS FILE IS FOR, STATED AS THE JOB AND NOT AS THE RULE. The store-access
bar is ONE text that ships in many files. A rule held in many places drifts.
One editor corrects a sentence in the file they opened, the others keep the old
sentence, and no reader of any single file can tell. This keeps the copies IN
SYNC WITH EACH OTHER, for as long as they ship.

🔴 WHAT A GREEN HERE DOES NOT MEAN, AND READ IT BEFORE YOU TRUST ONE.

  * IT DOES NOT TELL YOU THE SOURCE BLOCK IS CORRECT. It tells you the copies
    agree with the source. Somebody who edits the source and each copy together
    passes this file, and that is BY DESIGN: a deliberate reword must be able
    to land. The one-time check that the first placement matched the design was
    a separate act by a separate agent, and it is not repeatable from here.
  * IT DOES NOT CHECK THAT THE CAUSE IN THE PROSE IS TRUE. No test reads a
    natural-language cause for truth. If `PRAGMA journal_mode=WAL` leaves the
    connection function, or the context manager stops the close, the shipped
    cause goes stale and each arm here stays green.
  * IT DOES NOT COVER THE CALIBRATION SENTENCE. The same repair corrected an
    instruction in `agents/pact-secretary.md` about calibration statistics.
    That sentence ships with no arm in this file.
  * ITS WALK REACHES `pact-plugin/` AND NO FURTHER. A store-access surface
    written above that directory is outside each population here.

FOUR ARMS, AND EACH COVERS A FAILURE THE OTHERS MISS.

  1. FIDELITY. Each carrier holds one marked region, and each copy agrees with
     the source. A MISSING MARKER FAILS.
  2. EMPHASIS. A copy that agrees with the source apart from CAPITALISATION
     fails. The block shouts its bright line, and a quiet copy is a weaker rule
     that arm 1 passes in silence.
  3. POPULATION. Each instruction surface that reaches the store, and that
     takes no declared exemption, carries a marked region. This reaches a NEW
     teaching surface on the day it arrives.
  4. FACT. `setup_memory.py` holds no `--db-path` literal, and the source home
     names that file. The claim sits in one file and its evidence sits in
     another, which is the shape that earns a guard its place.

🔴 TWO POPULATIONS, AND ONE LIST CANNOT SERVE THE TWO.

  THE CARRIER POPULATION, for arms 1 and 2, is DERIVED FROM THE MARKER across
  ANY file type. A file that holds the begin marker is a carrier, whatever its
  extension and wherever it sits. A carrier added next month joins the
  comparison with no edit here, and no count in any document can go stale
  against it.

  THE INSTRUCTION-SURFACE POPULATION, for arm 3, is the five markdown patterns
  below. Arm 3 asks a different question: does a file hand a reader a route to
  the store with no rule attached. Widening THAT to Python would reach the
  memory package and each hook, which reach the store legitimately, and the
  result would be findings that are not defects.

🔴 AND A DECLARED FLOOR SITS BEHIND THE DERIVATION, BECAUSE DERIVATION ALONE
HAS A BLIND SPOT. Derivation notices a file that HAS a marker. Only the floor
notices a file that SHOULD have one and does not. A dropped marker removes a
file from the compared set in silence, and a smaller set that reports green
reads the same as a larger set that reports green.

MEASURED, AND IT IS WHY THE FLOOR CANNOT BE DROPPED IN FAVOUR OF AN ALPHABET:
of the three Python carriers, `archive_pin.py` names three route tokens and the
two memory-repair modules name NONE. So no widened alphabet reaches the two,
and a declared list is the only instrument that covers a member no alphabet can
speak to.

WHAT THIS GUARD HOLDS, AND WHAT IT MUST NOT.

  HOLDS: the two marker literals, which are the ADDRESS of the region. The
  declared carrier paths. The route tokens. The normalisation rule.

  DOES NOT HOLD: the block text. A copy of the guarded prose inside a test
  makes the TEST the specification, and it gives a prose maintainer one more
  place to edit, in the file they are least likely to open. Do not add the
  block here to make an arm stricter. ARM 2 OBEYS THIS TOO: it derives the
  emphasis from the source at run time rather than hold a sentence.

  THE BOUND ON THAT SENTENCE, because a reader applies a short rule more widely
  than it holds: THE MARKERS STAY. A marker is an address and not content. A
  test that drops the markers to obey the rule above cannot find the region it
  must compare.

WHY THE MARKERS FAIL CLOSED. Some context renderings strip an HTML comment, so
an agent that edits a carrier through such a rendering can drop a marker with
no intent to. A marker fault is a RED and not a skip.

CLASSIFY BY ROLE, NOT BY PRESENCE. THIS FILE HOLDS THE MARKER LITERALS, so a
marker-derived walk SELECTS IT. It is a GUARD and not a carrier, so it is named
out in `DERIVED_POPULATION_EXCLUSIONS` with its cause, and an arm below asserts
that the exclusion continues to do work. The design document sits above
`PLUGIN_DIR` and is outside each walk by a correct predicate, so it takes no
entry. A carve-out written for a non-member is the sign of a predicate aimed
wrong.

TO CHANGE ANY CONSTANT IN THIS FILE, STATE A CAUSE. Each module-level constant
sets what an arm requires, or which files an arm covers. A constant edited to
quiet a red turns this file into a MIRROR of the text it guards, and its green
then means nothing.

Used by: pytest.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent

# THE ADDRESS OF THE GUARDED REGION, and not a copy of it. An HTML comment does
# not appear when a reader renders the markdown, so the pair costs a reader
# nothing. A test reads the bytes from disk, so it sees them. THE PYTHON
# CARRIERS USE THE SAME SYNTAX: two marker syntaxes is two alphabets, and a
# wrong alphabet is the failure this work has met most often.
MARKER_BEGIN = "<!-- PACT_STORE_BAR_BEGIN -->"
MARKER_END = "<!-- PACT_STORE_BAR_END -->"

# THE SOURCE. Each copy is compared to this file, and this file is compared to
# nothing. That direction is the whole mechanism: a source compared to its
# copies takes the majority as truth, which lets a drifted copy win a vote.
SOURCE_HOME = "skills/pact-memory/SKILL.md"

# THE DECLARED FLOOR. Keys are POSIX paths relative to `PLUGIN_DIR`. A relative
# path without its base is a figure without its counting rule, so the root is
# named here. A key against another root matches nothing, the file leaves the
# floor, and the arm reddens, which is the safe direction.
#
# THIS IS A FLOOR AND NOT A CENSUS. The compared population is DERIVED from the
# marker, so it grows with no edit here. This set refuses SHRINKAGE: a carrier
# whose marker is dropped leaves the derived population in silence, and only a
# declared path notices the absence.
#
# THE THREE PYTHON CARRIERS ARE HERE FOR A MEASURED REASON. Two of them name no
# route token at all, so arm 3 cannot reach them and no widening of its alphabet
# would. Declaration is the only instrument that covers them.
#
# To remove a path, state a cause. A path removed to quiet a red turns the floor
# into a report of the current population, which is the mirror failure.
DECLARED_CARRIERS = frozenset(
    {
        SOURCE_HOME,
        "agents/pact-secretary.md",
        "commands/prune-memory.md",
        "reference/config.md",
        "protocols/pact-state-recovery.md",
        "protocols/pact-protocols.md",
        "skills/pact-handoff-harvest/SKILL.md",
        # A REFERENCE PAGE, and the first carrier that is not a SKILL.md. It
        # teaches module-level API calls against the store. The route alphabet
        # missed it, because it names the calls and no path, no CLI and no
        # store file. MEASURED across the 44 markdown files below a skill
        # directory that are not SKILL.md: this is the ONE live member.
        "skills/pact-memory/references/memory-patterns.md",
        "scripts/memory_repair/__init__.py",
        "scripts/memory_repair/shred_detect.py",
        "scripts/archive_pin.py",
    }
)

# FILES THAT HOLD A MARKER AND ARE NOT CARRIERS. Keys are relative to
# `PLUGIN_DIR`. Values state the cause.
#
# CLASSIFY BY ROLE AND NOT BY PRESENCE. A GUARD holds the address to test it. A
# CARRIER instructs an agent. This file is the first, so a marker-derived walk
# selects it and it must be named out. `test_each_derived_exclusion_does_work`
# refuses an entry that no walk reaches, so a stale key cannot sit here unread.
DERIVED_POPULATION_EXCLUSIONS = {
    "tests/test_store_access_bar_presence.py": (
        "This guard holds the two marker literals, because a marker is the "
        "ADDRESS of the region it must compare. So it is a GENUINE MEMBER of "
        "the derived walk and it must be named out. It instructs no agent, so "
        "by role it is a GUARD and not a carrier."
    )
}

# ARTEFACTS THAT ARE NOT SOURCE. A compiled module holds the string constants
# of the module it came from, so a cache directory carries the marker literals
# of THIS file and would enter the derived walk after the first run. That
# membership appears and vanishes with the cache, which is a flake rather than
# a finding. This is a NON-MEMBER predicate and not a carve-out.
NON_SOURCE_PARTS = frozenset({"__pycache__"})
NON_SOURCE_SUFFIXES = frozenset({".pyc", ".pyo"})

# THE INSTRUCTION-SURFACE POPULATION, for arm 3 alone. Five patterns below
# `PLUGIN_DIR`, which measure the set the design walked when it derived the
# homes.
#
# THE BOUND OF THE PATTERNS. A markdown file below a skill directory that is
# not `SKILL.md`, such as a reference page, is outside. Widen only with a
# stated cause, because a wider population needs an exemption for each file it
# reaches that teaches nothing about the store.
POPULATION_PATTERNS = (
    "agents/*.md",
    "commands/*.md",
    "protocols/*.md",
    "reference/*.md",
    "skills/*/SKILL.md",
    # THE MEMORY SKILL OWN DIRECTORY, and the cause is measured. A reference
    # page beside the skill that teaches store access taught 21 module-level
    # API calls, named the CLI zero times, and held no rule. `skills/*/SKILL.md`
    # cannot reach a reference page, so the page was outside the walk.
    # THIS IS NOT THE WIDENING REFUSED EARLIER. That one reached the memory
    # package and each hook, which reach the store legitimately. This reaches
    # one skill directory, where a page about the store is expected rather than
    # incidental, and it reaches the NEXT page written there.
    "skills/pact-memory/**/*.md",
    # THE MEMORY-REPAIR TOOLING DIRECTORY. `scripts/archive_pin.py` names three
    # route tokens outside the bar, so the alphabet reaches it. This gives it a
    # net that does NOT depend on `DECLARED_CARRIERS`. MEASURED: the one other
    # file here, `check_pin_caps.py`, names no route token, so this pattern
    # needs no exemption. It does NOT reach the memory package or the hooks.
    "scripts/*.py",
)

# THE MEMORY-REPAIR PACKAGE, walked as a DIRECTORY rather than as a path list.
# Each module here must carry the bar, so a module added later joins the rule
# on the day it arrives, and a path dropped from `DECLARED_CARRIERS` does not
# take the coverage with it.
#
# WHY A DIRECTORY AND NOT AN ALPHABET. MEASURED: with the bar region removed,
# the two modules here name NO route token at all. So no widening of
# `ROUTE_TOKENS` can reach them, and only membership of this directory can.
REPAIR_PACKAGE_DIR = "scripts/memory_repair"

# MODULES HERE THAT DO NOT HAVE TO CARRY THE BAR. Empty today. Add a path only
# with a stated cause, as for each other set in this file.
REPAIR_PACKAGE_EXEMPTIONS: dict = {}

# A FILE REACHES THE STORE WHEN IT NAMES ONE OF THESE. The token set is derived
# from the THREE ROUTES the acceptance test names, which are the CLI, an import,
# and a raw path. It is NOT derived from the wording of the bar.
#
# WHY THE DERIVATION MATTERS MORE THAN THE LIST. An alphabet read off one
# instance of the guarded thing misses each other spelling of it. That defect
# happened three times on this material: a census built from five sentences
# missed a paraphrase, a token set built from the spelling of the tool missed a
# file that instructs a bare CLI verb, and no token set reaches two of the three
# Python carriers at all.
#
# NO TOKEN HERE COMES FROM THE BAR TEXT. A token taken from the block selects
# a file BECAUSE it carries the block, so it can never reach a NEW silent
# surface, which is the one thing this alphabet is for.
# `TestTheAlphabetDoesNotSelectItself` measures that property rather than
# trusting this paragraph.
ROUTE_TOKENS = {
    "memory.db": "raw path: the store file",
    ".claude/pact-memory": "raw path: the store directory",
    "skills/pact-memory/scripts": "import: the store package",
    "cli.py": "the CLI, by module path",
    "cli command": "the CLI, by a verb instruction",
    # The search route as a caller writes it post-#1647: the query is
    # positional and quoted. Selects the two SKILL.md carriers that teach
    # the invocation (and cli.py, which is outside the markdown walk).
    'search "': "the CLI, by a verb instruction",
    "--limit": "the CLI, by a verb instruction",
    "archive_pin.py": "the CLI, at one remove",
    # THE IMPORT ROUTE, SPELLED AS A MODULE-LEVEL CALL. A page taught
    # `memory.save(...)` and `memory.search(...)` and named no package path, no
    # CLI and no store file, so each token above missed it. The route was
    # present and the SPELLING was new, which is the same miss this file has
    # recorded three times. These name the route as a caller writes it.
}

# 🔴 THE FORBIDDEN ROUTE, AND IT TAKES THE OPPOSITE RULE. These name the
# module API, which the store-access bar forbids. A file that teaches one of
# them teaches the route the rule removes.
#
# EACH TOKEN HERE IS EXPECTED TO BE IDLE. That is the whole difference from
# the set above. `ROUTE_TOKENS` name LEGITIMATE routes, so a token there that
# selects nothing is decay and an arm reports it. A token HERE that selects
# nothing means the tree is HEALTHY, and an arm that reddens on that is
# measuring the wrong thing.
#
# THE HISTORY THAT PRODUCED THE SPLIT, because a later reader will meet the
# same pressure. These three sat in `ROUTE_TOKENS`. A page taught the module
# API, the repair converted it to the CLI, and the tokens went idle one by
# one. The must-do-work arm reddened each time, and each red invited a token
# removal that would have left the guard blind to the NEXT page that teaches
# the forbidden route. ONE RULE OVER TWO KINDS OF TOKEN WAS THE DEFECT.
#
# THESE TOKENS DO SELECT FOR ARM 3. A file that names one reaches the store,
# so it needs the rule, and it needs it more than a file that uses the CLI.
# 🔴 STATED BOUND: THIS ALPHABET IS BOUND TO A BINDING NAME AND CANNOT SEE A
# REBIND. Each token below spells the receiver as `memory`. A reader who writes
# `m = PACTMemory()` and then `m.save(...)` reaches the store by the forbidden
# route and selects NOTHING here. MEASURED, and the instance is live outside
# this guard: a repository testing scenario binds `m` and calls `m.list(...)`.
#
# THE DANGEROUS DIRECTION IS THE REPAIR. An author who converts `memory.save(`
# to a different receiver satisfies this guard and keeps the forbidden route.
# So a green from arm 3 says "no token matched", and it does NOT say "no file
# reaches the store by the module API".
#
# DO NOT CLOSE THIS BY MORE SPELLINGS. A longer name list is the same defect
# with more rows, because the receiver is free. The repair is a predicate over
# the CALL rather than over the text. It is not on this branch, and the tracker
# carries it with the population widening, because either repair alone leaves
# the question open.
FORBIDDEN_ROUTE_TOKENS = {
    "memory.save(": "the module API, which the bar forbids",
    "memory.search(": "the module API, which the bar forbids",
    "memory.update(": "the module API, which the bar forbids",
}

# The set arm 3 selects with. A file that reaches the store by ONE route needs
# the rule, and the two kinds of route select alike.
SELECTING_TOKENS = {**ROUTE_TOKENS, **FORBIDDEN_ROUTE_TOKENS}

# THE ALPHABET FLOOR, and it is NOT the same set as `DECLARED_CARRIERS`. The
# alphabet exists to reach INSTRUCTION SURFACES, so its floor holds the markdown
# carriers alone. The three Python carriers are covered by declaration, and two
# of them name no route token, so a floor that demanded the alphabet reach them
# would demand an impossible thing and reward a widened alphabet that drowns
# arm 3 in files that reach the store legitimately.
ALPHABET_FLOOR = frozenset(path for path in DECLARED_CARRIERS if path.endswith(".md"))

# INSTRUCTION SURFACES THAT REACH THE STORE AND DO NOT HAVE TO CARRY THE BAR.
# Keys are relative to `PLUGIN_DIR`. Values state the cause.
#
# EMPTY TODAY. THE RESULT THAT MADE IT EMPTY IS A FLOOR AND NOT A CENSUS, and
# it is stated as the search that produced it: the tokens in `ROUTE_TOKENS`,
# applied to `POPULATION_PATTERNS`, selected the markdown carriers and no other
# file. THE COUNTS ARE NAMED BY THEIR CONSTANT RATHER THAN WRITTEN OUT, because
# a written count goes stale on the day a token or a pattern is added, and the
# stale form reads as a measurement.
# A WIDER ALPHABET REACHES MORE. An eighteen-token set reaches two further
# instruction surfaces, and the team-lead ruled those OUT on ACTIONABILITY: they
# give an INTENT a reader cannot type, so the reader must open the memory skill
# to learn the command, and the bar lives there. A file that gives a TYPEABLE
# command is in.
# SO DO NOT READ THIS EMPTINESS AS "NO OTHER FILE REACHES THE STORE". Read it
# as "no file this alphabet selects lacks the bar".
#
# 🔴 DO NOT ADD AN ENTRY FOR A CARRIER THAT IS NOT PLACED YET. A placement gap
# repaired with an exemption becomes a silent carve-out for a file the design
# calls a genuine hole. Report the gap and leave the red.
#
# ONE FALSE POSITIVE IS RECORDED IN THE DESIGN AND NEEDS NO ENTRY.
# `commands/pin-memory.md` names `PACT_MEMORY_PINNED_END`, which is a region
# marker inside CLAUDE.md and not a route to the store. No token above reaches
# it, so it is a NON-MEMBER by a correct predicate.
POPULATION_EXEMPTIONS: dict = {}

# A FLOOR ON THE SIZE OF THE SOURCE REGION, and it closes a hole in arm 1.
# EMPTY REGIONS AGREE WITH EACH OTHER. An edit that empties the block in each
# carrier, or a marker pair placed around nothing, passes the fidelity arm over
# a comparison of one empty string with another. This measures the region rather
# than reads it, so the guard gains no copy of the guarded text.
#
# 🔴 THE NUMBER IS A FLOOR AND NOT A PIN, AND IT RECORDS NO MEASUREMENT. This
# file does NOT state the length of the region, because the length is derivable
# from the source and a written count goes stale the moment the block is
# reworded. The block HAS been reworded during this work, which is the evidence
# for the rule rather than a reason to re-take the number.
# DO NOT RAISE THIS TO THE CURRENT SIZE. A pinned length reddens on each
# ordinary edit, which trains a reader to bump the number rather than read it.
# Keep it far below any plausible block, so it fires on a COLLAPSE alone.
MINIMUM_REGION_CHARS = 300

# ARM 2. A SHOUTED RUN is a run of capitals long enough to be emphasis rather
# than an acronym. The floor keeps `WAL` and `CLI` out, so an acronym that one
# copy spells and the source does not cannot redden the arm.
MINIMUM_SHOUT_CHARS = 8

# ARM 4. The claim lives in `SOURCE_HOME` and the evidence lives here.
EVIDENCE_FILE = "skills/pact-memory/scripts/setup_memory.py"

# The flag the shipped text says this script does not accept.
FORBIDDEN_FLAG = "--db-path"

# A CONTROL TOKEN, AND IT DISCRIMINATES. `db_path` IS in the evidence file, as
# the name of a function parameter. `--db-path`, the command-line flag, is not.
# The two differ by two characters, so a read that finds the first and not the
# second is a LIVE read of the correct file. Without this control, an empty read
# and a broken path both report a clean absence, which is the failure that reads
# as success.
EVIDENCE_CONTROL_TOKEN = "db_path"


def flatten(text: str) -> str:
    """Collapse each whitespace run to one space.

    The guarded region wraps across lines, and a copy can re-wrap at another
    column with no change of meaning. Flatten first, or a comparison reports a
    difference that no reader can see.
    """
    return re.sub(r"\s+", " ", text).strip()


def carries(text: str, needle: str) -> bool:
    """True when `text` holds `needle`, apart from wraps and capitalisation.

    ONE COMPARISON FOR EACH TEXT CHECK IN THIS FILE, so no two arms can drift
    into opposite treatments of one input.
    """
    return needle.casefold() in flatten(text).casefold()


def normalised(text: str) -> str:
    """The one form each region comparison uses."""
    return flatten(text).casefold()


def shouted_runs(text: str) -> list[str]:
    """Return the emphasised runs of `text`, sorted.

    A RUN IS DERIVED AND NOT HELD. This reads the source at run time, so the
    guard pins the EMPHASIS without holding one word of the wording.
    """
    runs = re.findall(r"[A-Z][A-Z ,.`'-]*[A-Z]", flatten(text))
    return sorted(r.strip() for r in runs if len(r.strip()) >= MINIMUM_SHOUT_CHARS)


def relative_name(path: Path, root: Path) -> str:
    """Return `path` as a POSIX path relative to `root`."""
    return path.relative_to(root).as_posix()


def read_carrier(root: Path, name: str) -> str:
    """Read one carrier by its relative path, or return an empty string.

    AN ABSENT FILE RETURNS EMPTY RATHER THAN RAISES, so an arm reports WHICH
    carrier is missing together with each other fault. An exception on the first
    absent path hides the rest of the result.
    """
    path = root / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def marker_fault(text: str) -> str | None:
    """Describe the marker fault in `text`, or return None when the pair is sound.

    THREE FAULTS, and each makes the region unreadable rather than wrong: a
    count other than one for either marker, and an end that comes first. A file
    with NO marker at all is the likely fault, because a context rendering can
    strip an HTML comment.
    """
    begins = text.count(MARKER_BEGIN)
    ends = text.count(MARKER_END)
    if begins != 1 or ends != 1:
        return f"{begins} begin marker(s) and {ends} end marker(s), expected 1 and 1"
    if text.index(MARKER_BEGIN) > text.index(MARKER_END):
        return "the end marker comes before the begin marker"
    return None


def marked_region(text: str) -> str:
    """Return the bytes between the markers.

    Call this only when `marker_fault` returns None.
    """
    start = text.index(MARKER_BEGIN) + len(MARKER_BEGIN)
    return text[start : text.index(MARKER_END)]


def strip_marked_regions(text: str) -> str:
    """Return `text` with each marked region and its markers removed.

    USED BY THE ALPHABET FLOOR. After the bar lands, the region names two route
    tokens, so each carrier selects itself. This removes the region, so an arm
    can ask whether the alphabet reaches a file FOR A REASON OF ITS OWN.
    """
    pattern = re.escape(MARKER_BEGIN) + ".*?" + re.escape(MARKER_END)
    return re.sub(pattern, " ", text, flags=re.DOTALL)


def pointer_warning(absent: list) -> str:
    """Return the pointer consequence when the SOURCE home is one of `absent`.

    THE ONE THING NO ARM HERE DETECTS, NAMED WHERE THE RED APPEARS. Each copy
    carries a POINTER SENTENCE that names the source home. A rename of the
    source deadens each pointer at one time. NO ARM READS A POINTER, because
    the sentence is free text and a guard on free text is the brittle kind.

    SO THIS IS GUIDANCE AND NOT DETECTION. The rename is detected by the arm
    that calls this and by the marker floor. A THIRD ARM WOULD ADD NO
    DETECTION AND WOULD REPORT ONE FAULT A THIRD TIME, which teaches a reader
    that the extra reports are noise.
    """
    if SOURCE_HOME not in absent:
        return ""
    return (
        f" 🔴 AND CHECK THE POINTERS. Each copy carries a sentence naming "
        f"{SOURCE_HOME} as the home of the full rule. No file sits at that "
        f"path now, so each of those sentences points at nothing. NO ARM "
        f"READS A POINTER, so a repair of this path alone leaves them dead "
        f"and green."
    )


def is_source_file(path: Path) -> bool:
    """False for a build artefact, which is not a carrier.

    A NON-MEMBER PREDICATE AND NOT A CARVE-OUT. A compiled module holds the
    string constants of its source, so a cache directory carries the marker
    literals of this guard and enters the walk after the first run.
    """
    if NON_SOURCE_PARTS.intersection(path.parts):
        return False
    return path.suffix not in NON_SOURCE_SUFFIXES


# ---------------------------------------------------------------------------
# THE POPULATION PREDICATES.
#
# ONE DEFINITION EACH, called by the shipped arm AND by the mutation model
# below. A model that re-implements a predicate drifts from it, and each
# hand-carry of an edit is correct until the one that is not.
#
# Each takes a root and returns a sorted list, so an empty list means the
# property holds and a non-empty list names the files that break it.
# ---------------------------------------------------------------------------


def derived_carriers(root: Path) -> list[str]:
    """Each file below `root` that holds the begin marker, by any extension.

    THE POPULATION IS DERIVED, so a carrier added later joins the comparison
    with no edit to this file, and no count in a document can go stale.
    """
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not is_source_file(path):
            continue
        name = relative_name(path, root)
        if name in DERIVED_POPULATION_EXCLUSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if MARKER_BEGIN in text:
            found.append(name)
    return found


def compared_carriers(root: Path) -> list[str]:
    """The union of the derived population and the declared floor.

    THE UNION IS THE POINT. Derivation reaches a carrier nobody declared. The
    floor reaches a carrier whose marker was dropped, which derivation cannot
    see, because a file with no marker is not in the derived set.
    """
    return sorted(set(derived_carriers(root)) | set(DECLARED_CARRIERS))


def repair_modules_without_the_bar(root: Path, exemptions: dict) -> list[str]:
    """Modules of the memory-repair package that hold no begin marker.

    🔴 THE SECOND NET, AND ITS INDEPENDENCE IS THE WHOLE POINT. The declared
    floor is a LIST, so an edit that drops a path from the list takes the
    coverage with it. This reads the DIRECTORY, so the same edit changes
    nothing here.

    MEASURED, and it is why the net is a directory rather than an alphabet:
    with the bar region removed, neither module here names one route token, so
    arm 3 cannot reach them by any widening of `ROUTE_TOKENS`.
    """
    package = root / REPAIR_PACKAGE_DIR
    if not package.is_dir():
        return []
    return sorted(
        relative_name(path, root)
        for path in package.glob("*.py")
        if relative_name(path, root) not in exemptions
        and MARKER_BEGIN not in path.read_text(encoding="utf-8", errors="replace")
    )


def declared_carriers_without_a_marker(root: Path) -> list[str]:
    """Declared carriers that hold no begin marker. The anti-shrinkage arm."""
    return sorted(
        name
        for name in DECLARED_CARRIERS
        if MARKER_BEGIN not in read_carrier(root, name)
    )


def carriers_with_a_marker_fault(root: Path) -> list[str]:
    """Carriers whose marker pair cannot be read.

    A DECLARED CARRIER THAT HOLDS NO MARKER AT ALL IS SKIPPED HERE and reported
    by the arm above. One fault reported twice teaches a reader that the second
    report is noise.
    """
    faults = []
    for name in compared_carriers(root):
        text = read_carrier(root, name)
        if MARKER_BEGIN not in text:
            continue
        fault = marker_fault(text)
        if fault is not None:
            faults.append(f"{name} ({fault})")
    return faults


def _readable_regions(root: Path) -> dict:
    """Map each carrier with a sound marker pair to its region."""
    regions = {}
    for name in compared_carriers(root):
        text = read_carrier(root, name)
        if marker_fault(text) is None:
            regions[name] = marked_region(text)
    return regions


def copies_that_differ_from_the_source(root: Path) -> list[str]:
    """Copies whose region disagrees with the region of the source."""
    regions = _readable_regions(root)
    if SOURCE_HOME not in regions:
        return []
    want = normalised(regions[SOURCE_HOME])
    return sorted(
        name
        for name, region in regions.items()
        if name != SOURCE_HOME and normalised(region) != want
    )


def copies_that_differ_only_by_case(root: Path) -> list[str]:
    """Copies that agree with the source apart from CAPITALISATION.

    ARM 2, AND IT IS DERIVED RATHER THAN HELD. A copy reported here says the
    same words as the source and emphasises a different set of them. The guard
    holds no sentence to make that judgement: it compares the source against
    the copy, so the emphasis comes from the source at run time.
    """
    regions = _readable_regions(root)
    if SOURCE_HOME not in regions:
        return []
    source = regions[SOURCE_HOME]
    differ = []
    for name, region in regions.items():
        if name == SOURCE_HOME:
            continue
        if normalised(region) != normalised(source):
            continue
        if shouted_runs(region) != shouted_runs(source):
            differ.append(name)
    return sorted(differ)


def population_files(root: Path) -> list[Path]:
    """Each instruction surface the arm-3 walk covers.

    THE PATTERNS ARE `POPULATION_PATTERNS`, and this docstring does not count
    them. A count here goes stale on the day one is added.

    🔴 THE YIELD IS DE-DUPLICATED AND THE DECLARATION IS NOT TOUCHED. Two of
    the patterns overlap on purpose, so one file can arrive two times.
    MEASURED: 76 paths yielded and 75 distinct, with
    `skills/pact-memory/SKILL.md` matched by `skills/*/SKILL.md` and by
    `skills/pact-memory/**/*.md`.

    DE-DUPLICATION HERE IS SET-IDENTICAL. It loses NO member, so it is neither
    a widening nor a narrowing of the covered population. Removal of a glob
    is a change to the DECLARED population, which must not move, so the
    repair belongs to the yield rather than to `POPULATION_PATTERNS`.

    WHY IT MATTERS WHEN NO ARM COUNTS. Each arm here asks `any(...)` or keys
    on the path, so a repeat cannot move a verdict. THE FRAGILITY CENSUS
    COUNTS, and that count decides whether a route token is one edit away from
    idle. AN INFLATED COUNT READS AS SAFETY.
    """
    found: list[Path] = []
    for pattern in POPULATION_PATTERNS:
        found.extend(root.glob(pattern))
    return sorted(set(found))


def files_reaching_the_store(root: Path, tokens: dict, ignore_bar: bool = False) -> dict:
    """Map each selected instruction surface to the route tokens that selected it.

    `ignore_bar` removes each marked region before the read, which asks whether
    a file names a route OF ITS OWN. The alphabet floor needs that question and
    the silence arm needs the other one.
    """
    selected = {}
    for path in population_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        if ignore_bar:
            text = strip_marked_regions(text)
        hits = sorted(token for token in tokens if carries(text, token))
        if hits:
            selected[relative_name(path, root)] = hits
    return selected


def store_reaching_files_without_the_bar(
    root: Path, tokens: dict, exemptions: dict
) -> list[str]:
    """Selected surfaces that hold no readable marked region and take no exemption."""
    silent = []
    for name in sorted(files_reaching_the_store(root, tokens)):
        if name in exemptions:
            continue
        if marker_fault(read_carrier(root, name)) is not None:
            silent.append(name)
    return silent


def surfaces_the_alphabet_does_not_reach(root: Path, tokens: dict) -> list[str]:
    """Markdown carriers the alphabet fails to select, WITH THE BAR REMOVED.

    🔴 THIS READS THE STRIPPED TEXT, AND THE READ IS THE WHOLE ARM. The bar
    names two route tokens, `memory.db` inside `memory.db-wal` and
    `skills/pact-memory/scripts`, so with the bar in place EVERY carrier selects
    itself and this floor can never fire. MEASURED on the tree: with the raw
    text, `archive_pin.py` can leave the alphabet and no arm reddens. With the
    bar removed, that removal drops `commands/prune-memory.md` and this floor
    names it.
    """
    selected = set(files_reaching_the_store(root, tokens, ignore_bar=True))
    return sorted(ALPHABET_FLOOR - selected)


class TestTheInstrumentIsLive:
    """Controls on the instrument, and not rules about the tree.

    Each arm here fails when a walk or the matcher breaks, and it passes
    whatever the guarded prose says. AN EMPTY POPULATION AGREES WITH EVERY RULE
    BELOW, which is why the first controls assert the walks find files.
    """

    def test_the_instruction_surface_walk_finds_files(self):
        found = population_files(PLUGIN_DIR)
        assert found, (
            f"the walk below {PLUGIN_DIR} at {POPULATION_PATTERNS} returned "
            f"nothing. Arm 3 then passes over an empty set."
        )

    def test_the_derived_walk_reaches_python_and_markdown(self):
        """The derived walk must not be a markdown walk by accident.

        SOME CARRIERS ARE PYTHON. A walk that reached markdown alone would
        compare the markdown carriers, report green, and leave each Python copy
        uncompared. That is the drift this guard exists to stop, arriving
        inverted: not an unmarked copy outside the population, but a MARKED
        copy the population cannot see.

        THIS WAS MEASURED AS A LIVE BLINDNESS, not reasoned. With a
        markdown-only population, the region stripped from a Python carrier
        produced NO red, and the same mutation on a markdown carrier produced
        one. Same instrument, same mutation, opposite outcome, and the file
        type was the only difference.
        """
        reachable = {
            relative_name(p, PLUGIN_DIR)
            for p in PLUGIN_DIR.rglob("*")
            if p.is_file() and is_source_file(p)
        }
        for expected in (SOURCE_HOME, "scripts/archive_pin.py"):
            assert expected in reachable, (
                f"{expected} is outside the derived walk, so a marker there "
                f"would never be compared."
            )

    def test_each_declared_carrier_path_is_a_file(self):
        """A stale path fails safe, and this says which one it is.

        THIS ARM ALSO COVERS A SECOND THING NO OTHER ARM REPORTS, and the
        message below is where that coverage lives. Each copy carries a
        POINTER SENTENCE naming the source home. A rename of the source
        deadens each pointer at one time, and no arm reads a pointer, because
        the sentence is free text and a guard on free text is brittle. So the
        rename is DETECTED here and its consequence is NAMED here.
        """
        absent = sorted(
            name for name in DECLARED_CARRIERS if not (PLUGIN_DIR / name).is_file()
        )
        assert not absent, (
            f"{absent} are declared carriers and no file sits at those paths. "
            f"If a carrier moved, correct the path. Do not remove it to quiet "
            f"a red. Paths are relative to {PLUGIN_DIR.name}."
            f"{pointer_warning(absent)}"
        )

    def test_each_derived_exclusion_does_work(self):
        """An exclusion that no walk reaches excludes nothing and reads as coverage.

        A STALE KEY FAILS SAFE, because the file rejoins the population and an
        arm reddens. AN ENTRY THAT CANNOT DO WORK FAILS OPEN. So the burden
        sits on REMOVAL rather than on addition.
        """
        idle = []
        for name, cause in DERIVED_POPULATION_EXCLUSIONS.items():
            path = PLUGIN_DIR / name
            if not path.is_file():
                idle.append(f"{name} (no file at that path)")
            elif MARKER_BEGIN not in path.read_text(encoding="utf-8", errors="replace"):
                idle.append(f"{name} (holds no begin marker)")
            elif not cause.strip():
                idle.append(f"{name} (states no cause)")
        assert not idle, (
            f"these derived-population exclusions cannot do work: {idle}. An "
            f"entry for a file that no walk selects excludes nothing from any "
            f"arm, and it reads as coverage that was considered."
        )

    def test_this_guard_is_excluded_by_role_and_not_by_accident(self):
        """The role rule, asserted rather than written.

        This file HOLDS the marker literals, so it is a genuine member of the
        derived walk. It must be named out, and it must not appear in the
        compared population.
        """
        me = relative_name(Path(__file__).resolve(), PLUGIN_DIR)
        assert me in DERIVED_POPULATION_EXCLUSIONS
        assert me not in compared_carriers(PLUGIN_DIR)

    def test_the_comparison_survives_a_wrap_and_a_recapitalisation(self):
        """A control on the matcher, and not on the files it reads."""
        wrapped = "a raw path such as\n    memory.db\n    is a route"
        assert "memory.db is a route" not in wrapped
        assert carries(wrapped, "memory.db is a route")

        shouted = "MEMORY.DB IS A ROUTE"
        assert "memory.db is a route" not in shouted
        assert carries(shouted, "memory.db is a route")

    def test_the_comparison_rejects_a_token_that_is_absent(self):
        """The negative direction. A matcher that agrees with anything is useless."""
        assert not carries("nothing of the kind appears here", "memory.db")

    def test_the_shout_reader_finds_emphasis_and_not_an_acronym(self):
        """A control on arm 2, and each half must do work.

        Without the floor an acronym counts as emphasis, and the arm then
        reddens when one copy spells `WAL` in a sentence the source does not.
        """
        assert shouted_runs("DO NOT USE the flag") == ["DO NOT USE"]
        assert shouted_runs("the WAL and the CLI") == []
        assert shouted_runs("do not use the flag") == []

    def test_the_region_reader_refuses_a_broken_pair(self):
        """Each marker fault must be a fault, and a sound pair must be sound."""
        sound = f"head {MARKER_BEGIN} body {MARKER_END} tail"
        assert marker_fault(sound) is None
        assert marked_region(sound) == " body "

        assert marker_fault(f"head {MARKER_BEGIN} body") is not None
        assert marker_fault(f"body {MARKER_END} tail") is not None
        assert marker_fault("no markers at all") is not None
        assert marker_fault(f"{MARKER_END} body {MARKER_BEGIN}") is not None
        assert (
            marker_fault(f"{MARKER_BEGIN} a {MARKER_END} {MARKER_BEGIN} b {MARKER_END}")
            is not None
        )

    def test_the_region_stripper_takes_the_region_and_leaves_the_rest(self):
        """A unit pin on the helper the alphabet floor rests on.

        WITHOUT THIS ARM THE STRIPPER CAN BREAK IN SILENCE. Measured on the
        live tree: the floor returns the same list with the strip working and
        with it disabled, because each markdown carrier names a route token
        outside its region anyway. So no shipped arm reads the difference.

        🔴 THE FIXTURE REGION MUST SPAN MORE THAN ONE LINE, and that is the
        whole design rather than a detail. The strip matches with `re.DOTALL`.
        A ONE-LINE fixture passes with that flag removed, so it catches a
        stripper that returns its input and MISSES a stripper that lost the
        flag. A fixture that cannot express one of the two faults is the
        wrong-alphabet failure this file records elsewhere, written into a
        control.

        THE SECOND HALF IS THE OTHER DIRECTION. A strip that takes too much
        removes a token the file names OF ITS OWN, which sends the floor the
        opposite lie.
        """
        text = (
            "Prose above names archive_pin.py for its own reason.\n"
            f"{MARKER_BEGIN}\n"
            "The rule names memory.db-wal and it wraps here, then it\n"
            "names skills/pact-memory/scripts on a second line.\n"
            f"{MARKER_END}\n"
            "Prose below names cli.py for its own reason.\n"
        )
        stripped = strip_marked_regions(text)
        for token in ("memory.db", "skills/pact-memory/scripts"):
            assert not carries(stripped, token), (
                f"{token!r} survived the strip, so a route token that sits "
                f"INSIDE the bar still selects the carrier holding it. The "
                f"alphabet floor then asks whether a file names a route OF "
                f"ITS OWN and reads the bar's answer instead, which makes the "
                f"floor unable to fire. TWO CAUSES: the strip returns its "
                f"input, or its pattern lost `re.DOTALL` and so cannot cross "
                f"the line break in the region above."
            )
        for token in ("archive_pin.py", "cli.py"):
            assert carries(stripped, token), (
                f"{token!r} sits OUTSIDE the marked region and the strip "
                f"removed it. The strip must take the region alone, or the "
                f"floor stops seeing a route a file names for its own reason."
            )


class TestEveryDeclaredCarrierIsPlaced:
    """The anti-shrinkage floor.

    DERIVATION NOTICES A FILE THAT HAS A MARKER. ONLY THIS NOTICES A FILE THAT
    SHOULD HAVE ONE AND DOES NOT. A dropped marker removes a carrier from the
    compared set in silence, and a smaller set reporting green reads the same as
    a larger set reporting green.
    """

    def test_each_repair_module_holds_the_marker(self):
        """THE SECOND NET, INDEPENDENT OF THE DECLARED LIST.

        A carrier loses ALL coverage when one edit drops its path from
        `DECLARED_CARRIERS` and a second drops its region. This arm reads the
        DIRECTORY, so the first edit cannot reach it.
        """
        missing = repair_modules_without_the_bar(PLUGIN_DIR, REPAIR_PACKAGE_EXEMPTIONS)
        assert not missing, (
            f"these modules of {REPAIR_PACKAGE_DIR} hold no begin marker: "
            f"{missing}. Each module of that package carries the store-access "
            f"rule. Place the marked region from {SOURCE_HOME}, or add the "
            f"path to REPAIR_PACKAGE_EXEMPTIONS WITH A STATED CAUSE. DO NOT "
            f"repair this by an edit to DECLARED_CARRIERS: that list is a "
            f"separate net and this arm is the one that survives its removal."
        )

    def test_each_repair_exemption_can_do_work(self):
        """An entry that no walk reaches excludes nothing and reads as coverage.

        THE TWO SIBLING SETS HOLD THIS RULE AND THIS ONE DID NOT.
        `POPULATION_EXEMPTIONS` and `DERIVED_POPULATION_EXCLUSIONS` each carry
        an arm that refuses an idle entry. MEASURED: an idle key added to
        `REPAIR_PACKAGE_EXEMPTIONS` left the suite green, so the third set
        took a rule the file states for the other two.

        A STALE KEY FAILS SAFE, because the module rejoins the package walk
        and `test_each_repair_module_holds_the_marker` reddens. AN ENTRY THAT
        CANNOT DO WORK FAILS OPEN. So the burden sits on REMOVAL.
        """
        package = PLUGIN_DIR / REPAIR_PACKAGE_DIR
        walked = {relative_name(path, PLUGIN_DIR) for path in package.glob("*.py")}
        idle = sorted(key for key in REPAIR_PACKAGE_EXEMPTIONS if key not in walked)
        assert not idle, (
            f"these repair-package exemptions cannot do work: {idle}. The "
            f"package walk covers {REPAIR_PACKAGE_DIR}/*.py, so a key outside "
            f"it excludes nothing from any arm and reads as coverage that was "
            f"considered. MEMBERSHIP OF THE WALK IS THE TEST, not `is_file()`: "
            f"a module present under another directory passes an existence "
            f"check and joins no population. Paths are relative to "
            f"{PLUGIN_DIR.name}."
        )

    def test_each_repair_exemption_states_a_cause(self):
        """An entry with no cause is a name, and a name goes stale in silence."""
        bare = sorted(
            key for key, cause in REPAIR_PACKAGE_EXEMPTIONS.items()
            if not str(cause).strip()
        )
        assert not bare, (
            f"these repair-package exemptions state no cause: {bare}. A "
            f"module is exempt from the store-access rule for a reason, and a "
            f"reader who meets the entry without one cannot judge whether it "
            f"continues to hold."
        )

    def test_each_declared_carrier_holds_the_marker(self):
        missing = declared_carriers_without_a_marker(PLUGIN_DIR)
        assert not missing, (
            f"these declared carriers hold no begin marker: {missing}. TWO "
            f"CAUSES, AND THEY MEAN OPPOSITE THINGS. EITHER the block is NOT "
            f"PLACED THERE YET, which is a sequencing state and not a defect, "
            f"OR a marker was dropped from a carrier that had one, which is "
            f"the silent shrinkage this floor exists to refuse. Read the file "
            f"before you decide. DO NOT repair either case by removal of a "
            f"path from DECLARED_CARRIERS, and DO NOT repair it with an "
            f"exemption. Paths are relative to {PLUGIN_DIR.name}."
        )


class TestTheMarkedRegionsAgree:
    """ARM 1. FIDELITY.

    The carriers hold one text. This reads the region from the source and
    compares each copy to it.
    """

    def test_each_carrier_holds_one_readable_marker_pair(self):
        faults = carriers_with_a_marker_fault(PLUGIN_DIR)
        assert not faults, (
            f"these carriers do not hold a readable marker pair: {faults}. A "
            f"HALF-PRESENT PAIR IS A RED AND NOT A SKIP. Some context "
            f"renderings strip an HTML comment, so a marker can go out with no "
            f"intent behind it. Restore the pair {MARKER_BEGIN} and "
            f"{MARKER_END} around the block."
        )

    def test_the_source_region_holds_a_rule(self):
        """Non-vacuity for the arms below, and not a duplicate of them.

        AN AGREEMENT ON NOTHING IS AN AGREEMENT. With the block emptied in each
        carrier, the arm below compares one empty string with another and
        reports green.
        """
        text = read_carrier(PLUGIN_DIR, SOURCE_HOME)
        assert marker_fault(text) is None, (
            f"{SOURCE_HOME} holds no readable marker pair, so there is no "
            f"source region to measure. THE COMPARISON ARMS RETURN AN EMPTY "
            f"LIST IN THIS STATE, so this arm is the one that reports it."
        )
        size = len(normalised(marked_region(text)))
        assert size >= MINIMUM_REGION_CHARS, (
            f"the marked region in {SOURCE_HOME} is {size} characters, below "
            f"the floor of {MINIMUM_REGION_CHARS}. An emptied region passes "
            f"the fidelity arm because each copy agrees with it. Restore the "
            f"block. DO NOT clear this red by a lower floor."
        )

    def test_each_copy_agrees_with_the_source(self):
        differ = copies_that_differ_from_the_source(PLUGIN_DIR)
        assert not differ, (
            f"the marked region in {differ} disagrees with the marked region "
            f"in {SOURCE_HOME}, after whitespace and case are removed. THE "
            f"SOURCE IS THE SOURCE: copy the region from {SOURCE_HOME} into "
            f"each file named, rather than the other way. IF THE CHANGE IS "
            f"DELIBERATE, edit the source and each copy in ONE commit. That "
            f"tax is the mechanism and not an accident of it. THIS ARM CANNOT "
            f"TELL YOU THE SOURCE IS CORRECT, only that the copies agree."
        )


class TestTheEmphasisSurvivesTheCopy:
    """ARM 2. EMPHASIS.

    THE CAPITALS ARE LOAD-BEARING. The block shouts one line so that it works
    for a reader who has misunderstood everything else, and a reader under
    context pressure obeys a shouted line more reliably than a quiet one. A copy
    that says the same words in a quieter voice is a WEAKENED rule, and arm 1
    casefolds, so arm 1 passes it in silence.

    WHY THIS IS NOT A CASE-SENSITIVE COMPARE OF THE WHOLE BLOCK. That form
    reddens on an ordinary capitalisation difference and its cheapest cure is a
    constant edit, which is the mirror failure. This compares the EMPHASIS
    alone, and it derives the emphasis from the source rather than hold a
    sentence, so the guard keeps no copy of the wording.
    """

    def test_the_source_region_shouts_something(self):
        """Non-vacuity, and the arm below is empty without it.

        TWO EMPTY LISTS AGREE. If a reword removes each capitalised run from
        the source, the arm below compares nothing with nothing for every copy
        and reports green for ever. It would then read as emphasis coverage
        while covering no emphasis at all.
        """
        text = read_carrier(PLUGIN_DIR, SOURCE_HOME)
        assert marker_fault(text) is None, (
            f"{SOURCE_HOME} holds no readable marker pair, so there is no "
            f"source region to read emphasis from."
        )
        runs = shouted_runs(marked_region(text))
        assert runs, (
            f"the marked region in {SOURCE_HOME} holds no run of "
            f"{MINIMUM_SHOUT_CHARS} or more capitals, so the arm below "
            f"compares an empty list with an empty list and can never fire. "
            f"EITHER the block lost its emphasis, which is the drift this arm "
            f"exists to catch and which no other arm in this file reports, OR "
            f"MINIMUM_SHOUT_CHARS was raised past the longest run. Read the "
            f"block before you change the constant."
        )

    def test_each_copy_shouts_what_the_source_shouts(self):
        quiet = copies_that_differ_only_by_case(PLUGIN_DIR)
        assert not quiet, (
            f"{quiet} say the same words as {SOURCE_HOME} and emphasise a "
            f"different set of them. A de-emphasised bright line is a weaker "
            f"rule for a reader who scans. Restore the capitalisation of the "
            f"source. IF THE CHANGE IS DELIBERATE, edit the source and each "
            f"copy in one commit."
        )


class TestEachStoreReachingSurfaceCarriesTheBar:
    """ARM 3. POPULATION.

    An instruction surface that hands a reader a route to the store, and no rule
    for it, is the defect this repair closes. This reaches such a file on the
    day it arrives.

    ITS POPULATION IS INSTRUCTION SURFACES ALONE. The memory package and the
    hooks reach the store legitimately, so a widened walk would report them and
    the report would be noise.
    """

    def test_the_alphabet_reaches_each_markdown_carrier(self):
        """The alphabet floor. A token set that narrows covers less in silence."""
        unreached = surfaces_the_alphabet_does_not_reach(PLUGIN_DIR, ROUTE_TOKENS)
        assert not unreached, (
            f"{unreached} are markdown carriers and no token in ROUTE_TOKENS "
            f"selects them. A token set that narrows takes a carrier out of "
            f"arm 3 while each other arm keeps passing. Restore the token."
        )

    def test_no_forbidden_route_token_is_taught_without_the_bar(self):
        """A file that teaches the FORBIDDEN route needs the rule most.

        THIS ARM IS EXPECTED TO PASS OVER AN EMPTY SELECTION, and that is the
        one place in this file where an empty set is the GOOD outcome. The
        companion arm that refuses an idle token applies to `ROUTE_TOKENS`
        alone, for that reason.
        """
        teaching = sorted(
            name
            for name, hits in files_reaching_the_store(
                PLUGIN_DIR, FORBIDDEN_ROUTE_TOKENS
            ).items()
            if marker_fault(read_carrier(PLUGIN_DIR, name)) is not None
        )
        assert not teaching, (
            f"{teaching} teach the module API and hold no marked region. The "
            f"bar forbids that route, so a reader of one of these files is "
            f"taught the thing the rule removes, with no rule beside it. "
            f"Place the marked region from {SOURCE_HOME}, or correct the file "
            f"to the CLI route."
        )

    def test_no_store_reaching_surface_is_silent(self):
        silent = store_reaching_files_without_the_bar(
            PLUGIN_DIR, SELECTING_TOKENS, POPULATION_EXEMPTIONS
        )
        assert not silent, (
            f"{silent} name a route to the memory store and hold no readable "
            f"marked region. A reader who obeys one of these files reaches the "
            f"store with no rule attached. Place the marked region from "
            f"{SOURCE_HOME}. 🔴 IF THE CAUSE IS THAT THE BLOCK IS NOT PLACED "
            f"THERE YET, REPORT IT AND LEAVE THE RED. Do NOT add the path to "
            f"POPULATION_EXEMPTIONS: a placement gap repaired with an "
            f"exemption is a silent carve-out for a file the design calls a "
            f"genuine hole."
        )

    def test_each_exemption_can_do_work(self):
        """An entry that no walk reaches excludes nothing and reads as coverage."""
        reached = set(files_reaching_the_store(PLUGIN_DIR, ROUTE_TOKENS))
        idle = sorted(key for key in POPULATION_EXEMPTIONS if key not in reached)
        assert not idle, (
            f"these exemption entries cannot do work: {idle}. The route "
            f"alphabet does not select them, so each excludes a file from a "
            f"question nobody puts."
        )

    def test_each_exemption_states_a_cause(self):
        """An entry with no cause is a name, and a name goes stale in silence."""
        bare = sorted(k for k, v in POPULATION_EXEMPTIONS.items() if not str(v).strip())
        assert not bare, f"these exemption entries state no cause: {bare}."


class TestTheAlphabetDoesNotSelectItself:
    """The non-vacuity control for arm 3.

    AFTER THE BAR LANDS, THE REGION NAMES TWO ROUTE TOKENS, which are
    `memory.db` inside `memory.db-wal` and `skills/pact-memory/scripts`. So each
    carrier selects itself, and the rule "a selected file carries the bar" holds
    for those files BY CONSTRUCTION. That does not weaken arm 3, because its
    work is over a file that names a token and holds NO region. It does make the
    arm easy to hollow out: an alphabet built from the block wording selects the
    carriers and reaches no new surface at all.

    THE ALPHABET FLOOR CARRIES HALF OF THIS PROPERTY, by reading the stripped
    text. The arm below carries the other half, over the tokens.
    """

    def test_the_population_holds_no_duplicate_path(self):
        """🔴 THE CLASS, NOT THE INSTANCE.

        `POPULATION_PATTERNS` holds two globs that overlap, so one file can
        arrive two times. `population_files` de-duplicates the yield, and THIS
        ARM GUARDS THAT DE-DUPLICATION.

        🔴 STATED ACCURATELY, BECAUSE A MUTANT CORRECTED THE FIRST WORDING.
        This arm does NOT detect a new overlapping glob, and it does not need
        to. MEASURED: with a third overlapping glob added, the arm stays
        GREEN, because the de-duplication absorbs the overlap and no duplicate
        reaches the yield. A new glob is harmless while the de-duplication
        stands. WHAT REDS IS THE REMOVAL OF THE DE-DUPLICATION, measured, and
        that is the one edit that re-opens the defect.

        THE FAILURE DIRECTION IS WHAT EARNS THE ROW. NO SHIPPED ARM COUNTS
        THIS POPULATION: each one asks `any(...)` or keys on the path, so a
        repeat moves no verdict and stays invisible. The fragility census is
        the one instrument that counts, and its count decides whether a route
        token sits one edit away from idle. AN INFLATED COUNT READS AS SAFETY,
        so the defect hides in the direction of a clean answer.

        MEASURED before the repair: 76 paths yielded, 75 distinct, the repeat
        being `skills/pact-memory/SKILL.md`.
        """
        paths = population_files(PLUGIN_DIR)
        repeated = sorted(
            {str(p) for p in paths if paths.count(p) > 1}
        )
        assert not repeated, (
            f"`population_files` yields {len(paths)} paths and "
            f"{len(set(paths))} distinct. Repeated: {repeated}. TWO PATTERNS "
            f"IN `POPULATION_PATTERNS` OVERLAP. DO NOT REPAIR THIS BY REMOVAL "
            f"OF A PATTERN: that changes the DECLARED population, and the "
            f"declaration must not move. De-duplicate the YIELD instead, which "
            f"loses no member and is set-identical."
        )

    def test_each_token_does_work_outside_the_bar_text(self):
        """No token may live only inside the marked regions."""
        idle = []
        stripped = [
            strip_marked_regions(p.read_text(encoding="utf-8", errors="replace"))
            for p in population_files(PLUGIN_DIR)
        ]
        for token in sorted(ROUTE_TOKENS):
            if not any(carries(text, token) for text in stripped):
                idle.append(token)
        # FORBIDDEN_ROUTE_TOKENS ARE NOT READ HERE, and the omission is the
        # ruling rather than an oversight. A forbidden-route token that
        # selects nothing means no file teaches that route, which is the
        # outcome the bar exists to produce.
        assert not idle, (
            f"these tokens appear only inside the bar text, or nowhere: "
            f"{idle}. A token taken from the guarded wording selects the "
            f"carriers and reaches no new surface. Remove it, or state a cause "
            f"for a token that names a route no file uses today."
        )


class TestTheWriteHalfFactHolds:
    """ARM 4. FACT.

    The shipped text tells an agent that `setup_memory.py` accepts no
    `--db-path`, so a write through that script cannot be steered away from the
    live store. The claim is in one file and the evidence is in another.

    THIS ARM READS THE EVIDENCE FILE AS TEXT. It imports nothing below the
    pact-memory package and it runs nothing, so it cannot reach the live store.
    """

    def test_the_evidence_file_is_readable_and_holds_its_control(self):
        """Non-vacuity, and it discriminates.

        `db_path` IS in the file as a parameter name. `--db-path`, the flag, is
        not. A read that finds the first proves the read is live, so the
        absence below cannot come from an empty read or from a moved file.
        """
        path = PLUGIN_DIR / EVIDENCE_FILE
        assert path.is_file(), (
            f"{EVIDENCE_FILE} is absent. THE ARM BELOW WOULD PASS OVER AN "
            f"EMPTY STRING and report a clean absence."
        )
        text = path.read_text(encoding="utf-8", errors="replace")
        assert carries(text, EVIDENCE_CONTROL_TOKEN), (
            f"{EVIDENCE_FILE} does not hold {EVIDENCE_CONTROL_TOKEN!r}. That "
            f"token is the control that separates a live read from an empty "
            f"one. Without it the arm below proves nothing."
        )

    def test_the_source_home_names_the_evidence_file(self):
        """The claim must have a carrier, or the arm below pins a fact nobody states.

        THIS HOLDS AN ADDRESS AND NOT A SENTENCE. A copy of the claim here
        would make this test the specification.
        """
        text = read_carrier(PLUGIN_DIR, SOURCE_HOME)
        assert carries(text, "setup_memory.py"), (
            f"{SOURCE_HOME} no longer names setup_memory.py. The arm below "
            f"pins a fact about a script that the shipped text does not "
            f"mention, so a green there tells a reader nothing."
        )

    def test_the_evidence_file_holds_no_db_path_flag(self):
        path = PLUGIN_DIR / EVIDENCE_FILE
        text = path.read_text(encoding="utf-8", errors="replace")
        assert FORBIDDEN_FLAG not in text, (
            f"{EVIDENCE_FILE} now holds {FORBIDDEN_FLAG!r}. The shipped text "
            f"in {SOURCE_HOME} tells an agent this script accepts no such "
            f"flag, so that claim is now stale. Correct the shipped text, or "
            f"remove the flag from the script. DO NOT clear this red by an "
            f"edit to FORBIDDEN_FLAG."
        )


# ---------------------------------------------------------------------------
# THE MUTATION MODEL.
#
# A GUARD THAT HAS NOT BEEN MADE TO FAIL IS A CLAIM RATHER THAN A GATE. Each
# arm below builds a small tree with the shape the walks expect, applies ONE
# deliberate mutation, and reads the SAME predicate the shipped arm calls.
#
# EACH MUTATION ASSERTS THAT IT CHANGED THE TEXT. A mutation that silently
# fails to mutate produces a proof of nothing, and it fails in the direction
# that reads as success.
#
# WHAT THE MODEL DOES NOT PROVE. It runs against a built tree, so it cannot
# show that a shipped arm reads the live one. The arms above close that from
# the other side, and `TestTheModelTreeIsSound` states the bound.
# ---------------------------------------------------------------------------

BODY = "STORE ACCESS. DO NOT USE the flag. A rule stands here.\n"


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _region(body: str = BODY) -> str:
    return f"{MARKER_BEGIN}\n{body}{MARKER_END}\n"


def _model_tree(tmp_path: Path) -> Path:
    """Build a tree with one carrier for each route, each holding the region.

    THE ROUTE TOKEN OF EACH MARKDOWN CARRIER SITS OUTSIDE THE REGION, so the
    tree models the property the alphabet floor asserts of the live tree. The
    PYTHON carriers name no route token, which models the measured fact that no
    alphabet reaches two of the three.
    """
    root = tmp_path / "plugin"
    prose = {
        SOURCE_HOME: "The CLI lives at cli.py, and setup_memory.py writes.",
        "agents/pact-secretary.md": "Use the update CLI command for a save.",
        "commands/prune-memory.md": "Run archive_pin.py --index 3.",
        "reference/config.md": "The package is skills/pact-memory/scripts.",
        "protocols/pact-state-recovery.md": "The store file is memory.db.",
        "protocols/pact-protocols.md": "The store file is memory.db.",
        "skills/pact-handoff-harvest/SKILL.md": 'Run search "context" for context.',
        # THE MODEL MUST AGREE WITH THE TREE ON THIS ONE. archive_pin.py names
        # three route tokens OUTSIDE its marked region, so the alphabet is its
        # second net. A model that gave it no token would report a hole this
        # carrier does not have.
        "scripts/archive_pin.py": "# Reached by cli.py, writes through archive_pin.py.",
    }
    # 🔴 THE DEFAULT DEPENDS ON THE FILE TYPE, AND THAT IS A FIX FOR A CLASS
    # RATHER THAN FOR ONE PATH. A markdown carrier joins `ALPHABET_FLOOR`, so
    # it must name a route token OUTSIDE the bar. A Python carrier does not,
    # and two of them name no token at all.
    #
    # ONE DEFAULT FOR THE TWO TYPES BROKE THE MODEL WHEN A MARKDOWN CARRIER WAS
    # DECLARED. Two model arms failed, in the model, while the tree was
    # correct. So a maintainer who did the live work correctly read a stale
    # model rather than a missing sentence. A type-aware default makes that
    # unrepresentable, so the NEXT markdown carrier costs no edit here.
    for name in sorted(DECLARED_CARRIERS):
        if name.endswith(".py"):
            default = "This module states the bar and names no route."
        else:
            default = "The store file is memory.db."
        text = prose.get(name, default)
        comment = "#" if name.endswith(".py") else ""
        _write(root, name, f"{comment} Heading\n\n{text}\n\n{_region()}")
    _write(root, "agents/pact-preparer.md", "This file teaches research.\n")
    _write(root, EVIDENCE_FILE, "def ensure_initialized(db_path=None):\n    pass\n")
    return root


def _mutate(path: Path, old: str, new: str) -> None:
    """Replace `old` with `new`, and refuse a mutation that changes nothing."""
    assert path.is_file(), (
        f"{path.name} is absent from the model tree, so this mutation cannot "
        f"run. THE LIKELY CAUSE IS A CARRIER PATH REMOVED FROM "
        f"DECLARED_CARRIERS, because the model builds one file for each "
        f"declared path. READ THAT SET BEFORE YOU EDIT THIS MODEL. A carrier "
        f"dropped from the declared list loses one of its two nets, and the "
        f"repair is to restore the path rather than to repair this arm."
    )
    text = path.read_text(encoding="utf-8")
    mutated = text.replace(old, new)
    assert mutated != text, (
        f"the mutation changed nothing in {path.name}, so the arm proves "
        f"nothing. Correct the mutation before you read its result."
    )
    path.write_text(mutated, encoding="utf-8")


class TestTheModelTreeIsSound:
    """The landing control, and the stated bound of the whole model.

    WITHOUT THIS ARM EACH MUTATION ARM BELOW CAN PASS ON A BROKEN TREE, because
    a predicate that reports every file reports the mutated one too.
    """

    def test_the_unmutated_tree_passes_each_predicate(self, tmp_path):
        root = _model_tree(tmp_path)
        assert declared_carriers_without_a_marker(root) == []
        assert carriers_with_a_marker_fault(root) == []
        assert copies_that_differ_from_the_source(root) == []
        assert copies_that_differ_only_by_case(root) == []
        assert surfaces_the_alphabet_does_not_reach(root, ROUTE_TOKENS) == []
        assert (
            store_reaching_files_without_the_bar(
                root, ROUTE_TOKENS, POPULATION_EXEMPTIONS
            )
            == []
        )

    def test_the_derived_walk_reaches_the_python_carriers(self, tmp_path):
        """The inverted-drift case, modelled.

        A markdown-only walk compares the markdown carriers, reports green, and
        leaves three marked copies uncompared.
        """
        root = _model_tree(tmp_path)
        derived = derived_carriers(root)
        for name in sorted(DECLARED_CARRIERS):
            assert name in derived, f"{name} is outside the derived population"
        assert any(name.endswith(".py") for name in derived)

    def test_the_model_reaches_the_innocent_file(self, tmp_path):
        """A model that reaches fewer files than it claims proves less than it says."""
        root = _model_tree(tmp_path)
        reached = {relative_name(p, root) for p in population_files(root)}
        assert "agents/pact-preparer.md" in reached
        assert "agents/pact-preparer.md" not in files_reaching_the_store(
            root, ROUTE_TOKENS
        )


class TestArmOneGoesRed:
    """FIDELITY, made to fail five ways.

    WHAT THESE DO NOT PROVE: that a shipped arm reads the live tree. They run
    against a built tree. The arms above close that half.
    """

    def test_a_drifted_copy_is_named(self, tmp_path):
        root = _model_tree(tmp_path)
        _mutate(root / "reference/config.md", "A rule stands here", "A rule shifted")
        assert copies_that_differ_from_the_source(root) == ["reference/config.md"]

    def test_a_drifted_python_copy_is_named(self, tmp_path):
        """The carrier type a markdown-only population could not see."""
        root = _model_tree(tmp_path)
        _mutate(root / "scripts/archive_pin.py", "A rule stands here", "A rule shifted")
        assert copies_that_differ_from_the_source(root) == ["scripts/archive_pin.py"]

    def test_a_drifted_source_names_each_copy(self, tmp_path):
        """The direction of the compare, asserted rather than described.

        An edit to the SOURCE alone puts each copy out of agreement. A guard
        that took the majority as truth would report the source instead, and
        one drifted copy would lose that vote and pass.
        """
        root = _model_tree(tmp_path)
        _mutate(root / SOURCE_HOME, "A rule stands here", "A rule shifted")
        expected = sorted(DECLARED_CARRIERS - {SOURCE_HOME})
        assert copies_that_differ_from_the_source(root) == expected

    def test_a_dropped_marker_is_named_and_does_not_pass_in_silence(self, tmp_path):
        """FAIL CLOSED. A half-present pair must not leave the population quietly."""
        root = _model_tree(tmp_path)
        _mutate(root / "protocols/pact-protocols.md", MARKER_END, "")
        faults = carriers_with_a_marker_fault(root)
        assert len(faults) == 1 and faults[0].startswith("protocols/pact-protocols.md")
        assert copies_that_differ_from_the_source(root) == [], (
            "the fidelity arm reported the same file as the marker arm. One "
            "fault reported twice teaches a reader that the second is noise."
        )

    def test_an_emptied_block_passes_the_fidelity_arm(self, tmp_path):
        """THE HOLE THE SIZE FLOOR CLOSES, shown rather than described."""
        root = _model_tree(tmp_path)
        for name in sorted(DECLARED_CARRIERS):
            _mutate(root / name, _region(), _region(body=""))
        assert copies_that_differ_from_the_source(root) == []
        assert carriers_with_a_marker_fault(root) == []
        source_region = marked_region(read_carrier(root, SOURCE_HOME))
        assert len(normalised(source_region)) < MINIMUM_REGION_CHARS

    def test_a_re_wrapped_copy_is_not_named(self, tmp_path):
        """The false-alarm direction. A re-wrap changes no word and must pass."""
        root = _model_tree(tmp_path)
        _mutate(
            root / "agents/pact-secretary.md",
            "A rule stands here.",
            "A rule\nstands\nhere.",
        )
        assert copies_that_differ_from_the_source(root) == []


class TestTheFloorGoesRed:
    """THE ANTI-SHRINKAGE FLOOR, and its unique coverage measured rather than claimed.

    A DROPPED MARKER IS INVISIBLE TO DERIVATION, because a file with no marker
    is not in the derived set. Only the declared floor sees the absence.
    """

    def test_a_carrier_that_loses_both_markers_is_named_by_the_floor_alone(
        self, tmp_path
    ):
        root = _model_tree(tmp_path)
        _mutate(root / "reference/config.md", _region(), "")
        assert declared_carriers_without_a_marker(root) == ["reference/config.md"]
        assert "reference/config.md" not in derived_carriers(root), (
            "the derived walk still reports the file, so the floor is not the "
            "only arm that catches this. Correct the stated cause above."
        )
        assert copies_that_differ_from_the_source(root) == [], (
            "the fidelity arm caught the removal, so the floor is no longer "
            "unique for this condition."
        )

    def test_a_python_carrier_that_is_never_placed_is_named(self, tmp_path):
        """The state this gate sits in before the placement lands."""
        root = _model_tree(tmp_path)
        _mutate(root / "scripts/memory_repair/__init__.py", _region(), "")
        assert declared_carriers_without_a_marker(root) == [
            "scripts/memory_repair/__init__.py"
        ]

    def test_the_pointer_warning_fires_for_the_source_and_not_for_a_copy(self):
        """A message branch nothing exercises is untested prose.

        THE TWO HALVES ARE THE ARM. The warning must appear when the SOURCE is
        the absent path, and it must stay silent for a copy, because a copy
        carries no pointer to itself and the note would then be incorrect.
        """
        assert SOURCE_HOME in pointer_warning([SOURCE_HOME])
        assert pointer_warning(["reference/config.md"]) == ""
        assert pointer_warning([]) == ""

    def test_a_rename_of_the_source_is_caught_by_two_arms_already(self, tmp_path):
        """WHY NO THIRD ARM GUARDS THE POINTER, measured rather than argued.

        A rename of the source home reddens the path arm AND the marker floor.
        A third arm checking the same path would report one fault a third
        time. This asserts the two, so a later reader who proposes that arm
        meets the measurement rather than the opinion.
        """
        root = _model_tree(tmp_path)
        (root / SOURCE_HOME).rename(root / "skills/pact-memory/GUIDE.md")
        absent = sorted(n for n in DECLARED_CARRIERS if not (root / n).is_file())
        assert absent == [SOURCE_HOME]
        assert declared_carriers_without_a_marker(root) == [SOURCE_HOME]
        assert SOURCE_HOME in pointer_warning(absent)

    def test_a_carrier_keeps_a_net_when_its_declared_path_goes(self, tmp_path):
        """🔴 THE TWO-EDIT ATTACK, AND IT IS THE ONE THAT DEFEATED THIS GUARD.

        Edit one drops a path from `DECLARED_CARRIERS`. Edit two drops that
        file region. The floor cannot report a path it no longer holds, and
        the derived population cannot see a file with no marker. So the
        carrier had NO net and the suite stayed green.

        EACH CARRIER MUST KEEP A SECOND NET THAT THE FIRST EDIT CANNOT REACH.
        A Python module of the repair package keeps the DIRECTORY net. Each
        other carrier keeps the ROUTE ALPHABET, measured with its region
        removed, so the token comes from the file rather than from the bar.
        """
        root = _model_tree(tmp_path)
        for victim in sorted(DECLARED_CARRIERS):
            _mutate(root / victim, _region(), "")
            reduced = frozenset(DECLARED_CARRIERS - {victim})

            floor_net = victim in [
                name
                for name in reduced
                if MARKER_BEGIN not in read_carrier(root, name)
            ]
            package_net = victim in repair_modules_without_the_bar(
                root, REPAIR_PACKAGE_EXEMPTIONS
            )
            alphabet_net = victim in store_reaching_files_without_the_bar(
                root, ROUTE_TOKENS, POPULATION_EXEMPTIONS
            )
            assert not floor_net, "the floor saw a path it no longer holds"
            assert package_net or alphabet_net, (
                f"{victim} has NO net once its declared path goes. One edit to "
                f"DECLARED_CARRIERS and one to the file then pass in silence. "
                f"Give it a second net: the repair package uses the directory, "
                f"and each other carrier must name a route token OUTSIDE its "
                f"marked region."
            )
            root = _model_tree(tmp_path)

    def test_the_floor_is_not_the_derived_population(self, tmp_path):
        """A carrier nobody declared joins the comparison, with no edit here."""
        root = _model_tree(tmp_path)
        _write(root, "agents/pact-newcomer.md", f"New surface.\n\n{_region()}")
        assert "agents/pact-newcomer.md" in derived_carriers(root)
        assert "agents/pact-newcomer.md" in compared_carriers(root)
        _mutate(root / "agents/pact-newcomer.md", "A rule stands here", "A rule shifted")
        assert copies_that_differ_from_the_source(root) == ["agents/pact-newcomer.md"]


class TestArmTwoGoesRed:
    """EMPHASIS, made to fail, with the controls that keep it off ordinary prose.

    WHAT THIS DOES NOT PROVE: that a shouted line is obeyed more often than a
    quiet one. That is the cause behind the ruling, and no test reads a cause.
    """

    def test_a_de_emphasised_copy_is_named(self, tmp_path):
        root = _model_tree(tmp_path)
        _mutate(root / "reference/config.md", "DO NOT USE", "do not use")
        assert copies_that_differ_only_by_case(root) == ["reference/config.md"]
        assert copies_that_differ_from_the_source(root) == [], (
            "the fidelity arm caught the de-emphasis, so arm 2 adds nothing. "
            "Arm 1 casefolds, so it must not see this."
        )

    def test_a_copy_that_changes_words_is_left_to_arm_one(self, tmp_path):
        """The two arms must not report one fault twice."""
        root = _model_tree(tmp_path)
        _mutate(root / "reference/config.md", "A rule stands here", "A rule shifted")
        assert copies_that_differ_only_by_case(root) == []
        assert copies_that_differ_from_the_source(root) == ["reference/config.md"]

    def test_an_acronym_difference_does_not_redden_arm_two(self, tmp_path):
        """The false-alarm direction, and it is why the shout floor is here."""
        root = _model_tree(tmp_path)
        _mutate(root / "reference/config.md", "the flag", "the WAL flag")
        _mutate(root / SOURCE_HOME, "the flag", "the WAL flag")
        assert copies_that_differ_only_by_case(root) == []


class TestArmThreeGoesRed:
    """POPULATION, made to fail two ways.

    WHAT THESE DO NOT PROVE: that the alphabet reaches a new surface written in
    WORDS OUTSIDE the token set. A file that teaches store access and names no
    token passes in silence. That is the paraphrase hole this material has hit
    three times, and no token list closes it. The declared floor is what covers
    the members no alphabet reaches.
    """

    def test_a_new_silent_surface_is_named(self, tmp_path):
        root = _model_tree(tmp_path)
        _write(root, "agents/pact-newcomer.md", "Read the store at memory.db.\n")
        silent = store_reaching_files_without_the_bar(
            root, ROUTE_TOKENS, POPULATION_EXEMPTIONS
        )
        assert silent == ["agents/pact-newcomer.md"]

    def test_the_token_is_what_selects_and_not_the_addition(self, tmp_path):
        """The control for the arm above."""
        root = _model_tree(tmp_path)
        _write(root, "agents/pact-newcomer.md", "This file teaches nothing.\n")
        assert (
            store_reaching_files_without_the_bar(
                root, ROUTE_TOKENS, POPULATION_EXEMPTIONS
            )
            == []
        )

    def test_an_exemption_lifts_the_rule_for_one_file_only(self, tmp_path):
        root = _model_tree(tmp_path)
        _write(root, "agents/pact-newcomer.md", "Read the store at memory.db.\n")
        _write(root, "agents/pact-other.md", "Run archive_pin.py to prune.\n")
        exempt = {"agents/pact-newcomer.md": "a stated cause"}
        assert store_reaching_files_without_the_bar(root, ROUTE_TOKENS, exempt) == [
            "agents/pact-other.md"
        ]

    def test_an_alphabet_that_narrows_reddens_the_floor(self, tmp_path):
        """Unique coverage of the alphabet floor, measured rather than claimed."""
        root = _model_tree(tmp_path)
        narrowed = {k: v for k, v in ROUTE_TOKENS.items() if k != "archive_pin.py"}
        assert surfaces_the_alphabet_does_not_reach(root, narrowed) == [
            "commands/prune-memory.md"
        ]
        _mutate(root / "commands/prune-memory.md", MARKER_BEGIN, "")
        assert (
            "commands/prune-memory.md"
            not in store_reaching_files_without_the_bar(
                root, narrowed, POPULATION_EXEMPTIONS
            )
        ), (
            "the silence arm caught the narrowed alphabet, so the floor is no "
            "longer unique. Correct the stated cause above."
        )

    def test_no_alphabet_reaches_two_of_the_python_carriers(self, tmp_path):
        """THE MEASURED FACT THAT MAKES THE DECLARED FLOOR LOAD-BEARING.

        A widened walk presents as a fix and selects ONE of the three. The two
        memory-repair modules name no route token, so no alphabet reaches them
        and only declaration covers them.
        """
        root = _model_tree(tmp_path)
        for name in (
            "scripts/memory_repair/__init__.py",
            "scripts/memory_repair/shred_detect.py",
        ):
            text = strip_marked_regions(read_carrier(root, name))
            assert not any(carries(text, token) for token in ROUTE_TOKENS)

    def test_the_floor_needs_the_strip_to_name_a_carrier(self, tmp_path):
        """🔴 THE ONE CASE IN WHICH THE STRIP DECIDES THE ANSWER.

        NEITHER STANDING POPULATION CAN SHOW THIS, and the two causes are
        mirrors. MEASURED on the live tree: each markdown carrier names a route
        token OUTSIDE its region, so the floor returns the same list with the
        strip working and with it disabled. MEASURED on the model tree: its
        region holds NO route token, so removal of the region changes nothing.
        A DISCRIMINATING CASE NEEDS THE TWO CONDITIONS TOGETHER.

        SO THIS BUILDS THEM. The region carries a route token, as the shipped
        rule does. One markdown carrier names NO token outside its region. With
        the strip working, that carrier is unreached and the floor NAMES it.
        With the strip disabled, the token inside its own region selects it and
        the floor names nothing.

        WHY IT IS NOT THE MODEL TREE. `_model_tree` gives each markdown carrier
        a token outside the region ON PURPOSE, because the alphabet floor is
        its second net. Changing that would move a property four other arms
        rest on. This builds a local tree instead and leaves the model alone.
        """
        root = tmp_path / "plugin"
        victim = "reference/config.md"
        assert victim in ALPHABET_FLOOR, (
            f"{victim} left ALPHABET_FLOOR, so this arm no longer builds the "
            f"case it describes. Choose another markdown carrier."
        )
        # THE REGION NAMES A ROUTE TOKEN, as the shipped rule does through
        # `memory.db-wal`. That is what makes a carrier select ITSELF when the
        # strip fails.
        body = "STORE ACCESS. Check memory.db-wal and memory.db-shm.\n"
        for name in sorted(ALPHABET_FLOOR):
            own = "" if name == victim else "This page names cli.py.\n"
            _write(root, name, f"Heading\n\n{own}\n{_region(body=body)}")

        assert surfaces_the_alphabet_does_not_reach(root, ROUTE_TOKENS) == [victim], (
            f"with a correct strip the floor must name {victim}, which names "
            f"no route token outside its own region. IF THIS READS AS AN EMPTY "
            f"LIST, THE STRIP IS NOT REMOVING THE REGION, so the token inside "
            f"the bar selected the file and the floor lost the one question it "
            f"asks. TWO CAUSES: the strip returns its input, or its pattern "
            f"lost `re.DOTALL`."
        )


class TestArmFourGoesRed:
    """FACT, made to fail, with the control that separates it from an empty read.

    WHAT THIS DOES NOT PROVE: that `setup_memory.py` cannot reach the live store
    by another route. The arm reads ONE literal.
    """

    @staticmethod
    def _flag_is_absent(root: Path) -> bool:
        return FORBIDDEN_FLAG not in (root / EVIDENCE_FILE).read_text(encoding="utf-8")

    def test_the_flag_returning_is_caught(self, tmp_path):
        root = _model_tree(tmp_path)
        assert self._flag_is_absent(root)
        _mutate(
            root / EVIDENCE_FILE,
            "def ensure_initialized",
            'parser.add_argument("--db-path")\ndef ensure_initialized',
        )
        assert not self._flag_is_absent(root)

    def test_an_empty_file_would_read_as_a_clean_absence(self, tmp_path):
        """THE FAILURE THAT READS AS SUCCESS, shown rather than described."""
        root = _model_tree(tmp_path)
        _write(root, EVIDENCE_FILE, "")
        assert self._flag_is_absent(root), (
            "an empty file no longer passes the flag arm, so the control that "
            "pairs with it is no longer load-bearing."
        )
        assert not carries(
            (root / EVIDENCE_FILE).read_text(encoding="utf-8"), EVIDENCE_CONTROL_TOKEN
        ), "the control token survived an empty file, so it separates nothing."

    @pytest.mark.parametrize(
        "spelling, expect_absent",
        [("--db-path", False), ("db_path", True), ("--db_path", True)],
    )
    def test_the_arm_reads_the_flag_and_not_the_parameter(
        self, tmp_path, spelling, expect_absent
    ):
        """The two spellings differ by two characters and mean different things."""
        root = _model_tree(tmp_path)
        _write(root, EVIDENCE_FILE, f"# uses {spelling}\n")
        assert self._flag_is_absent(root) is expect_absent
