"""Tests for hooks/shared/compaction_owner.py.

An in-process teammate's compaction frames are lead-shaped, and in every capture
the transcript records that tell the two apart landed after the compaction hooks
had returned. So
PostCompact stages the summary and settle() decides later. Every arm builds a
temporary projects/ tree under the config root the autouse fixture redirects
to, stages through stage_summary, and drives settle() with a fake clock: `now`
is fixed, `monotonic` advances only when `sleep` is called, and a callback can
append records on the Nth sleep.
"""

import ast
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fixtures.role_frames import (
    captured_compaction_lead_postcompact,
    captured_compaction_lead_precompact,
    captured_compaction_lead_sessionstart,
    captured_compaction_teammate_postcompact,
    captured_compaction_teammate_precompact,
    captured_compaction_teammate_sessionstart,
)
from shared import compaction_owner as co

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
STAGED = datetime(2026, 9, 14, 1, 24, 47, tzinfo=timezone.utc)
SID = "4ec31948-bbe5-4ef4-841c-631d1ef31e61"
BODY = (
    "1. Primary Request and Intent: read three short text files in a scratch project "
    "and report their line counts to the team-lead.\n2. Key Technical Concepts: reading "
    "files and counting lines.\n3. Current Work: the counts were gathered and sent.\n"
    "4. Pending Tasks: none."
)
SUMMARY = f"<analysis>\nWorking notes that are not kept.\n</analysis>\n\n<summary>\n{BODY}\n</summary>"
BODY_B = BODY.replace("three short text files", "two long text files")
SUMMARY_B = f"<summary>\n{BODY_B}\n</summary>"
LEAD_OWN = "THE LEAD'S OWN SUMMARY"


class Runaway(BaseException):
    """Raised past settle()'s own except when a poll does not stop."""


class Clock:
    """now() is fixed; monotonic() advances only through sleep()."""

    def __init__(self, at=STAGED, on_sleep=None, limit=200):
        self.at = at
        self.t = 1000.0
        self.sleeps = 0
        self.slept = 0.0
        self.on_sleep = on_sleep or {}
        self.limit = limit

    def now(self):
        return self.at

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps += 1
        if self.sleeps > self.limit:
            raise Runaway("settle did not stop polling")
        self.slept += seconds
        self.t += seconds
        action = self.on_sleep.get(self.sleeps)
        if action:
            action()


def later(seconds, **kwargs):
    return Clock(at=STAGED + timedelta(seconds=seconds), **kwargs)


def staged_ns(pending):
    return pending.name[len("compact-summary.pending-"):-len(".json")]


class Tree:
    """projects/<slug>/<sid>.jsonl, <sid>/subagents/agent-*.jsonl and a session dir."""

    def __init__(self, root):
        self.project = root / "projects" / "-scratch-cmp-lead"
        self.subagents = self.project / SID / "subagents"
        self.subagents.mkdir(parents=True)
        self.lead = self.project / f"{SID}.jsonl"
        self.teammate = self.subagents / "agent-acmp-probe-0123456789abcdef.jsonl"
        for path in (self.lead, self.teammate):
            self.append(path, {"type": "user", "message": {"role": "user", "content": "earlier turn"}})
        self.session = root / "pact-sessions" / "scratch-cmp-lead" / SID
        self.session.mkdir(parents=True)
        self.canonical = self.session / "compact-summary.txt"

    @staticmethod
    def append(path, record):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def summary(self, path, body=BODY):
        self.append(path, {"type": "user", "isCompactSummary": True,
                           "message": {"role": "user",
                                       "content": "This session is being continued from a previous conversation.\n\nSummary:\n" + body}})

    def frame(self, summary=SUMMARY):
        return {"hook_event_name": "PostCompact", "agent_type": "PACT:pact-orchestrator", "session_id": SID,
                "transcript_path": str(self.lead), "compact_summary": summary, "trigger": "auto"}

    def stage(self, summary=SUMMARY, at=STAGED, frame=None):
        assert co.stage_summary(frame or self.frame(summary), str(self.session), now=lambda: at)
        return sorted(self.session.glob("compact-summary.pending-*.json"), key=lambda p: int(staged_ns(p)))[-1]

    def settle(self, clock, wait_s=0.0):
        return co.settle(str(self.session), wait_s=wait_s, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)

    def names(self):
        return sorted(path.name for path in self.session.iterdir())

    def kept(self, kind):
        return sorted(int(path.name.rsplit("-", 1)[1][:-len(".txt")])
                      for path in self.session.glob(f"compact-summary.{kind}-*.txt"))

    def attributions(self):
        journal = self.session / "session-journal.jsonl"
        if not journal.exists():
            return []
        events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
        return [event for event in events if event.get("type") == "compaction_attributed"]

    def verdicts(self):
        return [(event["verdict"], event["basis"]) for event in self.attributions()]


