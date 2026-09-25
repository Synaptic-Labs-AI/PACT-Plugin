"""THE SEAM'S CALL SITES, DRIVEN END TO END OVER A CRLF TARGET.

WHAT THIS FILE CERTIFIES, AND WHY IT IS NOT THE SAME CLAIM AS THE SEAM PROBE.
`_atomic_write_text` applies the target's line ending for every caller. A
direct drive of the seam proves the SEAM. It cannot catch a caller that
FLATTENS the endings before the seam ever sees the content, because such a
test supplies the content itself. Only a production caller driven end to end
over a CRLF file on disk can catch that class, and that class is the whole
reason the restore moved into the seam.

THE POPULATION IS THE CALL SITES, NOT THE TWO TWIN MODULES. COUNTING RULE,
STATED BESIDE THE NUMBER: one entry for each CALL EXPRESSION of
`_atomic_write_text` in a `.py` file below `hooks/` and `skills/`, tests
excluded, imports and comment mentions not counted. An AST walk of 78 files
returns TEN, in five modules:

    hooks/staleness.py                            1   check_pinned_staleness
    hooks/pin_marker_writer.py                    1   _plan_and_write
    hooks/shared/session_resume.py                3   update_session_info
    hooks/shared/claude_md_manager.py             3   strip_orphan_kernel_block
                                                      ensure_project_memory_md
                                                      migrate_to_managed_structure
    skills/pact-memory/scripts/working_memory.py  2   sync_to_claude_md
                                                      sync_retrieved_to_claude_md

SITES THIS FILE DOES NOT DRIVE ARE NAMED WITH THEIR REASON, in
`test_the_undriven_sites_are_named_with_a_reason` below. A coverage report
that names its misses is worth more than a higher count that does not.
"""
import ast
from collections import Counter
from pathlib import Path

_MANAGED_START = (
    "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
)
_MANAGED_END = "<!-- PACT_MANAGED_END -->"
_MEMORY_START = "<!-- PACT_MEMORY_START -->"
_MEMORY_END = "<!-- PACT_MEMORY_END -->"

_DOC = (
    f"{_MANAGED_START}\n"
    "# PACT Framework and Managed Project Memory\n"
    "\n"
    "<!-- SESSION_START -->\n"
    "## Current Session\n"
    "<!-- SESSION_END -->\n"
    "\n"
    f"{_MEMORY_START}\n"
    "## Retrieved Context\n"
    "\n"
    "## Pinned Context\n"
    "\n"
    "## Working Memory\n"
    "<!-- Auto-managed by pact-memory skill. -->\n"
    "\n"
    f"{_MEMORY_END}\n"
    "\n"
    f"{_MANAGED_END}\n"
)


