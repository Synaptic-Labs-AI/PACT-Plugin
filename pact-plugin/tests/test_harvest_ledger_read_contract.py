"""The ledger read contract: the existence count, and the union rule's three sites.

Properties of `skills/pact-handoff-harvest/SKILL.md`, each of which can be
reverted today without reddening anything else in the suite. Checked when the
third class was added: deleting any one of them leaves the other two green.

1. `TestExistenceCountSurvivesTruncation` — the section-existence decision is a
   COUNT OVER HEADER LINES, whose answer is complete by construction at any file
   size, and not a search of whatever prefix a read returned. The arm EXTRACTS
   the command from Step 8 and RUNS it against a ledger larger than the read cut
   with the target section past that cut. The contrast against a
   search-what-you-read strategy on the SAME fixture is what makes the property
   measured rather than asserted: the count answers 1 where the prefix search
   answers 0.

2. `TestUnionRuleIsCoupledAcrossItsThreeSites` — the union rule lives at one
   write site and two reader sites with nothing coupling them. Change the write
   side alone and both readers go stale silently, under-reporting the processed
   set with no other test able to see it.

3. `TestEverySiteUsingTheLedgerObeysItsRule` — the rule lives at Step 8 and
   five other places read or write the same ledger. A site that neither states
   the rule nor cites it is one an agent executes from memory, which is how the
   Standard harvest's own population read went five specialist reviews without
   obeying the discipline it defines. The site set is derived from the file
   rather than listed here: a site cannot instruct anyone to touch the ledger
   without naming it.

WHY THE EXTRACTION RATHER THAN A PHRASE PIN: these arms fail if the mechanism is
removed OR reworded away, because they consume the command rather than matching
prose around it. A phrase pin over the same text disarms silently when the text
is correctly reworded, which is a green check that has stopped checking.
"""

import re
import subprocess
from pathlib import Path

import pytest
from clock_shift.clock_shift_env import carry_clock_shift

SKILL_FILE = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "pact-handoff-harvest"
    / "SKILL.md"
)

# The `Read` tool's default line budget. THIS IS A MODEL OF THE READ CUT, NOT
# THE TOOL: the arms below simulate truncation as a first-N-lines prefix, so
# they measure the property (a header count is complete regardless of file
# size) and not the tool's own truncation behaviour.
READ_CUT = 2000

# Narrative padding in the decoy section, in lines. IT MUST EXCEED `READ_CUT`
# ON ITS OWN, so the target section that follows it lands past the cut.
# SHRINKING THIS BELOW `READ_CUT` DOES NOT REDDEN ANYTHING BY ITSELF — the
# count would still be 1 and the arm would still pass, while testing nothing,
# because the failure only exists past the boundary. `test_fixture_exceeds_the_
# read_cut` is what turns that silent retirement into a red.
PAD_LINES = 2200

TARGET_TEAM = "session-abcd1234"
TARGET_HEADER = f"## team={TARGET_TEAM} (someproject, session 11111111-2222-3333-4444-555555555555)"
DECOY_HEADER = "## team=session-0decoy00 (otherproject, session 99999999-8888-7777-6666-555555555555)"

# The union rule's shared literal. Unlike the three sites' CANNOT-OBSERVE
# wording — which shares no token across the three artifacts — this phrase IS
# common to all three ledger sites, so one anchor is legitimate here.
UNION_ANCHOR = "UNION of every `Processed task IDs` line"

# Base rate over the WHOLE file: one hit per coupled site and no others.
# Raising this is a deliberate act — a fourth site carrying the rule must be
# added here, which is the point of a coupling pin.
UNION_SITES = 3

# The WHOLE command, not just its quoted pattern. Extracting only the pattern
# and then applying it to a file the ARM chose leaves nothing reading what the
# SKILL's command searches — measured: rewriting Step 8 to
# `head -2000 "{ledger path}" | grep -c '...'`, a literal revert to counting
# over the read prefix, left all eleven arms in this file green.
_GREP_C_COMMAND = re.compile(r"`([^`]*grep -c '[^']+'[^`]*)`")
_GREP_C = re.compile(r"grep -c '([^']+)'")
_SYSTEM_GREP = Path("/usr/bin/grep")


