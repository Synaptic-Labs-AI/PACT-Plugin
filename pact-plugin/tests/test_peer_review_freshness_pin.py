"""Structural pins for reviewer freshness at the dispatch decision.

A warm reviewer that reuses a teammate from an earlier phase of the session
carries that teammate's context into the review. The rule lives in prose, at
the point where the reviewer identity and `name=` are chosen, so the pins
are pattern pins bound to that paragraph and to the persona's
Reuse-vs-Spawn row. The command region ends at its own blank line; the
persona region is heading-bounded, and the workflow arm scans one line. A
token living in a neighbouring SENTENCE inside the same region can still
satisfy an arm, so every load-bearing claim is bound structurally: the scope
word to the freshness claim, the exception word to the review dispatch, the
exclusivity word to the reuse it scopes, and the negation to the reuse it
forbids. Words that merely elaborate a bound claim — the named examples of who
must not be reused — are not themselves pinned.

WHAT THIS DOES NOT CATCH: behaviour. The pins certify TEXT — that the rule
is stated, that its load-bearing words survive a rewording, and that a
deletion or an inversion reds. Whether a lead follows the rule on a live
dispatch is runtime behaviour no fixture observes; the first live review
under this prose is the probe. Each arm binds its claim's vocabulary, the
noun its scope word modifies, and — where a negation or exception word is
present — its polarity; the arms do not parse English. One unbounded class
remains stated rather than claimed closed: any contradictory wording that
carries no negation or exception word — an appended sentence that permits
what a pinned claim forbids, or a rewritten exception direction that keeps
the exception word but reverses what it excepts — still passes. Closing that
class would need exact-wording pins, which fail on benign rewordings.
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
    r"(?:(?:never|not|no)\b[^.;,]{0,80}\breus)"
    r"|(?:\breus\w*\b[^.;,]{0,80}\b(?:never|not\b|prohibit|forbid|barred|forbidden))"
)
# The rule is that freshness closes the CONVERSATIONAL channel only. Binding a
# bare `only` to a bare `channel` accepts a noun swap ('closes the agent-memory
# channel only') and, without the polarity guard, a negated verb ('does not
# close the conversational channel only').
EXCLUSIVITY_ON_CONVERSATIONAL = re.compile(
    r"\bconversational\s+channel\b[^.;]{0,40}\b(?:only|nowhere else|limited to)\b"
    r"|\b(?:only|nowhere else|limited to)\b[^.;]{0,40}\bconversational\s+channel\b"
)
# The other half of the claim: the agent-memory channel is what freshness does
# NOT close. A channel swap satisfies any bare-token arm, so bind the noun to
# the negation that qualifies it.
AGENT_MEMORY_NOT_CLOSED = re.compile(
    r"\bagent-memory\s+channel\b[^.;]{0,40}\b(?:does\s+not|do\s+not|never|not)\b"
    r"[^.;]{0,20}\bclose"
    r"|\b(?:does\s+not|do\s+not|never|not)\b[^.;]{0,20}\bclose\b[^.;]{0,40}"
    r"\bagent-memory\s+channel\b"
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
    r"\b(?:never|not)\b[^.;,]{0,60}\bre-?task\b"
    r"|\bre-?task\w*\b[^.;,]{0,60}\b(?:never|not)\b"
)
# The review row's reuse bar: a negation bound to reuse, either direction. A
# bare 'never|not' anywhere in the action cell is satisfied by an unrelated
# negation, so the row can lose its reuse bar and stay green.
ROW_NEGATION_NEAR_REUSE = re.compile(
    r"\b(?:never|not)\b[^.;,]{0,60}\breus\w*\b"
    r"|\breus\w*\b[^.;,]{0,60}\b(?:never|not)\b"
)
# The rule's scope: the freshness claim must bind WARM REVIEWERS, not reviewers
# in general. A bare '\bwarm\b' token is satisfied by the later 'Spawn each
# warm reviewer' sentence, and 'warm or cold, is freshly spawned' keeps the
# token within the window while distributing over the cold lane, so the scope
# word is bound to the reviewer noun and the comma-excluded window.
WARM_ON_FRESHNESS = re.compile(
    r"\bwarm\s+reviewers?\b[^.;,]{0,40}\bfresh\w*\b"
    r"|\bfresh\w*\b[^.;,]{0,40}\bwarm\s+reviewers?\b"
)
# The absolute governs a FRESH review dispatch. Without the scope phrase the
# sentence forbids the verify-only reuse the next sentence permits — the
# contradiction the phrase exists to prevent — so the phrase must sit in the
# same sentence as the freshness claim, not merely in the paragraph. Phrase
# and claim are separate patterns so a missing one is diagnosed as itself.
FRESH_DISPATCH_PHRASE = re.compile(r"\bfresh\s+review[-\s]+dispatch\b")
# The scope phrase DEFINES its rounds: the initial review and any later full
# review round. Deleting that definition, or narrowing it to the initial
# review alone, leaves 'fresh review dispatch' present but undefined.
FRESH_DISPATCH_DEFINITION = re.compile(
    r"\bthe initial review\b[^.;]{0,60}\bany later\b[^.;]{0,20}\breview round\b"
    r"|\bany later\b[^.;]{0,20}\breview round\b[^.;]{0,60}\bthe initial review\b"
)
# The claim's subject may be a generic universal ('a warm reviewer', 'every
# warm reviewer', 'all warm reviewers'); the bare noun phrase is accepted too.
FRESHNESS_ON_WARM_SUBJECT = re.compile(
    r"\b(?:(?:every|each|all|a)\s+)?warm\s+reviewers?\b[^.;]{0,40}"
    r"(?:\bfresh\w*\b[^.;]{0,40}\bspawn\w*\b"
    r"|\bspawn\w*\b[^.;]{0,40}\bfresh\w*\b)"
)
# The reuse exception is exhaustive: 'Reuse a warm reviewer ONLY for X or Y'.
# A bare 'only' token is satisfied by 'verify-only', so the exclusivity word
# must bind to REUSE and the hyphenated compound must not satisfy it.
EXCLUSIVITY_NEAR_REUSE = re.compile(
    r"(?<!-)\bonly\b[^.;]{0,60}\breuse\w*\b"
    r"|\breuse\w*\b[^.;]{0,60}(?<!-)\bonly\b"
)
# 'anything but a verify-only re-review' keeps 'only' bound to reuse while
# reversing the permitted set, so the tail check must see exception words too.
NEGATION_OR_EXCEPTION = re.compile(
    r"\b(?:never|not|no|without|cannot|unless|but|except)\b"
)
# The distinct-name lever: a bare 'distinct' token accepts 'a distinct role'
# and a negated 'a name that need not be distinct'.
DISTINCT_ON_NAME = re.compile(
    r"\bdistinct\b[^.;,]{0,40}\bname\b"
    r"|\bname\b[^.;,]{0,40}\bdistinct\b"
)
# The Default and shutdown paragraphs scope review dispatch OUT of the reuse
# default. A bare 'not' anywhere is satisfied by 'not the qualification' (or by
# an inverted 'not different'), so the exception word must bind to REVIEW
# DISPATCH itself.
REVIEW_DISPATCH_EXCEPTED = re.compile(
    r"\breview dispatch\b[^.;]{0,30}\b(?:exception|except\w*|outside|excluded?)\b"
    r"|\b(?:except|outside|exclud\w*)\b[^.;]{0,30}\breview dispatch\b"
)
# A negation word. The arms below care about the claim's polarity, not just
# its vocabulary: 'need not be spawned fresh' and 'a name that need not be
# distinct' carry every token of the positive claim and say the opposite.
NEGATION = re.compile(r"\b(?:never|not|no|without|cannot|unless)\b")
# A negation governs the reuse it forbids only when the reuse noun phrase
# follows it directly. A comma-free window alone still accepts an unrelated
# negation with an intervening list: 'never the builder and any specialist
# reused is acceptable' reads as permission.
NEGATION_GOVERNS_REUSE = re.compile(
    r"\b(?:never|not)\b\s+(?:(?:a|an|the|any|one)\s+)?(?:\w+\s+){0,3}\breus\w*"
)
# The bar forbids reusing a PERSON. 'Continuing a warm reviewer … is not reuse'
# is a scope clarification with no person in its negation's reach, so it must
# not satisfy the bar: a negation that reaches only the abstract noun 'reuse'
# is not the prohibition the clause exists to state.
NEGATION_GOVERNS_PERSON_REUSE = re.compile(
    r"\b(?:never|not)\b[^.;,]{0,80}\b(?:teammate|specialist|builder|reviewer)\b"
    r"[^.;,]{0,80}\breus\w*"
    r"|\b(?:never|not)\b[^.;,]{0,80}\breus\w*[^.;,]{0,80}"
    r"\b(?:teammate|specialist|builder|reviewer)\b"
)
# The bar is scoped to THIS SESSION: the conflict is reuse from an EARLIER
# phase of this session. Binding only the person accepts 'an earlier session'
# (a different scope) and a bar stripped of its scope entirely.
SESSION_SCOPE_ON_REUSE = re.compile(
    r"\breus\w*\b[^.;]{0,80}\b(?:earlier|prior|previous)\b[^.;]{0,40}"
    r"\bthis\s+session\b"
    r"|\breus\w*\b[^.;]{0,80}\bthis\s+session\b[^.;]{0,40}"
    r"\b(?:earlier|prior|previous)\b"
)
# The continuation carve-out is a SCOPE clarification: continuing a reviewer
# this round already dispatched is NOT the reuse the rule forbids. Bind the
# marker, the dispatch state, and the negated reuse in one sentence, so
# deleting the clause or dropping its negation reds — and the surrounding
# freshness sentence cannot satisfy the arm on its own.
CONTINUATION_NOT_REUSE = re.compile(
    r"\bcontinuing\b[^.;]{0,120}\bthis round already dispatched\b[^.;]{0,120}"
    r"\bis\s+not\s+(?:a\s+)?re-?use\b"
    r"|\bis\s+not\s+(?:a\s+)?re-?use\b[^.;]{0,120}\bcontinuing\b"
    r"[^.;]{0,120}\bthis round already dispatched\b"
)
# The anti-re-task corollary is a denial of PERMISSION. 'never prohibited to
# re-task' keeps the same tokens and inverts it to permission.
NEGATION_OF_PERMISSION = re.compile(
    r"\b(?:never|not|no)\b[^.;,]{0,20}"
    r"\b(?:permission|permitted|permit\w*|allowed|licen[cs]\w*|authoriz\w*)\b"
)
# The permitted-reuse list is scoped to WARM reviewers. A bare 'reus' token is
# satisfied by the cold lane's own sentence, which permits no such reuse.
WARM_ON_REUSE = re.compile(
    r"\bwarm\b[^.;,]{0,40}\breus\w*"
    r"|\breus\w*\b[^.;,]{0,40}\bwarm\b"
)
# The distinct-name lever compares against the LIVE roster. Without the
# comparison scope, 'distinct' does not say what the name is distinct from.
DISTINCT_FROM_LIVE = re.compile(r"\bdistinct\b[^.;,]{0,40}\blive\b")
# The gate's duplicate-name refusal fails OPEN when the team config is absent
# or unreadable, so the prose must keep the precondition on its claim.
PRESENT_AND_READABLE = re.compile(
    r"\bpresent\b[^.;,]{0,20}\breadable\b"
    r"|\breadable\b[^.;,]{0,20}\bpresent\b"
)
# The precondition qualifies a CLAIM: the gate REFUSES a live name. Binding
# only the precondition accepts 'the gate permits a live name' and, without a
# polarity guard, 'the gate does not refuse a live name'.
REFUSAL_ON_LIVE_NAME = re.compile(
    r"\brefus(?:e|es|ed|ing)\b[^.;]{0,60}\bname\b"
    r"|\bname\b[^.;]{0,60}\brefus(?:e|es|ed|ing)\b"
)
# The refusal is a NAMING RULE, not advice. 'guidance' or a deleted claim
# turns the hard gate into a suggestion while every other token survives.
NAMING_RULE_CLAIM = re.compile(
    r"\bthat refusal is a naming rule\b"
    r"|\brefus\w*\b[^.;]{0,40}\b(?:is|as)\s+(?:a\s+)?naming rule\b"
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

    The slice starts AFTER the anchor comment, so the anchor's own words
    ('fresh') cannot satisfy a token arm, and a blank line between the
    comment and the prose does not truncate the slice. The region ends at the
    first blank line, so the prose stays one paragraph.
    """
    section = _section()
    start = section.find(ANCHOR)
    assert start != -1, f"peer-review.md lost the {ANCHOR!r} anchor"
    # The slice starts AFTER the anchor comment, skipping leading whitespace:
    # a blank line between the comment and the prose is a normal markdown
    # edit, and slicing from the anchor would then return the comment alone.
    rest = section[start + len(ANCHOR):].lstrip()
    end = rest.find("\n\n")
    clause = rest[:end if end != -1 else None]
    clause = re.sub(r"\s+", " ", clause).strip().lower()
    assert "<!--" not in clause, (
        "the freshness clause still carries an HTML comment — every token "
        "arm below can be satisfied by the comment rather than by the rule"
    )
    # The bold lead-in is a heading, not the rule. Left in the slice it is
    # one more source of 'fresh', so a body stripped of the freshness
    # statement could still pass a token arm.
    lead_in = re.match(r"\*\*reviewer freshness[.:]\*\*", clause)
    assert lead_in is not None, (
        "the freshness clause no longer opens with its bold lead-in — the "
        "slice does not start at the rule"
    )
    clause = clause[lead_in.end():].strip()
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