def _seed_crlf(path: Path, body: str = _DOC) -> None:
    """Write `body` with CRLF BYTES.

    `write_text` translates on some platforms, and a target that is not
    CRLF-dominant sends `_detect_line_ending` down its LF branch, where
    `_restore_line_ending` returns early. An arm on an LF seed would pass
    without exercising the conversion at all.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))


def _endings(path: Path):
    """Return (crlf_count, bare_lf_count, doubled_cr_count) from the BYTES."""
    raw = path.read_bytes()
    crlf = raw.count(b"\r\n")
    return crlf, raw.count(b"\n") - crlf, raw.count(b"\r\r\n")


def _assert_crlf_survived(path: Path, site: str, before: bytes) -> None:
    """Assert the write happened AND kept the endings.

    🔴 THE WRITE-HAPPENED GATE IS FIRST AND IT IS NOT A FORMALITY. A caller
    that takes an early return writes nothing, and every ending assertion below
    then passes on the untouched SEED. That arm certifies the seam while never
    reaching it. This was not hypothetical: the first draft of the staleness
    arm in this file passed exactly that way, on a document whose shape the
    parser did not recognise, and the pass returned None without a write.
    """
    after = path.read_bytes()
    assert after != before, (
        f"{site}: the pass did NOT rewrite the file, so every ending "
        f"assertion below would hold on the untouched seed. This arm reached "
        f"no write and certifies nothing. Fix the fixture so the caller writes"
    )
    before_crlf = before.count(b"\r\n")
    crlf, bare_lf, doubled = _endings(path)
    assert doubled == 0, (
        f"{site}: the write produced {doubled} DOUBLED carriage return(s). "
        f"The ending was applied to content that already carried CRLF"
    )
    assert bare_lf == 0, (
        f"{site}: the write left {bare_lf} bare LF ending(s) in a CRLF file. "
        f"The caller flattened the document and the user sees a whole-file "
        f"change they did not make"
    )
    assert crlf >= before_crlf, (
        f"{site}: CRLF count fell from {before_crlf} to {crlf}"
    )


class TestSeedFixtureIsCrlf:
    """POSITIVE CONTROL ON THE FIXTURE, not on the code under test.

    Each arm below rests on the seed being CRLF-dominant. If the seed were LF,
    `_detect_line_ending` returns "\\n", the restore takes its early return,
    and every arm in this file would pass while exercising nothing.
    """

    def test_the_seed_is_crlf_dominant(self, tmp_path):
        target = tmp_path / "CLAUDE.md"
        _seed_crlf(target)
        crlf, bare_lf, doubled = _endings(target)
        assert crlf > 0 and bare_lf == 0 and doubled == 0, (
            f"seed is not CRLF-clean: crlf={crlf} lf={bare_lf} doubled={doubled}"
        )


class TestWorkingMemoryTwinCallSites:
    """skills/pact-memory/scripts/working_memory.py, 2 of the 10 sites."""

    def test_sync_to_claude_md_keeps_the_crlf_of_the_target(
        self, tmp_path, monkeypatch
    ):
        """SITE `skills/pact-memory/scripts/working_memory.py::sync_to_claude_md`.

        THE PAYLOAD CARRIES A CARRIAGE RETURN ON PURPOSE. The document
        contributes none, because the read translates. A payload field is
        interpolated into the text handed to the seam, so it is the route by
        which CRLF reaches the restore in the shipped tree.
        """
        from scripts.working_memory import sync_to_claude_md

        project = tmp_path / "project"
        target = project / ".claude" / "CLAUDE.md"
        _seed_crlf(target)
        before = target.read_bytes()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        sync_to_claude_md(
            {"context": "one\r\ntwo", "goal": "carry a carriage return"},
            memory_id="seam-site",
        )

        _assert_crlf_survived(target, "skills/pact-memory/scripts/working_memory.py::sync_to_claude_md", before)

    def test_sync_retrieved_to_claude_md_keeps_the_crlf_of_the_target(
        self, tmp_path, monkeypatch
    ):
        """SITE `skills/pact-memory/scripts/working_memory.py::sync_retrieved_to_claude_md`."""
        from scripts.working_memory import sync_retrieved_to_claude_md

        project = tmp_path / "project"
        target = project / ".claude" / "CLAUDE.md"
        _seed_crlf(target)
        before = target.read_bytes()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        sync_retrieved_to_claude_md(
            [{"context": "alpha\r\nbeta", "goal": "retrieved"}],
            "a query",
        )

        _assert_crlf_survived(
            target, "skills/pact-memory/scripts/working_memory.py::sync_retrieved_to_claude_md", before
        )


class TestCanonicalTwinCallSites:
    """hooks/shared/claude_md_manager.py, 3 of the 10 sites."""

    def test_migrate_to_managed_structure_keeps_the_crlf_of_the_target(
        self, tmp_path, monkeypatch
    ):
        """SITE `hooks/shared/claude_md_manager.py::migrate_to_managed_structure`.

        The migration reads an UNMANAGED document and rewrites it wrapped in
        the managed boundary. It rewrites the whole file, so a flattening
        caller here rewrites every line of the user's document.
        """
        from shared.claude_md_manager import migrate_to_managed_structure

        project = tmp_path / "project"
        target = project / ".claude" / "CLAUDE.md"
        _seed_crlf(target, "# Project Memory\n\nSome user text.\n")
        before = target.read_bytes()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = migrate_to_managed_structure()

        assert result and "failed" not in result.lower(), (
            f"the migration did not run, so this arm measured nothing: {result!r}"
        )
        _assert_crlf_survived(
            target, "hooks/shared/claude_md_manager.py::migrate_to_managed_structure", before
        )


class TestStalenessCallSite:
    """hooks/staleness.py, 1 of the 10 sites."""

    def test_check_pinned_staleness_keeps_the_crlf_of_the_target(self, tmp_path):
        """SITE `hooks/staleness.py::check_pinned_staleness`.

        This is the site the seam repair REMOVED a call-site restore from. It
        is the one site the design records as already armed end to end, and it
        is repeated here so the ten sites read from one table.
        """
        from staleness import check_pinned_staleness

        project = tmp_path / "project"
        target = project / ".claude" / "CLAUDE.md"
        from test_staleness import over_budget_body

        _seed_crlf(
            target,
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Big Feature\n{over_budget_body()}\n\n",
        )
        before = target.read_bytes()

        check_pinned_staleness(claude_md_path=target)

        _assert_crlf_survived(target, "hooks/staleness.py::check_pinned_staleness", before)


class TestTheCoverageReportNamesItsMisses:
    """THE MISSES ARE PART OF THE RESULT, NOT AN OMISSION FROM IT.

    A site left undriven that is never named reads as covered. This arm holds
    the reasons in the suite itself, so a later reader meets them beside the
    arms rather than in a hand-off nobody reopens.
    """

    # SITE KEYS ARE `relative/path.py::enclosing_function`, NOT line numbers.
    # A line number moves whenever anybody edits above it, so a line-keyed
    # table goes stale on an unrelated edit and reports a defect that is not
    # there. A function name moves only when somebody renames it, which is the
    # event this table SHOULD notice.
    #
    # THE VALUE IS A COUNT, because one function can hold more than one call.
    # `update_session_info` holds three. A set of names would collapse those
    # three into one and lose the cardinality this table exists to hold.
    DRIVEN = {
        "skills/pact-memory/scripts/working_memory.py::sync_to_claude_md": 1,
        "skills/pact-memory/scripts/working_memory.py::sync_retrieved_to_claude_md": 1,
        "hooks/shared/claude_md_manager.py::migrate_to_managed_structure": 1,
        "hooks/staleness.py::check_pinned_staleness": 1,
    }

    # site -> the arm(s) that DRIVE that site, as `ClassName::test_name`.
    #
    # WHY A MAPPING AND NOT A TOTAL. A cardinality over the driving classes
    # answers "are there four arms" and never "is there one arm for each
    # site". Two arms for one site plus zero for another satisfies a total
    # PERFECTLY, so the total cannot see an arm that was re-aimed away from
    # its site while its neighbour gained a second. That is the same class of
    # defect the mapping comparison above closes, one level down: an added
    # entry and a removed entry cancel in a sum and are named in a mapping.
    DRIVEN_SITE_TO_ARMS = {
        "skills/pact-memory/scripts/working_memory.py::sync_to_claude_md": [
            "TestWorkingMemoryTwinCallSites::"
            "test_sync_to_claude_md_keeps_the_crlf_of_the_target",
        ],
        "skills/pact-memory/scripts/working_memory.py::"
        "sync_retrieved_to_claude_md": [
            "TestWorkingMemoryTwinCallSites::"
            "test_sync_retrieved_to_claude_md_keeps_the_crlf_of_the_target",
        ],
        "hooks/shared/claude_md_manager.py::migrate_to_managed_structure": [
            "TestCanonicalTwinCallSites::"
            "test_migrate_to_managed_structure_keeps_the_crlf_of_the_target",
        ],
        "hooks/staleness.py::check_pinned_staleness": [
            "TestStalenessCallSite::"
            "test_check_pinned_staleness_keeps_the_crlf_of_the_target",
        ],
    }

    # site -> the reason it carries no end-to-end CRLF arm in this file. Keyed
    # like DRIVEN, so the keys are UNDRIVEN_COUNTS' keys and a rename reddens
    # the comparison below instead of leaving a stale label.
    UNDRIVEN = {
        "hooks/shared/claude_md_manager.py::ensure_project_memory_md": (
            "CREATE-ONLY, so the behaviour is not constructible. The function "
            "returns None when the target is available, so it writes only "
            "when no file is present. `_detect_line_ending` reports LF for a "
            "target that is not on disk, so there is no CRLF to preserve. An "
            "arm here would assert LF output and would stay green under every "
            "mutation of the restore."
        ),
        "hooks/shared/claude_md_manager.py::strip_orphan_kernel_block": (
            "Targets the GLOBAL ~/.claude/CLAUDE.md rather than a project "
            "file, so driving it needs a redirected home. NOT ATTEMPTED here "
            "to keep this file free of a home-redirect fixture. The behaviour "
            "is the same seam call, and the gap is real rather than argued "
            "away."
        ),
        "hooks/pin_marker_writer.py::_plan_and_write": (
            "A private entry point that reads its plan from the hook "
            "invocation rather than from arguments, so an end-to-end drive "
            "needs the hook input harness. NOT ATTEMPTED here."
        ),
        "hooks/shared/session_resume.py::update_session_info": (
            "THREE sites in ONE function, reached by three different document "
            "shapes: a rewrite of an existing session block, an insertion "
            "before a marker, and an append at the end. Driving all three "
            "needs three seeds. NOT ATTEMPTED here."
        ),
    }

    def test_the_undriven_sites_are_named_with_a_reason(self):
        """Every entry must carry a NON-EMPTY reason.

        NON-VACUITY: a dict that emptied out would satisfy an "each reason is
        present" assertion perfectly and record nothing.
        """
        assert self.UNDRIVEN, "the undriven-site table is empty"
        for site, reason in self.UNDRIVEN.items():
            assert reason.strip(), f"{site} is recorded with no reason"

    # Keys of UNDRIVEN carry their own count, because one of them holds three
    # calls in one function.
    UNDRIVEN_COUNTS = {
        "hooks/shared/claude_md_manager.py::ensure_project_memory_md": 1,
        "hooks/shared/claude_md_manager.py::strip_orphan_kernel_block": 1,
        "hooks/pin_marker_writer.py::_plan_and_write": 1,
        "hooks/shared/session_resume.py::update_session_info": 3,
    }

    _SEARCH_ROOTS = ("hooks", "skills")
    _SEAM = "_atomic_write_text"

    @classmethod
    def _walk_the_tree(cls):
        """DISCOVER the seam call sites. Return (Counter, files_parsed, spellings).

        COUNTING RULE, STATED BESIDE THE NUMBER: one entry for each CALL
        EXPRESSION of `_atomic_write_text` in a `.py` file below `hooks/` and
        `skills/`, attributed to its INNERMOST enclosing function. `tests/` is
        below neither root, so tests are excluded by the roots rather than by a
        filter. A `def`, an `import` and a mention inside a comment are not
        calls.

        THE SPELLING BREAKDOWN IS RETURNED RATHER THAN ASSUMED. A walk that
        reached only the bare-name form would under-report, and the
        under-report would read as a small clean number. The caller asserts on
        the breakdown, so a narrow walk is visible rather than silent.
        """
        plugin_root = Path(__file__).parent.parent
        found = Counter()
        spellings = Counter()
        parsed = 0

        def visit(node, enclosing, rel):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, child.name, rel)
                    continue
                if isinstance(child, ast.Call):
                    f = child.func
                    if isinstance(f, ast.Name):
                        name, spelling = f.id, "Name"
                    elif isinstance(f, ast.Attribute):
                        name, spelling = f.attr, "Attribute"
                    else:
                        name, spelling = None, None
                    if name == cls._SEAM:
                        found[f"{rel}::{enclosing}"] += 1
                        spellings[spelling] += 1
                visit(child, enclosing, rel)

        for root in cls._SEARCH_ROOTS:
            for path in sorted((plugin_root / root).rglob("*.py")):
                parsed += 1
                rel = path.relative_to(plugin_root).as_posix()
                visit(ast.parse(path.read_text(encoding="utf-8")), None, rel)
        return found, parsed, spellings

    def test_the_walk_finds_a_population_at_all(self):
        """NON-VACUITY, AND IT RUNS FIRST.

        A walk that parsed nothing, or that found no call, satisfies a set
        comparison against an empty expectation perfectly. The comparison below
        is evidence only once the walk is known to have read files and found
        calls. A broken root path lands here rather than in a confident green.
        """
        found, parsed, spellings = self._walk_the_tree()

        assert parsed > 0, "the walk parsed no Python file at all"
        assert found, (
            f"the walk found no call of {self._SEAM!r} across {parsed} files. "
            f"Either the seam was renamed, or the roots are wrong, and either "
            f"way the coverage claim in this file is measuring nothing"
        )
        assert spellings["Name"] + spellings["Attribute"] == sum(found.values())

    def test_every_seam_call_site_is_driven_or_named(self):
        """THE DENOMINATOR, DERIVED FROM THE TREE AND NOT FROM TYPED LITERALS.

        WHAT THIS REPLACES, AND WHY THE OLD SHAPE PROVED NOTHING. The arm here
        before compared two integers typed in this file against a third integer
        typed in this file. It could not see the tree at all. Peer review
        measured that in three legs: a dead eleventh call site left it green, a
        LIVE reachable eleventh call site left it green, and deleting one
        driven arm left it green with the literal now incorrect.

        THE TWO SIDES COME FROM TWO INDEPENDENT SOURCES, WHICH IS THE WHOLE
        REPAIR. The left side is a walk of `hooks/` and `skills/`. The right
        side is the two tables above, which a person maintains. If the two came
        from the same walk the comparison would be n against n, a tautology
        with a derivation in place of a literal, and it would pass this same
        re-run while holding nothing.

        IT COMPARES MAPPINGS RATHER THAN TOTALS. Two totals can agree while the
        sites behind them differ, so an added site and a removed site in one
        change would cancel. A mapping comparison names the site that moved.
        """
        found, _, _ = self._walk_the_tree()

        accounted = Counter(self.DRIVEN)
        accounted.update(self.UNDRIVEN_COUNTS)

        missing = {k: v for k, v in found.items() if accounted.get(k) != v}
        stale = {k: v for k, v in accounted.items() if found.get(k) != v}

        assert not missing and not stale, (
            f"THE SEAM CALL-SITE TABLES IN THIS FILE NO LONGER AGREE WITH THE "
            f"TREE.\n"
            f"  in the tree, not accounted for: {sorted(missing.items())}\n"
            f"  accounted for, not in the tree: {sorted(stale.items())}\n"
            f"\n"
            f"A site in the FIRST list is a seam caller with NO end-to-end arm "
            f"and NO recorded reason, so the coverage report in this file is "
            f"now incorrect. DRIVE it with an arm, or add it to UNDRIVEN_COUNTS "
            f"with the reason it cannot be driven.\n"
            f"A site in the SECOND list was renamed or removed, so a table "
            f"entry points at a function that is gone."
        )

    def test_the_undriven_table_and_its_count_table_hold_the_same_sites(self):
        """The reason table and the count table are two halves of one record.

        UNDRIVEN carries the prose and UNDRIVEN_COUNTS carries the cardinality.
        A site added to one and not the other gives a coverage report whose
        reasons and whose numbers disagree, and the comparison above would then
        pass while the prose says something else. The two are compared as SETS
        of keys, not by length: a length check passes when one site is swapped
        for another.
        """
        assert set(self.UNDRIVEN) == set(self.UNDRIVEN_COUNTS), (
            f"UNDRIVEN and UNDRIVEN_COUNTS describe different sites.\n"
            f"  a reason with no count: "
            f"{sorted(set(self.UNDRIVEN) - set(self.UNDRIVEN_COUNTS))}\n"
            f"  a count with no reason: "
            f"{sorted(set(self.UNDRIVEN_COUNTS) - set(self.UNDRIVEN))}\n"
            f"The two tables describe the same sites and must gain and lose "
            f"entries together"
        )

    def test_each_driven_site_has_an_arm_collected_in_this_module(self):
        """THE LEG THE MAPPING COMPARISON CANNOT REACH.

        A driven site stays in DRIVEN when its arm is renamed out of
        collection, so the comparison above keeps passing while the arm no
        longer runs. Peer review measured exactly that: one driven arm renamed
        out gave 6 passed and nothing reported it.

        IT BINDS EACH SITE TO ITS OWN ARM BY NAME, rather than counting arms.
        A total answers "are there four arms" and never "is there one arm for
        each site", so two arms for one site plus zero for another satisfies a
        total while one site is silently undriven. `DRIVEN_SITE_TO_ARMS` is
        that binding, and the three checks below hold it from three sides: the
        two tables describe the same sites, each named arm COLLECTS, and no arm
        in the driving classes is left unclaimed by any site.
        """
        driving_classes = (
            TestWorkingMemoryTwinCallSites,
            TestCanonicalTwinCallSites,
            TestStalenessCallSite,
        )
        collected = {
            f"{cls.__name__}::{name}"
            for cls in driving_classes
            for name in dir(cls)
            if name.startswith("test_")
        }

        # NON-VACUITY: an empty collection would satisfy the per-site loop
        # below only by way of an empty table, and would satisfy the unclaimed
        # check trivially. Assert the population is real before reading it.
        assert collected, (
            "the three driving classes collect NO test at all, so every check "
            "below reads an empty set and reports nothing"
        )

        assert set(self.DRIVEN_SITE_TO_ARMS) == set(self.DRIVEN), (
            f"the site-to-arm table and the DRIVEN table describe different "
            f"sites.\n"
            f"  driven with no arm recorded: "
            f"{sorted(set(self.DRIVEN) - set(self.DRIVEN_SITE_TO_ARMS))}\n"
            f"  arm recorded for a site that is not driven: "
            f"{sorted(set(self.DRIVEN_SITE_TO_ARMS) - set(self.DRIVEN))}"
        )

        # LEG ONE: each site's named arm exists and collects, and the count of
        # arms bound to that site agrees with the count DRIVEN records for it.
        for site, arms in sorted(self.DRIVEN_SITE_TO_ARMS.items()):
            missing = [arm for arm in arms if arm not in collected]
            assert not missing, (
                f"the site {site} records the arm(s) {missing}, and this file "
                f"does not collect them. An arm was RENAMED, deleted, or moved "
                f"out of a driving class, so that site is now UNDRIVEN while "
                f"it keeps a driven label. Peer review measured this exact "
                f"shape: one driven arm renamed out gave 6 passed and nothing "
                f"reported it.\n"
                f"  collected here: {sorted(collected)}"
            )
            assert len(arms) == self.DRIVEN[site], (
                f"the site {site} is bound to {len(arms)} arm(s) and DRIVEN "
                f"records {self.DRIVEN[site]} call site(s) there. The two "
                f"numbers describe the same thing and must move together"
            )

        # LEG TWO: no arm drifts free of a site. Without this an arm re-aimed
        # at another site's behaviour keeps collecting, keeps its name, and
        # every check above continues to pass.
        claimed = {arm for arms in self.DRIVEN_SITE_TO_ARMS.values()
                   for arm in arms}
        assert collected == claimed, (
            f"a driving class holds arm(s) that no site claims: "
            f"{sorted(collected - claimed)}. Bind each to its site in "
            f"DRIVEN_SITE_TO_ARMS, or move it out of the driving classes. An "
            f"unclaimed arm inflates the appearance of coverage while it "
            f"guards a site the table does not name"
        )
