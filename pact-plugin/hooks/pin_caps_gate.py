#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/pin_caps_gate.py
Summary: PreToolUse hook that refuses an Edit or Write of the project CLAUDE.md
         only when it adds pins past the cap, grows a pin past the size cap
         without an override, or carries an invalid size override.
Used by: hooks.json PreToolUse with matcher "Edit|Write" (registered after
         pin_staleness_gate.py so stale-block deny takes precedence).

The verdict comes from `shared.pin_growth.pin_cap_decision`, the one decision
the gate and the Bash report share: it compares the file before the change
with the file after it. This hook builds the text after (`gate_decision`),
checks the size override of every pin the change adds or edits, and prints
the verdict.

Gate fires when ALL hold:
  1. Tool is Edit or Write (enforced by hooks.json matcher)
  2. `claude_md_manager.gate_frame` names the frame: the lead, a PACT
     specialist type, or any frame whose session belongs to a PACT team
     (in-process teammates and Agent-tool subagents share the lead's session).
     A plain session and a non-PACT --agent session are not gated. A team
     member's count denial tells it not to change CLAUDE.md by any route and
     to tell the team-lead, instead of naming the pin command.
  3. `claude_md_manager.gate_target` returns a target: the project CLAUDE.md
     the resolver returns once the change exists, so a Write that creates it
     is gated too. The text before is the file the resolver returns now, or
     empty when none resolves.

FAIL-OPEN. An over-block is the worst outcome, so every failure allows:
  - a module-load failure prints a systemMessage saying the gate is not
    checking pin caps, and exits 0;
  - any exception while deciding is recorded with failure_log.append_failure
    and allows;
  - the decision itself allows with an advisory when the Pinned section cannot
    be located, when the check runs past its step budget or timer, or when it
    fails;
  - an Edit or a Write over a CLAUDE.md that exists but cannot be read is
    allowed with an advisory: without the text before, the change cannot be
    compared with it.

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: a deny (hookSpecificOutput.permissionDecision, exit 2), an allow with
        an advisory (hookSpecificOutput.additionalContext, no permissionDecision),
        or {"suppressOutput": true}.
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_allow BEFORE wrapped imports) ─
import bisect
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple, NoReturn, Optional

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _emit_load_failure_allow(stage: str, error: BaseException) -> NoReturn:
    """Stdlib-only fail-open for a module-load failure: a broken install must
    not refuse every Edit and Write of every file, so it allows them and says
    on each one that pin caps are not being checked."""
    message = (
        f"PACT pin_caps_gate could not load and is not checking pin caps "
        f"({stage}): {type(error).__name__}: {error}"
    )
    print(json.dumps({"systemMessage": message}))
    print(message, file=sys.stderr)
    sys.exit(0)


# ─── fail-open wrapper on cross-package imports ────────────────────────────
try:
    import shared.pact_context as pact_context
    from shared.claude_md_manager import MEMBER_PIN_INSTRUCTION
    from shared.failure_log import append_failure
    from pin_caps import OVERRIDE_RATIONALE_MAX
except BaseException as _module_load_error:  # noqa: BLE001 — fail-open catch-all
    _emit_load_failure_allow("module imports", _module_load_error)

_GATED_TOOLS = frozenset({"Edit", "Write"})

_FAIL_BASELINE_READ = "pin_caps_gate_baseline_read"
_FAIL_DECISION = "pin_caps_gate_decision"
_FAIL_UNEXPECTED = "pin_caps_gate_unexpected"


def _validate_override_rationale(rationale: Optional[str]) -> Optional[str]:
    """Return a deny-reason string if the rationale is invalid, else None.

    A present-but-invalid rationale denies. A None rationale (no override
    line at all) returns None — the SIZE predicate will still catch a
    too-large pin body downstream.
    """
    if rationale is None:
        return None
    if not rationale:
        return "Override rationale is empty — provide a non-empty reason."
    if len(rationale) > OVERRIDE_RATIONALE_MAX:
        return (
            f"Override rationale is {len(rationale)} chars "
            f"(max: {OVERRIDE_RATIONALE_MAX}). Shorten it."
        )
    return None


def _collapsed(content: str) -> str:
    """`content` with each whitespace run made one space and the ends
    stripped, so a re-indent, a trailing space or a line-ending rewrite leaves
    it the same."""
    return " ".join(content.split())


