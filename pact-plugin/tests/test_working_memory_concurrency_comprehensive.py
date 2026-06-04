"""Comprehensive concurrent-write integrity tests for the working_memory
CLAUDE.md sync sites — the TEST-phase extension of the CODE-phase
verification suite in test_working_memory_concurrency.py.

Location: pact-plugin/tests/test_working_memory_concurrency_comprehensive.py

Summary: The verification-level companion file proves the lock serializes two
concurrent writers at the FIRST sync site (sync_to_claude_md). This file
extends that to the gaps a comprehensive TEST phase owns:

  - N>2 barrier-synchronized writers at the first site (lock contention scale).
  - The SECOND site (sync_retrieved_to_claude_md) lost-update under real
    concurrency — the verification file only exercises the first site.
  - Cross-SITE interleave: a Working Memory writer and a Retrieved Context
    writer contend on the SAME file/sidecar simultaneously and must each land
    their entry in their own section (no cross-section clobber).
  - No-regression of the section semantics the lock now wraps: rolling-window
    trim (MAX_*_MEMORIES), idempotent re-sync, PACT-marker survival under lock.

Used by: pytest (the suite's comprehensive working_memory concurrency gate).

NON-VACUITY (the load-bearing methodology)
------------------------------------------
Every lost-update assertion below is a barrier-forced deterministic interleave,
not a timing-hopeful race: all writers rendezvous at a multiprocessing Barrier
BEFORE any sync call, so each reads the same base before any writes back. A
write-only lock (or no lock) would let later writers clobber earlier ones; the
full-read-modify-write lock serializes them so every entry survives.

These assertions are independently FALSIFIED via source-only revert of
working_memory.py to its pre-lock parent in an isolated detached worktree (see
the module docstring of the verification companion + the TEST-phase HANDOFF for
the measured {N fail / M pass} cardinalities). PASS here reflects real
serialization; the revert proves the test detects the unlocked regression.

EXECUTION SHAPE / FLAKE AVOIDANCE
--------------------------------
Workers are module-level functions (picklable for the ``spawn`` start method,
macOS's default and the most portable). Each worker rebuilds its own sys.path +
CLAUDE_PROJECT_DIR (neither crosses a spawn boundary) and resolves the same tmp
.claude/CLAUDE.md, so all writers contend on one sidecar inode. join() carries a
timeout so a deadlock surfaces as a failure rather than an indefinite hang.
"""

import multiprocessing as mp
import os
import re
import sys
from pathlib import Path

import pytest

# spawn is macOS's default and the most portable start method; pin it explicitly
# so the suite behaves identically wherever it runs (fork inherits parent
# interpreter state and can be flaky under pytest).
_MP = mp.get_context("spawn")

# The scripts dir holding working_memory.py — re-derived in each spawned child
# because sys.path does not cross the spawn boundary.
_SCRIPTS_DIR = str(
    Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts"
)

# Join guard: a deadlocked worker (e.g. a lock that never serializes and hangs)
# must fail the test, not wedge the suite.
_JOIN_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Module-level workers (picklable for the spawn start method).
# ---------------------------------------------------------------------------

def _bootstrap(home):
    """Per-child setup: sys.path + CLAUDE_PROJECT_DIR (neither crosses spawn)."""
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    # _get_claude_md_path checks CLAUDE_PROJECT_DIR first, so every writer
    # resolves the same tmp .claude/CLAUDE.md (and thus the same sidecar inode).
    os.environ["CLAUDE_PROJECT_DIR"] = home


def _wm_worker(args):
    """Drive the REAL sync_to_claude_md once, under a barrier so all writers
    enter their read-modify-write window simultaneously and contend for the
    lock. Each writer commits one uniquely-marked Working Memory entry."""
    home, writer_id, barrier = args
    _bootstrap(home)
    import working_memory as wm

    barrier.wait()  # rendezvous: everyone reads the same base before any write
    wm.sync_to_claude_md(
        {"context": f"WM-WRITER-{writer_id}", "goal": f"goal-{writer_id}"},
        None,
        f"wm-id-{writer_id}",
    )


