"""Certification populations for the pin-growth rule (hooks/shared/pin_growth.py).

Every change below carries a label decided by how it was built, never by the rule
or the finder (tests/fixtures/pin_growth/). Each one runs through the shipped
decision, pin_cap_decision, and every axis counts:

- an over-block, a faithful change denied, fails the test in every population;
- an under-block, a growth change allowed past the cap, must belong to a family the
  user signed off, and the count per population may only go down (a ratchet);
- the gate and the naive oracle must agree on whether the Pinned section can be
  located, so the advisory path cannot hide a case;
- no honest change reaches the step budget.

Every gate runs the slice, a fixed-seed prefix of the full sweep. With CI set the
full sweep runs instead, with the certified seeds and counts. The local runner
replays ordinary edits on real CLAUDE.md files named by PACT_PIN_GROWTH_REAL_FILES
and is skipped without it; it reads those files and keeps nothing. There an edit
that grows a pin past the size cap must be refused for its size, and every other
ordinary edit must be allowed.
"""

import collections
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import pin_growth

from fixtures.pin_growth import generators as G
from fixtures.pin_growth import harness as H
from fixtures.pin_growth import replay
from fixtures.pin_growth import rows as R
from fixtures.pin_growth.fixed_row_flips import OLD_VERDICT

TESTS = Path(__file__).resolve().parent
FULL = bool(os.environ.get("CI"))
SCALE = "full" if FULL else "slice"
REAL_FILES_ENV = "PACT_PIN_GROWTH_REAL_FILES"
TRIM_DIFFERENTIAL_ENV = "PACT_PIN_GROWTH_TRIM_DIFFERENTIAL"

# sha256 over the 202 ported rows' texts, in builder order. The port was checked
# byte for byte against the scratch model's rows; a change here is a changed row.
FIXED_ROWS_DIGEST = "9ef63299104ea9dfd94e350b5a1c8133f9be81d1c8be1c8f2f07b4374c011006"

# The first 200 cases of each stream, the same on every interpreter and hash seed.
DETERMINISM = {
    "transition, bodies half the time": "c69df4fb270c7b53ddc0cb01b3a35d9bacc09493cd3b073df2831dea166a6c24",
    "transition, bodies always": "c61751fd59ab2660781f072fa5b3aa8da4fea52276d79425b15524368ea0e10f",
    "located": "bb09a715abd80a9eca1148f38d0220d72061df2d6b3f07d15daa48eb41dc152e",
    "real layouts": "ee017ac450fc2f57be3a989bf12b6e4c802721e1c38d9ec9844e6b36e6a7c241",
}


ALL_ROWS = R.FIXED_ROWS + R.COMMENTED_ROWS + R.CONTENT_FENCE_ROWS + R.REMOVAL_ROWS + R.CEILING_ROWS + R.REVEAL_ROWS


def _commented(transition_cases, layout_cases):
    return (H.items_from_cases(G.commented_stream(transition_cases))
            + H.items_from_cases(G.commented_stream(layout_cases)))


def _chain(*streams):
    return [c for s in streams for c in s]


