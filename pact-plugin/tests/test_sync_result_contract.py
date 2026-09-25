"""
Location: pact-plugin/tests/test_sync_result_contract.py
Summary: MUTATION PINS for the two properties of `SyncResult` that the rest of
         the sync suite silently depends on. Both are cheap to break by a
         well-meant edit and expensive to notice, because breaking either one
         turns assertions elsewhere GREEN rather than red.

         PIN 1 -- the reasons stay mutually distinguishable. The channel exists
         because a refusal, a suppression and an unresolved target were one
         indistinguishable `False`. Any edit that lets two reasons compare equal
         re-creates that collapse, and every test that merely asks "is it falsy"
         keeps passing while it happens.

         PIN 2 -- only `wrote` is truthy. Roughly two dozen assertions across the
         sync suite read this result, several of them as a bare truthiness test.
         If `__bool__` ever returns True for a non-write, those assertions do not
         fail: they PASS on a refusal. That is the exact silent inversion the
         class was shaped to prevent, so it needs a pin that fails loudly.

         WHY A DEDICATED FILE. These pin the TYPE's contract, not any one call
         site, and a reader who breaks the contract should find the objection
         under an obvious name rather than inside a worktree-sync file.
Used by: pytest.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


from scripts.working_memory import SyncResult


# THE ALPHABET IS SPELLED OUT, NOT READ OFF `SyncResult`, AND THAT IS THE WHOLE
# POINT OF WRITING IT TWICE.
#
# Deriving these from `SyncResult.WROTE` and friends looks tidier and destroys
# the pins. Measured, not reasoned: with the constants collapsed so that
# `REFUSED == "wrote"`, a `[r for r in ALL_REASONS if r != WROTE]` filter simply
# DROPS `refused` from the sweep -- the mutation deletes the case that would
# have caught it, and the truthiness pin goes green over a five-element
# alphabet without reporting that it shrank.
#
# A test whose input alphabet comes from the implementation cannot falsify the
# implementation's choice of alphabet. So the literals are the alphabet, and
# `test_constants_match_the_declared_alphabet` below is what ties them back to
# the code: a rename fails there, loudly, instead of silently narrowing a sweep.
LITERAL_REASONS = (
    "wrote",
    "refused",
    "suppressed",
    "unresolved",
    "missing",
    "failed",
    "empty",
    "no_window",
    "resolve_error",
)

LITERAL_NON_WRITE_REASONS = tuple(r for r in LITERAL_REASONS if r != "wrote")


class TestReasonsStayDistinguishable:
    """PIN 1. Two reasons must never compare equal.

    Stated over the WHOLE reason set rather than over the one pair that
    motivated it. `wrote` against `refused` is the pair the channel was built
    for, and it gets its own arm below, but a collapse anywhere in the set
    rebuilds the same ambiguity somewhere else -- `refused` against
    `suppressed` is the pair that emptied a negative control in the archival
    suite, and it is not the pair anyone would have thought to guard.
    """

    def test_constants_match_the_declared_alphabet(self):
        """Ties the spelled-out alphabet back to the code.

        This is the arm that makes it safe for every other test here to use
        literals. A renamed or re-valued constant fails HERE, where the message
        says the alphabet moved, rather than by quietly shrinking a sweep
        somewhere else.
        """
        actual = (
            SyncResult.WROTE,
            SyncResult.REFUSED,
            SyncResult.SUPPRESSED,
            SyncResult.UNRESOLVED,
            SyncResult.MISSING,
            SyncResult.FAILED,
            SyncResult.EMPTY,
            SyncResult.NO_WINDOW,
            SyncResult.RESOLVE_ERROR,
        )
        assert actual == LITERAL_REASONS, (
            f"the reason constants no longer match the alphabet these pins "
            f"sweep: {actual} != {LITERAL_REASONS}"
        )

    def test_no_two_reason_constants_share_a_value(self):
        """The cheapest way to break pin 1 is a copy-paste in the constants."""
        actual = (
            SyncResult.WROTE,
            SyncResult.REFUSED,
            SyncResult.SUPPRESSED,
            SyncResult.UNRESOLVED,
            SyncResult.MISSING,
            SyncResult.FAILED,
            SyncResult.EMPTY,
            SyncResult.NO_WINDOW,
            SyncResult.RESOLVE_ERROR,
        )
        assert len(set(actual)) == len(actual), (
            f"two reason constants share a value: {actual}"
        )

    def test_every_reason_constant_is_in_the_declared_alphabet(self):
        """The two tuples above list constants by hand, so a constant added to
        the class and not to them is swept by nothing -- EMPTY and NO_WINDOW
        were missing from both. This reads the class's upper-case string
        attributes, so a new reason fails here until the alphabet names it."""
        declared = {
            value
            for name, value in vars(SyncResult).items()
            if name.isupper() and isinstance(value, str)
        }
        assert declared == set(LITERAL_REASONS), (
            f"reasons on the class but not in the alphabet: "
            f"{sorted(declared - set(LITERAL_REASONS))}; in the alphabet but not "
            f"on the class: {sorted(set(LITERAL_REASONS) - declared)}"
        )

    @pytest.mark.parametrize("left", LITERAL_REASONS)
    @pytest.mark.parametrize("right", LITERAL_REASONS)
    def test_distinct_reasons_are_unequal_and_equal_reasons_are_equal(
        self, left, right
    ):
        """Both directions, because equality has two failure modes.

        An `__eq__` that always returns True collapses the set. An `__eq__`
        that always returns False makes every reason check unsatisfiable, which
        would surface as a red suite rather than a silent pass -- but pinning
        only the first direction would leave a test that can never fail.
        """
        if left == right:
            assert SyncResult(left) == SyncResult(right)
        else:
            assert SyncResult(left) != SyncResult(right)

    def test_wrote_and_refused_specifically_do_not_collapse(self):
        """The motivating pair, named so the reason for the pin survives.

        A sync that WROTE and a sync the guard REFUSED left the same evidence on
        disk. `.reason` is the only thing that separates them, so it must
        separate them through every channel a caller might use.
        """
        wrote = SyncResult("wrote")
        refused = SyncResult("refused")

        assert wrote != refused
        assert wrote.reason != refused.reason
        assert bool(wrote) != bool(refused)
        assert repr(wrote) != repr(refused)
        assert hash(wrote) != hash(refused)

    def test_a_syncresult_is_not_equal_to_a_bare_bool(self):
        """Pins the CURRENT behaviour, and it is worth knowing deliberately.

        `__eq__` returns NotImplemented for a non-`SyncResult`, so Python falls
        back to identity and `SyncResult('wrote') == True` is False. Callers
        must therefore read `bool(result)` or `result.reason`, never `result ==
        True`. If a later change makes this comparison succeed, that is a
        decision to take on purpose rather than to discover in a caller.
        """
        assert not (SyncResult("wrote") == True)  # noqa: E712
        assert not (SyncResult("failed") == False)  # noqa: E712


class TestOnlyWroteIsTruthy:
    """PIN 2. `__bool__` is true for `wrote` and for nothing else.

    THIS IS THE PIN THAT PROTECTS THE ASSERTIONS THAT CANNOT PROTECT
    THEMSELVES. A truthiness read of this result cannot detect its own
    inversion: if `__bool__` starts returning True for a refusal, `assert
    result` goes on passing and reports that a refused sync succeeded. So the
    property has to be asserted here, directly, where a break is loud.
    """

    def test_wrote_is_truthy(self):
        """POSITIVE ARM. Without it the negative arms below are also satisfied
        by a `__bool__` that returns False for everything, which would make the
        whole class pass while the type reports failure for a successful sync.
        """
        assert bool(SyncResult("wrote")) is True

    @pytest.mark.parametrize("reason", LITERAL_NON_WRITE_REASONS)
    def test_every_non_write_reason_is_falsy(self, reason):
        assert bool(SyncResult(reason)) is False, (
            f"`{reason}` is truthy -- every truthiness read of a sync result "
            f"now reports success on this outcome"
        )

    def test_an_unrecognised_reason_is_falsy(self):
        """TOTALITY. `__init__` takes any string, so the truthiness rule must be
        `reason == WROTE` rather than a list of known failures. A rule written
        as "not one of these five" would call a typo'd or newly added reason a
        successful write -- failing OPEN on the one outcome that must never be
        assumed.
        """
        assert bool(SyncResult("some-reason-nobody-has-defined-yet")) is False
        assert bool(SyncResult("")) is False

    def test_wrote_attribute_and_truthiness_agree(self):
        """`.wrote` and `bool()` are two spellings of one fact, so they must not
        drift. A change to either one alone is the shape this pin catches.
        """
        for reason in LITERAL_REASONS:
            result = SyncResult(reason)
            assert bool(result) is result.wrote


WORKING_MEMORY_SCAFFOLD = (
    "# Probe\n\n"
    "## Working Memory\n"
    "<!-- Auto-managed by pact-memory skill. -->\n"
)


class TestTheReasonSurvivesTheProcessBoundary:
    """THE OBJECT ALONE DOES NOT FIX THIS, WHICH IS WHY THESE ARMS ARE HERE.

    A refused sync and a suppressed one leave IDENTICAL evidence on disk: the
    file is untouched and the CLI exits 0 either way. Inside one process
    `SyncResult` tells them apart, but the pin-archival path spawns the memory
    CLI as a CHILD, and a return value does not cross a process boundary. The
    reason therefore has to travel as a field in the JSON envelope, and these
    arms are what prove it arrives.

    ARM 3 IS A POSITIVE CONTROL AND IT IS NOT OPTIONAL. Arms 1 and 2 both assert
    that the file did not change. If the child could not write AT ALL -- a bad
    scratch project, a resolver miss, an import failure -- both would still
    pass, and the suite would certify a discrimination that no longer exists.
    Arm 3 makes an UNSUPPRESSED save actually write, so a harness that has gone
    blind fails loudly here instead of going quietly green there.
    """

    def _save(self, db_path, project, extra_args=(), pytest_marker=None):
        """Run the REAL CLI in a child process.

        Returns (envelope, file bytes, stderr). The third element exists because
        stderr is the CLI's structured JSON channel, so what an arm writes there
        is part of its observable contract and not merely diagnostic noise.

        `db_path` IS A STORE THAT IS PRESENT, not a bare temp path. `cli.main`
        refuses a `--db-path` naming a store that is absent, for each command
        other than `setup`. Callers pass the `memory_store` fixture result.
        """
        pkg_root = str(Path(__file__).parent.parent / "skills" / "pact-memory")
        db = str(db_path)
        child = (
            "import sys, json\n"
            f"sys.path.insert(0, {pkg_root!r})\n"
            "from scripts import cli\n"
            "cli.main(['save', " + ", ".join(repr(a) for a in extra_args)
            + (", " if extra_args else "")
            + "'--db-path', " + repr(db) + ", "
            "json.dumps({'context': 'boundary probe'})])\n"
        )
        env = dict(os.environ)
        env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        # The same decision as the fault-path contract in
        # test_embedding_status_contract.py, and here it protects a stronger
        # property. huggingface_hub relays server-sent warning headers through
        # logger.warning(), which lastResort puts on stderr. The differential
        # below cancels pre-existing emitters BECAUSE they fire identically in
        # both arms -- but this one is triggered per REQUEST by rate limiting,
        # so it can land on one arm and not the other and break exactly that
        # precondition. Silencing the library's own logger at source restores
        # it. The text is the Hub's, not ours, so it could never be matched on.
        env["HF_HUB_VERBOSITY"] = "error"
        # SCOPED TO A SCRATCH PROJECT. --db-path scopes the DATABASE only; the
        # sync still resolves a CLAUDE.md ambiently. Without this the arms below
        # would write into the operator's live file.
        env["CLAUDE_PROJECT_DIR"] = str(project)
        # The guard keys on this variable being set in the CHILD's environment,
        # so each arm states its own value rather than inheriting the parent's.
        env.pop("PYTEST_CURRENT_TEST", None)
        if pytest_marker is not None:
            env["PYTEST_CURRENT_TEST"] = pytest_marker

        proc = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True,
            timeout=180, env=env,
        )
        assert proc.returncode == 0, f"child failed: {proc.stderr[:400]}"
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True, payload
        md = project / "CLAUDE.md"
        return payload["result"], md.read_bytes(), proc.stderr

    @pytest.fixture
    def project(self, tmp_path):
        p = tmp_path / "scratch-project"
        p.mkdir()
        (p / "CLAUDE.md").write_text(WORKING_MEMORY_SCAFFOLD, encoding="utf-8")
        return p

    def test_suppressed_reaches_the_parent(self, memory_store, project):
        """ARM 1 -- the caller declined the sync."""
        before = (project / "CLAUDE.md").read_bytes()
        result, after, _ = self._save(
            memory_store("probe.db"), project, extra_args=("--no-sync",)
        )

        assert result["sync_status"] == "suppressed", result
        assert after == before, "a suppressed save wrote to CLAUDE.md"

    def test_refused_reaches_the_parent(self, memory_store, project):
        """ARM 2 -- the guard declined the sync, and this is the one that used
        to be invisible. The child is spawned the way a test process spawns it,
        so the ambient-target guard raises inside the child, save() logs it as
        non-critical, and the parent previously saw only an untouched file.
        """
        before = (project / "CLAUDE.md").read_bytes()
        result, after, _ = self._save(
            memory_store("probe.db"), project,
            pytest_marker="probe.py::test_x (call)",
        )

        assert result["sync_status"] == "refused", result
        assert after == before, "a refused save wrote to CLAUDE.md"

    def test_wrote_reaches_the_parent_and_the_file_really_changed(
        self, memory_store, project
    ):
        """ARM 3 -- the positive control. See the class docstring."""
        before = (project / "CLAUDE.md").read_bytes()
        result, after, _ = self._save(memory_store("probe.db"), project)

        assert result["sync_status"] == "wrote", result
        assert after != before, (
            "an unsuppressed, unrefused save left CLAUDE.md unchanged -- the "
            "child cannot write at all, so arms 1 and 2 prove nothing"
        )

    def test_the_two_silent_outcomes_are_separated_only_by_the_status(
        self, memory_store, project
    ):
        """THE WHOLE POINT, ASSERTED DIRECTLY RATHER THAN IMPLIED BY TWO ARMS.

        Suppression and refusal produce byte-identical disk evidence. If the
        status field is ever dropped from the envelope, the two become one
        observation again -- and no assertion about the FILE can notice, because
        the file is the thing that is identical.
        """
        # SEPARATE STORES so the two children do not share a database file.
        # Each one must be PRESENT before a command other than `setup` is handed
        # its path, which is what the `memory_store` fixture supplies.
        db_a = memory_store("a.db")
        db_b = memory_store("b.db")

        suppressed, after_suppressed, err_suppressed = self._save(
            db_a, project, extra_args=("--no-sync",)
        )
        refused, after_refused, err_refused = self._save(
            db_b, project, pytest_marker="probe.py::test_x (call)"
        )

        assert after_suppressed == after_refused, (
            "precondition: the two outcomes must be indistinguishable on disk, "
            "otherwise this test is not measuring what it claims"
        )

        # THE SAME THESIS, ON THE SECOND CHANNEL. stderr is where this CLI puts
        # its error envelope, so a free-text line there is not diagnostic noise
        # -- it is corruption of a channel callers parse. A DIFFERENTIAL rather
        # than an emptiness check on purpose: several pre-existing emitters can
        # write here when the sqlite/vector dependencies are absent, and those
        # fire identically in both arms. Comparing the arms cancels them and
        # measures only what the REFUSAL adds, which is the thing under test.
        assert err_suppressed == err_refused, (
            "the two outcomes are distinguishable on stderr, which is this "
            "CLI's structured JSON channel. The status field is supposed to be "
            "the ONLY thing that separates them.\n"
            f"  suppressed stderr: {err_suppressed!r}\n"
            f"  refused stderr:    {err_refused!r}"
        )
        assert suppressed["sync_status"] != refused["sync_status"], (
            "the two outcomes collapsed back into one observation"
        )
        assert {suppressed["sync_status"], refused["sync_status"]} == {
            "suppressed", "refused"
        }


class TestAResolveErrorIsNotAnUnresolvedTarget:
    """Resolution that found nothing because it could not LOOK reports
    RESOLVE_ERROR; resolution that looked and found nothing reports UNRESOLVED.
    Before the reason existed the two were one `(None, None)`.

    Both arms pass `claude_md_root`, which is the only route past the
    ambient-target refusal under pytest; it declares the containment anchor
    and does not steer resolution. The cwd is an empty non-repository
    directory, so the declared project is the only rung that can answer.
    """

    @pytest.mark.skipif(
        os.geteuid() == 0,
        reason="root searches a mode-0 directory, so no EACCES can be built",
    )
    def test_an_unsearchable_project_dir_is_a_resolve_error(
        self, tmp_path, monkeypatch
    ):
        from scripts.working_memory import sync_to_claude_md

        locked = tmp_path / "locked"
        (locked / "proj").mkdir(parents=True)
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(locked / "proj"))
        locked.chmod(0o000)
        try:
            result = sync_to_claude_md({"context": "c"}, None, "id", claude_md_root=tmp_path)
        finally:
            locked.chmod(0o700)

        assert result.reason == SyncResult.RESOLVE_ERROR, result

    def test_a_readable_empty_project_dir_is_unresolved(self, tmp_path, monkeypatch):
        """The control: the same call, the project directory readable and
        empty, is UNRESOLVED."""
        from scripts.working_memory import sync_to_claude_md

        (tmp_path / "proj").mkdir()
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))

        result = sync_to_claude_md({"context": "c"}, None, "id", claude_md_root=tmp_path)

        assert result.reason == SyncResult.UNRESOLVED, result