@pytest.fixture
def tree(tmp_path):
    return Tree(tmp_path / ".claude")


def _fill(path, mebibytes):
    line = json.dumps({"type": "assistant", "message": {"content": "x" * 1000}}) + "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line * (mebibytes * 1024 * 1024 // len(line) + 1))


# --------------------------------------------------------------------------
# Stage
# --------------------------------------------------------------------------


def test_stage_writes_one_private_pending_file_and_leaves_the_summary_file_alone(tree):
    tree.canonical.write_text(LEAD_OWN, encoding="utf-8")
    pending = tree.stage()
    assert tree.canonical.read_text(encoding="utf-8") == LEAD_OWN
    assert tree.names() == [pending.name, "compact-summary.txt"]
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
    record = json.loads(pending.read_text(encoding="utf-8"))
    assert set(record) == {"summary", "transcript_path", "session_id", "staged_at", "offsets"}
    assert (record["summary"], record["session_id"], record["transcript_path"]) == (SUMMARY, SID, str(tree.lead))
    assert datetime.fromisoformat(record["staged_at"]) == STAGED
    assert record["offsets"] == {
        os.path.realpath(tree.lead): tree.lead.stat().st_size,
        os.path.realpath(tree.teammate): tree.teammate.stat().st_size,
    }


@pytest.mark.parametrize("summary", ["", None, 42])
def test_stage_without_a_summary_writes_nothing(tree, summary):
    frame = {**tree.frame(), "compact_summary": summary}
    assert co.stage_summary(frame, str(tree.session)) is False
    assert tree.names() == []


def test_stage_records_no_offsets_when_the_transcripts_cannot_be_located(tree):
    pending = tree.stage(frame={**tree.frame(), "session_id": "../escape"})
    assert json.loads(pending.read_text(encoding="utf-8"))["offsets"] == {}


def test_an_offset_failure_stages_the_summary_without_offsets(tree, monkeypatch):
    """A transcript that vanishes between locating it and reading its size must
    not cost the summary: it is staged without offsets and read from the start."""
    real_transcripts = co._transcripts

    def vanished(*args):
        raise FileNotFoundError("the transcript went away before its size was read")

    monkeypatch.setattr(co, "_transcripts", vanished)
    pending = tree.stage()
    record = json.loads(pending.read_text(encoding="utf-8"))
    assert (record["summary"], record["offsets"]) == (SUMMARY, {})
    monkeypatch.setattr(co, "_transcripts", real_transcripts)
    tree.summary(tree.lead)
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]


# --------------------------------------------------------------------------
# Settle: the verdicts
# --------------------------------------------------------------------------


def test_a_record_in_the_lead_transcript_promotes_the_summary(tree):
    tree.canonical.write_text("AN OLDER LEAD SUMMARY", encoding="utf-8")
    tree.stage()
    tree.summary(tree.lead)
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]
    assert tree.canonical.read_text(encoding="utf-8") == SUMMARY
    assert stat.S_IMODE(tree.canonical.stat().st_mode) == 0o600
    assert tree.names() == ["compact-summary.txt", "session-journal.jsonl"]
    [event] = tree.attributions()
    assert (event["verdict"], event["basis"], event["latency_s"]) == ("lead", "content", 9.0)


def test_a_record_in_a_teammate_transcript_keeps_the_lead_summary(tree):
    tree.canonical.write_text(LEAD_OWN, encoding="utf-8")
    ns = staged_ns(tree.stage())
    tree.summary(tree.teammate)
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]
    assert tree.canonical.read_text(encoding="utf-8") == LEAD_OWN
    assert (tree.session / f"compact-summary.teammate-{ns}.txt").read_text(encoding="utf-8") == SUMMARY
    assert tree.names() == ["compact-summary.teammate-" + ns + ".txt", "compact-summary.txt", "session-journal.jsonl"]
    assert tree.verdicts() == [("teammate", "content")]


@pytest.mark.parametrize("seconds", [0, co.EXPIRE_S - 1])
def test_an_unmatched_summary_younger_than_expiry_stays_staged(tree, seconds):
    tree.canonical.write_text(LEAD_OWN, encoding="utf-8")
    pending = tree.stage()
    assert tree.settle(later(seconds)) == []
    assert tree.names() == [pending.name, "compact-summary.txt"]
    assert tree.canonical.read_text(encoding="utf-8") == LEAD_OWN
    assert tree.attributions() == []


