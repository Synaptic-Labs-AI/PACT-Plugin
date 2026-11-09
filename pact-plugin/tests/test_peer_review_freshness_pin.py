"""Structural pins for reviewer freshness at the dispatch decision.

A warm reviewer that reuses a teammate from an earlier phase of the session
carries that teammate's context into the review. The rule lives in prose, at
the point where the reviewer identity and `name=` are chosen, so the pins
are pattern pins bound to that paragraph and to the persona's
Reuse-vs-Spawn row. The command region ends at its own blank line; the
persona region is heading-bounded, and the workflow arm scans one line. A
token living in a neighbouring SENTENCE inside the same region can still
satisfy an arm, so the structural arms bind the words that matter.

WHAT THIS DOES NOT CATCH: behaviour. The pins certify TEXT — that the rule
is stated, that its load-bearing words survive a rewording, and that a
deletion or a move reds. Whether a lead follows the rule on a live dispatch
is runtime behaviour no fixture observes; the first live review under this
prose is the probe. The evasion ceiling is prose reworded into uselessness
while every pinned token survives.
"""
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
PEER_REVIEW = PLUGIN_ROOT / "commands" / "peer-review.md"
PERSONA = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"

SECTION_HEAD = "**Dispatch reviewers**"
SECTION_END = "### Reviewer Teachback"
ANCHOR = "<!-- ANCHOR-STABLE: REVIEWER-FRESHNESS -->"
PERSONA_HEAD = "### Reuse vs. Spawn Decision"
PERSONA_END = "### Agent Shutdown Guidance"
REVIEW_ROW = "review dispatch"
UNIQUENESS = re.compile(r"\b(?:distinct|unique|different)\b")
NEGATION_NEAR_REUSE = re.compile(
    r"(?:(?:never|not|no)\b[^.;]{0,80}\breus)"
    r"|(?:\breus\w*\b[^.;]{0,80}\b(?:never|not\b|prohibit|forbid|barred|forbidden))"
)
EXCLUSIVITY_NEAR_CHANNEL = re.compile(
    r"\bchannel\b[^.;]{0,60}\b(?:only|nowhere else|limited to)\b"
    r"|\b(?:only|nowhere else|limited to)\b[^.;]{0,60}\bchannel\b"
)
# A freshness claim on a spawn, in either word order: 'freshly spawned' and
# 'spawned fresh' both carry the rule, and 'Freshness closes ...' does not,
# because no spawn sits within 40 chars before the sentence ends.
FRESHNESS_ON_SPAWN = re.compile(
    r"\bfresh\w*\b[^.;]{0,40}\bspawn\w*\b"
    r"|\bspawn\w*\b[^.;]{0,40}\bfresh\w*\b"
)
# The gate-refusal corollary: a refusal is never permission to re-task the
# existing teammate. Bound like the reuse negation, so a stray negation in a
# neighbouring sentence cannot satisfy it.
NEGATION_NEAR_RETASK = re.compile(
    r"\b(?:never|not)\b[^.;]{0,60}\bre-?task\b"
    r"|\bre-?task\w*\b[^.;]{0,60}\b(?:never|not)\b"
)
# The review row's reuse bar: a negation bound to reuse, either direction. A
# bare 'never|not' anywhere in the action cell is satisfied by an unrelated
# negation, so the row can lose its reuse bar and stay green.
ROW_NEGATION_NEAR_REUSE = re.compile(
    r"\b(?:never|not)\b[^.;]{0,60}\breus\w*\b"
    r"|\breus\w*\b[^.;]{0,60}\b(?:never|not)\b"
)


def _peer_review_text() -> str:
    return PEER_REVIEW.read_text(encoding="utf-8")


def _persona_text() -> str:
    return PERSONA.read_text(encoding="utf-8")


def _section() -> str:
    """The dispatch section: its lead-in to the Reviewer Teachback heading."""
    text = _peer_review_text()
    start = text.find(SECTION_HEAD)
    assert start != -1, f"peer-review.md lost the {SECTION_HEAD!r} lead-in"
    assert text.count(SECTION_HEAD) == 1, (
        f"the {SECTION_HEAD!r} lead-in now occurs more than once, so the "
        f"section it bounds is ambiguous"
    )
    end = text.find(SECTION_END, start)
    assert end != -1, (
        f"the dispatch section is no longer followed by {SECTION_END!r} — "
        f"the section boundary moved"
    )
    section = text[start:end]
    assert "follow the steps for [Teachback-Gated Dispatch]" in section, (
        "the dispatch section lost its Teachback-Gated Dispatch pointer — "
        "the extraction is looking at the wrong region"
    )
    assert len(section) > 1000, (
        "the dispatch section is too short to carry the freshness rule and "
        "the dispatch steps — an edit truncated it"
    )
    return section


