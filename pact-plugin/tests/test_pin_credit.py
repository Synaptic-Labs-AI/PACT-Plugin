"""
Location: pact-plugin/tests/test_pin_credit.py

The pin-cap credit record, `shared.pin_credit`, through its four entry points
and the gate's own decision: a pin an agent removed is credited to that agent's
next allowed changes while nothing else changes the file, per agent, per base,
within the bounds and the expiry; every failure reads as no credit and writes
nothing. Each row runs in a tmp_path config root with the lead's session
context; `_decide` below is the gate's read, decide and record sequence.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from pathlib import Path

import pytest

from shared import pin_credit
from shared.claude_md_drift import _digest
from shared.claude_md_manager import MANAGED_END_MARKER, MANAGED_START_MARKER, MEMORY_END_MARKER, MEMORY_START_MARKER

SID = "credit-session"
LEAD = {"agent_type": "PACT:pact-orchestrator", "session_id": SID}
CODER = {"agent_type": "PACT:pact-backend-coder", "session_id": SID, "agent_id": "acoder-1"}
KEYLESS = {"agent_type": "PACT:pact-backend-coder", "session_id": SID}


def pin(i, body=None):
    return f"<!-- pinned: 2026-04-21 -->\n### Pin {i}\n{body or f'body {i}'}\n\n"


def doc(pins, working="## Working Memory\n\n### 2026-01-02\nentry\n"):
    return (f"{MANAGED_START_MARKER}\n# Project Memory\n\n{MEMORY_START_MARKER}\n## Retrieved Context\n\n"
            f"## Pinned Context\n\n{''.join(pins)}{working}{MEMORY_END_MARKER}\n{MANAGED_END_MARKER}\n")


P13 = [pin(i) for i in range(13)]
B13 = doc(P13)
NO_3 = doc(P13[:3] + P13[4:])                    # Pin 3 removed: 12 pins
MOVED = doc(P13[:3] + P13[4:] + [P13[3]])        # Pin 3 back, at the end: 13 pins
BIG = "x" * 1700


@pytest.fixture
def store(tmp_path, monkeypatch, pact_context):
    """The lead's session context in a tmp_path config root; the project base."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    project = tmp_path / "project"
    project.mkdir()
    pact_context(session_id=SID, project_dir=str(project))
    from shared.pact_context import get_session_dir

    session_dir = Path(get_session_dir())
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir, project


def _kind(frame):
    return "lead" if frame is LEAD else "member"


def _decide(frame, base, before, after):
    """The gate's sequence for a Write of `after` over `before`: read the
    credit, decide, and record after an ALLOW."""
    from pin_caps_gate import gate_decision

    h0 = _digest(before)
    credit = pin_credit.read(frame, _kind(frame), base, h0)
    detail: dict = {}
    decision = gate_decision(before, "Write", {"content": after}, credit=credit, detail=detail)
    if decision.verdict == "ALLOW":
        pin_credit.record(frame, _kind(frame), base, h0, detail, decision, credit)
    return decision


def _record_file(session_dir, key="lead"):
    return session_dir / "claude-md-pin-credit" / f"{key}.json"


def _states(session_dir, key="lead"):
    return json.loads(_record_file(session_dir, key).read_text(encoding="utf-8"))["states"]


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------

def test_no_record_reads_as_no_credit(store):
    _, base = store
    assert pin_credit.read(LEAD, "lead", base, _digest(B13)) == pin_credit.Credit.none()


def test_a_pin_moved_by_two_changes_is_allowed_and_the_credit_spent(store):
    session_dir, base = store
    assert _decide(LEAD, base, NO_3, MOVED).verdict == "DENY"  # control: no chain, no credit
    assert _decide(LEAD, base, B13, NO_3).verdict == "ALLOW"
    credit = pin_credit.read(LEAD, "lead", base, _digest(NO_3))
    assert credit.count == 1 and [p.heading for p in credit.removed] == ["### Pin 3"]
    assert _decide(LEAD, base, NO_3, MOVED).verdict == "ALLOW"
    state = _states(session_dir)[_digest(MOVED)]
    assert (state["credit"], state["credited"], state["removed"]) == (0, True, [])