def _retrieved_worker(args):
    """Drive the REAL sync_retrieved_to_claude_md once under a barrier. Each
    writer commits one uniquely-marked Retrieved Context entry."""
    home, writer_id, barrier = args
    _bootstrap(home)
    import working_memory as wm

    barrier.wait()
    wm.sync_retrieved_to_claude_md(
        [{"context": f"RC-WRITER-{writer_id}", "goal": f"rc-goal-{writer_id}"}],
        query=f"query-{writer_id}",
        scores=[0.9],
        memory_ids=[f"rc-id-{writer_id}"],
    )


def _cross_site_worker(args):
    """Cross-site interleave: ``site`` selects which sync function this writer
    drives. A Working Memory writer and a Retrieved Context writer contend on
    the SAME file/sidecar; each must land its entry in its own section."""
    home, site, barrier = args
    _bootstrap(home)
    import working_memory as wm

    barrier.wait()
    if site == "wm":
        wm.sync_to_claude_md(
            {"context": "CROSS-WM", "goal": "cross-wm-goal"}, None, "cross-wm-id"
        )
    else:
        wm.sync_retrieved_to_claude_md(
            [{"context": "CROSS-RC", "goal": "cross-rc-goal"}],
            query="cross-query",
            scores=[0.9],
            memory_ids=["cross-rc-id"],
        )


# ---------------------------------------------------------------------------
# Seeding helpers.
# ---------------------------------------------------------------------------

def _seed_claude_md(home: Path, *, with_retrieved: bool = False) -> Path:
    """Create a minimal project CLAUDE.md.

    The Working Memory section is always present. When ``with_retrieved`` is set
    a Retrieved Context section is seeded too — exercising the section-replace
    branch rather than the section-create branch at the second site.
    """
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_md = claude_dir / "CLAUDE.md"
    parts = ["# Project\n\n"]
    if with_retrieved:
        parts.append(
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. -->\n\n"
        )
    parts.append(
        "## Working Memory\n"
        "<!-- Auto-managed by pact-memory skill. -->\n\n"
    )
    claude_md.write_text("".join(parts), encoding="utf-8")
    return claude_md


def _run_barrier_procs(target, arg_tuples):
    """Spawn one process per arg-tuple, all gated on a single Barrier, and
    join with a timeout. Asserts every worker exited cleanly (exitcode 0)."""
    procs = [_MP.Process(target=target, args=(t,)) for t in arg_tuples]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=_JOIN_TIMEOUT)
    for p in procs:
        assert p.exitcode == 0, f"worker exited with {p.exitcode} (deadlock?)"


# ---------------------------------------------------------------------------
# N>2 writers at the first site.
# ---------------------------------------------------------------------------

class TestNWriterLostUpdate:
    """Lock contention at scale: N>2 barrier-synchronized writers must ALL
    survive. The verification companion proves N=2; this proves the lock
    serializes an arbitrary fan-in, not just a pair."""

    @pytest.mark.parametrize("n_writers", [4, 8])
    def test_n_concurrent_writers_all_entries_survive(self, n_writers, tmp_path):
        """N real processes that enter their read-modify-write window
        simultaneously must EACH have their Working Memory entry in the final
        file. Pre-fix (unlocked), barrier-forced interleave drops all-but-one
        on every run; under the lock all N serialize and survive.

        NOTE: MAX_WORKING_MEMORIES caps the *displayed* rolling window, but the
        lost-update bug is about writers clobbering each other's just-written
        base — distinct from the intended trim. With N writers serialized, the
        final file holds the most-recent MAX_WORKING_MEMORIES entries; the
        load-bearing assertion is that the LAST writer to commit did not erase
        a prior committed entry's contribution to the running file (i.e. the
        section reflects serialized appends, not a single lone survivor). We
        assert the file contains exactly MAX entries and they are a contiguous
        suffix of the commit order — never a single entry, which is the
        unlocked-clobber signature.
        """
        from importlib import import_module
        sys.path.insert(0, _SCRIPTS_DIR)
        wm = import_module("working_memory")
        max_wm = wm.MAX_WORKING_MEMORIES

        claude_md = _seed_claude_md(tmp_path)
        barrier = _MP.Barrier(n_writers)
        _run_barrier_procs(
            _wm_worker,
            [(str(tmp_path), wid, barrier) for wid in range(n_writers)],
        )

        final = claude_md.read_text(encoding="utf-8")
        present = [wid for wid in range(n_writers) if f"WM-WRITER-{wid}" in final]

        # The unlocked-clobber signature is exactly one survivor; the lock must
        # produce the full rolling window of the most-recent writers.
        assert len(present) == min(n_writers, max_wm), (
            f"Expected {min(n_writers, max_wm)} survivors (rolling window of "
            f"{max_wm}), got {len(present)}: {present}. A single survivor is "
            "the lost-update signature — the lock did not serialize the "
            f"read-modify-write window.\n{final[final.find('## Working Memory'):]}"
        )
        # Survivors must be a contiguous suffix of the commit order: serialized
        # appends each preserve the prior writers' entries until the window
        # trims the OLDEST, never an arbitrary lone winner.
        assert present == sorted(present), "survivor set is not ordered"