def _clause() -> str:
    """The freshness paragraph alone, normalized and stripped of its anchor.

    The anchor comment contains the word 'fresh'; without the strip a token
    arm can be satisfied with no prose carrying it. The region ends at the
    first blank line, so the anchored prose must stay one paragraph.
    """
    section = _section()
    start = section.find(ANCHOR)
    assert start != -1, f"peer-review.md lost the {ANCHOR!r} anchor"
    end = section.find("\n\n", start)
    clause = section[start:end if end != -1 else None]
    clause = re.sub(r"<!--.*?-->", "", clause, count=1)
    clause = re.sub(r"\s+", " ", clause).strip().lower()
    assert "<!--" not in clause, (
        "the anchor comment survived the strip — every token arm below can "
        "be satisfied by the anchor rather than by the rule"
    )
    # The bold lead-in is a heading, not the rule. Left in the slice it is
    # one more source of 'fresh', so a body stripped of the freshness
    # statement could still pass a token arm.
    lead_in = "**reviewer freshness.**"
    assert clause.startswith(lead_in), (
        "the freshness clause no longer opens with its bold lead-in — the "
        "slice does not start at the rule"
    )
    clause = clause[len(lead_in):].strip()
    # Extraction sanity, following test_peer_review_independence_pin.py:
    # anchors run to the FIRST blank line, so a lost blank line silently
    # widens the slice into the numbered dispatch steps.
    assert re.search(r"\s\d+\.\s", clause) is None, (
        "the freshness clause extraction has run PAST the clause and "
        "swallowed a numbered step — restore the blank line after the "
        "paragraph"
    )
    assert clause.endswith("."), (
        "the freshness clause extraction is TRUNCATED — it stops mid-"
        "sentence, so the token arms below search only part of the rule"
    )
    return clause


@pytest.mark.parametrize("pattern, lost", [
    (FRESHNESS_ON_SPAWN, "a freshness claim bound to a spawn (either word order)"),
    (re.compile(r"\bspawn"), "the action the rule requires"),
    (re.compile(r"\bname\b"), "the uniqueness lever"),
    (re.compile(r"\b(?:distinct|unique|different)\b"), "the name the reviewer must take"),
    (re.compile(r"\bteammate"), "the object of the reuse bar"),
    (re.compile(r"\breus"), "the reuse bar itself"),
    (re.compile(r"verify-only"), "the one re-review reuse exception"),
    (re.compile(r"fixer"), "the reviewer-to-fixer reuse exception"),
    (re.compile(r"memory"), "the pointer to the REVIEWER-MEMORY-CHANNEL clause that names the channel freshness does not close"),
    (re.compile(r"\bcold\b"), "the cold lane's decisive read"),
])
def test_clause_keeps_its_load_bearing_tokens(pattern, lost):
    clause = _clause()
    assert pattern.search(clause), (
        f"the reviewer-freshness clause no longer carries {lost} "
        f"({pattern.pattern!r}) — without it the rule is not stated"
    )


def test_negation_is_bound_to_reuse():
    """A clause that mentions reuse without a negation nearby reads as
    permission to reuse. The bound is 80 chars and stops at a sentence end,
    so a negation in a neighbouring sentence cannot satisfy it."""
    clause = _clause()
    assert NEGATION_NEAR_REUSE.search(clause), (
        "the freshness clause mentions reuse without a negation bound to it "
        "— 'never a teammate reused' is the load-bearing shape"
    )


def test_negation_is_bound_to_retasking():
    """The gate-refusal corollary: a refusal is not permission to re-task the
    existing teammate. Bound like the reuse negation, so a negation in a
    neighbouring sentence cannot satisfy it."""
    clause = _clause()
    assert NEGATION_NEAR_RETASK.search(clause), (
        "the freshness clause no longer binds a negation to re-tasking the "
        "existing teammate — the gate-refusal corollary was deleted or "
        "detached, and nothing else pins it"
    )