def _trimmed(key: str) -> str:
    """`key` less its trailing run of non-word characters, the run
    `override_rationale_text` drops before a reconfirmation."""
    from pin_caps import _WORD_CHAR

    end = len(key)
    while end and not _WORD_CHAR.match(key, end - 1):
        end -= 1
    return key[:end]


class _Row(NamedTuple):
    """An override comment as `_invalid_override` compares it."""

    rationale: str  # what `override_rationale_text` reads
    key: str  # that rationale, whitespace collapsed
    # The text written between the override field and a reconfirmation after
    # it, whitespace collapsed; None when no reconfirmation follows the field.
    written: Optional[str]
    pin: Optional[int]  # the index of the pin it heads in a located Pinned section

    @property
    def valid(self) -> bool:
        return _validate_override_rationale(self.rationale) is None


def _row(content: str, rationale: str, pin: Optional[int]) -> _Row:
    """The `_Row` for the override comment `content`, whose rationale
    `override_rationale_text` read as `rationale`."""
    from pin_caps import _OVERRIDE_FIELD, RECONFIRMED_DATE_RE

    field = _OVERRIDE_FIELD.search(content)
    reconfirm = RECONFIRMED_DATE_RE.search(content, field.end()) if field else None
    written = _collapsed(content[field.end():reconfirm.start()]) if field and reconfirm else None
    return _Row(rationale, _collapsed(rationale), written, pin)


def _rationales(doc, spans: list) -> list:
    """The override comments in the parsed document `doc` as `_Row`s, one for
    each row that holds only an override comment, of any kind. A fenced row,
    or one past an unclosed fence, is read as if it stood alone. Only a pin's
    own comment row (the first row of its span in `spans`) names its pin."""
    from pin_caps import override_rationale_text
    from shared.claude_md_markers import Kind, parse

    pins = {first: index for index, (first, _end) in enumerate(spans)}
    found = []
    for line in doc.lines:
        if "<!--" not in line.content or "-->" not in line.content:
            continue
        if line.kind is Kind.PROSE:
            rationale = override_rationale_text(doc, line.row)
            pin = pins.get(line.row)
        else:
            rationale = override_rationale_text(parse(line.content.strip()), 0)
            pin = None
        if rationale is not None:
            found.append(_row(line.content, rationale, pin))
    return found


def _kept(new: _Row, old: _Row) -> bool:
    """Whether a matched new row keeps the old row's text as written: the
    same read, or a reconfirmation moved or removed (the match already needs
    the new read to begin the old written text), or a reconfirmation added
    after the old rationale left whole."""
    return (new.key == old.key or old.written is not None
            or (new.written is not None and new.written.startswith(old.key)))


def _stands_for(new: _Row, old: _Row) -> bool:
    """Whether the new row may stand for the old one under the rule `_Edges`
    lists: the same read, or the reconfirmation's cut within one group of
    reads that keep a word character."""
    if new.key == old.key:
        return True
    group = _trimmed(new.key)
    if not group or group != _trimmed(old.key):
        return False
    return new.written is not None or (old.written is not None and old.written.startswith(new.key))