def region(text, start_heading, end_heading):
    """The slice from `start_heading` up to `end_heading`, exclusive.

    Deliberately NOT a slice to the next `## `: Step 8 contains a `## team=`
    file-format example, so a `## `-terminated slice of it stops inside the
    example and every absence read out of it would be an absence from a
    fragment. Callers assert the slice is non-empty before reading it.
    """
    start = text.find(start_heading)
    if start == -1:
        return ""
    rest = text[start + len(start_heading):]
    end = rest.find(end_heading)
    return rest if end == -1 else rest[:end]


def extract_existence_pattern(skill_text, team_id):
    """The `grep -c` pattern Step 8 names, with its placeholder substituted.

    Returns `None` when Step 8 carries no `grep -c` — which is the pre-fix
    state, where existence was decided by searching what had been read. Every
    arm below fails on `None` rather than skipping, so a revert to that state
    reddens instead of quietly passing.
    """
    step8 = region(skill_text, "### Step 8", "### Step 9")
    if not step8:
        return None
    found = _GREP_C.findall(step8)
    # A SECOND `grep -c` would make `search` silently pick whichever came
    # first, and the arms would then measure a command nobody meant to test.
    # Ambiguity is a failure, not a fallback — measured on the sibling arm in
    # this stage, where a looser pattern fell through to a different command
    # and every assertion stayed green.
    assert len(found) <= 1, (
        f"{len(found)} `grep -c` commands in a {len(step8)}-char Step 8: {found}"
    )
    if not found:
        return None
    return found[0].replace("{your team_id}", team_id)


def extract_existence_command(skill_text, team_id, ledger_path):
    """The whole shell command Step 8 names, ready to run.

    The pattern alone cannot express this file's thesis. The claim is that the
    count is taken over the WHOLE FILE; a command that truncates its input
    first carries an identical pattern, so only running the command as written
    can tell the two apart.
    """
    step8 = region(skill_text, "### Step 8", "### Step 9")
    if not step8:
        return None
    found = _GREP_C_COMMAND.findall(step8)
    assert len(found) <= 1, (
        f"{len(found)} `grep -c` commands in a {len(step8)}-char Step 8: {found}"
    )
    if not found:
        return None
    return (found[0]
            .replace("{your team_id}", team_id)
            .replace("{ledger path}", str(ledger_path)))


def count_matches(pattern, text):
    """`grep -c` semantics in Python: lines matching `pattern`.

    Equivalent to the extracted command only while the pattern uses no BRE
    metacharacter beyond a leading `^`; `test_extracted_pattern_is_portable`
    pins that precondition, and the system-grep arm below measures the
    equivalence rather than assuming it.
    """
    return len(re.findall(pattern, text, flags=re.MULTILINE))


def build_oversized_ledger(path, headers):
    """A ledger whose `headers` all sit PAST `READ_CUT`. Returns its line count."""
    lines = ["# Session Processed Tasks", "", DECOY_HEADER]
    lines += [f"- decoy narrative line {i}" for i in range(PAD_LINES)]
    for header in headers:
        lines += [header, "Processed task IDs: 1, 2, 3", "Last processed: 2026-09-07", ""]
    path.write_text("\n".join(lines) + "\n")
    return len(lines)


@pytest.fixture
def skill_text():
    return SKILL_FILE.read_text()


@pytest.fixture
def pattern(skill_text):
    found = extract_existence_pattern(skill_text, TARGET_TEAM)
    assert found is not None, (
        "Step 8 names no `grep -c` existence test. The section-existence "
        "decision has reverted to a search of what was read, which answers "
        "'not found' for a section that is there but past the read cut."
    )
    return found