def test_uniqueness_is_bound_to_name():
    """`distinct` alone can qualify anything; the rule is that the NAME is
    distinct from every live member."""
    clause = _clause()
    hits = [m.start() for m in re.finditer(r"\bname\b", clause)]
    assert hits, "the freshness clause no longer names `name=`"
    assert any(
        UNIQUENESS.search(clause[max(0, i - 60):i + 60]) for i in hits
    ), (
        "the freshness clause no longer binds a uniqueness word (distinct, "
        "unique, different) to `name` within 60 chars — the distinct-name "
        "sentence was deleted or detached"
    )


def test_exclusivity_is_bound_to_the_conversational_channel():
    """The rule is that freshness closes the CONVERSATIONAL channel only.
    A bare `only` arm would pass on 'verify-only' elsewhere in the clause,
    so the exclusivity word must sit near the channel it scopes."""
    clause = _clause()
    assert EXCLUSIVITY_NEAR_CHANNEL.search(clause), (
        "the freshness clause no longer says what freshness does NOT close "
        "— the memory-limit sentence was deleted or detached from 'channel'"
    )


def test_clause_sits_before_the_spawn_template():
    """The identity and `name=` are chosen before the spawn call, so the rule
    must sit before the Agent template that dispatches it.

    In the current document order the step-1 assertion below subsumes the
    template assertion: any anchor before step 1 is also before the template.
    The template arm fires independently only if the steps and the template
    are reordered, so it guards that reordering rather than acting as an
    independent witness today.
    """
    section = _section()
    anchor = section.find(ANCHOR)
    template = section.find("\nAgent(\n")
    steps = section.find("\n1. `TaskCreate(")
    assert anchor != -1, f"peer-review.md lost the {ANCHOR!r} anchor"
    assert template != -1, "the dispatch section lost its Agent( spawn template"
    assert steps != -1, (
        "the dispatch section lost its numbered step 1 (`TaskCreate`) — the "
        "position arm can no longer bind the clause to the steps"
    )
    assert anchor < steps, (
        "the reviewer-freshness clause now sits after step 1 — the reviewer "
        "identity and `name=` are chosen there, so the rule arrives after "
        "the decision it governs"
    )
    assert anchor < template, (
        "the reviewer-freshness clause now sits AFTER the Agent( spawn "
        "template — it arrives after the decision it governs"
    )


def _persona_reuse_section() -> str:
    """The Reuse vs. Spawn section, with liveness controls."""
    text = _persona_text()
    start = text.find(PERSONA_HEAD)
    assert start != -1, f"the persona lost the {PERSONA_HEAD!r} section"
    end = text.find(PERSONA_END, start)
    assert end != -1, (
        f"the Reuse vs. Spawn section is no longer followed by "
        f"{PERSONA_END!r} — the section boundary moved"
    )
    section = text[start:end]
    assert "| Situation | Action |" in section, (
        "the Reuse vs. Spawn section lost its table header"
    )
    assert "Idle agent has relevant context" in section, (
        "the Reuse vs. Spawn table lost its generic idle-agent row — the "
        "extraction is looking at the wrong region"
    )
    return section