POPULATIONS = {
    "fixed rows": (
        lambda: H.items_from_rows(ALL_ROWS),
        lambda: H.items_from_rows(ALL_ROWS)),
    "transition, bodies half the time": (
        lambda: H.items_from_cases(G.transition_stream(1, 0.5, 1000)),
        lambda: H.items_from_cases(_chain(*(G.transition_stream(s, 0.5, 3000) for s in (1, 2, 3))))),
    "transition, bodies always": (
        lambda: H.items_from_cases(G.transition_stream(1, 0.0, 1000)),
        lambda: H.items_from_cases(_chain(*(G.transition_stream(s, 0.0, 3000) for s in (1, 2, 3))))),
    "located": (
        lambda: H.items_from_cases(H.first(G.located_stream(1, 4000), 1000)),
        lambda: H.items_from_cases(_chain(*(G.located_stream(s, 4000) for s in (1, 2, 3))))),
    "real layouts": (
        lambda: H.items_from_cases(G.layout_stream(1, 650)),
        lambda: H.items_from_cases(_chain(*(G.layout_stream(s, 1200) for s in (1, 7, 11, 23))))),
    # region R's start must skip a commented-out old Pinned section above the real one
    "commented-out old Pinned section": (
        lambda: _commented(G.transition_stream(1, 0.5, 300), G.layout_stream(1, 300)),
        lambda: _commented(_chain(*(G.transition_stream(s, 0.5, 3000) for s in (1, 2, 3))),
                           _chain(*(G.layout_stream(s, 1200) for s in (1, 7, 11, 23))))),
    # user notes above and below PACT's managed block, edited or not
    "notes outside the managed block": (
        lambda: H.items_from_cases(G.outside_stream(G.layout_stream(1, 650), 1)),
        lambda: H.items_from_cases(_chain(*(G.outside_stream(G.layout_stream(s, 1200), s) for s in (1, 7, 11, 23))))),
}

# Under-blocks recorded per population and scale. A fix may lower one; a rise fails.
RECORDED_UNDER = {
    "fixed rows": {"slice": 23, "full": 23},
    "transition, bodies half the time": {"slice": 25, "full": 236},
    "transition, bodies always": {"slice": 20, "full": 251},
    "located": {"slice": 26, "full": 260},
    "real layouts": {"slice": 63, "full": 435},
    "commented-out old Pinned section": {"slice": 39, "full": 671},
    "notes outside the managed block": {"slice": 63, "full": 432},
}

CLAUSES = ("clause_region_r", "clause_intact", "clause_refenced", "clause_guarded_pairing",
           "clause_moved_block", "clause_edited_in_place", "clause_no_leaving_credit")

# Clauses a population's construction reaches fewer than FLOOR times even in the full
# sweep, so the per-population floor does not apply; the whole-slice floor still does.
BELOW_FLOOR = {
    # R is the located Pinned span whenever the section before the change is located
    ("located", "clause_region_r"),
    # the first generator almost never leaves an aligned fenced line whose line before
    # was code and whose fence partners moved: 0 in 18,000 transition cases, 2 in 11,860 located
    ("transition, bodies half the time", "clause_guarded_pairing"),
    ("transition, bodies always", "clause_guarded_pairing"),
    ("located", "clause_guarded_pairing"),
}

FLOOR = 20


def _decisive(result) -> bool:
    if isinstance(result, tuple):
        return result[0] <= result[1]
    return bool(result)


_RESULTS = {}


def _run(name, trim=True, full=FULL):
    """(tally, clause calls, clause decisive results, items) for one population, cached."""
    key = (name, full, trim)
    if key in _RESULTS:
        return _RESULTS[key]
    items = POPULATIONS[name][1 if full else 0]()
    calls, decisive = collections.Counter(), collections.Counter()

    def spy(clause_name, original):
        def wrapper(*args, **kwargs):
            result = original(*args, **kwargs)
            calls[clause_name] += 1
            decisive[clause_name] += _decisive(result)
            return result
        return wrapper

    def decide(pre, post):
        return pin_growth.pin_cap_decision(pre if pre is not None else "", post, use_timer=False, trim=trim)

    with pytest.MonkeyPatch.context() as mp:
        for clause_name in CLAUSES:
            mp.setattr(pin_growth, clause_name, spy(clause_name, getattr(pin_growth, clause_name)))
        tally = H.evaluate(items, decide=decide)
    _RESULTS[key] = (tally, calls, decisive, items)
    return _RESULTS[key]


def test_the_fixed_rows_are_the_ported_rows():
    assert len(R.FIXED_ROWS) == 202
    assert R.digest([(r.pre, r.post) for r in R.FIXED_ROWS]) == FIXED_ROWS_DIGEST
    assert len({r.key for r in ALL_ROWS}) == len(ALL_ROWS)
    families = collections.Counter(r.family for r in R.FIXED_ROWS if r.family)
    assert sum(families.values()) == 18, families
    assert set(families) <= set(R.FAMILIES)
    assert all(r.label == R.GROWTH for r in ALL_ROWS if r.family)