class _Edges:
    """Which old rows a new row may stand for, without listing every pair.

    Rows group by their read less its trailing non-word characters; edges
    never leave a group. In it, a new row may stand for an old row with the
    same read and written text (rank 0), with the same read (rank 1), or, by
    the reconfirmation's cut (rank 2), for any old row when the new row is
    reconfirmed, and otherwise for a reconfirmed old row whose written text
    starts with the new read. A read with no word character trims to nothing
    and has no cut edge: it stands only for an old row with the same read.
    Each kind of edge is one list of old rows in their order, shared by every
    new row with that read or group: so a reconfirmed new row stands for any
    old row in its group."""

    def __init__(self, old: list):
        self.trimmed = {}  # read -> trimmed read
        self.groups = defaultdict(list)  # trimmed read -> old rows
        self.same = defaultdict(list)  # read -> old rows
        self.exact = defaultdict(list)  # (read, written) -> old rows
        self.identical = defaultdict(list)  # (uncollapsed read, written) -> old rows
        written = defaultdict(list)  # trimmed read -> (written, old row)
        for j, row in enumerate(old):
            group = self._group(row.key)
            self.groups[group].append(j)
            self.same[row.key].append(j)
            self.exact[row.key, row.written].append(j)
            self.identical[row.rationale, row.written].append(j)
            if row.written is not None:
                written[group].append((row.written, j))
        self.written = {group: sorted(rows) for group, rows in written.items()}
        self.prefixed = {}

    def _group(self, read: str) -> str:
        if read not in self.trimmed:
            self.trimmed[read] = _trimmed(read)
        return self.trimmed[read]

    def lists(self, row: _Row, rank: Optional[int] = None) -> list:
        """(name, old rows) for `row`'s edges of `rank`, or of every rank in
        rank order; the name is shared by every new row with the same list."""
        group = self._group(row.key)
        out = []
        if rank in (None, 0):
            out.append((("exact", row.key, row.written), self.exact.get((row.key, row.written), ())))
        if rank in (None, 1):
            out.append((("same", row.key), self.same.get(row.key, ())))
        if rank in (None, 2) and group:
            if row.written is not None:
                out.append((("group", group), self.groups.get(group, ())))
            else:
                out.append((("prefix", row.key), self._prefixed(group, row.key)))
        return out

    def _prefixed(self, group: str, read: str) -> list:
        """The reconfirmed old rows of `group` whose written text starts with
        `read`, in row order. Kept once worked out: an old row's written text
        has one prefix per character, so all of them together are no larger
        than the text."""
        if read not in self.prefixed:
            rows = self.written.get(group, ())
            found, k = [], bisect.bisect_left(rows, (read,))
            while k < len(rows) and rows[k][0].startswith(read):
                found.append(rows[k][1])
                k += 1
            self.prefixed[read] = sorted(found)
        return self.prefixed[read]


def _augment(start: int, owner: dict, seen: set, scan, free, budget) -> bool:
    """Kuhn's step: find an alternating path from new row `start` to an old row
    no new row holds, skipping old rows in `seen`, and flip it in `owner` (old
    row -> new row). Each row on the path first takes a free old row of any
    rank (`free`) and only then descends through held ones in rank order
    (`scan`), so a free row one step away is never reached through a long
    chain. Iterative, so a long path cannot exhaust the stack. Each old row
    looked at spends a step of `budget`."""
    frames, via = [(start, scan(start))], []
    while frames:
        new, choices = frames[-1]
        old = free(new)
        if old is None:
            for old in choices:
                budget.spend(1)
                if old not in seen:
                    break
            else:
                frames.pop()
                if via:
                    via.pop()
                continue
            seen.add(old)
            via.append(old)
            frames.append((owner[old], scan(owner[old])))
            continue
        via.append(old)
        for (row, _choices), held in zip(frames, via):
            owner[held] = row
        return True
    return False