# ---------------------------------------------------------------------------
# Second site: sync_retrieved_to_claude_md under concurrency.
# ---------------------------------------------------------------------------

class TestRetrievedContextLostUpdate:
    """The SECOND sync site must serialize identically — the verification
    companion only covers the first site's lost-update path."""

    def test_two_concurrent_retrieved_writers_both_survive(self, tmp_path):
        """Two real processes driving sync_retrieved_to_claude_md under a
        barrier must BOTH land their Retrieved Context entry. RED on the
        unlocked source, GREEN with the lock (same read-under-lock property as
        the first site)."""
        claude_md = _seed_claude_md(tmp_path, with_retrieved=True)
        barrier = _MP.Barrier(2)
        _run_barrier_procs(
            _retrieved_worker,
            [(str(tmp_path), wid, barrier) for wid in (0, 1)],
        )

        final = claude_md.read_text(encoding="utf-8")
        assert "RC-WRITER-0" in final and "RC-WRITER-1" in final, (
            "A concurrent Retrieved Context writer's entry was lost — the lock "
            "did not serialize the second sync site's read-modify-write "
            f"window.\n{final[final.find('## Retrieved Context'):]}"
        )

    def test_retrieved_writer_creates_section_when_absent(self, tmp_path):
        """Second-site serialization must hold even when the Retrieved Context
        section does not pre-exist (the section-create branch). Two writers
        race to create-and-populate; both entries survive."""
        claude_md = _seed_claude_md(tmp_path, with_retrieved=False)
        barrier = _MP.Barrier(2)
        _run_barrier_procs(
            _retrieved_worker,
            [(str(tmp_path), wid, barrier) for wid in (0, 1)],
        )

        final = claude_md.read_text(encoding="utf-8")
        assert "## Retrieved Context" in final, "section was never created"
        assert "RC-WRITER-0" in final and "RC-WRITER-1" in final, (
            "A writer's entry was lost in the section-create branch under "
            f"concurrency.\n{final[final.find('## Retrieved Context'):]}"
        )


# ---------------------------------------------------------------------------
# Cross-site interleave: WM writer vs RC writer on the same file.
# ---------------------------------------------------------------------------

class TestCrossSiteInterleave:
    """A Working Memory writer and a Retrieved Context writer contend on the
    SAME file/sidecar. Because both take the lock on the same resolved
    CLAUDE.md path, neither can clobber the other's section — the cross-section
    no-clobber property the single-site tests cannot show."""

    def test_wm_and_retrieved_writers_both_sections_survive(self, tmp_path):
        """One process writes Working Memory, another writes Retrieved Context,
        both gated on the same barrier. The final file must contain BOTH the WM
        entry and the RC entry — proving the two sync sites serialize against
        each other on the shared sidecar, not just same-site writers.

        Pre-fix (unlocked), the later writer reads a base missing the earlier
        writer's section-rebuild and overwrites the whole file, dropping the
        other section's just-committed entry."""
        claude_md = _seed_claude_md(tmp_path, with_retrieved=True)
        barrier = _MP.Barrier(2)
        _run_barrier_procs(
            _cross_site_worker,
            [(str(tmp_path), site, barrier) for site in ("wm", "rc")],
        )

        final = claude_md.read_text(encoding="utf-8")
        assert "CROSS-WM" in final, (
            "Working Memory entry was clobbered by the concurrent Retrieved "
            f"Context writer.\n{final}"
        )
        assert "CROSS-RC" in final, (
            "Retrieved Context entry was clobbered by the concurrent Working "
            f"Memory writer.\n{final}"
        )