def test_each_row_the_old_gate_decided_differently_keeps_its_new_verdict():
    """The fixed rows the old gate decided the other way, with its verdict
    named per row: each still gets the opposite verdict from the gate that
    replaced it, and each flip is a correction or a signed-off residual. A
    faithful row the old gate refused is allowed now; a growth row it allowed
    is refused now; a growth row it refused and the gate allows carries the
    family the user signed off."""
    from pin_caps_gate import gate_decision

    rows = {r.key: r for r in R.FIXED_ROWS}
    assert set(OLD_VERDICT) <= set(rows)
    kinds = collections.Counter()
    for key, old in OLD_VERDICT.items():
        row = rows[key]
        new = gate_decision(row.pre, "Write", {"content": row.post}).verdict
        new = "DENY" if new == "DENY" else "ALLOW"
        assert new != old, key
        if row.label == R.FAITHFUL:
            assert new == "ALLOW", key
            kinds["honest edit no longer refused"] += 1
        elif new == "DENY":
            kinds["growth now refused"] += 1
        else:
            assert row.family, key
            kinds["signed-off residual"] += 1
    assert kinds == {"honest edit no longer refused": 54, "growth now refused": 3, "signed-off residual": 10}


@pytest.mark.parametrize("population", list(POPULATIONS))
def test_the_counted_matcher_gives_the_stdlib_opcodes_on_every_population(population):
    """The rule's alignment copies CPython's find_longest_match to count its steps;
    on every population's slice it must give difflib's own opcodes, so a change to
    the stdlib on any interpreter turns this red."""
    import difflib

    from shared.claude_md_markers import parse

    for item in POPULATIONS[population][0]():
        a = [line.content for line in parse(item.pre or "").lines]
        b = [line.content for line in parse(item.post).lines]
        counted = pin_growth.CountedMatcher(a, b, pin_growth._Budget(10 ** 12)).get_opcodes()
        assert counted == difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes(), item.key


def _with_a_replaced_byte(pre, post):
    """Both texts with the last character of one unchanged pin-body line replaced
    by U+FFFD, which is what the gate reads for a byte that is not UTF-8. The
    line keeps its length, so no size charge moves. None when no body line is
    unchanged and unique on both sides."""
    for line in pre.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if (len(body) > 1 and body[0] not in "#<`~>-*|" and pre.count(line) == 1 and post.count(line) == 1):
            marked = body[:-1] + "\ufffd" + line[len(body):]
            return pre.replace(line, marked), post.replace(line, marked)
    return None


def test_a_byte_that_is_not_utf8_in_a_pin_body_changes_no_faithful_decision():
    checked = 0
    for row in R.FIXED_ROWS:
        if row.label != R.FAITHFUL or not row.pre:
            continue
        marked = _with_a_replaced_byte(row.pre, row.post)
        if marked is None:
            continue
        checked += 1
        plain = pin_growth.pin_cap_decision(row.pre, row.post, use_timer=False)
        replaced = pin_growth.pin_cap_decision(*marked, use_timer=False)
        assert (replaced.verdict, replaced.cause) == (plain.verdict, plain.cause), row.key
    assert checked >= 100


def test_undated_pins_are_never_refused_and_a_smuggled_heading_is_denied_on_the_count():
    for row in R.REMOVAL_ROWS:
        d = pin_growth.pin_cap_decision(row.pre, row.post, use_timer=False)
        if row.label == R.GROWTH and not row.family:
            assert (d.verdict, d.cause) == ("DENY", "count"), (row.key, d)
        else:
            assert d.verdict == "ALLOW", (row.key, d)