def test_an_expired_summary_is_parked_even_when_there_is_no_summary_file(tree):
    """A summary no transcript records may be a teammate's, so it never becomes
    compact-summary.txt, even when that file does not exist."""
    ns = staged_ns(tree.stage())
    assert tree.settle(later(co.EXPIRE_S + 1)) == [(co.UNKNOWN, co.EXPIRED)]
    assert not tree.canonical.exists()
    assert (tree.session / f"compact-summary.unattributed-{ns}.txt").read_text(encoding="utf-8") == SUMMARY


def test_an_expired_summary_never_replaces_an_existing_summary_file(tree):
    tree.canonical.write_text(LEAD_OWN, encoding="utf-8")
    ns = staged_ns(tree.stage())
    assert tree.settle(later(co.EXPIRE_S + 1)) == [(co.UNKNOWN, co.EXPIRED)]
    assert tree.canonical.read_text(encoding="utf-8") == LEAD_OWN
    assert (tree.session / f"compact-summary.unattributed-{ns}.txt").read_text(encoding="utf-8") == SUMMARY


@pytest.mark.parametrize("canonical", [None, LEAD_OWN])
@pytest.mark.parametrize("content", ["{not json", json.dumps({"summary": 42}), json.dumps(["a", "list"])])
def test_an_unreadable_staged_file_is_parked_and_never_becomes_the_summary_file(tree, canonical, content):
    if canonical:
        tree.canonical.write_text(canonical, encoding="utf-8")
    (tree.session / "compact-summary.pending-1000.json").write_text(content, encoding="utf-8")
    assert tree.settle(Clock()) == [(co.UNKNOWN, co.EXPIRED)]
    assert (tree.session / "compact-summary.unattributed-1000.txt").read_text(encoding="utf-8") == content
    assert tree.canonical.exists() is bool(canonical)
    if canonical:
        assert tree.canonical.read_text(encoding="utf-8") == canonical
    [event] = tree.attributions()
    assert "latency_s" not in event


_PARK_CHILD = """
import json, sys
from shared import compaction_owner as co
print(json.dumps(co.settle(sys.argv[1])))
"""


def test_a_fifo_pending_is_parked_unread_without_stalling(tree):
    """Opening a FIFO blocks until a writer appears, so settle runs in a child
    process that a timeout can end."""
    os.mkfifo(tree.session / "compact-summary.pending-1000.json")
    root = tree.project.parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(PYTHONPATH=str(HOOKS), CLAUDE_CONFIG_DIR=str(root), HOME=str(root.parent))
    try:
        proc = subprocess.run([sys.executable, "-c", _PARK_CHILD, str(tree.session)],
                              capture_output=True, text=True, timeout=15, env=env)
    except subprocess.TimeoutExpired:
        pytest.fail("settle blocked opening a FIFO pending")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [[co.UNKNOWN, co.EXPIRED]]
    assert stat.S_ISFIFO(os.lstat(tree.session / "compact-summary.unattributed-1000.txt").st_mode)
    [event] = tree.attributions()
    assert (event["verdict"], event["basis"]) == ("unknown", "expired") and "latency_s" not in event


def test_a_linked_pending_is_parked_without_reading_through_the_link(tree, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"summary": "TEXT FROM OUTSIDE THE SESSION", "transcript_path": str(tree.lead),
                                   "session_id": SID, "staged_at": STAGED.isoformat(), "offsets": {}}), encoding="utf-8")
    before = outside.read_bytes()
    (tree.session / "compact-summary.pending-1000.json").symlink_to(outside)
    assert tree.settle(later(co.EXPIRE_S + 1)) == [(co.UNKNOWN, co.EXPIRED)]
    parked = tree.session / "compact-summary.unattributed-1000.txt"
    assert parked.is_symlink() and os.readlink(parked) == str(outside)
    assert outside.read_bytes() == before
    assert not tree.canonical.exists()
    [event] = tree.attributions()
    assert "latency_s" not in event


def test_a_stamp_far_in_the_future_cannot_hold_the_queue(tree):
    tree.stage(at=STAGED + timedelta(days=1))
    assert tree.settle(Clock()) == [(co.UNKNOWN, co.EXPIRED)]