class TestExistenceCountSurvivesTruncation:
    """G4 — the count is complete by construction at any file size."""

    def test_extracted_pattern_is_portable(self, pattern):
        # Bounds the Python/grep equivalence the other arms rely on, and pins
        # that the pattern is ANCHORED — an unanchored `## team=` would also
        # match the header quoted inside prose, inflating the count.
        assert pattern.startswith("^## team="), pattern
        assert not set(pattern) & set(r".*[]\+?(){}|$"), (
            f"pattern carries a regex metacharacter, so the Python count and "
            f"the shell `grep -c` no longer agree by construction: {pattern!r}"
        )

    def test_fixture_exceeds_the_read_cut(self, tmp_path):
        # THE FIXTURE MUST EXCEED THE READ CUT OR EVERY ARM BELOW IS VACUOUS.
        # The failure this file exists for only occurs past line READ_CUT, so a
        # fixture that shrinks below it leaves arms that pass while testing
        # nothing. This arm is what makes that shrink RED instead of silent.
        ledger = tmp_path / "session_processed_tasks.md"
        total = build_oversized_ledger(ledger, [TARGET_HEADER])
        assert total > READ_CUT, f"fixture is {total} lines, read cut is {READ_CUT}"

        body = ledger.read_text().splitlines()
        header_line = body.index(TARGET_HEADER) + 1
        assert header_line > READ_CUT, (
            f"target section starts at line {header_line}, which is inside the "
            f"first {READ_CUT} lines — the truncation case is not reproduced"
        )

    def test_count_is_one_where_a_prefix_search_finds_nothing(self, tmp_path, pattern):
        """The measured contrast. Same file, same pattern, two strategies."""
        ledger = tmp_path / "session_processed_tasks.md"
        build_oversized_ledger(ledger, [TARGET_HEADER])
        whole = ledger.read_text()
        prefix = "\n".join(whole.splitlines()[:READ_CUT])

        # What an agent that searches what it read would conclude: absent.
        # Region size stated beside the zero, so an empty input is
        # distinguishable from a real zero.
        assert len(prefix.splitlines()) == READ_CUT
        assert count_matches(pattern, prefix) == 0

        # What the shipped mechanism concludes: present. This is the branch
        # that stops `create-if-missing` firing on a prefix and producing a
        # duplicate section with the dedup baseline lost.
        assert count_matches(pattern, whole) == 1

    def test_two_headers_are_reported_as_two(self, tmp_path, pattern):
        # `2` or more routes to refuse-and-report. The arm asserts the COUNT,
        # not the verdict: a verdict assertion passes trivially on any fixture.
        ledger = tmp_path / "session_processed_tasks.md"
        build_oversized_ledger(ledger, [TARGET_HEADER, TARGET_HEADER])
        assert count_matches(pattern, ledger.read_text()) == 2

    def test_zero_headers_stay_zero(self, tmp_path, pattern):
        # First-harvest bootstrap. This arm must pass, or the fix breaks
        # section creation for a team that genuinely has none.
        ledger = tmp_path / "session_processed_tasks.md"
        build_oversized_ledger(ledger, [])
        assert count_matches(pattern, ledger.read_text()) == 0

    def test_each_count_routes_to_its_own_action(self, skill_text):
        """The count-to-action mapping, which the count arms above cannot see.

        HONEST LABEL: this is a PROSE PIN, and it is the only part of Half B
        that is not mechanisable — the counts are executed above, the routing
        is a sentence an agent reads. It is here because the command surviving
        while its routing inverts is a real regression that every other arm in
        this file stays green through. It is keyed on CLAUSE POSITION rather
        than on wording, so rewording the surrounding prose leaves it armed and
        swapping two branches reddens it.
        """
        step8 = region(skill_text, "### Step 8", "### Step 9")
        assert step8, "Step 8 region is empty — nothing was read"
        routing = step8[step8.find("`1`:"):]
        one, rest = routing.split("`0`:", 1)
        zero, two_plus = rest.split("`2` or more:", 1)

        # ASSERT WHAT EACH BRANCH ADMITS, NOT WHICH TOKENS IT MENTIONS. A
        # routing clause fails by GAINING a permission, and a token-presence
        # check only ever catches one that LOSES a token. Measured: inserting
        # "creating a fresh section first if the extract comes back empty"
        # into the exists branch left every arm in this file green, because
        # "creating" does not contain "create".
        creates = re.compile(r"creat", re.IGNORECASE)

        # 1 → the section exists: extract and APPEND. Creation in any word
        # form is the widening this branch must never admit.
        assert "append" in one
        assert not creates.search(one), (
            f"the exists branch admits creating a section, which duplicates a "
            f"section whose dedup baseline is then split: {one!r}"
        )
        # 0 → genuinely absent: create. The only creating branch.
        assert "create it" in zero
        # 2+ → refuse. NOT checked for writing verbs: this slice runs to the
        # end of Step 8 and swallows unrelated prose, so `creat` matches the
        # clause explaining how a duplicate AROSE and `append` matches the
        # `sed` warning below it. Both are descriptions, not permissions, and
        # asserting over them fails on correct text.
        assert "refuse and report" in two_plus

    @pytest.mark.skipif(
        not _SYSTEM_GREP.exists(), reason="no /usr/bin/grep on this platform"
    )
    def test_literal_command_agrees_with_the_python_count(self, tmp_path, pattern):
        """Validates the instrument in BOTH directions against known answers.

        Names the binary absolutely and does not use a bare `grep`: a bare
        `grep` resolves to whatever shim the operator has installed, and this
        skill ships to consumers whose tools are not this machine's.
        """
        ledger = tmp_path / "session_processed_tasks.md"
        build_oversized_ledger(ledger, [TARGET_HEADER])
        prefix = tmp_path / "prefix.md"
        prefix.write_text("\n".join(ledger.read_text().splitlines()[:READ_CUT]) + "\n")

        def grep_count(path):
            # `grep -c` exits 1 on zero matches; that is a count, not an error.
            done = subprocess.run(
                [str(_SYSTEM_GREP), "-c", pattern, str(path)],
                capture_output=True,
                text=True,
            )
            assert done.returncode in (0, 1), done.stderr
            return int(done.stdout.strip())

        assert grep_count(ledger) == 1
        assert grep_count(prefix) == 0

    def test_the_shipped_command_counts_over_the_whole_file(self, tmp_path, skill_text):
        """THE THESIS OF THIS FILE, executed as written rather than modelled.

        Every other arm here applies the extracted PATTERN to a file the arm
        chose, so none of them reads what the shipped command searches. A
        command that truncates its own input carries an identical pattern and
        is invisible to all of them — measured: rewriting Step 8 to
        `head -2000 "{ledger path}" | grep -c '...'` left all eleven green.

        This runs the command Step 8 actually names, against a ledger whose
        target section sits past the read cut, under `/bin/sh` with `PATH`
        pinned so `grep` is the system binary rather than an operator's shim.
        """
        ledger = tmp_path / "session_processed_tasks.md"
        build_oversized_ledger(ledger, [TARGET_HEADER])
        command = extract_existence_command(skill_text, TARGET_TEAM, ledger)
        assert command is not None, (
            "Step 8 names no `grep -c` existence command; the section-existence "
            "decision has reverted to a search of what was read"
        )

        done = subprocess.run(
            ["/bin/sh", "-c", command],
            capture_output=True, text=True, env=carry_clock_shift({"PATH": "/usr/bin:/bin"}),
        )
        assert done.returncode in (0, 1), done.stderr
        assert done.stdout.strip() == "1", (
            f"the shipped command answered {done.stdout.strip()!r} for a section "
            f"that IS present but sits past line {READ_CUT}. It is counting over "
            f"part of the file, not over the file: {command!r}"
        )