@pytest.mark.parametrize("name", list(POPULATIONS))
def test_no_population_has_an_over_block_and_under_blocks_stay_signed_off(name):
    tally, _calls, _decisive, _items = _run(name)
    print(f"{name} [{SCALE}]: {tally.summary()}")
    assert tally.counts["faithful"] and tally.counts["engaged growth"], tally.summary()
    assert tally.counts["DENY"] and tally.counts["ALLOW"], tally.summary()
    assert tally.over == [], f"faithful changes refused: {tally.over[:10]}"
    assert tally.divergent == [], f"the gate and the oracle disagree on locating Pinned: {tally.divergent[:10]}"
    assert tally.size_bound == [], f"honest changes reached the step budget: {tally.size_bound[:10]}"
    assert tally.unclassified == [], f"under-blocks in no signed-off family: {tally.unclassified[:10]}"
    recorded = RECORDED_UNDER[name][SCALE]
    assert recorded is not None, f"record the under-block count: {len(tally.under)}"
    assert len(tally.under) <= recorded, f"{len(tally.under)} under-blocks, recorded {recorded}: {tally.under[:10]}"


@pytest.mark.parametrize("name", list(POPULATIONS))
def test_every_population_reaches_every_clause_it_can(name):
    _tally, calls, decisive, _items = _run(name)
    # The slice reaches the rarest clauses only a few times in some populations (the
    # first generator calls clause_refenced 3-8 times per 1,000 cases); the floor of
    # decisive calls over the whole slice is in the next test.
    floor = FLOOR if FULL and name != "fixed rows" else 1
    short = {c: calls[c] for c in CLAUSES if (name, c) not in BELOW_FLOOR and calls[c] < floor}
    assert not short, f"{name}: clauses called fewer than {floor} times: {short}"
    grown = {c: calls[c] for c in CLAUSES if (name, c) in BELOW_FLOOR and calls[c] >= FLOOR}
    assert not grown, f"{name} now reaches {grown}; drop them from BELOW_FLOOR"


def test_the_slice_makes_every_clause_decide_and_the_leaving_line_never_earns_credit():
    calls, decisive = collections.Counter(), collections.Counter()
    for name in POPULATIONS:
        _tally, c, d, _items = _run(name)
        calls.update(c)
        decisive.update(d)
    for c in CLAUSES:
        if c == "clause_no_leaving_credit":
            assert calls[c] >= FLOOR and decisive[c] == 0, (calls[c], decisive[c])
        else:
            assert decisive[c] >= FLOOR, f"{c} decided {decisive[c]} times over every population"


@pytest.mark.parametrize("group,names", [
    ("the first generator, transition", ("transition, bodies half the time", "transition, bodies always")),
    ("the first generator, located", ("located",)),
    ("the second generator", ("real layouts",)),
])
def test_the_slice_reaches_every_operation_and_format_kind(group, names):
    kinds = collections.Counter(k for name in names for it in _run(name)[3] for k in it.kinds)
    rare = {k: n for k, n in kinds.items() if n < 50}
    assert len(kinds) >= 8 and not rare, (group, rare)


@pytest.mark.parametrize("name", list(POPULATIONS))
def test_trimming_changes_no_faithful_decision_and_only_tightens_growth(name):
    if not os.environ.get(TRIM_DIFFERENTIAL_ENV):
        pytest.skip(f"the trimming differential is a one-off run over the full sweep: set {TRIM_DIFFERENTIAL_ENV}=1")
    on, _c, _d, items = _run(name, trim=True, full=True)
    off = _run(name, trim=False, full=True)[0]
    labels = {it.key: it.label for it in items}
    changed = collections.Counter()
    for key, (verdict_on, _cause) in on.decisions.items():
        verdict_off = off.decisions[key][0]
        if verdict_on == verdict_off:
            continue
        if labels[key] == R.FAITHFUL:
            changed["faithful"] += 1
        elif verdict_on == "DENY":
            changed["growth, allowed untrimmed and denied trimmed"] += 1
        else:
            changed["growth, denied untrimmed and allowed trimmed"] += 1
    print(f"{name} [full] trimming differential: {dict(changed)}")
    assert changed["faithful"] == 0, changed
    assert changed["growth, denied untrimmed and allowed trimmed"] == 0, changed


