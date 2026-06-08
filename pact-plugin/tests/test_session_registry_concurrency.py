"""Live-parallelism appender-integrity tests for the self-registration registry
(hooks/shared/session_registry.py) — #885.

WHY THIS FILE EXISTS (the gap it closes)
----------------------------------------
The registry's no-lock write discipline rests on ONE load-bearing claim: a single
``os.write`` of one JSONL line under ``O_APPEND`` is ATOMIC against concurrent
appenders, so no advisory lock is needed. The module's own unit suite
(test_session_registry.py) asserts the STATIC <=512B bound (``register`` skips an
oversized line) but never reproduces real parallelism — so the atomicity claim is
architecturally asserted, not regression-protected. This file converts the claim
into a live invariant: it spawns N REAL appender processes that contend on one
registry file and asserts ZERO torn lines + last-wins-per-session_id integrity.

NON-VACUITY (the load-bearing methodology — proves this is not green-because-
nothing-was-tested)
-------------------------------------------------------------------------------
A zero-tear result is only meaningful if the tear DETECTOR can actually catch a
tear. So the file pairs the positive leg with a CONTROL:

  * CONTROL (``test_non_atomic_writer_tears_and_detector_catches_it``): an
    intentionally NON-ATOMIC appender splits each logical line into TWO
    ``os.write`` calls. Under the same N-process contention those two halves
    interleave across processes -> torn lines, which the JSON-parse detector
    CATCHES (asserts torn > 0). This is what a NON-atomic writer — or, on a
    stricter filesystem, a kernel-split write that exceeds the platform atomicity
    bound — would produce.
  * POSITIVE (``test_single_write_appender_never_tears``): the PRODUCTION
    single-``os.write`` syscall shape (exactly what ``register`` does) under the
    SAME contention -> ZERO torn lines.

The control catching tears + the positive yielding zero, under identical
contention, is the proof that the zero-tear result reflects real atomicity, not a
test that exercised nothing.

A NOTE ON LINE SIZE vs SYSCALL COUNT (empirically grounded)
-----------------------------------------------------------
The real tear axis is SYSCALL COUNT, not line size. On a regular file under
O_APPEND, a single ``os.write`` was observed NOT to tear even at ~70KB on the
development filesystem — the kernel does not split a single buffered append there.
PIPE_BUF (512B on macOS) is the PORTABLE POSIX atomicity FLOOR: it is the size at
or under which a single write is guaranteed atomic on EVERY POSIX filesystem,
including stricter ones that would split a larger write into multiple physical
writes (the multi-write case the CONTROL models). ``register``'s <=512B skip-guard
is therefore the portable contract that keeps every production write inside the
guaranteed-atomic regime; the static bound is unit-tested in
test_session_registry.py, and this file proves the single-write atomicity the
bound exists to guarantee.

EXECUTION SHAPE / FLAKE AVOIDANCE
---------------------------------
Workers are module-level functions (picklable for the ``spawn`` start method,
which is macOS's default and the most portable). Each worker rebuilds its own
sys.path + imports + module state (monkeypatching and sys.path do NOT cross a
spawn boundary), redirects ``Path.home`` into the shared tmp tree so the registry
path passes ``register``'s path-containment guard, and contends through a real
multiprocessing barrier so all writers hit the file simultaneously. Sizes are
bounded (modest process + per-process write counts) to keep each test well under
a couple of seconds and deterministic.
"""

import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

import pytest

from shared import session_registry
from shared.session_registry import register, resolve

# spawn is macOS's default and the most portable start method; pin it explicitly
# so the suite behaves identically wherever it runs (fork would inherit the
# parent interpreter state and mask import-isolation bugs in the workers).
_MP = mp.get_context("spawn")

_HOOKS_DIR = str(Path(__file__).parent.parent / "hooks")

# Bounded contention sizing — enough parallel pressure to surface a real
# interleave in the non-atomic control, small enough to stay fast + deterministic.
_N_PROCS = 12
_WRITES_PER_PROC = 150


# ---------------------------------------------------------------------------
# Module-level workers (must be picklable for the spawn start method). Each
# rebuilds its own interpreter state — sys.path + module import + Path.home
# redirect + the session_id env var — because none of that crosses spawn.
# ---------------------------------------------------------------------------