def test_a_second_add_on_one_credit_is_refused(store):
    _, base = store
    _decide(LEAD, base, B13, NO_3)
    _decide(LEAD, base, NO_3, MOVED)
    decision = _decide(LEAD, base, MOVED, doc(P13[:3] + P13[4:] + [P13[3], pin(20)]))
    assert (decision.verdict, decision.cause) == ("DENY", "count")


def test_an_unrelated_allowed_change_carries_the_chain(store):
    _, base = store
    edited = doc(P13[:3] + P13[4:], working="## Working Memory\n\n### 2026-01-02\nentry edited\n")
    _decide(LEAD, base, B13, NO_3)
    assert _decide(LEAD, base, NO_3, edited).verdict == "ALLOW"
    assert pin_credit.read(LEAD, "lead", base, _digest(edited)).count == 1
    readded = doc(P13[:3] + P13[4:] + [P13[3]], working="## Working Memory\n\n### 2026-01-02\nentry edited\n")
    assert _decide(LEAD, base, edited, readded).verdict == "ALLOW"


def test_a_change_that_never_reached_the_file_keeps_the_credit(store):
    """An allowed change the tool then failed or the user declined leaves a
    state for a text the file never holds; the file's own state is unchanged."""
    _, base = store
    _decide(LEAD, base, B13, NO_3)
    _decide(LEAD, base, NO_3, doc(P13[:3] + P13[4:], working="## Working Memory\n\nother\n"))
    assert pin_credit.read(LEAD, "lead", base, _digest(NO_3)).count == 1
    assert _decide(LEAD, base, NO_3, MOVED).verdict == "ALLOW"


def test_an_outside_change_breaks_the_chain(store):
    _, base = store
    _decide(LEAD, base, B13, NO_3)
    outside = NO_3.replace("body 7", "body 7 edited in an editor")
    assert pin_credit.read(LEAD, "lead", base, _digest(outside)).count == 0
    readded = doc(P13[:3] + P13[4:] + [P13[3]]).replace("body 7", "body 7 edited in an editor")
    assert _decide(LEAD, base, outside, readded).verdict == "DENY"


def test_each_agent_keeps_its_own_chain(store):
    session_dir, base = store
    assert _decide(CODER, base, B13, NO_3).verdict == "ALLOW"
    assert _record_file(session_dir, "acoder-1").exists()
    assert pin_credit.read(LEAD, "lead", base, _digest(NO_3)).count == 0
    assert _decide(LEAD, base, NO_3, MOVED).verdict == "DENY"
    assert _decide(CODER, base, NO_3, MOVED).verdict == "ALLOW"


def test_a_teammate_with_no_key_gets_no_credit_and_writes_nothing(store):
    session_dir, base = store
    assert _decide(KEYLESS, base, B13, NO_3).verdict == "ALLOW"
    assert not (session_dir / "claude-md-pin-credit").exists()
    assert pin_credit.read(KEYLESS, "member", base, _digest(NO_3)) == pin_credit.Credit.none()


def test_a_record_for_another_base_gives_none(store, tmp_path):
    _, base = store
    _decide(LEAD, base, B13, NO_3)
    other = tmp_path / "other"
    other.mkdir()
    assert pin_credit.read(LEAD, "lead", other, _digest(NO_3)).count == 0


def test_two_spellings_of_one_base_share_the_record(store, tmp_path):
    _, base = store
    link = tmp_path / "link"
    link.symlink_to(base, target_is_directory=True)
    _decide(LEAD, link, B13, NO_3)
    assert pin_credit.read(LEAD, "lead", base, _digest(NO_3)).count == 1


