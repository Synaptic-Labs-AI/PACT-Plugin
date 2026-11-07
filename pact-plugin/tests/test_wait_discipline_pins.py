"""
Structural pin tests for the wait-discipline rules in the orchestrator
persona (agents/pact-orchestrator.md §5 Wait in Silence, §12 Intentional
Waiting).

Pins the amendment surfaces:

  §5 Wait in Silence:
    - the narration-versus-milestone rule (the idle-turn list names a
      milestone the user is owed; silence does not suppress a milestone; a
      milestone is something the user would act on and cannot otherwise
      see, of three kinds; relayed to the user in one line, only when it is
      new since your last report to the user, carrying a number where there
      is one rather than a description of the activity, and not a running
      tally of the same work; the fan-out tally test; "no actionable
      content" means nothing the team needs AND nothing the user is owed;
      the lead relays each milestone as it reaches it and never polls or
      nudges for one, and asks for progress signals in a long dispatch's
      brief);
    - the filler-call compulsion block (names the reflex, states the
      turn-manufacture mechanics, states the terminal action);
    - the termination test — the discriminator stated ONCE at its §5 home:
      legitimate wait-time activity terminates on the awaited event;
    - the watcher rule (instrument at wait-START, timeout not optional,
      backgrounded template loop, report-the-findings-surface) incl. the
      watcher-liveness provision (a dead watcher is presumed on any
      post-timeout turn without a result — check once and re-arm; a
      user-reported wait carries a user-visible deadline).
    - the terminating condition is the awaited event; the launch flag that
      re-invokes the lead and the shell `&` that does not; a fired
      watcher is spent; a replacement is live before the incumbent is
      killed; the push notification; the recurring scheduled fallback.
  §12 Intentional Waiting:
    - the bidirectional silence rule (neither "stalled" nor "still working"
      is licensed by silence);
    - fallback instruments when the task store drains (session journal,
      branch state, filesystem mtimes) incl. the mtime-is-not-liveness
      caveat and the missed_wake_scan fallback machinery;
    - the `SendMessage` nudge-first rule with the probe-versus-noise
      boundary and the named crossed-wake-confirm boundary (nudge-first
      does not license accelerating nudges).

The discriminator single-home constraint is pinned STRUCTURALLY, not by
count: the canonical phrase ("legitimate wait-time activity terminates on
the awaited event") is pinned against the §5 section slice, and the
reference language ("termination test") is pinned against the §12 slice
and — in its watcher-rule form ("passes the termination test") — against
the §5 slice. Stated once at the §5 home, referenced by the other two
amendment sites: that is the structural test for "stated once, referenced
by all three."

PRESENCE pins, not counts (per the test_wake_ordering_pinned.py pattern —
none of these phrases is intended to recur a fixed number of times).
Matching is backtick-AND-whitespace-normalized on BOTH sides (see
_phrase) so a re-wrap or an inline-code rendering inside a pinned span
does not fail the pin while a re-WORD still does. Section slices are
fence-aware: the §5 watcher template is a fenced bash block whose `#`
comment lines must not terminate the §5 slice early.

Counter-test-by-construction: each block of phrase pins is run against the
persona text from before its rule landed, where every phrase pin of that
block is RED and the heading-anchor cases are GREEN (the headings are slice
anchors, not amendment text), then against the amended text, where the
full module is GREEN. Case counts are deliberately not recorded here: they
change with every added pin. The two heading pins guard the slice
anchors so a heading rename fails with a clear message rather than an
empty-slice failure per phrase pin.
"""

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

ORCHESTRATOR = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"

WAIT_IN_SILENCE = "### Wait in Silence"
INTENTIONAL_WAITING = "### Intentional Waiting (orchestrator responsibilities)"

SECTION_HEADINGS = [WAIT_IN_SILENCE, INTENTIONAL_WAITING]


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _phrase(text: str) -> str:
    """Phrase-matching normalization: strip backticks, then collapse any
    whitespace run (including newlines from hard-wrapping) to a single
    space. Applied to BOTH sides (section text and pinned phrase) so
    phrases stored with or without backticks match consistently — tool
    language inside a pinned span is inline-code formatted in the shipped
    markdown."""
    return " ".join(text.replace("`", "").split())


def _section(path: Path, heading: str) -> str:
    """Raw text of one section: from the exact heading line to the next
    markdown heading or horizontal rule, whichever comes first. Fence-aware:
    a `#`-leading line inside a fenced code block (the §5 watcher template
    carries `#` comments) is example text, not a heading, and must not end
    the slice early. Returns "" when the heading line is absent, so a
    renamed heading fails every phrase pin on that slice (and the heading
    pin below names the cause)."""
    lines = _raw(path).splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        return ""
    body = []
    in_fence = False
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        elif not in_fence and (stripped.startswith("#") or stripped == "---"):
            break
        body.append(line)
    return "\n".join(body)