def test_a_summary_whose_transcript_cannot_be_located_never_blocks_a_later_one(tree):
    """A lookup that raises must make the summary unattributable, not restart the
    pass. Restored and re-raised, one bad record holds the queue for good: the
    raise comes before the age check, so it never expires either."""
    poison = tree.stage(frame={**tree.frame(), "transcript_path": f"{tree.lead}\x00x"})
    tree.stage(SUMMARY_B)
    tree.summary(tree.lead, body=BODY_B)

    assert tree.settle(later(9)) == []
    assert not tree.canonical.exists(), "the poisoned summary must hold its place until it expires"

    assert tree.settle(later(co.EXPIRE_S + 1)) == [(co.UNKNOWN, co.EXPIRED), (co.LEAD, co.CONTENT)]
    assert tree.canonical.read_text(encoding="utf-8") == SUMMARY_B
    parked = tree.session / f"compact-summary.unattributed-{staged_ns(poison)}.txt"
    assert parked.read_text(encoding="utf-8") == SUMMARY
    assert tree.verdicts() == [("unknown", "expired"), ("lead", "content")]


def test_any_raising_transcript_lookup_parks_the_summary(tree, monkeypatch):
    """The guard is not narrowed to the one error a NUL path happens to raise."""
    def unreachable(*args):
        raise OSError("the transcripts directory is unreadable")

    monkeypatch.setattr(co, "_locate", unreachable)
    tree.stage()
    assert tree.settle(later(co.EXPIRE_S + 1)) == [(co.UNKNOWN, co.EXPIRED)]
    assert not tree.canonical.exists()


# --------------------------------------------------------------------------
# Settle: offsets, order, claims and the poll
# --------------------------------------------------------------------------


def test_a_record_followed_by_nine_mebibytes_is_still_found_from_the_offset(tree):
    tree.stage()
    tree.summary(tree.lead)
    _fill(tree.lead, 9)
    assert tree.lead.stat().st_size > 9 * 1024 * 1024
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]


def test_an_identical_body_recorded_before_the_offset_does_not_match(tree):
    tree.summary(tree.lead)
    tree.summary(tree.teammate)
    tree.stage()
    assert tree.settle(later(9)) == []


def test_a_transcript_now_shorter_than_its_offset_is_read_from_the_start(tree):
    _fill(tree.teammate, 1)
    tree.stage()
    tree.teammate.write_text("", encoding="utf-8")
    tree.summary(tree.teammate)
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]


def test_a_teammate_transcript_created_after_staging_is_read_from_the_start(tree):
    tree.stage()
    tree.summary(tree.subagents / "agent-anew-0123456789abcdef.jsonl")
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]


def test_a_lead_record_matches_when_the_session_has_no_teammate_transcripts(tree):
    shutil.rmtree(tree.subagents.parent)
    tree.stage()
    tree.summary(tree.lead)
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]


def test_an_unresolved_older_summary_holds_back_a_newer_one(tree):
    older = tree.stage()
    newer = tree.stage(SUMMARY_B)
    assert int(staged_ns(older)) < int(staged_ns(newer))
    tree.summary(tree.lead, body=BODY_B)
    assert tree.settle(later(9)) == []
    assert newer.exists() and not tree.canonical.exists()
    tree.summary(tree.lead)
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT), (co.LEAD, co.CONTENT)]
    assert tree.canonical.read_text(encoding="utf-8") == SUMMARY_B


def test_a_summary_another_settler_claimed_first_is_skipped(tree, monkeypatch):
    tree.stage()
    tree.summary(tree.lead)
    real_replace = os.replace
    raced = []

    def replace(src, dst):
        if not raced and ".claimed-" in str(dst):
            raced.append(dst)
            assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]
        return real_replace(src, dst)

    monkeypatch.setattr(co.os, "replace", replace)
    assert tree.settle(later(9)) == []
    assert raced
    assert tree.verdicts() == [("lead", "content")]
    assert tree.names() == ["compact-summary.txt", "session-journal.jsonl"]


@pytest.mark.parametrize("error", [OSError("no space left on device"), KeyboardInterrupt()],
                         ids=["os-error", "keyboard-interrupt"])
def test_anything_raised_after_the_claim_returns_it_to_pending(tree, monkeypatch, error):
    pending = tree.stage()
    tree.summary(tree.lead)
    real_write = co._write_atomic

    def fail(path, text):
        raise error

    monkeypatch.setattr(co, "_write_atomic", fail)
    if isinstance(error, Exception):
        assert tree.settle(later(9)) == []
    else:
        with pytest.raises(KeyboardInterrupt):
            tree.settle(later(9))
    assert tree.names() == [pending.name]
    monkeypatch.setattr(co, "_write_atomic", real_write)
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]
    assert tree.canonical.read_text(encoding="utf-8") == SUMMARY


