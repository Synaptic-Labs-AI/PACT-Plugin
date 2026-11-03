"""The census instrument's own check, run against a SYNTHETIC corpus.

NO ARM HERE PINS A REAL-WORLD COUNT. The journal population includes the
handoffs written while measuring it -- it moved by 3 events between this
arc's own runs -- so any test asserting a number from the live roots rots by
the next session. These arms build a fixture corpus under tmp_path, where the
expected counts are known by construction, and assert the instrument reports
what was planted.

The arms that touch the shipped code rather than the fixture guard what the
journal arm's confidence rests on: the presence-only reconstruction, and the
inline handoff passing through unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import handoff_census as hc  # noqa: E402

CANONICAL = {
    "produced": ["a"], "decisions": ["b"], "reasoning_chain": "c",
    "uncertainty": [], "integration": [], "open_questions": [],
}
NO_REASONING = {k: v for k, v in CANONICAL.items() if k != "reasoning_chain"}
LEGACY = {**NO_REASONING, "key_decisions": ["x"]}
del LEGACY["decisions"]


def _corpus(home: Path) -> None:
    """Plant a two-root corpus with known-by-construction shapes."""
    journal = home / ".claude" / "pact-sessions" / "proj" / "sess"
    journal.mkdir(parents=True)
    events = [
        {"type": "agent_handoff", "handoff": list(CANONICAL)},
        {"type": "agent_handoff", "handoff": list(NO_REASONING)},
        {"type": "agent_handoff", "handoff": list(LEGACY)},
        # Skipped on the cheap substring pre-filter alone.
        {"type": "session_start", "handoff": ["wrong event type"]},
        # THE DECOY, and it is the one that matters. The reader's cheap
        # pre-filter tests for the QUOTED string "agent_handoff", so the decoy
        # must carry it as a VALUE -- a prose mention of agent_handoff does not
        # contain the quotes and is rejected one step earlier, by the filter
        # this row is not aiming at. Measured both ways: with the quoted form
        # below, deleting the type check reds this module; with an unquoted
        # prose mention it stays GREEN and the row proves nothing.
        {"type": "session_start", "supersedes": "agent_handoff",
         "handoff": ["decoy"]},
    ]
    (journal / "session-journal.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\nNOT JSON\n", encoding="utf-8"
    )

    tasks = home / ".claude-other" / "tasks" / "team"
    tasks.mkdir(parents=True)
    for name, handoff in [("1", CANONICAL), ("2", LEGACY), ("3", "not a dict")]:
        (tasks / f"{name}.json").write_text(
            json.dumps({"metadata": {"handoff": handoff}}), encoding="utf-8"
        )
    (tasks / "4.json").write_text(json.dumps({"metadata": {}}), encoding="utf-8")
    (tasks / "5.json").write_text("{ broken", encoding="utf-8")


def test_key_set_reconstruction_is_faithful():
    """The historical list-of-keys shape is reconstructed as {key: None}. That
    is exact only while the validator is presence-only, and this is the guard
    that fails loudly rather than letting the census drift into a wrong
    answer."""
    hc.assert_presence_only()  # raises SystemExit if the validator inspects values


def test_journal_reader_passes_an_inline_handoff_through_unchanged(tmp_path):
    """The real shape: a current event carries the full handoff dict. It must
    be yielded as written, not reconstructed from its keys."""
    journal = tmp_path / ".claude" / "pact-sessions" / "proj" / "sess"
    journal.mkdir(parents=True)
    (journal / "session-journal.jsonl").write_text(
        json.dumps({"type": "agent_handoff", "handoff": CANONICAL}) + "\n",
        encoding="utf-8",
    )
    assert list(hc.journal_handoffs(tmp_path / ".claude")) == [CANONICAL]


def test_tally_counts_the_planted_shapes():
    counts = hc.tally([CANONICAL, NO_REASONING, LEGACY, "not a dict", 7])
    assert counts["n"] == 5
    # LEGACY is missing `decisions`; the two non-dicts are malformed.
    assert counts["fires"] == 3
    assert counts["not_a_dict"] == 2
    assert counts["no_reasoning_chain"] == 2      # NO_REASONING + LEGACY
    assert counts["missing_canonical"] == 2
    assert counts["legacy_any"] == 1
    assert counts["legacy_key_decisions"] == 1
    assert counts["legacy_areas_of_uncertainty"] == 0


def test_tally_of_an_empty_population_is_all_zero():
    counts = hc.tally([])
    assert counts["n"] == 0
    assert set(counts.values()) == {0}


def test_journal_reader_selects_agent_handoff_and_survives_junk(tmp_path):
    _corpus(tmp_path)
    found = list(hc.journal_handoffs(tmp_path / ".claude"))
    assert len(found) == 3, (
        "Expected the 3 agent_handoff events only -- a wrong event type and an "
        "unparseable line must be skipped, not counted and not fatal."
    )
    assert all(isinstance(f, dict) for f in found)


def test_task_reader_takes_the_handoff_exactly_as_written(tmp_path):
    _corpus(tmp_path)
    found = list(hc.task_handoffs(tmp_path / ".claude-other"))
    assert len(found) == 3, (
        "Expected the 3 task files carrying metadata.handoff -- one without the "
        "key and one unparseable file must be skipped."
    )
    assert "not a dict" in found, (
        "The task-file arm must pass a non-dict through UNCHANGED. It is the "
        "only instrument with no emission filter, so mapping malformed shapes "
        "away here would manufacture the zero the standing trigger reads."
    )


def test_roots_are_discovered_and_named(tmp_path):
    _corpus(tmp_path)
    (tmp_path / ".claude-empty").mkdir()
    (tmp_path / "not-a-claude-root").mkdir()
    (tmp_path / ".clauderc").write_text("a file, not a root", encoding="utf-8")
    names = [p.name for p in hc.config_roots(tmp_path)]
    assert names == [".claude", ".claude-empty", ".claude-other"], (
        "Root discovery must take every .claude* DIRECTORY and nothing else. A "
        "census scoped to one root is a scoped result, so a silently dropped "
        "root changes every rate the report prints."
    )