# ---------------------------------------------------------------------------
# Heading pins — line-anchored exact match (slice anchors).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("heading", SECTION_HEADINGS, ids=lambda h: h.lstrip("# ")[:40])
def test_section_heading_present(heading: str):
    """The section heading must exist as an exact line — it is the slice
    anchor for every phrase pin in this module, so a rename must fail here
    with the cause named, not as an empty-slice failure per phrase pin."""
    assert heading in _raw(ORCHESTRATOR).splitlines(), (
        f"pact-orchestrator.md: heading {heading!r} not found as an exact "
        f"line. If the section was intentionally renamed, update this pin "
        f"AND the slice anchor in this module in lockstep."
    )


# ---------------------------------------------------------------------------
# Phrase pins — section-scoped, whitespace/backtick-normalized presence.
# ---------------------------------------------------------------------------

SECTION_PHRASE_PINS = [
    # --- §5 Wait in Silence: the filler-call compulsion block ---
    (WAIT_IN_SILENCE, "The filler-call compulsion"),
    # The turn-manufacture mechanics sentence — the deviation-justifying
    # fact that makes "end with NO tool call" non-negotiable.
    (WAIT_IN_SILENCE, "every tool result generates a new turn, so a filler call manufactures the next turn"),
    (WAIT_IN_SILENCE, "A tool call that produces no new information is a discipline violation"),
    # --- §5: narration versus milestone (a SCOPE SEPARATION, not an
    # exception: narration stays suppressed and lies outside what silence
    # governs, so this rule adds no second exception beside the one
    # protocol-defined exception above) ---
    # The idle-turn list must name the milestone itself, so the sentence is
    # complete without the redefinition below it.
    (WAIT_IN_SILENCE, "no user input, no milestone the user is owed — emit no reply"),
    (WAIT_IN_SILENCE, "Narration is not a milestone"),
    # The paragraph's central claim, as the whole sentence to its period:
    # a qualifier added after it fails, and so does listing a tool call as
    # narration (filler calls are the next paragraph's separate failure).
    (WAIT_IN_SILENCE, 'Silence suppresses narration — "waiting on X", "still running" — and it does not suppress a milestone.'),
    # The whole definition sentence, to its period: the qualifier leads so
    # that it governs all three kinds, and an appended kind fails the pin.
    (WAIT_IN_SILENCE, "A milestone is something the user would act on and cannot otherwise see: a dispatch's result, a measured number, or a state change."),
    # The relay sentence's load-bearing clauses, each pinned to a span that
    # is UNIQUE to it and that carries its own polarity word ("Relay",
    # "only when"): "to the user" alone occurs three times in this slice,
    # so the bare phrase would stay green if the relay clause were deleted,
    # and a span without its polarity word stays green when the clause is
    # inverted around it.
    (WAIT_IN_SILENCE, "Relay it to the user in one line"),
    # Without "to the user" here a milestone already sent to a teammate
    # reads as suppressed for the user, which is the ambiguity this clause
    # closes.
    (WAIT_IN_SILENCE, "only when it is new since your last report to the user"),
    (WAIT_IN_SILENCE, "a number where there is one"),
    (WAIT_IN_SILENCE, "not a running tally of the same work"),
    (WAIT_IN_SILENCE, "rather than a description of the activity"),
    # The tally test separates the two fan-out readings the rule treats
    # oppositely.
    (WAIT_IN_SILENCE, "In a fan-out, a count of members finished is a tally; a member's result the user would act on is a milestone."),
    # The redefinition is pinned separately: it is the clause that reverses the
    # old safe reading, so a presence pin on the definition alone would stay
    # green if a later edit softened this back to a contrast.
    (WAIT_IN_SILENCE, '"No actionable content" means nothing the team needs AND nothing the user is owed.'),
    # The lead is the subject that relays, it relays what reaches it, and it
    # never polls or nudges to obtain a milestone; the mid-run source is a
    # request in the dispatch brief.
    (WAIT_IN_SILENCE, "Relay each milestone as it reaches you, unprompted"),
    (WAIT_IN_SILENCE, "never poll or nudge a teammate to obtain one."),
    (WAIT_IN_SILENCE, "When you want a long dispatch reported mid-run, ask for progress signals in its brief."),
    # --- §5: the termination test (discriminator canonical phrase, §5 home) ---
    (WAIT_IN_SILENCE, "Legitimate wait-time activity terminates on the awaited event"),
    # --- §5: the watcher rule ---
    (WAIT_IN_SILENCE, "set up a watcher AT THAT MOMENT"),
    (WAIT_IN_SILENCE, "the timeout is not optional"),
    # The watcher rule's reference to the §5-home discriminator.
    (WAIT_IN_SILENCE, "passes the termination test"),
    # --- §5: the watcher-liveness provision ---
    (WAIT_IN_SILENCE, "the watcher is presumed dead"),
    (WAIT_IN_SILENCE, "re-arm if still pending"),
    (WAIT_IN_SILENCE, "state the deadline in that report"),
    # --- §5: report the findings surface ---
    (WAIT_IN_SILENCE, "Report the findings surface, not only completion"),
    # --- §12: bidirectional silence ---
    (INTENTIONAL_WAITING, 'neither "stalled" nor "still working" is licensed'),
    # --- §12: fallback instruments + mtime caveat + existing machinery ---
    (INTENTIONAL_WAITING, "the session journal, branch state, and filesystem mtimes"),
    (INTENTIONAL_WAITING, "a last-change time, not a liveness signal"),
    (INTENTIONAL_WAITING, "missed_wake_scan is the existing fallback machinery"),
    # --- §12: nudge-first + probe-versus-noise boundary ---
    (INTENTIONAL_WAITING, "a SendMessage nudge is the first move"),
    (INTENTIONAL_WAITING, "asks a question whose answer changes your next action is a probe"),
    # §12's reference to the §5-home discriminator (the boundary the nudge
    # rule is keyed to — the second reference site).
    (INTENTIONAL_WAITING, "termination test"),
    # The named crossed-wake-confirm boundary: nudge-first does not collide
    # with the anti-acceleration rule.
    (INTENTIONAL_WAITING, "never accelerate nudging in response to idle ticks"),
    # --- §5: the terminating condition is the awaited event itself ---
    (WAIT_IN_SILENCE, "That terminating condition must BE the awaited event, never a correlate of it."),
    (WAIT_IN_SILENCE, "If the awaited event cannot be tested directly, poll until it can be"),
    # --- §5: what makes a watcher's exit re-invoke the lead ---
    (WAIT_IN_SILENCE, "Launch it with `run_in_background: true` — that flag is what makes its exit re-invoke you."),
    (WAIT_IN_SILENCE, "Never background it with a shell `&` inside a foreground call instead"),
    # --- §5: a watcher that fires is spent; retiring one ---
    (WAIT_IN_SILENCE, "A watcher that FIRES is spent, and firing does not re-arm it."),
    (WAIT_IN_SILENCE, "Confirm the replacement is LIVE before you kill the incumbent, never after."),
    # --- §5: reaching a human who is not reading ---
    (WAIT_IN_SILENCE, "send a `PushNotification` in the same turn"),
    (WAIT_IN_SILENCE, "Never describe its mobile leg as delivered"),
    # --- §5: the scheduled fallback ---
    (WAIT_IN_SILENCE, "schedule a recurring `CronCreate` alongside it"),
    (WAIT_IN_SILENCE, "Key the cron on the DELIVERABLE, never on whether anyone reported"),
    (WAIT_IN_SILENCE, "Delete it once when the wait resolves."),
    (WAIT_IN_SILENCE, "It does not cover a dead session."),
]


@pytest.mark.parametrize(
    "heading, phrase",
    SECTION_PHRASE_PINS,
    ids=[f"{h.lstrip('# ')[:20]}::{p[:40]}" for h, p in SECTION_PHRASE_PINS],
)
def test_rule_phrase_present(heading: str, phrase: str):
    """Each load-bearing wait-discipline phrase must be present on its own
    section slice. Section-scoping is what makes the discriminator
    single-home constraint structural: the canonical phrase is pinned
    against the §5 slice and the reference language against the §12 slice,
    so moving the statement out of its §5 home (or dropping a reference
    site) flips exactly the pin for that site. If the wording was changed
    intentionally, update the pin in lockstep."""
    normalized_phrase = _phrase(phrase)
    assert normalized_phrase in _phrase(_section(ORCHESTRATOR, heading)), (
        f"pact-orchestrator.md {heading}: rule phrase {phrase!r} not found "
        f"in the section (backtick-and-whitespace-normalized match). If the "
        f"wording was changed intentionally, update this pin in lockstep; "
        f"otherwise the wait-discipline rule this phrase carries is missing "
        f"from a runtime-loaded surface."
    )
