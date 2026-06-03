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


def _non_atomic_worker(args):
    """CONTROL worker: deliberately NON-atomic — split each logical line into TWO
    os.write calls. Under contention the halves interleave -> torn lines. This is
    the negative leg that proves the detector is real (it is NOT register())."""
    home, session_id, n = args
    _prepare_child(home)
    reg_path = Path(home) / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps({"session_id": session_id, "value": "y" * 40}) + "\n"
    ).encode("utf-8")
    half = len(payload) // 2
    first, second = payload[:half], payload[half:]
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | nofollow
    for _ in range(n):
        fd = os.open(str(reg_path), flags, 0o600)
        try:
            os.write(fd, first)   # two syscalls per line -> interleave window
            os.write(fd, second)
        finally:
            os.close(fd)


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

def test_non_atomic_writer_tears_and_detector_catches_it(concurrency_env):
    """NON-VACUITY CONTROL: an intentionally non-atomic appender (two os.write
    calls per line) racing N processes produces torn lines, and the JSON-parse
    detector catches them (torn > 0). Without this leg, a zero-tear positive
    result could be green-because-nothing-was-tested. The two halves interleaving
    across processes is exactly what a non-atomic (or kernel-split-beyond-the-
    atomicity-bound) write would do."""
    args = [
        (concurrency_env.home, f"sess-control-{i}", _WRITES_PER_PROC)
        for i in range(_N_PROCS)
    ]
    with _MP.Pool(_N_PROCS) as pool:
        pool.map(_non_atomic_worker, args)

    total, torn = _count_torn(concurrency_env.registry_path)
    assert total > 0, "the control wrote nothing — the harness itself is broken"
    assert torn > 0, (
        f"the non-atomic control produced {torn} torn lines out of {total}. "
        f"It MUST tear (two-syscall interleave under contention) — if it does "
        f"not, the tear detector is vacuous and the zero-tear positive result "
        f"below proves nothing."
    )


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