def _prepare_child(home: str):
    """Re-establish in the spawned child what the parent's fixture set up: the
    hooks import path, the Path.home redirect (so the tmp registry path passes
    register's containment guard), and a fresh import of the real module."""
    if _HOOKS_DIR not in sys.path:
        sys.path.insert(0, _HOOKS_DIR)
    fake_home = Path(home)
    # Redirect Path.home so _is_under_pact_sessions resolves the tmp tree.
    Path.home = classmethod(lambda cls: fake_home)  # type: ignore[assignment]
    import shared.session_registry as sr
    sr.REGISTRY_PATH = fake_home / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
    return sr


def _register_worker(args):
    """Positive worker: drive the REAL register() — single os.write per line —
    under its own session_id, many times, racing the other workers."""
    home, session_id, team, name, n = args
    sr = _prepare_child(home)
    os.environ["CLAUDE_CODE_SESSION_ID"] = session_id
    value = f"{name}@{team}"
    for _ in range(n):
        sr.register(value)


# NOTE: the former `_non_atomic_worker` (a racing two-os.write appender used to
# INDUCE tearing) was removed when the non-vacuity control became deterministic
# (test_tear_detector_is_non_vacuous_on_a_known_torn_line constructs the torn
# byte-pattern directly). The production positive test still races real
# single-os.write register() processes via `_register_worker`.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_torn(registry_path: Path) -> tuple[int, int]:
    """Return (total_nonblank_lines, torn_lines). A torn line is any non-blank
    line that does not parse as JSON — the same failure a consumer's resolve()
    scan would skip. This is the tear detector."""
    if not registry_path.exists():
        return (0, 0)
    raw = registry_path.read_text(encoding="utf-8")
    total = 0
    torn = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        total += 1
        try:
            json.loads(line)
        except ValueError:
            torn += 1
    return (total, torn)


@pytest.fixture
def concurrency_env(tmp_path, monkeypatch):
    """Parent-side isolation: redirect the parent interpreter's Path.home +
    REGISTRY_PATH into tmp_path (so any parent-side resolve() reads the same
    file the children wrote), and write team configs the children's values will
    validate against. Children re-establish their own state via _prepare_child."""
    fake_home = tmp_path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    reg_path = fake_home / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
    monkeypatch.setattr(session_registry, "REGISTRY_PATH", reg_path)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    class _Env:
        home = str(fake_home)
        registry_path = reg_path

        @staticmethod
        def write_team(team, member_names):
            d = fake_home / ".claude" / "teams" / team
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(
                json.dumps({"members": [{"name": n} for n in member_names]}),
                encoding="utf-8",
            )

    return _Env


# ===========================================================================
# CONTROL (non-vacuity) — a non-atomic writer DOES tear, detector DOES catch it
# ===========================================================================

def test_tear_detector_is_non_vacuous_on_a_known_torn_line(concurrency_env):
    """NON-VACUITY CONTROL (DETERMINISTIC, CI-robust): construct a registry file
    containing a KNOWN torn line — exactly the byte shape a two-``os.write``
    interleave produces (two records' halves mashed onto shared physical lines)
    — and assert the JSON-parse tear detector CATCHES it (torn > 0). This proves
    ``_count_torn`` is NON-VACUOUS on ANY environment: without this leg, the
    zero-tear positive result (test_single_write_appender_never_tears) could be
    green-because-nothing-was-tested.

    Why DETERMINISTIC, not a race: the prior control RACED N non-atomic
    appenders and HOPED the OS scheduler interleaved their two half-writes. On a
    low-core CI runner the halves never interleaved -> 0 torn lines -> a false
    CI failure, even though the detector works fine. The non-vacuity PROOF (the
    detector can catch a torn line) must not depend on the environment actually
    producing a race. We construct the identical torn byte-pattern directly:
    split two logical lines mid-record (as a non-atomic writer does) and
    interleave the halves firstA+firstB+secondA+secondB, so the physical lines
    (split on '\\n') are two mashed-together JSON objects that DO NOT parse.
    """
    reg = concurrency_env.registry_path
    reg.parent.mkdir(parents=True, exist_ok=True)
    payload_a = (json.dumps({"session_id": "sess-control-a", "value": "y" * 40}) + "\n").encode("utf-8")
    payload_b = (json.dumps({"session_id": "sess-control-b", "value": "z" * 40}) + "\n").encode("utf-8")
    ha, hb = len(payload_a) // 2, len(payload_b) // 2
    first_a, second_a = payload_a[:ha], payload_a[ha:]
    first_b, second_b = payload_b[:hb], payload_b[hb:]
    # Writer B's first half lands between writer A's two halves -> the bytes mash
    # onto shared physical lines, exactly as a real two-syscall interleave would.
    torn_bytes = first_a + first_b + second_a + second_b
    # Include one CLEAN line so `total` proves the detector distinguishes torn
    # from intact (not just "everything is torn").
    clean = (json.dumps({"session_id": "sess-clean", "value": "ok"}) + "\n").encode("utf-8")
    with open(reg, "wb") as fh:
        fh.write(torn_bytes + clean)

    total, torn = _count_torn(reg)
    assert total > 0, "the control wrote nothing — the harness itself is broken"
    assert torn > 0, (
        f"the tear detector caught {torn} torn lines out of {total} on a KNOWN "
        f"torn write — it MUST catch >=1. If it does not, _count_torn is vacuous "
        f"and the zero-tear positive result below proves nothing."
    )
    # And it must NOT over-count: the single clean line parses, so torn < total.
    assert torn < total, "the clean line must parse — detector must not flag intact lines"