def _match(new: list, old: list, own: Optional[dict] = None, ahead: Optional[dict] = None) -> dict:
    """A maximum matching of the new rows to the old rows `_Edges` allows, as
    {new index: old index}.

    Each row in `ahead` (`_contest`: new index -> old index) takes its old
    row first, so no exact copy can take the row a continuation's edited row
    stands for. Next a new row identical to an old one (the same read,
    whitespace and all, and the same written text) is that row and takes it,
    its own pin's old row (`own`: new index -> old index) before any other
    identical one; no other row may claim it, so a copy of an old override,
    however close, is checked unless an old row is left for it, and an
    untouched pin cannot take the old row of an edited pin whose override
    reads the same. Next each new row takes the old row of its own pin when
    `_stands_for` allows it, and no other row may claim that either, so a
    copy on another pin cannot take an edited original's row. Then invalid
    new rows are matched first, and Kuhn's algorithm never unmatches a row,
    so as many invalid rows as any matching can cover are covered. The rows
    first take free old rows rank by rank, every row the same row first, then
    the same read, then the cut, so no row takes by a looser edge an old row
    another row holds by a closer one; a row left over searches (`_augment`).

    Every walk over an edge list is shared by the rows that share the list:
    an old row once held is never freed, so a search for a free row resumes
    where the last one stopped, and within one search an old row seen once is
    skipped by every row. The work spends steps of the pin-growth rule's
    budget (`STEP_BUDGET`) and past it raises `SizeBound`."""
    from shared import pin_growth

    budget = pin_growth._Budget(pin_growth.STEP_BUDGET)
    edges = _Edges(old)
    owner, cursors, passes, seen = {}, {}, {}, set()

    def first_free(i, rank):
        for name, rows in edges.lists(new[i], rank):
            k = cursors.get(name, 0)
            while k < len(rows) and rows[k] in owner:
                budget.spend(1)
                k += 1
            cursors[name] = k
            if k < len(rows):
                return rows[k]
        return None

    def scan(i):
        for name, rows in edges.lists(new[i]):
            if name not in passes:
                passes[name] = iter(rows)
            for j in passes[name]:
                if j in frozen:
                    budget.spend(1)
                else:
                    yield j

    held, frozen = set(), set()
    ahead = ahead or {}
    for i, j in [*ahead.items(), *(own or {}).items()]:
        if i not in held and j not in owner and (
                ahead.get(i) == j or (new[i].rationale, new[i].written) == (old[j].rationale, old[j].written)):
            owner[j] = i
            held.add(i)
            frozen.add(j)
    for i, row in enumerate(new):
        if i in held:
            continue
        rows = edges.identical.get((row.rationale, row.written), ())
        k = cursors.get(("identical", row.rationale, row.written), 0)
        while k < len(rows) and rows[k] in owner:
            budget.spend(1)
            k += 1
        if k < len(rows):
            owner[rows[k]] = i
            held.add(i)
            frozen.add(rows[k])
            k += 1
        cursors["identical", row.rationale, row.written] = k
    for i, j in (own or {}).items():
        if i not in held and j not in owner and _stands_for(new[i], old[j]):
            owner[j] = i
            held.add(i)
            frozen.add(j)
    for valid in (False, True):
        offered = [i for i, row in enumerate(new) if row.valid is valid]
        for rank in (0, 1, 2):
            for i in offered:
                if i not in held:
                    free = first_free(i, rank)
                    if free is not None:
                        owner[free] = i
                        held.add(i)
        for i in offered:
            # A failed search changes nothing, so what it saw stays
            # unreachable, and its passes stay valid, until a search succeeds.
            if i not in held and _augment(i, owner, seen, scan, lambda row: first_free(row, None), budget):
                held.add(i)
                seen.clear()
                passes.clear()
        seen.clear()
        passes.clear()
    return {i: j for j, i in owner.items()}


def _spans(doc, located) -> list:
    """The pin spans of the Pinned section `located` in `doc` (`pin_spans`),
    or none when it is not located."""
    from shared.claude_md_markers import State
    from shared.pin_growth import pin_spans

    if located.state is not State.FOUND:
        return []
    heading, last = located.spans[0]
    return pin_spans(doc, (heading + 1, last))


def _spans_are_pins(doc, located, spans: list) -> bool:
    """Whether `section_pins` reads as many pins as `spans` lists, so a pin
    index names one pin to both."""
    from pin_caps import section_pins

    return bool(spans) and len(section_pins(doc, located)) == len(spans)


def _pin_texts(doc, spans: list) -> list:
    """(heading, body) for each pin span: the heading row whitespace-collapsed
    and casefolded, and the rows under it as written."""
    from shared.claude_md_markers import Kind
    from shared.pin_growth import _is_head

    texts = []
    for first, end in spans:
        row = next(j for j in range(first, end + 1)
                   if doc.lines[j].kind is Kind.PROSE and _is_head(doc.lines[j].content))
        texts.append((" ".join(doc.lines[row].content.split()).casefold(),
                      " ".join(doc.lines[j].content for j in range(row + 1, end + 1))))
    return texts


def _word_pairs(text: str) -> Counter:
    """`text` as a multiset of consecutive word pairs; one word is itself."""
    words = text.split()
    if len(words) < 2:
        return Counter(words)
    return Counter(f"{left} {right}" for left, right in zip(words, words[1:]))


def _share(mine: Counter, theirs: Counter, whole: int) -> float:
    """The fraction of a before pin's word pairs `theirs` (`whole` in all)
    that an after pin's word pairs `mine` keep."""
    return sum((mine & theirs).values()) / whole if whole else 0