def test_an_expired_state_gives_none(store, monkeypatch):
    session_dir, base = store
    _decide(LEAD, base, B13, NO_3)
    later = time.time() + 12 * 3600 + 1
    monkeypatch.setattr(pin_credit.time, "time", lambda: later)
    assert pin_credit.read(LEAD, "lead", base, _digest(NO_3)).count == 0
    assert pin_credit.state(session_dir, "lead", base, _digest(NO_3)) is None


def test_the_removed_snapshot_is_dropped_once_its_pin_is_back(store):
    session_dir, base = store
    _decide(LEAD, base, B13, NO_3)
    _decide(LEAD, base, NO_3, MOVED)
    assert _states(session_dir)[_digest(MOVED)]["removed"] == []


def test_an_oversize_pin_moved_by_two_changes_keeps_its_size(store):
    """Re-adding a removed oversize pin unchanged is allowed; a copy of it
    after the re-add is still refused on size."""
    _, base = store
    pins = [pin(i) for i in range(5)] + [pin(9, BIG)]
    before, removed = doc(pins), doc(pins[:5])
    readded = doc([pins[5]] + pins[:5])
    assert _decide(LEAD, base, before, removed).verdict == "ALLOW"
    assert _decide(LEAD, base, removed, readded).verdict == "ALLOW"
    copy = doc([pins[5]] + pins[:5] + [pin(9, BIG).replace("### Pin 9", "### Pin 10")])
    assert (_decide(LEAD, base, readded, copy).verdict, _decide(LEAD, base, readded, copy).cause) == ("DENY", "size")


# ---------------------------------------------------------------------------
# Landed states and exclusions
# ---------------------------------------------------------------------------

INDENTED = doc(P13[:12], working="## Working Memory\n").replace("body 4\n", "body 4\n  ### Sub heading\nsub body\n")
TWELVE = doc(P13[:12], working="## Working Memory\n")


def test_landed_is_set_only_by_confirm_on_the_matching_text(store):
    session_dir, base = store
    assert _decide(LEAD, base, TWELVE, INDENTED).verdict == "ALLOW"
    h1 = _digest(INDENTED)

    def landed():
        state = pin_credit.state(session_dir, "lead", base, h1)
        assert state is not None
        return state["landed"]

    assert landed() is False
    pin_credit.confirm(session_dir, "lead", base, _digest(TWELVE))
    assert landed() is False
    pin_credit.confirm(session_dir, "lead", base, h1)
    assert landed() is True


def test_exclusions_are_the_union_over_landed_states_only(store):
    session_dir, base = store
    _decide(LEAD, base, TWELVE, INDENTED)
    assert pin_credit.read(LEAD, "lead", base, _digest(INDENTED)).exclude.indented == frozenset()
    pin_credit.confirm(session_dir, "lead", base, _digest(INDENTED))
    other = TWELVE.replace("body 7\n", "body 7\n\t### Other heading\n")
    _decide(LEAD, base, TWELVE, other)  # allowed, never landed
    exclude = pin_credit.read(LEAD, "lead", base, _digest(TWELVE)).exclude
    assert exclude.indented == frozenset({"### Sub heading"}) and exclude.below == frozenset()


def test_the_agents_own_indented_heading_un_indented_at_twelve_is_refused(store):
    session_dir, base = store
    _decide(LEAD, base, TWELVE, INDENTED)
    pin_credit.confirm(session_dir, "lead", base, _digest(INDENTED))
    revealed = INDENTED.replace("  ### Sub heading\n", "### Sub heading\n")
    assert (_decide(LEAD, base, INDENTED, revealed).verdict, _decide(LEAD, base, INDENTED, revealed).cause) == (
        "DENY", "count")


def test_a_users_own_indented_heading_un_indented_at_twelve_is_allowed(store):
    _, base = store
    revealed = INDENTED.replace("  ### Sub heading\n", "### Sub heading\n")
    assert _decide(LEAD, base, INDENTED, revealed).verdict == "ALLOW"