def _digests_in_subprocess(hash_seed):
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); "
            "from fixtures.pin_growth.generators import determinism_digests; "
            "print(json.dumps(determinism_digests()))")
    env = {**os.environ, "PYTHONHASHSEED": str(hash_seed), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, "-c", code, str(TESTS)], capture_output=True, text=True,
                         env=env, cwd=str(TESTS), timeout=300)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_generators_give_the_committed_cases_on_every_interpreter_and_hash_seed():
    assert G.determinism_digests() == DETERMINISM
    for hash_seed in (0, 4242):
        assert _digests_in_subprocess(hash_seed) == DETERMINISM, hash_seed


def test_the_full_sweep_runs_under_ci():
    if not FULL:
        pytest.skip("the full sweep runs when CI is set; this run used the slice")
    for name in POPULATIONS:
        assert _run(name)[0].total >= 202


def _real_files():
    raw = os.environ.get(REAL_FILES_ENV, "")
    return [Path(p) for p in raw.split(os.pathsep) if p]


def _replay_fault(label, decision):
    """What is wrong with `decision` on a replayed change labelled `label`, or None."""
    refused = decision.verdict == "DENY"
    if label == R.FAITHFUL:
        return "refused" if refused else None
    if label == R.GROWTH:
        return None if refused else "allowed"
    if label == replay.SIZE:
        return None if refused and decision.cause == "size" else "not refused for the pin's size"
    if label == replay.SIZE_AFTER_SLIP:
        # Unlike SIZE, an allow passes. The text before held an unclosed fence, so the
        # gate compared with pins it could not see, and the growth it allows is the
        # under-block that uncertain text already carries. A refusal is still only
        # for the pin's size.
        return "refused, not for the pin's size" if refused and decision.cause != "size" else None
    raise ValueError(f"unknown label {label!r}")


def _decision(verdict, cause=None):
    return pin_growth.PinDecision(verdict, 13, 13, 0, cause, None)


def _row_id(value):
    if isinstance(value, pin_growth.PinDecision):
        return value.verdict + (f"/{value.cause}" if value.cause else "")
    return str(value)


@pytest.mark.parametrize("label,decision,passes", [
    (R.FAITHFUL, _decision("ALLOW"), True),
    (R.FAITHFUL, _decision("ALLOW_ADVISORY", "not_found"), True),
    (R.FAITHFUL, _decision("DENY", "size"), False),
    (replay.SIZE, _decision("DENY", "size"), True),
    (replay.SIZE, _decision("DENY", "count"), False),
    (replay.SIZE, _decision("ALLOW"), False),
    (replay.SIZE, _decision("ALLOW_ADVISORY", "size_bound"), False),
    (replay.SIZE_AFTER_SLIP, _decision("ALLOW"), True),
    (replay.SIZE_AFTER_SLIP, _decision("DENY", "size"), True),
    (replay.SIZE_AFTER_SLIP, _decision("DENY", "count"), False),
    (R.GROWTH, _decision("DENY", "count"), True),
    (R.GROWTH, _decision("ALLOW"), False),
], ids=_row_id)
def test_the_real_file_runner_passes_only_the_decision_each_label_expects(label, decision, passes):
    assert (_replay_fault(label, decision) is None) is passes


def _pin(body, comment="<!-- pinned: 2026-10-01 -->"):
    return f"{comment}\n### A pin\n{body}\n\n"


EXAMPLE = replay.snippet("```md", "```")
NEAR_CAP = "word " * 296 + "end"  # 1,483 characters