def _sources(before: list, after: list, wanted, offered) -> dict:
    """{after pin index: before pin index} for the after pins `wanted` and
    the before pins `offered` (those carrying override rows), given each
    pin's (heading, body) from `_pin_texts`: the pin each continues.

    A pair's share is the fraction of the before pin's word pairs that the
    after pin keeps, so the pin keeping more of the old text continues it and
    a short fragment of it does not. Every pair is scored: best, the heading
    partner with a share of at least half; then a pin with that share alone;
    then the heading partner alone; within each, the larger share first. A
    heading can be reused or renumbered by the change, so content outranks
    it. Each before pin goes to the after pin with its best score, and each
    after pin keeps the best before pin it won; where two tie, neither is
    given, so a copy as good as the original falls back to the cross-pin
    matching. A pin with that share alone is compared only with before pins
    no heading partner reaches it with, since it cannot outrank one. Each
    word pair compared, and each pin a word pair's index entry adds, spends
    a step of the pin-growth rule's budget (`STEP_BUDGET`), and past it
    raises `SizeBound`."""
    from pin_caps import _DESCENT_SHARE
    from shared import pin_growth

    budget = pin_growth._Budget(pin_growth.STEP_BUDGET)
    by_heading = defaultdict(list)
    for p in offered:
        by_heading[before[p][0]].append(p)
    pairs = {p: _word_pairs(before[p][1]) for p in offered}
    wholes = {p: sum(pairs[p].values()) for p in offered}
    mine = {q: _word_pairs(after[q][1]) for q in wanted}

    def share(q, p):
        budget.spend(len(mine[q]))
        return _share(mine[q], pairs[p], wholes[p])

    bids = defaultdict(list)  # before pin -> [(score, after pin)]
    for q in wanted:
        for p in by_heading.get(after[q][0], ()):
            budget.spend(1)
            held = share(q, p)
            bids[p].append(((0 if held >= _DESCENT_SHARE else 2, -held), q))
    holders = defaultdict(set)  # word pair -> before pins no partner reaches half with
    for p in offered:
        if all(score[0] for score, _q in bids.get(p, ())):
            for pair in pairs[p]:
                holders[pair].add(p)
    for q in wanted:
        near = set()
        for pair in mine[q]:
            found = holders.get(pair, ())
            budget.spend(1 + len(found))
            near.update(found)
        for p in near.difference(by_heading.get(after[q][0], ())):
            held = share(q, p)
            if held >= _DESCENT_SHARE:
                bids[p].append(((1, -held), q))
    won = defaultdict(list)  # after pin -> [(score, before pin)]
    for p, offers in bids.items():
        best = min(score for score, _q in offers)
        winners = [q for score, q in offers if score == best]
        if len(winners) == 1:
            won[winners[0]].append((best, p))
    source = {}
    for q, claims in won.items():
        best = min(score for score, _p in claims)
        kept = [p for score, p in claims if score == best]
        if len(kept) == 1:
            source[q] = kept[0]
    return source


