"""
Teeth for POLL_INTERVAL_SECONDS (hooks/pact_wait.py) — the live poll
cadence is a real-behavior constant no other test arm reads: a reviewer
teeth pass (2026-11-21, mutation 5.0 -> 500.0) reddened nothing, so the
production interval could silently change by 100x. These arms observe the
interval's BEHAVIORAL EFFECT at the sleep seam: a recorder stands in for
time.sleep and captures the duration the poll loop REQUESTS, so both the
magnitude and the deadline truncation mutate visibly without wall-clock
cost.

The magnitude arm pins the DOCUMENTED 5.0 literally, not via the live
constant: an assertion computed from the pinned constant is self-confirming
(a shrunken constant would flow through min() and equal itself). A second
assertion binds behavior to the declared constant, so the module cannot
drift from its own headline value.

Robustness: no wall-clock upper bound is asserted anywhere; the delayed
delivery (threading.Timer at 1.0s) only has to land inside a 12s deadline,
so CI load cannot flip these arms except by stalling a single test past
12 seconds.
"""
import json
import threading
import time
from pathlib import Path

import pytest

import pact_wait


def _write_task(tasks_base: Path, team: str, task_id: str, payload: dict) -> Path:
    team_dir = tasks_base / team
    team_dir.mkdir(parents=True, exist_ok=True)
    task_file = team_dir / f"{task_id}.json"
    task_file.write_text(json.dumps(payload), encoding="utf-8")
    return task_file


def _handoff_task(produced) -> dict:
    return {"metadata": {"handoff": {"produced": produced, "decisions": "d"}}}


_REAL_SLEEP = time.sleep  # captured BEFORE any patching: the recorder
# replaces time.sleep on the shared module, so it must not call it.


class _SleepRecorder:
    """Stands in for time.sleep: records each requested duration, then
    sleeps a token 1ms for real so the poll loop does not spin hot."""

    def __init__(self):
        self.requested = []

    def __call__(self, seconds: float) -> None:
        self.requested.append(seconds)
        _REAL_SLEEP(0.001)


def test_poll_interval_is_the_documented_five_seconds(tmp_path, monkeypatch):
    """The poll loop's real cadence is the documented 5.0s: the first sleep
    request of a default-interval watch (deadline 12 > interval, delivery
    at 1.0s) must be exactly 5.0 — hardcoded, because min(interval,
    remaining) equals whatever the constant is and would self-confirm —
    AND equal to the module's declared constant, so behavior and headline
    cannot drift apart."""
    assert pact_wait.POLL_INTERVAL_SECONDS == 5.0
    task_file = _write_task(tmp_path, "team-a", "7", {"metadata": {}})
    recorder = _SleepRecorder()
    monkeypatch.setattr(pact_wait.time, "sleep", recorder)
    timer = threading.Timer(
        1.0,
        lambda: task_file.write_text(
            json.dumps(_handoff_task("delivered late")), encoding="utf-8"
        ),
    )
    timer.start()
    try:
        rc = pact_wait.wait_for_deliverable(
            "team-a", "7", "handoff", deadline=12,
            tasks_base_dir=str(tmp_path),
        )
    finally:
        timer.join()
    assert rc == pact_wait.EXIT_FIRED
    assert recorder.requested, "the watch never slept — poll loop broken"
    assert recorder.requested[0] == 5.0
    assert recorder.requested[0] == pact_wait.POLL_INTERVAL_SECONDS


def test_final_sleep_is_truncated_to_the_remaining_deadline(tmp_path, monkeypatch):
    """The exit contract bounds total watch time at the deadline: every
    sleep request must be min(poll_interval, remaining), so with a 0.3s
    deadline and a 5.0s interval no request exceeds the deadline and the
    first one actually advances toward it. Removes the min() and the 5.0
    request reds the bound; shrinks the interval to 0.1 and the 0.25 floor
    reds; zeroes the sleep and the floor catches that too."""
    _write_task(tmp_path, "team-a", "7", {"metadata": {}})
    recorder = _SleepRecorder()
    monkeypatch.setattr(pact_wait.time, "sleep", recorder)
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=0.3,
        tasks_base_dir=str(tmp_path),
    )
    assert rc == pact_wait.EXIT_EXPIRED
    assert recorder.requested, "the watch never slept — poll loop broken"
    assert all(d <= 0.3 for d in recorder.requested)
    assert recorder.requested[0] > 0.25


# --- S1-test-side (review cycle 1): the live-probed immortal-watcher class --
#
# A non-finite --deadline armed a watcher that can never emit EXPIRED (nan
# fails every remaining <= 0 comparison; inf never runs out) — and a
# watcher is carve-out-exempt, so the lead idled with a scheduled wake that
# never fires. Measured live on the real CLI pre-fix (2026-11-21): nan and
# inf both hung until externally killed while a --deadline 1 control
# exited 3. f30c7d95 rejects non-finite as a usage error; these arms pin
# that END-TO-END through the real main() entrypoint — the invocation
# shape the probe hung on. The _positive_float unit arms live in
# test_pact_wait.py (fixer lane); the literal-spelling set there does not
# include the overflow-parse route below.

@pytest.mark.parametrize("text", ["nan", "inf", "1e999"])
def test_non_finite_deadline_exits_2_before_any_watching(text):
    """Through the real main(), a non-finite deadline is usage error exit 2
    raised by the argument parser BEFORE the poll loop starts. `1e999` is
    the typo route: it parses to inf without spelling anything non-finite,
    so a rejection keyed on literal spellings instead of isfinite would
    re-open the immortal watcher through exactly this input. Regression
    direction: with the rejection removed the arm reds, never hangs — the
    target below is a team dir that does not exist, so even an unfixed
    parser returns TERMINAL (4) immediately and the SystemExit assertion
    fails DID-NOT-RAISE."""
    with pytest.raises(SystemExit) as excinfo:
        pact_wait.main([
            "--team", "no-such-pollteeth-team", "--task", "7",
            "--predicate", "handoff",
            "--deadline", text, "--tasks-base-dir", "/tmp",
        ])
    assert excinfo.value.code == 2


def test_finite_deadline_control_still_runs_the_watch_contract(tmp_path):
    """Finite control for the S1 rejection: a sane deadline parses, the
    watch RUNS, and the exit comes through the watch contract (TERMINAL on
    the missing target) — the rejection must not over-block finite
    values."""
    rc = pact_wait.main([
        "--team", "team-a", "--task", "7", "--predicate", "handoff",
        "--deadline", "1", "--tasks-base-dir", str(tmp_path),
    ])
    assert rc == pact_wait.EXIT_TERMINAL