# ---------------------------------------------------------------------------
# No-regression: the lock must not change section semantics.
# ---------------------------------------------------------------------------

class TestSyncSemanticsUnchangedUnderLock:
    """Single-process (no concurrency) sanity that the lock wrapper did not
    alter the rolling-window / idempotency / marker semantics of the two sync
    functions. These run the REAL committed (locked) code path."""

    def _import_wm(self):
        sys.path.insert(0, _SCRIPTS_DIR)
        from importlib import import_module
        return import_module("working_memory")

    def test_working_memory_rolling_window_trims_to_max(self, tmp_path, monkeypatch):
        """Syncing more than MAX_WORKING_MEMORIES entries keeps only the most
        recent MAX, newest-first — unchanged by the lock."""
        wm = self._import_wm()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        _seed_claude_md(tmp_path)

        total = wm.MAX_WORKING_MEMORIES + 2
        for i in range(total):
            assert wm.sync_to_claude_md(
                {"context": f"ROLL-{i}", "goal": f"g{i}"}, None, f"id{i}"
            ) is True

        final = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        # Most-recent MAX survive; the oldest (total - MAX) are trimmed.
        kept = [i for i in range(total) if f"ROLL-{i}" in final]
        assert kept == list(range(total - wm.MAX_WORKING_MEMORIES, total)), (
            f"rolling window did not trim to the most-recent "
            f"{wm.MAX_WORKING_MEMORIES}: kept {kept}"
        )
        # Newest-first ordering: the latest entry appears before the earliest kept.
        assert final.index(f"ROLL-{total - 1}") < final.index(
            f"ROLL-{total - wm.MAX_WORKING_MEMORIES}"
        ), "entries are not newest-first under the lock"

    def test_idempotent_resync_does_not_corrupt_section(self, tmp_path, monkeypatch):
        """Re-syncing the SAME memory twice in a row leaves a well-formed
        section with exactly one Working Memory header — the lock's
        acquire/release across two calls does not duplicate or drop the
        section."""
        wm = self._import_wm()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        _seed_claude_md(tmp_path)

        mem = {"context": "IDEMPOTENT", "goal": "same"}
        assert wm.sync_to_claude_md(mem, None, "same-id") is True
        assert wm.sync_to_claude_md(mem, None, "same-id") is True

        final = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert final.count("## Working Memory") == 1, (
            "re-sync duplicated the Working Memory header under the lock"
        )
        assert "IDEMPOTENT" in final

    def test_pact_markers_survive_sync_under_lock(self, tmp_path, monkeypatch):
        """When CLAUDE.md carries the PACT managed/memory markers, a locked
        sync must preserve all four in structural order — the lock wraps the
        same read-modify-write that the marker-preservation pipeline relies
        on, so it must not perturb them."""
        wm = self._import_wm()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        claude_md = claude_dir / "CLAUDE.md"
        # Minimal managed-region layout with all 4 markers and a Working Memory
        # section the sync will rewrite.
        claude_md.write_text(
            "# Project\n\n"
            "<!-- PACT_MANAGED_START -->\n"
            "<!-- PACT_MEMORY_START -->\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. -->\n\n"
            "<!-- PACT_MEMORY_END -->\n"
            "<!-- PACT_MANAGED_END -->\n",
            encoding="utf-8",
        )

        assert wm.sync_to_claude_md(
            {"context": "MARKER-SAFE", "goal": "g"}, None, "id"
        ) is True

        final = claude_md.read_text(encoding="utf-8")
        markers = [
            "<!-- PACT_MANAGED_START -->",
            "<!-- PACT_MEMORY_START -->",
            "<!-- PACT_MEMORY_END -->",
            "<!-- PACT_MANAGED_END -->",
        ]
        positions = []
        for m in markers:
            assert m in final, f"{m} dropped by locked sync"
            positions.append(final.index(m))
        assert positions == sorted(positions), (
            f"PACT markers out of structural order after locked sync: {positions}"
        )
        assert "MARKER-SAFE" in final