class TestUnionRuleIsCoupledAcrossItsThreeSites:
    """The union rule at its write site and both reader sites.

    Nothing else couples them. A change to the write clause alone leaves both
    readers taking the newest `Processed task IDs` line, which under-reports
    the processed set and silently reprocesses work already done.
    """

    SITES = (
        ("write clause", "### Step 8", "### Step 9"),
        ("standard reader", "### Step 2", "### Step 3"),
        ("incremental reader", "## Incremental Harvest Workflow", "## Consolidation Harvest Workflow"),
    )

    @pytest.mark.parametrize("name,start,end", SITES, ids=[s[0] for s in SITES])
    def test_each_site_states_the_union_rule(self, skill_text, name, start, end):
        block = region(skill_text, start, end)
        assert block, f"{name}: region {start!r}..{end!r} is empty — nothing was read"
        assert block.count(UNION_ANCHOR) == 1, (
            f"{name}: {block.count(UNION_ANCHOR)} hits in a {len(block)}-char "
            f"region; expected exactly 1"
        )

    def test_base_rate_over_the_whole_file(self, skill_text):
        # Stated beside the per-region results: the three hits above are three
        # DIFFERENT sites and not three hits inside one of them.
        assert skill_text.count(UNION_ANCHOR) == UNION_SITES