def _unnegated(pattern: re.Pattern, text: str) -> bool:
    """A match of `pattern` with no negation governing it.

    The check runs from the previous sentence/clause boundary through the end
    of the match, so 'need not be spawned fresh' and 'a name that need not be
    distinct' do not satisfy a positive-claim arm, while a negation in the
    previous sentence does not spoil the claim.
    """
    for match in pattern.finditer(text):
        start = max(text.rfind(".", 0, match.start()),
                    text.rfind(";", 0, match.start())) + 1
        if not NEGATION.search(text[start:match.end()]):
            return True
    return False


def _unnegated_tail(pattern: re.Pattern, text: str,
                    negation: re.Pattern = NEGATION) -> bool:
    """A match of `pattern` whose own sentence adds no negation or exception.

    Catches the inversion that keeps the claim and appends an exception:
    'a distinct name unless its usual name is already live' and 'only when it
    is not a verify-only re-review'. The permitted-list arms pass
    NEGATION_OR_EXCEPTION so 'anything but X' counts as the exception it is.
    """
    for match in pattern.finditer(text):
        end = text.find(".", match.end())
        if not negation.search(text[match.end():end if end != -1 else None]):
            return True
    return False


def _carveout_is_positive(text: str, case: str) -> bool:
    """A permitted-reuse noun bound to a POSITIVE reuse verb.

    The carve-out says verify-only and fixer work DO reuse the reviewer; a
    bare token check accepts 'does not reuse that reviewer'.
    """
    pattern = re.compile(
        rf"\b{re.escape(case)}\b[^.;]{{0,80}}\breus\w*"
        rf"|\breus\w*[^.;]{{0,80}}\b{re.escape(case)}\b"
    )
    return _unnegated(pattern, text)