def _claim(pending, claimed_at, pid=4242):
    """Rename a pending the way a settler claims it, stamped at claimed_at."""
    claim_ns = int(claimed_at.timestamp()) * 1_000_000_000 + claimed_at.microsecond * 1_000
    claim = pending.with_name(f"{pending.name}.claimed-{pid}-{claim_ns}")
    os.replace(pending, claim)
    return claim


def test_a_claim_older_than_fresh_is_returned_and_settles_on_the_next_pass(tree):
    """A settler killed while it held a claim leaves the claim behind."""
    pending = tree.stage()
    tree.summary(tree.lead)
    _claim(pending, STAGED + timedelta(seconds=9))
    assert tree.settle(later(9 + co.FRESH_S + 1)) == [(co.LEAD, co.CONTENT)]
    assert tree.canonical.read_text(encoding="utf-8") == SUMMARY
    assert not list(tree.session.glob("*.claimed-*"))


@pytest.mark.parametrize("stamp", ["4242", "4242-", "4242-soon", "pid-123"],
                         ids=["no-claim-time", "empty-claim-time", "word-claim-time", "word-pid"])
def test_a_claim_whose_name_does_not_parse_counts_as_stale(tree, stamp):
    pending = tree.stage()
    tree.summary(tree.lead)
    os.replace(pending, pending.with_name(f"{pending.name}.claimed-{stamp}"))
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]
    assert not list(tree.session.glob("*.claimed-*"))


def test_a_claim_younger_than_fresh_is_left_to_its_settler(tree):
    pending = tree.stage()
    tree.summary(tree.lead)
    claim = _claim(pending, STAGED + timedelta(seconds=9))
    assert tree.settle(later(9 + co.FRESH_S - 1)) == []
    assert tree.names() == [claim.name]


def test_a_record_that_lands_during_the_poll_is_found(tree):
    tree.stage()
    clock = later(2, on_sleep={2: lambda: tree.summary(tree.lead)})
    assert tree.settle(clock, wait_s=co.READ_WAIT_S) == [(co.LEAD, co.CONTENT)]
    assert clock.sleeps == 2


def test_a_record_that_never_lands_ends_the_poll_at_its_bound(tree):
    pending = tree.stage()
    clock = later(2)
    assert tree.settle(clock, wait_s=co.READ_WAIT_S) == []
    assert co.READ_WAIT_S <= clock.slept <= co.READ_WAIT_S + co.POLL_S
    assert pending.exists()


@pytest.mark.parametrize("wait_s, seconds", [(0.0, 2), (co.READ_WAIT_S, co.FRESH_S + 1)])
def test_no_poll_without_a_wait_or_past_freshness(tree, wait_s, seconds):
    tree.stage()
    clock = later(seconds)
    assert tree.settle(clock, wait_s=wait_s) == []
    assert clock.sleeps == 0


@pytest.mark.parametrize("kind, other", [("teammate", "unattributed"), ("unattributed", "teammate")])
def test_only_the_newest_settled_summaries_of_each_kind_are_kept(tree, kind, other):
    for ns in range(1, co.KEEP_SETTLED + 3):
        (tree.session / f"compact-summary.{kind}-{ns}.txt").write_text(str(ns), encoding="utf-8")
    (tree.session / f"compact-summary.{other}-1.txt").write_text("the other kind", encoding="utf-8")
    tree.canonical.write_text(LEAD_OWN, encoding="utf-8")
    archive = tree.session / "compact-summary-2026-09-14T01-24-47.txt"
    archive.write_text("archived", encoding="utf-8")
    assert tree.settle(Clock()) == []
    assert tree.kept(kind) == list(range(3, co.KEEP_SETTLED + 3))
    assert tree.kept(other) == [1]
    assert tree.canonical.exists() and archive.exists()


def test_settle_on_an_empty_or_missing_folder_is_a_no_op(tree):
    assert co.settle("") == []
    assert co.settle(str(tree.session / "absent")) == []


# --------------------------------------------------------------------------
# The content match
# --------------------------------------------------------------------------


def _analysed(analysis, body=BODY):
    return f"<analysis>\n{analysis}\n</analysis>\n\n<summary>\n{body}\n</summary>"