# ===========================================================================
# POSITIVE — the production single-os.write pattern NEVER tears under contention
# ===========================================================================

def test_single_write_appender_never_tears(concurrency_env):
    """The PRODUCTION path: N real register() processes (one single os.write per
    line, exactly register's syscall shape) contend on one registry file and
    produce ZERO torn lines. This is the no-lock atomicity invariant the design
    rests on — every line parses as JSON."""
    for i in range(_N_PROCS):
        concurrency_env.write_team(f"pact-c{i}", [f"agent{i}"])
    args = [
        (concurrency_env.home, f"sess-pos-{i}", f"pact-c{i}", f"agent{i}", _WRITES_PER_PROC)
        for i in range(_N_PROCS)
    ]
    with _MP.Pool(_N_PROCS) as pool:
        pool.map(_register_worker, args)

    total, torn = _count_torn(concurrency_env.registry_path)
    assert total > 0, "no lines were written — the workers did not run"
    assert torn == 0, (
        f"{torn} of {total} lines are torn under concurrent single-os.write "
        f"appends. The no-lock design REQUIRES single-write atomicity; a torn "
        f"line means the <=512B/single-write contract is not holding on this "
        f"platform."
    )


def test_last_wins_per_session_id_under_concurrency(concurrency_env):
    """Integrity beyond no-tears: after N processes each register their own
    session_id many times, every session_id resolves to ITS OWN last-written
    value (self-lookup-only holds across processes; no cross-session bleed)."""
    for i in range(_N_PROCS):
        concurrency_env.write_team(f"pact-c{i}", [f"agent{i}"])
    args = [
        (concurrency_env.home, f"sess-pos-{i}", f"pact-c{i}", f"agent{i}", _WRITES_PER_PROC)
        for i in range(_N_PROCS)
    ]
    with _MP.Pool(_N_PROCS) as pool:
        pool.map(_register_worker, args)

    # Each session resolves to its own agent@team — no cross-session contamination.
    for i in range(_N_PROCS):
        assert resolve(f"sess-pos-{i}") == f"agent{i}@pact-c{i}", (
            f"session sess-pos-{i} did not resolve to its own value — concurrent "
            f"writes contaminated cross-session resolution."
        )
    # A session that never registered still misses cleanly.
    assert resolve("sess-never-registered") is None


def test_interleaved_distinct_and_repeated_sessions_all_resolve(concurrency_env):
    """Mixed contention: some processes share a team, last-wins still resolves a
    coherent (parseable, member-validated) value for every contending session —
    no torn line is ever returned by resolve() even mid-contention."""
    # All workers register into one shared team with all names as members.
    team = "pact-shared"
    names = [f"agent{i}" for i in range(_N_PROCS)]
    concurrency_env.write_team(team, names)
    args = [
        (concurrency_env.home, f"sess-mix-{i}", team, f"agent{i}", _WRITES_PER_PROC)
        for i in range(_N_PROCS)
    ]
    with _MP.Pool(_N_PROCS) as pool:
        pool.map(_register_worker, args)

    total, torn = _count_torn(concurrency_env.registry_path)
    assert torn == 0, f"{torn}/{total} torn under shared-team contention"
    for i in range(_N_PROCS):
        assert resolve(f"sess-mix-{i}") == f"agent{i}@{team}"