@pytest.mark.parametrize("before,after,grows", [
    (_pin(NEAR_CAP), replay.with_body_insert(_pin(NEAR_CAP), EXAMPLE), True),
    (_pin(NEAR_CAP[:1300]), replay.with_body_insert(_pin(NEAR_CAP[:1300]), EXAMPLE), False),
    (_pin(NEAR_CAP * 2), _pin((NEAR_CAP * 2).replace("e", "E", 1)), False),
    (_pin(NEAR_CAP, "<!-- pinned: 2026-10-01, pin-size-override: verbatim form -->"),
     replay.with_body_insert(_pin(NEAR_CAP, "<!-- pinned: 2026-10-01, pin-size-override: verbatim form -->"),
                             EXAMPLE), False),
    (_pin(NEAR_CAP), _pin(NEAR_CAP + "\n<!-- STALE: Last relevant 2026-01-01 -->"), False),
    (_pin(NEAR_CAP), _pin(NEAR_CAP + "\n```\n<!-- pinned: 2026-01-01 -->\n```"), True),
], ids=["an example takes it over", "an example keeps it under", "over but no larger", "size override",
        "a STALE marker is free", "a fenced comment is charged"])
def test_a_pin_counts_as_grown_past_the_cap_only_when_it_ends_over_it_and_larger(before, after, grows):
    """Over the cap and larger, counting as the size cap counts; a pin with a size
    override never counts, and a pin or STALE comment costs nothing outside a fence."""
    assert replay.grows_past_cap(before, after) is grows


# A pin just under the cap, one over it and a small one; the replay pads to 13.
SYNTHETIC = R.doc([("### Near the cap", [NEAR_CAP]), ("### Over the cap", [NEAR_CAP + " " + NEAR_CAP[:300]]),
                   ("### Small", ["A short body."])])


def test_the_replay_labels_agree_with_the_gate_on_a_synthetic_file():
    """The real-file runner's check on a file every run has: each replayed change
    through the real gate, judged as the runner judges it."""
    cases = [(label, pin_growth.pin_cap_decision(pre, post, use_timer=False))
             for _, pre, post, label in replay.replay(SYNTHETIC)]
    assert {label for label, _ in cases} >= {R.FAITHFUL, replay.SIZE, R.GROWTH}
    assert [(label, d.verdict, d.cause) for label, d in cases if _replay_fault(label, d)] == []
    # A size refusal recast as a refusal for count, or as an allow, must be flagged.
    sized = [d for label, d in cases if label == replay.SIZE]
    assert all(_replay_fault(replay.SIZE, d._replace(cause="count")) for d in sized)
    assert all(_replay_fault(replay.SIZE, d._replace(verdict="ALLOW", cause=None)) for d in sized)


def test_ordinary_edits_on_real_files_are_refused_only_for_pin_size():
    """Replays files a user keeps: a project's or a home CLAUDE.md. Leave out the
    parser corpus and census repro files: their pins sit inside fenced documents the
    replay's splitter reads as text, or their Pinned section is uncertain, so the
    labels the replay gives them do not hold."""
    files = _real_files()
    if not files:
        pytest.skip(f"set {REAL_FILES_ENV} to CLAUDE.md paths, separated by {os.pathsep!r}, to replay real files")
    fixtures = [str(p) for p in files if TESTS in p.resolve().parents]
    assert not fixtures, f"these are test fixtures, not files a user keeps: {fixtures}"
    report, failures = {}, []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        n = collections.Counter()
        for what, pre, post, label in replay.replay(text):
            d = pin_growth.pin_cap_decision(pre, post, use_timer=False)
            n[label, "refused" if d.verdict == "DENY" else "allowed"] += 1
            fault = _replay_fault(label, d)
            if fault:
                failures.append((str(path), what, label, fault, d.verdict, d.cause))
        if n:
            report[str(path)] = {f"{label}, {outcome}": count for (label, outcome), count in sorted(n.items())}
    print(f"real-file replay: {report}")
    assert report, "no file had a Pinned section with three pins"
    assert failures == [], failures[:20]