def _data_rows(section: str) -> list[str]:
    lines = section.splitlines()
    sep = next(
        (i for i, ln in enumerate(lines)
         if re.fullmatch(r"\|[\s\-|]+\|", ln.strip())),
        None,
    )
    assert sep is not None, "the Reuse vs. Spawn table lost its separator row"
    rows = []
    for line in lines[sep + 1:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        rows.append(stripped)
    assert rows, "the Reuse vs. Spawn table has no data rows"
    return rows


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _review_row(section: str) -> str:
    rows = [row for row in _data_rows(section)
            if REVIEW_ROW in _cells(row)[0].lower()]
    assert len(rows) == 1, (
        f"expected exactly 1 Reuse-vs-Spawn row whose first cell names "
        f"{REVIEW_ROW!r}, found {len(rows)}"
    )
    return rows[0]


def test_persona_has_one_review_dispatch_row():
    row = _review_row(_persona_reuse_section())
    action = _cells(row)[1].lower()
    assert "spawn" in action and ROW_NEGATION_NEAR_REUSE.search(action), (
        f"the review-dispatch row no longer says to spawn fresh and never "
        f"(or not) reuse: {row!r}"
    )
    assert "sendmessage" not in action, (
        f"the review-dispatch row routes the reviewer through SendMessage "
        f"— that is the reuse this row exists to forbid: {row!r}"
    )


def test_persona_review_row_precedes_the_generic_idle_agent_row():
    """The generic 'Idle agent has relevant context' row is a plausible match
    for a reviewer, so the review row must precede it — not occupy the very
    first slot, which would reject an unrelated row legitimately added above."""
    section = _persona_reuse_section()
    rows = _data_rows(section)
    review = _review_row(section)
    idle = next(
        (i for i, row in enumerate(rows)
         if "idle agent has relevant context" in _cells(row)[0].lower()),
        None,
    )
    assert idle is not None, (
        "the Reuse vs. Spawn table lost its generic idle-agent row — the "
        "ordering arm has no anchor"
    )
    assert rows.index(review) < idle, (
        f"the review-dispatch row no longer precedes the generic idle-agent "
        f"row: {rows[idle]!r}"
    )


def test_persona_default_scopes_reuse_away_from_review_dispatch():
    """The Default sentence is a reuse absolute; without its exception the
    table row is contradicted four lines below it.

    This pin asserts the SCOPING, not a restatement of the rule: the fresh
    spawn is carried by the review row above it."""
    section = _persona_reuse_section()
    start = section.find("**Default**")
    assert start != -1, "the Reuse vs. Spawn section lost its **Default** sentence"
    end = section.find("\n\n", start)
    default = section[start:end if end != -1 else None].lower()
    assert "reuse" in default and "review" in default, (
        "the **Default** sentence no longer mentions both reuse and review "
        "dispatch, so its exception cannot be stated"
    )
    assert any(word in default for word in
               ("not", "never", "except", "outside", "exclude")), (
        "the **Default** sentence states reuse without scoping review "
        "dispatch out of it — the unscoped absolute is back"
    )


def _persona_shutdown_section() -> str:
    """The Agent Shutdown Guidance section, with liveness controls."""
    text = _persona_text()
    start = text.find(PERSONA_END)
    assert start != -1, f"the persona lost the {PERSONA_END!r} section"
    end = text.find("\n### ", start + len(PERSONA_END))
    section = text[start:end if end != -1 else None]
    assert "Do **not** shut down teammates preemptively." in section, (
        "the Agent Shutdown Guidance section lost its preemptive-shutdown "
        "absolute — the extraction is looking at the wrong region"
    )
    return section


def test_shutdown_guidance_scopes_reuse_away_from_review_dispatch():
    """The shutdown absolute is a second reuse surface; unpinned, its
    exception can be dropped without reddening any other arm."""
    section = _persona_shutdown_section()
    start = section.find("Do **not** shut down teammates preemptively.")
    end = section.find("\n\n", start)
    paragraph = section[start:end if end != -1 else None].lower()
    assert "reuse idle teammates whenever possible" in paragraph, (
        "the shutdown guidance no longer states the reuse default"
    )
    assert all(token in paragraph for token in
               ("review dispatch", "fresh", "distinct")), (
        "the shutdown guidance states reuse without its review-dispatch "
        "exception — the second reuse surface is back"
    )


def _warm_review_workflow_line() -> str:
    text = _persona_text()
    lines = [ln for ln in text.splitlines()
             if "Invoke **at least 3 warm reviewers in parallel**" in ln]
    assert len(lines) == 1, (
        f"expected exactly 1 persona line invoking the warm reviewer "
        f"combination, found {len(lines)}"
    )
    return lines[0]


def test_persona_review_workflow_line_requires_a_fresh_spawn():
    line = _warm_review_workflow_line().lower()
    assert FRESHNESS_ON_SPAWN.search(line) and re.search(r"\bdistinct\b", line), (
        f"the persona's PR Review Workflow no longer requires each warm "
        f"reviewer to be spawned fresh under a distinct name: {line!r}"
    )


def test_persona_workflow_line_scopes_freshness_to_warm_reviewers():
    """The cold reviewer is an anonymous general-purpose subagent with no
    `name=` and no teammate role. A freshness quantifier that does not name
    warm reviewers distributes over the whole coordination, cold reviewer
    included — the defect this arm exists to prevent."""
    line = _warm_review_workflow_line().lower()
    assert re.search(r"\beach\b[^.;]{0,20}\bwarm\b", line), (
        "the PR Review Workflow line's 'each' no longer names warm reviewers "
        "— as a bare 'each freshly spawned' it distributes over the cold "
        f"reviewer too, which the Cold Reviewer Lane forbids: {line!r}"
    )