def _contest(new: list, old: list, source: dict, texts: list, old_texts: list) -> dict:
    """{new index: old index}: for each old pin's row that two or more new
    rows stand for, one of them edited, the edited row of the pin that
    continues it, which takes it ahead of every other. The pins whose rows
    stand for the old row compete: the one keeping the most of the old pin's
    word pairs (`_share`, given each pin's (heading, body) in `texts` and
    `old_texts`) continues it; on an equal share, the pin `source` maps to
    the old pin does, unless another leader's body reads the same
    (whitespace collapsed), which is a tie that names none. A pin `source`
    maps to another old pin continues none here. Where no new row copies the
    old row exactly, the pin `source` maps to it takes the old row next
    anyway, so only another continuation is given a place, and an old row
    whose mapped pin keeps all of its word pairs is not contested. The pins
    that may lead are those holding a word pair of the old pin's body and
    the pin `source` maps to it; where none of them keeps any, every row
    standing for the old row leads. Each row and word pair examined spends a
    step of the pin-growth rule's budget (`STEP_BUDGET`), and past it raises
    `SizeBound`."""
    from shared import pin_growth

    exact, row_at = defaultdict(list), {}
    for i, row in enumerate(new):
        exact[row.rationale, row.written].append(i)
        row_at[row.pin] = i
    pinned = [j for j, row in enumerate(old) if row.pin is not None]
    if not pinned:
        return {}
    budget = pin_growth._Budget(pin_growth.STEP_BUDGET)
    pairs, holders = {}, defaultdict(set)  # word pair -> after pins with an override row holding it

    def word_pairs(side, k, text):
        if (side, k) not in pairs:
            pairs[side, k] = _word_pairs(text)
            budget.spend(len(pairs[side, k]))
        return pairs[side, k]

    for q in row_at:
        for pair in word_pairs("after", q, texts[q][1]):
            holders[pair].add(q)

    def kept(q, p):
        mine, theirs = word_pairs("after", q, texts[q][1]), word_pairs("before", p, old_texts[p][1])
        budget.spend(len(mine))
        return _share(mine, theirs, sum(theirs.values()))

    def body(q):
        return " ".join(texts[q][1].split())

    mapped = {p: q for q, p in source.items()}
    ahead = {}
    for j in pinned:
        row, p = old[j], old[j].pin
        same = (row.rationale, row.written)
        copied = same in exact
        mine = row_at.get(mapped.get(p))
        if not copied and mine is not None and _stands_for(new[mine], row) and kept(new[mine].pin, p) == 1:
            continue
        near = {mapped[p]} & row_at.keys() if p in mapped else set()
        for pair in word_pairs("before", p, old_texts[p][1]):
            found = holders.get(pair, ())
            budget.spend(1 + len(found))
            near.update(found)
        claims = [row_at[q] for q in near if _stands_for(new[row_at[q]], row)]
        if all((new[i].rationale, new[i].written) == same for i in claims):
            continue
        shares = {i: kept(new[i].pin, p) for i in claims}
        top = max(shares.values())
        leaders = [i for i in claims if shares[i] == top]
        if top == 0:
            if not copied:
                continue
            budget.spend(len(new))
            leaders = [i for i in range(len(new)) if _stands_for(new[i], row)]
        if len(leaders) > 1:
            named = [i for i in leaders if new[i].pin == mapped.get(p)]
            if not named or any(body(new[i].pin) == body(new[named[0]].pin) for i in leaders if i not in named):
                continue
            leaders = named
        i = leaders[0]
        if (new[i].rationale, new[i].written) == same or source.get(new[i].pin, p) != p:
            continue
        if not copied:
            if new[i].pin == mapped.get(p):
                continue
            if len(claims) < 2:
                budget.spend(len(new))
                if not any(k != i and _stands_for(new[k], row) for k in range(len(new))):
                    continue
        ahead.setdefault(i, j)
    return ahead