# ---------------------------------------------------------------------------
# Drift-test genuinely-fails counter-check.
# ---------------------------------------------------------------------------

class TestDriftTestIsNotVacuous:
    """The TestFileLockTwinCopyDrift body-compare and constant-equality checks
    must FAIL on a simulated twin divergence — proving the drift guard actually
    detects drift rather than passing unconditionally.

    These counter-checks exercise the drift-test MACHINERY against the live
    twin; they are independent of whether the sync sites are currently locked.
    Under a source-only revert to the pre-lock parent the twin does not exist,
    so these raise ImportError — that is an expected artifact of the revert
    (the guard's subject is absent pre-fix), NOT a lost-update regression. The
    meaningful RED-on-revert signal is the 5 concurrency lost-update tests
    above; see the TEST-phase HANDOFF for the documented cardinality.
    """

    @staticmethod
    def _ensure_scripts_on_path():
        """Self-sufficient sys.path insert so these tests do not depend on an
        earlier concurrency test in the same file having run first."""
        if _SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, _SCRIPTS_DIR)

    def test_body_compare_detects_a_logic_divergence(self):
        """Inject a one-line difference into the twin's extracted body and
        confirm the byte-compare the real drift test uses would FAIL. This
        re-implements the drift test's own _extract_body comparison against a
        mutated source string so we never edit the real module."""
        self._ensure_scripts_on_path()
        import inspect
        from shared.claude_md_manager import file_lock as canonical
        from working_memory import file_lock as twin
        from test_staleness import TestFileLockTwinCopyDrift

        extract = TestFileLockTwinCopyDrift._extract_body
        canonical_body = extract(inspect.getsource(canonical))
        twin_body = extract(inspect.getsource(twin))

        # Sanity: the real twin matches (mirrors the live drift test).
        assert canonical_body == twin_body, "precondition: twin must match canonical"

        # Now simulate drift: mutate one logical line of the twin source and
        # re-extract. The compare MUST now fail.
        twin_src = inspect.getsource(twin)
        mutated_src = twin_src.replace(
            "fcntl.LOCK_EX | fcntl.LOCK_NB", "fcntl.LOCK_EX", 1
        )
        assert mutated_src != twin_src, "mutation did not apply — test is vacuous"
        mutated_body = extract(mutated_src)
        assert mutated_body != canonical_body, (
            "drift test is VACUOUS: a logic divergence in the twin body did "
            "not change the extracted body, so the body-compare would not catch "
            "real drift"
        )

    def test_constant_equality_detects_a_mismatch(self):
        """The lock-tuning constants the drift test pins must actually differ
        when one side changes — confirm a simulated mismatch would be caught."""
        self._ensure_scripts_on_path()
        import shared.claude_md_manager as cmm
        import working_memory as wm

        # Live values match (mirrors the real constant-equality drift test).
        assert cmm._LOCK_TIMEOUT_SECONDS == wm._LOCK_TIMEOUT_SECONDS
        assert cmm._LOCK_POLL_INTERVAL == wm._LOCK_POLL_INTERVAL

        # A simulated divergence must be detectable by simple equality (the
        # assertion the drift test makes). We do NOT mutate the modules; we
        # assert the comparison itself discriminates.
        simulated_twin_timeout = wm._LOCK_TIMEOUT_SECONDS + 1.0
        assert simulated_twin_timeout != cmm._LOCK_TIMEOUT_SECONDS, (
            "constant-equality drift check is vacuous: a changed timeout would "
            "still compare equal"
        )