def _governed_phrase_has_one_negation(text: str) -> bool:
    """EVERY negation governing a reuse noun phrase is the only negation in
    that phrase. 'never a teammate not reused' carries a second negation
    INSIDE the governed phrase and reads as requiring the reuse; a second
    governed phrase appended later must not slip past the check."""
    matches = list(NEGATION_GOVERNS_REUSE.finditer(text))
    return bool(matches) and all(
        len(NEGATION.findall(match.group(0))) == 1 for match in matches
    )


@pytest.mark.parametrize("pattern, lost", [
    (WARM_ON_FRESHNESS, "the rule's scope — the freshness claim bound to WARM reviewers"),
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
    # The freshness claim is a POSITIVE claim: 'need not be spawned fresh'
    # carries every token and inverts it, so the freshness row binds polarity.
    found = (_unnegated(pattern, clause) if pattern is FRESHNESS_ON_SPAWN
             else pattern.search(clause))
    assert found, (
        f"the reviewer-freshness clause no longer carries {lost} "
        f"({pattern.pattern!r}) — without it the rule is not stated"
    )


def test_negation_is_bound_to_reuse():
    """A clause that mentions reuse without a negation nearby reads as
    permission to reuse. The window stops at a comma and a sentence end, so
    a negation attached to the examples ('not the one that wrote the code')
    cannot satisfy it; the governance arm additionally requires the negation
    to reach the reuse noun phrase, so an unrelated negation in the same
    comma-free clause cannot either."""
    clause = _clause()
    assert NEGATION_NEAR_REUSE.search(clause), (
        "the freshness clause mentions reuse without a negation bound to it "
        "— 'never a teammate reused' is the load-bearing shape"
    )
    assert NEGATION_GOVERNS_REUSE.search(clause), (
        "the freshness clause negates something other than the reuse — "
        "'never the builder and any specialist reused is acceptable' keeps "
        "the window green while permitting the reuse the rule forbids"
    )
    assert _governed_phrase_has_one_negation(clause), (
        "the freshness clause carries a second negation INSIDE the reuse "
        "bar — 'never a teammate not reused' reads as requiring the reuse "
        "the rule forbids"
    )
    assert NEGATION_GOVERNS_PERSON_REUSE.search(clause), (
        "the freshness clause no longer binds its reuse negation to a "
        "PERSON — 'Continuing a warm reviewer … is not reuse' is a scope "
        "clarification and must not stand in for the bar"
    )
    assert SESSION_SCOPE_ON_REUSE.search(clause), (
        "the freshness clause no longer scopes the reuse bar to THIS "
        "SESSION's earlier phase — 'an earlier session' or a scope-stripped "
        "bar changes which teammates the rule forbids"
    )


def test_negation_is_bound_to_retasking():
    """The gate-refusal corollary: a refusal is not permission to re-task the
    existing teammate. Bound like the reuse negation, so a negation in a
    neighbouring sentence cannot satisfy it, and bound to PERMISSION rather
    than to any negation: 'never prohibited to re-task' carries the same
    tokens and inverts the corollary to permission."""
    clause = _clause()
    assert NEGATION_NEAR_RETASK.search(clause), (
        "the freshness clause no longer binds a negation to re-tasking the "
        "existing teammate — the gate-refusal corollary was deleted or "
        "detached, and nothing else pins it"
    )
    assert NEGATION_OF_PERMISSION.search(clause), (
        "the freshness clause no longer says the refusal is not PERMISSION "
        "to re-task — 'it is never prohibited to re-task' binds a negation "
        "to re-tasking while permitting it"
    )


def test_uniqueness_is_bound_to_name():
    """`distinct` alone can qualify anything; the rule is that the NAME is
    distinct from every LIVE MEMBER. The uniqueness word must be positive
    ('need not be distinct' inverts it) and carry its comparison scope
    ('distinct' with nothing to be distinct from says nothing)."""
    clause = _clause()
    hits = [m.start() for m in re.finditer(r"\bname\b", clause)]
    assert hits, "the freshness clause no longer names `name=`"
    assert any(
        _unnegated(UNIQUENESS, clause[max(0, i - 60):i + 60]) for i in hits
    ), (
        "the freshness clause no longer binds a positive uniqueness word "
        "(distinct, unique, different) to `name` within 60 chars — the "
        "distinct-name sentence was deleted, detached, or negated"
    )
    assert _unnegated(DISTINCT_FROM_LIVE, clause), (
        "the freshness clause no longer makes a POSITIVE comparison of the "
        "name against the LIVE roster — 'need not be distinct from every "
        "live member' keeps every token, and 'distinct' alone does not say "
        "what the name is distinct from"
    )


def test_exclusivity_is_bound_to_the_conversational_channel():
    """The rule is that freshness closes the CONVERSATIONAL channel only and
    does NOT close the agent-memory channel. A bare `only` arm would pass on
    'verify-only' elsewhere in the clause; a bare `channel` arm accepts a
    noun swap and a negated verb ('does not close the conversational channel
    only')."""
    clause = _clause()
    assert _unnegated(EXCLUSIVITY_ON_CONVERSATIONAL, clause), (
        "the freshness clause no longer says freshness closes the "
        "CONVERSATIONAL channel only — the memory-limit sentence was "
        "deleted, detached, noun-swapped, or negated"
    )
    assert AGENT_MEMORY_NOT_CLOSED.search(clause), (
        "the freshness clause no longer names the agent-memory channel as "
        "the one freshness does NOT close — a channel swap leaves the rule "
        "pointing at the wrong channel"
    )


def test_exclusivity_is_bound_to_reuse():
    """The reuse exception is exhaustive: 'Reuse a warm reviewer only for a
    verify-only re-review or for reviewer-to-fixer work'. A bare `only` arm
    would pass on the 'only' inside 'verify-only', so the exclusivity word
    must bind to REUSE and the hyphenated compound must not satisfy it.
    The scope must also stay positive: 'only when it is NOT a verify-only
    re-review' keeps every token and forbids the permitted set."""
    clause = _clause()
    assert EXCLUSIVITY_NEAR_REUSE.search(clause), (
        "the freshness clause permits reuse without an exclusivity word "
        "bound to it — dropping 'only' from 'Reuse a warm reviewer only for "
        "…' turns the permitted-reuse list into two examples rather than the "
        "exhaustive set"
    )
    assert _unnegated(EXCLUSIVITY_NEAR_REUSE, clause), (
        "the freshness clause negates the permitted-reuse list before the "
        "exclusivity word — 'not only for a verify-only re-review' keeps "
        "'only' bound to reuse and widens the set beyond the one case it "
        "permits"
    )
    assert _unnegated_tail(
        EXCLUSIVITY_NEAR_REUSE, clause, NEGATION_OR_EXCEPTION
    ), (
        "the freshness clause negates the permitted-reuse list — 'only when "
        "it is not a verify-only re-review' or 'only for anything but a "
        "verify-only re-review' keeps 'only' bound to reuse and forbids "
        "exactly the cases the rule permits"
    )


def test_permitted_reuse_is_scoped_to_warm_reviewers():
    """The reuse exception names WARM reviewers. Without the scope word the
    exception also covers the cold reviewer, whom the Cold Reviewer Lane
    forbids from both fixer work and reuse."""
    clause = _clause()
    assert WARM_ON_REUSE.search(clause), (
        "the freshness clause permits reuse without scoping it to WARM "
        "reviewers — 'Reuse a reviewer only for …' also covers the cold "
        "reviewer the Cold Reviewer Lane forbids from reuse"
    )


def test_fresh_dispatch_scope_is_bound_to_the_freshness_claim():
    """The freshness absolute governs a FRESH review dispatch only. The
    scope phrase and the freshness claim are asserted separately, so a
    missing scope is not misreported as a missing claim (or the reverse);
    the claim accepts a generic-universal subject ('a warm reviewer')."""
    clause = _clause()
    claim = FRESHNESS_ON_WARM_SUBJECT.search(clause)
    assert claim, (
        "the freshness clause lost its freshness claim — no 'warm reviewer "
        "is freshly spawned' (either word order, any generic-universal "
        "subject) — so the absolute it scopes is gone"
    )
    scope = FRESH_DISPATCH_PHRASE.search(clause)
    assert scope, (
        "the freshness clause states its absolute without the 'For a fresh "
        "review dispatch' scope phrase — the sentence then forbids the "
        "verify-only reuse the next sentence permits"
    )
    between = clause[scope.end():claim.start()]
    assert (scope.start() < claim.start()
            and "." not in between and ";" not in between), (
        "the fresh-dispatch scope phrase no longer sits with the freshness "
        "claim — the phrase must scope the claim, not merely appear in the "
        "paragraph"
    )
    assert FRESH_DISPATCH_DEFINITION.search(between), (
        "the fresh-dispatch scope no longer DEFINES its rounds — 'the "
        "initial review or any later full review round' was deleted or "
        "narrowed, so the absolute is scoped to an undefined population"
    )


def test_gate_refusal_keeps_its_config_precondition():
    """The gate fails OPEN when the team config is absent or unreadable;
    without the precondition the clause claims a refusal that cannot fire.
    The precondition is bound to the REFUSAL it qualifies, positively: a
    'permits' verb or a 'not readable' precondition inverts the claim."""
    clause = _clause()
    presence = PRESENT_AND_READABLE.search(clause)
    refusal = REFUSAL_ON_LIVE_NAME.search(clause)
    assert presence is not None and _unnegated(PRESENT_AND_READABLE, clause), (
        "the freshness clause states the duplicate-name refusal without its "
        "positive 'present and readable' config precondition — a config-less "
        "session would read as enforced when the gate has already failed open"
    )
    assert refusal is not None and _unnegated(REFUSAL_ON_LIVE_NAME, clause), (
        "the freshness clause no longer says the gate REFUSES a live name — "
        "'permits a name=' or 'does not refuse' keeps every token and "
        "inverts the mechanism the clause claims"
    )
    lo, hi = sorted((presence.start(), refusal.start()))
    between = clause[lo:hi]
    assert "." not in between and ";" not in between, (
        "the 'present and readable' precondition is no longer in the same "
        "sentence as the refusal it qualifies — the reader cannot bind the "
        "fails-open caveat to the claim"
    )
    assert NAMING_RULE_CLAIM.search(clause), (
        "the freshness clause no longer says the duplicate-name refusal is a "
        "NAMING RULE — 'guidance' or a deleted claim turns a hard gate into "
        "a suggestion while the refusal sentence survives"
    )


def test_memory_pointer_names_the_clause_it_points_at():
    """The clause points at REVIEWER-MEMORY-CHANNEL by name. The name is
    load-bearing: a pointer to a renamed or deleted clause leaves the reader
    with no place to look, and the bare 'memory' token survives in
    'agent-memory' without it."""
    clause = _clause()
    assert "reviewer-memory-channel" in clause, (
        "the freshness clause no longer names the REVIEWER-MEMORY-CHANNEL "
        "clause it points at — the reader is told a channel exists but not "
        "where its limit is stated"
    )
    assert "ANCHOR-STABLE: REVIEWER-MEMORY-CHANNEL" in _peer_review_text(), (
        "the freshness clause points at a REVIEWER-MEMORY-CHANNEL clause "
        "that no longer exists in peer-review.md"
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
    # Locate the template and the numbered step by their shapes, not by exact
    # whitespace: a formatting-only edit must not report a lost step or
    # template.
    template_match = re.search(r"\n[ \t]*Agent\(", section)
    steps_match = re.search(r"\n1\.[ \t]+`TaskCreate\(", section)
    steps = steps_match.start() if steps_match else -1
    assert anchor != -1, f"peer-review.md lost the {ANCHOR!r} anchor"
    assert template_match is not None, (
        "the dispatch section lost its Agent( spawn template"
    )
    template = template_match.start()
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
    """The row carries four separate claims, each deletable on its own: the
    freshness claim is scoped to WARM reviewers, it is a SPAWN, the name is
    DISTINCT, and the reuse exception names its two permitted cases. They are
    bound here, not merely present: a token that survives its own deletion
    proves nothing."""
    row = _review_row(_persona_reuse_section())
    action = _cells(row)[1].lower()
    assert "spawn" in action and ROW_NEGATION_NEAR_REUSE.search(action), (
        f"the review-dispatch row no longer says to spawn and never (or not) "
        f"reuse: {row!r}"
    )
    assert NEGATION_GOVERNS_REUSE.search(action), (
        f"the review-dispatch row negates something other than the reuse — "
        f"'never the builder and a specialist reused' keeps the window green "
        f"while permitting the reuse the row forbids: {row!r}"
    )
    assert _governed_phrase_has_one_negation(action), (
        f"the review-dispatch row carries a second negation INSIDE its reuse "
        f"bar — 'never a specialist not reused' reads as requiring the reuse "
        f"the row forbids: {row!r}"
    )
    assert NEGATION_GOVERNS_PERSON_REUSE.search(action), (
        f"the review-dispatch row no longer binds its reuse negation to a "
        f"PERSON — 'Continuing a reviewer … is not reuse' must not stand in "
        f"for the bar: {row!r}"
    )
    assert SESSION_SCOPE_ON_REUSE.search(action), (
        f"the review-dispatch row no longer scopes its reuse bar to THIS "
        f"SESSION's earlier phase — 'an earlier session' or a scope-stripped "
        f"bar changes which teammates the row forbids: {row!r}"
    )
    assert WARM_ON_FRESHNESS.search(action), (
        f"the review-dispatch row no longer scopes its freshness claim to "
        f"WARM reviewers — a bare 'Spawn the reviewer fresh' also covers the "
        f"cold reviewer, which the Cold Reviewer Lane forbids: {row!r}"
    )
    hits = [m.start() for m in re.finditer(r"\bname\b", action)]
    assert hits and any(
        _unnegated(UNIQUENESS, action[max(0, i - 60):i + 60]) for i in hits
    ), (
        f"the review-dispatch row no longer binds a POSITIVE uniqueness word "
        f"(distinct, unique, different) to 'name' within 60 chars — 'a name "
        f"that need not be distinct' keeps the token and drops the lever: "
        f"{row!r}"
    )
    assert (_carveout_is_positive(action, "verify-only")
            and _carveout_is_positive(action, "fixer")), (
        f"the review-dispatch row lost its POSITIVE permitted-reuse cases — "
        f"a bare token from the pointer ('see Verify-Only Re-Review') or "
        f"'does not reuse that reviewer' keeps the words and drops the "
        f"carve-out: {row!r}"
    )
    assert EXCLUSIVITY_NEAR_REUSE.search(action), (
        f"the review-dispatch row permits reuse without an exclusivity word "
        f"bound to it — dropping 'only' turns the permitted-reuse list into "
        f"two examples rather than the exhaustive set: {row!r}"
    )
    assert _unnegated(EXCLUSIVITY_NEAR_REUSE, action), (
        f"the review-dispatch row negates the permitted-reuse list before "
        f"its exclusivity word — 'not only for a verify-only re-review' "
        f"keeps 'only' bound to reuse and widens the set: {row!r}"
    )
    assert _unnegated_tail(
        EXCLUSIVITY_NEAR_REUSE, action, NEGATION_OR_EXCEPTION
    ), (
        f"the review-dispatch row negates its permitted-reuse list — 'only "
        f"when it is not a verify-only re-review' or 'only for anything but "
        f"a verify-only re-review' forbids exactly the cases the row "
        f"permits: {row!r}"
    )
    # SendMessage is the PERMITTED-REUSE path, not the fresh-spawn path: the
    # row may name it for a verify-only re-review or fixer work, but a
    # SendMessage in the spawn sentence routes the reviewer through the reuse
    # the row exists to forbid.
    for sentence in re.split(r"(?<=[.;])\s+", action):
        if "sendmessage" in sentence:
            assert "verify-only" in sentence or "fixer" in sentence, (
                f"the review-dispatch row routes the fresh spawn through "
                f"SendMessage — SendMessage is the permitted-reuse path "
                f"only: {row!r}"
            )


def test_continuation_clause_is_not_reuse():
    """Continuing a reviewer this round already dispatched is a SCOPE
    clarification, not the reuse the rule forbids. The marker, the dispatch
    state, and the negated reuse are bound in one sentence, so deleting the
    clause or dropping its negation reds, and the surrounding freshness
    sentence cannot satisfy the arm on its own."""
    clause = _clause()
    assert CONTINUATION_NOT_REUSE.search(clause), (
        "the freshness clause lost its continuation carve-out — 'Continuing "
        "a warm reviewer this round already dispatched … is not reuse' — so "
        "a mid-round continuation now reads as the reuse the rule forbids"
    )
    action = _cells(_review_row(_persona_reuse_section()))[1].lower()
    assert CONTINUATION_NOT_REUSE.search(action), (
        "the review-dispatch row lost its continuation carve-out — the "
        "command permits continuing a reviewer this round already dispatched, "
        "and the row now reads as an absolute ban"
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
    assert _unnegated(REVIEW_DISPATCH_EXCEPTED, default), (
        "the **Default** sentence states reuse without POSITIVELY excepting "
        "review dispatch — 'Review dispatch is not excepted' keeps the "
        "exception word and excepts nothing"
    )
    assert (_carveout_is_positive(default, "verify-only")
            and _carveout_is_positive(default, "fixer")), (
        "the **Default** sentence lost its POSITIVE permitted-reuse carve-out "
        "— 'does not reuse that reviewer' keeps both tokens and forbids "
        "exactly the cases the rule permits"
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
    assert _unnegated(REVIEW_DISPATCH_EXCEPTED, paragraph), (
        "the shutdown guidance states reuse without POSITIVELY excepting "
        "review dispatch — 'review dispatch is not excepted' keeps the "
        "exception word and excepts nothing"
    )
    assert _unnegated(FRESHNESS_ON_SPAWN, paragraph), (
        "the shutdown guidance no longer binds a POSITIVE freshness claim to "
        "the spawn — 'need not be spawned fresh' carries every token and "
        "inverts the exception"
    )
    assert _unnegated(DISTINCT_ON_NAME, paragraph), (
        "the shutdown guidance lost its POSITIVE distinct-NAME lever — "
        "'a name that need not be distinct' keeps the token and negates it"
    )
    assert (_carveout_is_positive(paragraph, "verify-only")
            and _carveout_is_positive(paragraph, "fixer")), (
        "the shutdown guidance lost its POSITIVE permitted-reuse carve-out — "
        "'does not reuse that reviewer' keeps both tokens and forbids exactly "
        "the cases the rule permits"
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
    """Freshness and the distinct-name lever are both load-bearing: the
    dispatch gate can only refuse a collision on a `name=`. A bare 'distinct'
    token is satisfied by 'under a distinct role', which names nothing."""
    line = _warm_review_workflow_line().lower()
    hits = [m.start() for m in re.finditer(r"\bname\b", line)]
    assert (
        _unnegated(FRESHNESS_ON_SPAWN, line)
        and hits
        and any(
            _unnegated(UNIQUENESS, line[max(0, i - 60):i + 60]) for i in hits
        )
        and _unnegated_tail(UNIQUENESS, line)
    ), (
        f"the persona's PR Review Workflow no longer requires each warm "
        f"reviewer to be spawned fresh under a distinct `name=` — a detached "
        f"or negated lever ('need not be distinct', 'unless its usual name "
        f"is already live') leaves nothing for the dispatch gate to enforce: "
        f"{line!r}"
    )


def test_persona_workflow_line_scopes_freshness_to_warm_reviewers():
    """The cold reviewer is an anonymous general-purpose subagent with no
    `name=` and no teammate role. A freshness quantifier that does not name
    warm reviewers distributes over the whole coordination, cold reviewer
    included — the defect this arm exists to prevent."""
    line = _warm_review_workflow_line().lower()
    assert re.search(r"\beach\b[^.;,]{0,18}\bwarm\s+reviewers?\b", line), (
        "the PR Review Workflow line's 'each' no longer names a warm "
        "reviewer — as 'each reviewer, warm or cold' or a bare 'each freshly "
        "spawned' it distributes over the cold reviewer too, which the Cold "
        f"Reviewer Lane forbids: {line!r}"
    )