def _invalid_override(before: str, after: str) -> tuple[Optional[str], tuple]:
    """The deny reason for an invalid size override on a pin the change adds
    or edits, else None, and the pins whose override the size check must count
    as none: (indices in the Pinned section before, indices after).

    Read from the fence-aware parse of the text after, on the one row the
    parser attributes to each pin as its comment (the first row `pin_spans`
    gives it), through `pin_caps.override_rationale_text`, so the gate and the
    parser read the same override. An override-shaped row elsewhere in a body,
    or in a fenced example, is body text for both, and a row holding a line
    break `str.splitlines` breaks at is never attributed.

    An override is checked only when the change added it or edited its
    rationale: each one is matched to an override comment on a row of the
    text before, of any kind (prose, code or past an unclosed fence), and a
    matched one is not checked. Each old row stands for at most one row
    after, so the change cannot raise the number of invalid rationales, and
    an invalid one grants no size exemption either way. A new row may stand
    for an old row with the same rationale, whitespace collapsed; the rest of
    the row, the date, is not compared. So an untouched old invalid override
    is not refused for an edit elsewhere in its pin, a new date on its row, a
    rename, a move, a line-ending rewrite or a fence-closing Write.

    The reader cuts a reconfirmation written after the override field from
    the rationale together with the run of non-word characters before it, so
    adding, removing or moving one changes the rationale's trailing
    punctuation without the curator touching it. So a new row may also stand
    for an old row whose rationale is the same once both lose their trailing
    run of non-word characters, where it or the old row carries such a
    reconfirmation. A reconfirmed rationale reads no longer than the old one
    it matches; one without a reconfirmation matches only an old reconfirmed
    row whose written text it begins, so it holds nothing the curator did not
    write. Where neither row carries one, as when only the punctuation is
    edited, the match stays exact.

    The rows are matched as a whole (`_match`), each pin's row first to its
    own pin's old row (`_sources`, or `_contest` where an exact copy competes
    with an edited row), so one pin cannot take the old row its neighbour
    needs. Where a matched old row was a pin's comment, the new row keeps its
    text (`_kept`), the two rationales disagree on validity, and the two pins
    continue each other (or neither continues anything else), both
    pins count as having no valid override, as each would if read alike, so a
    reconfirmation that moves neither grants an exemption nor takes one away.
    Only when the pins `pin_spans` lists before and after are the ones
    `section_pins` reads.
    """
    from pin_caps import override_rationale_text
    from shared.claude_md_markers import State, parse
    from shared.pin_growth import locate_pinned

    doc = parse(after)
    located = locate_pinned(doc)
    if located.state is not State.FOUND:
        return None, ((), ())
    spans = _spans(doc, located)
    new = []
    for index, (first, _end) in enumerate(spans):
        rationale = override_rationale_text(doc, first)
        if rationale is not None:
            new.append(_row(doc.lines[first].content, rationale, index))
    before_doc = parse(before)
    before_located = locate_pinned(before_doc)
    before_spans = _spans(before_doc, before_located)
    old = _rationales(before_doc, before_spans)
    row_of = {row.pin: j for j, row in enumerate(old) if row.pin is not None}
    source, own, ahead = {}, {}, {}
    if new and row_of:
        texts, old_texts = _pin_texts(doc, spans), _pin_texts(before_doc, before_spans)
        source = _sources(old_texts, texts, sorted({row.pin for row in new}), sorted(row_of))
        own = {i: row_of[source[row.pin]] for i, row in enumerate(new) if row.pin in source}
        ahead = _contest(new, old, source, texts, old_texts)
    matched = _match(new, old, own, ahead)
    for i, row in enumerate(new):
        reason = None if i in matched else _validate_override_rationale(row.rationale)
        if reason is not None:
            return reason, ((), ())
    # A carry lands only on a pair of pins that continue each other (as mapped,
    # or as the contest decided), or that neither continues anything else.
    continued = set(source.values())
    revoked = [(old[j].pin, new[i].pin) for i, j in matched.items()
               if old[j].pin is not None and old[j].valid != new[i].valid and _kept(new[i], old[j])
               and (source.get(new[i].pin) == old[j].pin or ahead.get(i) == j
                    or (new[i].pin not in source and old[j].pin not in continued))]
    if not revoked or not (_spans_are_pins(doc, located, spans)
                           and _spans_are_pins(before_doc, before_located, before_spans)):
        return None, ((), ())
    return None, (frozenset(p for p, _ in revoked), frozenset(q for _, q in revoked))


def gate_decision(before: str, tool_name: str, tool_input: dict):
    """The verdict on an Edit or Write of the project CLAUDE.md whose text is
    `before` ("" when no readable file). Pure: no I/O.

    The text after is `shared.edit_simulation.simulate`'s, the edit the tool
    will make: an empty old_string creates or fills a blank file, and a curly
    quote matches its straight form. A malformed payload raises TypeError,
    which the caller's fail-open catch allows. A change that leaves the text
    as it is allows. An invalid size override on a pin the change adds or
    edits denies, cause "override". Otherwise `pin_cap_decision` decides.
    Both checks run under the pin cap's one timer, and the override check
    under its step budget as well; either stopping them allows, as the pin
    cap check does when it stops early, never refuses.
    """
    from shared.edit_simulation import simulate
    from shared.pin_growth import PinDecision, SizeBound, pin_cap_decision, run_with_timer, stopped_early

    after = simulate(before, tool_name, tool_input)
    if after is None:
        raise TypeError(f"{tool_name} tool_input is not a well-formed {tool_name} payload")
    if after == before:
        return PinDecision("ALLOW", 0, 0, None, None, None)

    def decide():
        invalid, revoked = _invalid_override(before, after)
        if invalid is not None:
            return PinDecision("DENY", 0, 0, None, "override", f"Pin cap violation (invalid override): {invalid}")
        return pin_cap_decision(before, after, use_timer=False, revoked=revoked)

    try:
        return run_with_timer(decide)
    except SizeBound as bound:
        return stopped_early(bound)