def test_a_raw_body_with_a_newline_run_matches_its_rendered_record(tree):
    """The transcript renders a summary as "Summary:" plus the body stripped, with
    runs of newlines collapsed. A raw body with a three-newline run and edge
    whitespace inside its tags still matches that record."""
    raw_body = "\n\n  " + BODY.replace("\n2.", "\n\n\n2.") + "\n\n\n"
    summary = f"<analysis>\nnotes\n</analysis>\n\n<summary>{raw_body}</summary>"
    rendered = "Summary:\n" + re.sub(r"\n\n+", "\n\n", raw_body.strip())
    assert raw_body.strip() not in rendered, "the raw body must differ from its rendered form"
    tree.stage(summary)
    tree.append(tree.teammate, {"type": "user", "isCompactSummary": True,
                                "message": {"role": "user", "content": "This session is being continued.\n\n" + rendered}})
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]


_LONG_MENTION = "an earlier plan that was replaced before any work began. " * 5


@pytest.mark.parametrize(
    "summary",
    [
        _analysed("The reply goes in a <summary> block after these notes."),
        _analysed(f"An earlier draft read <summary>{_LONG_MENTION}</summary> and was dropped."),
        _analysed("An earlier draft read <summary>too short to match</summary> and was dropped."),
        f"<summary>\n{BODY}\n</summary>",
        _analysed("The notes quote the literal </analysis> tag, then say the reply goes in a <summary> block."),
    ],
    ids=["unclosed-mention", "closed-long-mention", "closed-short-mention", "no-analysis", "analysis-quotes-its-tag"],
)
def test_the_analysis_block_never_moves_the_match(tree, summary):
    """The transcript drops the analysis block before rendering the summary, and
    an analysis that quotes its own closing tag is also read at its longest."""
    assert len(_LONG_MENTION.strip()) >= co.MIN_BODY_CHARS
    tree.stage(summary)
    tree.summary(tree.teammate)
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]


def test_a_summary_that_mentions_the_analysis_closing_tag_still_matches(tree):
    """The longest reading of the analysis block would swallow this summary, so the
    shortest reading must still be tried."""
    body = BODY + "\n5. Notes: the analysis block ends at a </analysis> tag."
    tree.stage(_analysed("Working notes that are not kept.", body=body))
    tree.summary(tree.teammate, body=body)
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]


def test_a_body_quoting_the_summary_closing_tag_early_still_matches(tree):
    """A body that quotes </summary> near its start leaves a shortest reading too
    short to match, so the longest reading of the summary block is tried too."""
    body = "1. Primary Request: say why a reply quoting the literal </summary> tag early still parses. " + BODY
    assert len(body.split("</summary>")[0].strip()) < co.MIN_BODY_CHARS
    tree.stage(_analysed("Working notes that are not kept.", body=body))
    tree.summary(tree.lead, body=body)
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]


_EXCERPT = ("An earlier compaction listed the files read, the counts sent to the team-lead "
            "and the tasks still pending, one item per line. ") * 3


def _quoting_summary():
    """A lead summary that quotes an analysis closing tag and then a summary block,
    so the longest analysis reading leaves the quoted excerpt as a second body."""
    body = BODY + ("\n5. Notes: an earlier analysis quoted a </analysis> tag and then "
                   f"<summary>{_EXCERPT}</summary> from a previous compaction.")
    return f"<analysis>\nWorking notes that are not kept.\n</analysis>\n\n<summary>\n{body}\n</summary>", body


@pytest.mark.parametrize("teammate_record", [
    lambda tree: tree.append(tree.teammate, {"type": "user", "message": {"role": "user", "content": "Quoting it here: " + _EXCERPT}}),
    lambda tree: tree.summary(tree.teammate, body=BODY + "\n5. Quoted: " + _EXCERPT),
    lambda tree: tree.summary(tree.teammate, body=BODY + "\n5. An older compaction record:\n\nSummary:\n" + _EXCERPT),
], ids=["ordinary-message", "inside-another-summary", "after-a-later-summary-line"])
def test_a_quoted_excerpt_in_a_teammate_transcript_does_not_take_the_lead_summary(tree, teammate_record):
    """A candidate counts only directly after a summary record's first "Summary:"
    line. The teammate's record is on disk first; the lead's lands during the poll."""
    summary, body = _quoting_summary()
    assert _EXCERPT.strip() in co._summary_bodies(summary), "the excerpt must be a candidate"
    tree.stage(summary)
    teammate_record(tree)
    clock = later(2, on_sleep={2: lambda: tree.summary(tree.lead, body=body)})
    assert tree.settle(clock, wait_s=co.READ_WAIT_S) == [(co.LEAD, co.CONTENT)]


def test_a_body_directly_after_the_first_summary_line_matches_despite_a_later_one(tree):
    tree.stage()
    tree.summary(tree.teammate, body=BODY + "\n5. An older compaction record:\n\nSummary:\nearlier work.")
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]