# ---------------------------------------------------------------------------
# Bounds and no-ops
# ---------------------------------------------------------------------------

def test_a_change_that_carries_nothing_writes_no_record(store):
    session_dir, base = store
    assert _decide(LEAD, base, TWELVE, TWELVE.replace("body 2", "body two")).verdict == "ALLOW"
    assert not (session_dir / "claude-md-pin-credit").exists()


def test_at_most_thirty_two_states_are_kept_newest_first(store):
    session_dir, base = store
    _decide(LEAD, base, B13, NO_3)
    text = NO_3
    for n in range(33):
        nxt = text.replace("entry", f"entry {n}", 1) if n == 0 else text.replace(f"entry {n - 1}", f"entry {n}")
        assert _decide(LEAD, base, text, nxt).verdict == "ALLOW"
        text = nxt
    states = _states(session_dir)
    assert len(states) == 32 and _digest(text) in states and _digest(NO_3) not in states


def test_at_most_twenty_four_snapshots_are_kept(store):
    session_dir, base = store
    many = [pin(i) for i in range(30)]
    _decide(LEAD, base, doc(many), doc(many[:2]))
    assert len(_states(session_dir)[_digest(doc(many[:2]))]["removed"]) == 24


def test_at_most_sixty_four_added_keys_are_kept(store):
    session_dir, base = store
    after = TWELVE.replace("body 4\n", "body 4\n" + "".join(f"  ### Sub {k}\n" for k in range(65)))
    _decide(LEAD, base, TWELVE, after)
    assert len(_states(session_dir)[_digest(after)]["added_indented"]) == 64


# ---------------------------------------------------------------------------
# Failures read as no credit and write nothing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"version": 1, "base": 7, "states": []}'],
                         ids=["corrupt JSON", "a list", "wrong field types"])
def test_a_malformed_record_gives_none(store, content):
    session_dir, base = store
    _decide(LEAD, base, B13, NO_3)
    _record_file(session_dir).write_text(content, encoding="utf-8")
    assert pin_credit.read(LEAD, "lead", base, _digest(NO_3)) == pin_credit.Credit.none()
    assert _decide(LEAD, base, NO_3, MOVED).verdict == "DENY"


def test_a_record_over_one_megabyte_gives_none(store):
    session_dir, base = store
    _decide(LEAD, base, B13, NO_3)
    record = json.loads(_record_file(session_dir).read_text(encoding="utf-8"))
    record["padding"] = "x" * (2 * 1024 * 1024)
    _record_file(session_dir).write_text(json.dumps(record), encoding="utf-8")
    assert pin_credit.read(LEAD, "lead", base, _digest(NO_3)) == pin_credit.Credit.none()


def test_an_unreadable_record_gives_none(store):
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    session_dir, base = store
    _decide(LEAD, base, B13, NO_3)
    _record_file(session_dir).chmod(0o000)
    try:
        assert pin_credit.read(LEAD, "lead", base, _digest(NO_3)) == pin_credit.Credit.none()
        assert _decide(LEAD, base, NO_3, MOVED).verdict == "DENY"
    finally:
        _record_file(session_dir).chmod(0o600)


def test_no_session_dir_gives_none_and_writes_nothing(tmp_path, monkeypatch, pact_context):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    pact_context(session_id="", project_dir="")
    assert _decide(LEAD, tmp_path, B13, NO_3).verdict == "ALLOW"
    assert pin_credit.read(LEAD, "lead", tmp_path, _digest(NO_3)) == pin_credit.Credit.none()
    assert not (tmp_path / "cfg").exists()


