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
and is skipped without it; it reads those files and keeps nothing.
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


ALL_ROWS = R.FIXED_ROWS + R.COMMENTED_ROWS + R.CONTENT_FENCE_ROWS + R.REMOVAL_ROWS + R.CEILING_ROWS


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


def test_ordinary_edits_on_real_files_are_never_refused():
    files = _real_files()
    if not files:
        pytest.skip(f"set {REAL_FILES_ENV} to CLAUDE.md paths, separated by {os.pathsep!r}, to replay real files")
    report, failures = {}, []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        n = collections.Counter()
        for what, pre, post, label in replay.replay(text):
            d = pin_growth.pin_cap_decision(pre, post, use_timer=False)
            n[label] += 1
            if (label == R.FAITHFUL) == (d.verdict == "DENY"):
                failures.append((str(path), what, label, d.verdict, d.cause))
        report[str(path)] = dict(n)
    print(f"real-file replay: {report}")
    assert any(report.values()), f"no file had a Pinned section with three pins: {report}"
    assert failures == [], failures[:20]