def test_a_rendered_summary_pasted_into_an_ordinary_message_does_not_match(tree):
    """Only a record the platform marks as a compact summary can carry the body."""
    tree.stage()
    tree.append(tree.teammate, {"type": "user", "message": {"role": "user", "content": "Pasting the last compaction:\n\nSummary:\n" + BODY}})
    assert tree.settle(later(9)) == []


def test_a_candidate_that_resolves_outside_its_folder_is_skipped(tree, tmp_path):
    tree.stage()
    outside = tmp_path / "outside" / "planted.jsonl"
    outside.parent.mkdir()
    tree.summary(outside)
    (tree.subagents / "agent-aplanted-0123456789abcdef.jsonl").symlink_to(outside)
    assert tree.settle(later(9)) == []


def test_a_candidate_that_resolves_inside_its_folder_is_read(tree):
    """The containment check follows a link rather than refusing every link."""
    tree.stage()
    target = tree.subagents / "agent-atarget-0123456789abcdef.jsonl.data"
    tree.summary(target)
    (tree.subagents / "agent-alinked-0123456789abcdef.jsonl").symlink_to(target)
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]


def test_a_record_nested_past_the_parser_limit_does_not_discard_a_match(tree):
    """A line too deeply nested to decode is skipped like any other unparseable line."""
    tree.stage()
    depth = 500_000
    with open(tree.lead, "a", encoding="utf-8") as handle:
        handle.write("[" * depth + '"isCompactSummary"' + "]" * depth + "\n")
    with pytest.raises(RecursionError):
        json.loads("[" * depth + "]" * depth)
    tree.summary(tree.teammate)
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]


@pytest.mark.parametrize("case", [
    "no_transcript_path", "session_id_not_a_string", "unsafe_session_id", "outside_projects", "lead_transcript_missing",
])
def test_an_unlocatable_staged_summary_never_matches(tree, tmp_path, case):
    frame = tree.frame()
    if case == "no_transcript_path":
        del frame["transcript_path"]
    elif case == "session_id_not_a_string":
        frame["session_id"] = 42
    elif case == "unsafe_session_id":
        frame["session_id"] = "../escape"
    elif case == "outside_projects":
        frame["transcript_path"] = str(Tree(tmp_path / "elsewhere").lead)
    elif case == "lead_transcript_missing":
        frame["transcript_path"] = str(tree.project / "absent.jsonl")
    tree.stage(frame=frame)
    tree.summary(tree.lead)
    tree.summary(tree.teammate)
    assert tree.settle(later(9)) == []


_FIFO_CHILD = """
import json, sys
from shared import compaction_owner as co
elapsed = [0.0]
def sleep(seconds):
    elapsed[0] += seconds
print(json.dumps(co.settle(sys.argv[1], wait_s=co.READ_WAIT_S, monotonic=lambda: elapsed[0], sleep=sleep)))
"""


def test_a_fifo_candidate_is_never_opened(tree):
    """Opening a FIFO blocks until a writer appears, so settle runs in a child
    process that a timeout can end."""
    tree.stage(at=datetime.now(timezone.utc))
    os.mkfifo(tree.subagents / "agent-afifo-0123456789abcdef.jsonl")
    root = tree.project.parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(PYTHONPATH=str(HOOKS), CLAUDE_CONFIG_DIR=str(root), HOME=str(root.parent))
    try:
        proc = subprocess.run([sys.executable, "-c", _FIFO_CHILD, str(tree.session)],
                              capture_output=True, text=True, timeout=15, env=env)
    except subprocess.TimeoutExpired:
        pytest.fail("settle blocked opening a FIFO candidate")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == []


# --------------------------------------------------------------------------
# Captured frames
# --------------------------------------------------------------------------


@pytest.mark.parametrize("captured, side", [
    (captured_compaction_teammate_postcompact, co.TEAMMATE),
    (captured_compaction_lead_postcompact, co.LEAD),
])
def test_captured_frames_stage_and_settle_to_their_owner(tree, captured, side):
    frame = {**captured(), "transcript_path": str(tree.lead)}
    assert frame["session_id"] == SID
    tree.stage(frame=frame)
    tree.summary(tree.teammate if side == co.TEAMMATE else tree.lead,
                 body=co._summary_bodies(frame["compact_summary"])[0])
    assert tree.settle(later(9)) == [(side, co.CONTENT)]