def _read_baseline(claude_md_path: Path) -> tuple[Optional[str], Optional[str]]:
    """Read the CLAUDE.md before the change, without the writers' lock, with
    undecodable bytes replaced.

    The gate only reads, so it takes no lock: a writer holding it would delay
    the gate up to the lock timeout and then turn the read into a failure,
    which leaves the change unchecked.

    Returns (content, error_classification). On success: (text, None).
    On I/O failure: (None, _FAIL_BASELINE_READ), and the caller allows the
    Edit or Write with an advisory that the pin cap was not checked.
    """
    try:
        return claude_md_path.read_text(encoding="utf-8", errors="replace"), None
    except OSError:
        return None, _FAIL_BASELINE_READ


def _unreadable_decision(claude_md_path: Path, tool_name: str, tool_input: dict):
    """The decision when the project CLAUDE.md exists but cannot be read: an
    Edit or a Write is allowed with an advisory, because the change cannot be
    compared with a text the hook cannot read."""
    from shared.pin_growth import PinDecision

    return PinDecision(
        "ALLOW_ADVISORY", 0, 0, None, "unreadable",
        f"PACT could not read {claude_md_path}, so the pin cap was not checked for this {tool_name}.",
    )


def _member_reason(decision):
    """A team member's denial: a count denial keeps its violation line and asks
    the team-lead instead of naming the pin command; others are unchanged."""
    if decision.verdict != "DENY" or decision.cause != "count" or not decision.reason:
        return decision
    violation = decision.reason.split(". ", 1)[0].rstrip(".")
    return decision._replace(reason=f"{violation}. {MEMBER_PIN_INSTRUCTION}")


def _gate(input_data: dict):
    """The decision for a frame the gate checks, or None for one it does not."""
    tool_name = input_data.get("tool_name", "")
    if tool_name not in _GATED_TOOLS:
        return None

    tool_input = input_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return None

    # The basename test comes first, so no frame or resolver work runs for any
    # other file.
    file_path = tool_input.get("file_path", "")
    if not isinstance(file_path, str) or Path(file_path).name.casefold() != "claude.md":
        return None

    pact_context.init(input_data)
    from shared.claude_md_manager import gate_frame, gate_target

    frame = gate_frame(input_data)
    if frame is None:
        return None

    target = gate_target(file_path)
    if target is None:
        return None
    decision = _decide(target, tool_name, tool_input)
    return _member_reason(decision) if frame == "member" else decision


def _decide(target, tool_name: str, tool_input: dict):
    """The decision on a change to `target`, compared with the file that
    resolves before it, or with empty text when none does."""
    before = ""
    if target.before is not None:
        before, read_error = _read_baseline(target.before)
        if before is None:
            append_failure(
                classification=read_error or _FAIL_BASELINE_READ,
                error=f"read failed for {target.before}",
                source=tool_name,
            )
            return _unreadable_decision(target.before, tool_name, tool_input)

    decision = gate_decision(before, tool_name, tool_input)
    if decision.cause == "error":
        append_failure(classification=_FAIL_DECISION, error=decision.reason or "", source=tool_name)
    return decision


def _check_tool_allowed(input_data: dict) -> Optional[str]:
    """The deny reason when the gate refuses the call, else None."""
    decision = _gate(input_data)
    if decision is not None and decision.verdict == "DENY":
        return decision.reason
    return None


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        decision = _gate(input_data)
    except Exception as exc:  # noqa: BLE001 — SACROSANCT fail-open
        try:
            append_failure(
                classification=_FAIL_UNEXPECTED,
                error=f"{type(exc).__name__}: {exc}",
                source=str(input_data.get("tool_name", "")),
            )
        except Exception:  # noqa: BLE001 — logging must never cascade
            pass
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if decision is not None and decision.verdict == "DENY":
        # hookEventName is required by the harness; missing it silently fails open
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.reason,
            }
        }))
        sys.exit(2)

    if decision is not None and decision.verdict == "ALLOW_ADVISORY" and decision.reason:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": decision.reason,
            }
        }))
        sys.exit(0)

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