class TestEverySiteUsingTheLedgerObeysItsRule:
    """Every site that touches the ledger states the read rule or cites it.

    The rule lives at Step 8. Five other places in this file read or write the
    same ledger, and a site that neither states the rule nor points at it is
    one an agent will execute from memory -- which is how the Standard
    harvest's own population read at the top of the file went five specialist
    reviews without obeying the discipline it defines.

    HOW THE SITE SET IS DERIVED, and it is not a hand-maintained list: a site
    cannot instruct anyone to touch the ledger without naming it, so the
    filename enumerates the sites. Fenced blocks are excluded because a format
    template is not an instruction -- that exclusion is structural, so a future
    template inside a fence drops out on its own.

    WHAT THIS CANNOT CATCH. A site that refers to the ledger without naming it
    ("the processed task list", "that file") is invisible here, and so is one
    naming it only as a path fragment. The filename is a coupling, not a
    guarantee: it enumerates sites only where sites spell it the same way. The
    bare token is matched deliberately rather than `session_processed_tasks.md`
    so that dropping the extension does not hide a site. And a site added
    INSIDE the Step 8 region is exempt, because that region is where the rule
    is stated -- measured, by inserting an uncited site at Step 8.5 and
    watching this stay green. Outside the region the same insertion reddens.
    """

    LEDGER = "session_processed_tasks"
    _STATES_RULE = re.compile(r"grep -[cn] '")

    @staticmethod
    def _strip_fences(skill_text):
        """Drop fenced blocks by SCANNING, not by pairing ```...``` with a regex.

        Measured: a non-greedy `re.sub` over the whole document mis-pairs the
        moment a fence is added or removed anywhere above, silently swallowing
        prose between two markers that were never a pair -- which hid a `Step
        8` citation and turned a correct file red. A line scanner cannot
        mis-pair, because it decides one line at a time.
        """
        out, fenced = [], False
        for line in skill_text.splitlines():
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            out.append("" if fenced else line)
        return "\n".join(out)

    def _sites(self, skill_text):
        """Ledger-naming blocks, paired with whether each sits inside Step 8."""
        unfenced = self._strip_fences(skill_text)
        start = unfenced.find("### Step 8")
        end = unfenced.find("### Step 9")
        assert -1 < start < end, "Step 8 region not found -- the parser is blind"
        sites, offset = [], 0
        for block in unfenced.split("\n\n"):
            if self.LEDGER in block:
                sites.append((block, start <= offset < end))
            offset += len(block) + 2
        return sites

    def test_the_site_census_is_not_empty(self, skill_text):
        # A zero here would make every assertion below vacuously true.
        sites = self._sites(skill_text)
        assert len(sites) >= 5, (
            f"only {len(sites)} ledger sites found; the filename has stopped "
            f"enumerating them and the arm below is measuring nothing"
        )
        assert any(defines for _, defines in sites), "no site inside Step 8"

    def test_every_site_states_the_rule_or_cites_it(self, skill_text):
        for block, defines in self._sites(skill_text):
            if defines or self._STATES_RULE.search(block):
                continue  # this site IS the rule, or restates it inline
            assert "Step 8" in block, (
                f"a site touching the ledger neither states the read rule nor "
                f"cites Step 8, so an agent reaching it has nothing to follow "
                f"but memory: {block.strip()[:200]!r}"
            )