@pytest.mark.parametrize("teammate, lead", [
    (captured_compaction_teammate_precompact, captured_compaction_lead_precompact),
    (captured_compaction_teammate_sessionstart, captured_compaction_lead_sessionstart),
    (captured_compaction_teammate_postcompact, captured_compaction_lead_postcompact),
])
def test_teammate_and_lead_compaction_frames_share_one_key_set(teammate, lead):
    assert set(teammate()) == set(lead())
    assert teammate()["agent_type"] == lead()["agent_type"]
    assert not {"agent_id", "agent_name", "agent_transcript_path"} & set(teammate())


# --------------------------------------------------------------------------
# The journal event
# --------------------------------------------------------------------------


def test_compaction_attributed_takes_an_optional_float_latency():
    from shared.session_journal import _validate_event_schema, make_event

    assert _validate_event_schema(make_event("compaction_attributed", verdict="teammate", basis="content", latency_s=8.5))[0]
    assert _validate_event_schema(make_event("compaction_attributed", verdict="unknown", basis="expired"))[0]
    assert not _validate_event_schema(make_event("compaction_attributed", verdict="lead", basis="content", latency_s="8.5"))[0]
    assert not _validate_event_schema(make_event("compaction_attributed", basis="content", latency_s=8.5))[0]


def test_a_compaction_attributed_missing_its_verdict_is_refused_on_stderr(tmp_path):
    """The journal CLI names the refusal; the in-process writer refuses silently."""
    from shared.session_journal import append_event, make_event

    assert not append_event(make_event("compaction_attributed", basis="content", latency_s=8.5), session_dir=str(tmp_path))
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "shared" / "session_journal.py"), "write", "--type", "compaction_attributed",
         "--session-dir", str(tmp_path), "--data", json.dumps({"basis": "content", "latency_s": 8.5})],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    assert "invalid event schema" in proc.stderr
    assert not (tmp_path / "session-journal.jsonl").exists()


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

_PERMITTED_KEYS = {"session_id", "transcript_path", "compact_summary"}
_OWNER_SOURCE = HOOKS / "shared" / "compaction_owner.py"


def test_only_the_permitted_frame_keys_are_read():
    read = set()
    for node in ast.walk(ast.parse(_OWNER_SOURCE.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "frame"):
            read.add(node.args[0].value)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "frame":
            read.add(getattr(node.slice, "value", None))
    assert read == _PERMITTED_KEYS, sorted(read, key=str)


def test_no_sleep_is_reachable_from_stage_summary():
    """The compaction hooks return at once: staging never waits."""
    functions = {node.name: node for node in ast.parse(_OWNER_SOURCE.read_text(encoding="utf-8")).body
                 if isinstance(node, ast.FunctionDef)}
    reached, called, stack = set(), set(), ["stage_summary"]
    while stack:
        name = stack.pop()
        if name in reached or name not in functions:
            continue
        reached.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Call):
                callee = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                called.add(callee)
                stack.append(callee)
    assert {"stage_summary", "_locate", "_transcripts", "_write_atomic"} <= reached
    assert "sleep" not in called and "settle" not in reached


_CONSUMER_ATTRIBUTES = {
    "postcompact_archive.py": {"settle", "stage_summary"},
    "session_init.py": {"settle"},
    "bootstrap_gate.py": {"settle", "READ_WAIT_S"},
}


@pytest.mark.parametrize("hook, allowed", sorted(_CONSUMER_ATTRIBUTES.items()))
def test_each_consumer_uses_only_its_part_of_the_module(hook, allowed):
    """No hook in the compaction chain decides ownership, so no consumer calls
    anything that could."""
    tree = ast.parse((HOOKS / hook).read_text(encoding="utf-8"))
    used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "compaction_owner"}
    assert used == allowed


@pytest.mark.parametrize("hook", sorted(_CONSUMER_ATTRIBUTES))
def test_consumers_reach_the_module_through_its_name(hook):
    """A test patch on shared.compaction_owner reaches the call, and no by-value
    copy exists for conftest's import guard to track."""
    tree = ast.parse((HOOKS / hook).read_text(encoding="utf-8"))
    module_imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "shared"
                      and any(alias.name == "compaction_owner" for alias in node.names)]
    by_value = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                and node.module == "shared.compaction_owner"]
    assert module_imports and not by_value


@pytest.mark.parametrize("hook", ["session_init.py", "postcompact_archive.py"])
def test_the_compaction_chain_settles_without_waiting(hook):
    calls = [node for node in ast.walk(ast.parse((HOOKS / hook).read_text(encoding="utf-8")))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "settle"]
    assert calls and all(not call.keywords for call in calls)