def test_a_file_where_the_record_directory_belongs_writes_nothing_and_never_raises(store):
    session_dir, base = store
    (session_dir / "claude-md-pin-credit").write_text("not a directory", encoding="utf-8")
    assert _decide(LEAD, base, B13, NO_3).verdict == "ALLOW"
    assert pin_credit.read(LEAD, "lead", base, _digest(NO_3)) == pin_credit.Credit.none()


def test_a_held_record_lock_times_out_and_writes_nothing(store, monkeypatch):
    """Another writer holding the record's sidecar lock: the record waits its
    short bound, writes nothing and returns; the chain is as before."""
    session_dir, base = store
    _decide(LEAD, base, B13, NO_3)
    before = _record_file(session_dir).read_bytes()
    monkeypatch.setattr(pin_credit, "_LOCK_TIMEOUT_SECONDS", 0.1)
    fd = os.open(str(_record_file(session_dir)) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        worker = threading.Thread(target=_decide, args=(LEAD, base, NO_3, MOVED), daemon=True)
        worker.start()
        worker.join(timeout=60)
        assert not worker.is_alive(), "the record waited on the lock past its bound"
    finally:
        os.close(fd)
    assert _record_file(session_dir).read_bytes() == before


def test_a_change_too_large_to_compare_records_nothing(store):
    """Pins before and after past the comparison bound: the change records
    nothing, so the chain breaks rather than the gate spending unbounded work."""
    session_dir, base = store
    many = [pin(i) for i in range(60)]
    assert _decide(LEAD, base, doc(many), doc(many[:59])).verdict == "ALLOW"
    assert not (session_dir / "claude-md-pin-credit").exists()


# ---------------------------------------------------------------------------
# The credit moves by the growth rule's count in every case
# ---------------------------------------------------------------------------

B12 = doc(P13[:12]).replace("body 4\n", "body 4\n  ### Sub heading\n")


def test_remove_reveal_re_add_under_the_cap_is_allowed(store):
    """A reveal is growth 0, so at and under the cap it spends nothing the
    re-add needs; one Edit doing all three is allowed too."""
    _, base = store
    removed = B12.replace(P13[2], "")
    revealed = removed.replace("  ### Sub heading\n", "### Sub heading\n")
    readded = revealed.replace(P13[11], P13[11] + P13[2])
    assert _decide(LEAD, base, B12, readded).verdict == "ALLOW"  # one Edit doing all three
    assert _decide(LEAD, base, B12, removed).verdict == "ALLOW"
    assert _decide(LEAD, base, removed, revealed).verdict == "ALLOW"
    assert pin_credit.read(LEAD, "lead", base, _digest(revealed)).count == 1
    assert _decide(LEAD, base, revealed, readded).verdict == "ALLOW"


@pytest.mark.parametrize("stop", ["step budget", "timer"])
def test_a_stop_in_the_detail_only_growth_changes_no_decision_field(monkeypatch, stop):
    """At the cap the decision does not run the rule, so `detail` alone runs
    it. A stop there, from the step budget or a timer, leaves the decision
    field for field as it is without `detail`, and the growth unknown."""
    import signal

    from shared import pin_growth

    before, after = B12, B12.replace("body 7\n", "body seven\n")
    plain = pin_growth.pin_cap_decision(before, after)
    assert plain.verdict == "ALLOW"
    if stop == "step budget":
        monkeypatch.setattr(pin_growth, "STEP_BUDGET", 1)
    else:
        if not hasattr(signal, "setitimer"):
            pytest.skip("no interval timer on this platform")
        real = pin_growth.pin_growth

        def timed_out(*args, **kwargs):
            signal.raise_signal(signal.SIGPROF)
            return real(*args, **kwargs)

        monkeypatch.setattr(pin_growth, "pin_growth", timed_out)
    detail: dict = {}
    assert pin_growth.pin_cap_decision(before, after, detail=detail) == plain
    assert detail["growth"] is None
    if hasattr(signal, "getitimer"):
        assert signal.getitimer(signal.ITIMER_PROF) == (0.0, 0.0)
