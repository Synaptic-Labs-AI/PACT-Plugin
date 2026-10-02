#!/usr/bin/env python3
"""Census of real HANDOFF shapes, over both populations, as a standing instrument.

Run it:  python3 scripts/handoff_census.py            # every ~/.claude* root
         python3 scripts/handoff_census.py --home DIR # a fixture corpus

TWO POPULATIONS, DIFFERENT COUNTING UNITS, NOT INTERCHANGEABLE:

  JOURNAL   agent_handoff events in session-journal.jsonl under
            <root>/pact-sessions/. Unit: EVENTS. Append-only history, so
            absence here is strong evidence. Blind to anything that never
            emitted: the emit path is completion-keyed and rejects a handoff
            that is absent, empty, or not a dict, so those shapes are
            invisible BY THE INSTRUMENT rather than absent from the world.

  TASK      metadata.handoff in <root>/tasks/<team>/<id>.json. Unit: TASK
            FILES. No emission filter at all, so it is the only view of
            SUBMITTED shape -- but task files are reaped, so it is a RETAINED
            subset, not history, and absence here is weaker evidence.

WHY IT REPORTS RATES AND NEVER PINS COUNTS: the journal population includes
the handoffs written while measuring it. Every absolute number here is
as-of-run. Two runs an hour apart legitimately disagree.

AND NAME THE ROOTS BEFORE COMPARING. Rates differ several-fold between config
roots, so a combined figure hides the spread and a single-root run is a scoped
result, not a property of the world. The report prints per-root and combined;
quote the roots beside any number taken from it.

THE PREDICATES ARE IMPORTED AND CALLED, NEVER RESTATED. validate_handoff_schema
and the alias map come from the shipped module, so a re-run stays comparable
when the schema changes. A re-implemented predicate measures your reading of
the predicate, and it fails in the direction that MANUFACTURES findings.

ONE ADAPTATION, STATED BECAUSE IT BOUNDS THE RESULT: a journal event stores
`handoff` as the LIST OF KEY NAMES, not the handoff itself, so the journal arm
calls the shipped validator on a key-set reconstruction ({key: None}). That is
exact for a presence-only validator and would stop being exact the moment the
validator inspects VALUES. assert_presence_only() below fails loudly if that
ever changes, rather than letting the census silently drift into a wrong
answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

from shared.handoff_schema import (  # noqa: E402
    HANDOFF_CANONICAL_FIELDS,
    HANDOFF_LEGACY_ALIASES,
    HANDOFF_REQUIRED_FIELDS,
    validate_handoff_schema,
)


def assert_presence_only() -> None:
    """The key-set reconstruction is only faithful while the validator is
    presence-only. Same keys, different values, must give the same verdict."""
    keys = {f: None for f in HANDOFF_CANONICAL_FIELDS}
    values = {f: ["x"] for f in HANDOFF_CANONICAL_FIELDS}
    if validate_handoff_schema(keys) != validate_handoff_schema(values):
        raise SystemExit(
            "ABORT: validate_handoff_schema now inspects VALUES, so a journal "
            "event's key list can no longer stand in for the handoff. The "
            "journal arm of this census would report a wrong answer. Fix the "
            "reconstruction before trusting any number below."
        )


def config_roots(home: Path) -> list[Path]:
    return sorted(p for p in home.glob(".claude*") if p.is_dir())


def journal_handoffs(root: Path):
    """Yield {key: None} reconstructions of each agent_handoff event."""
    for journal in root.glob("pact-sessions/*/*/session-journal.jsonl"):
        try:
            lines = journal.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            if '"agent_handoff"' not in line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                continue
            if event.get("type") != "agent_handoff":
                continue
            keys = event.get("handoff")
            if isinstance(keys, list):
                yield {k: None for k in keys if isinstance(k, str)}
            else:
                yield keys  # not the expected shape -- count it, don't drop it


def task_handoffs(root: Path):
    """Yield metadata.handoff exactly as written, past no gate at all."""
    for task in root.glob("tasks/*/*.json"):
        try:
            data = json.loads(task.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        metadata = data.get("metadata")
        if isinstance(metadata, dict) and "handoff" in metadata:
            yield metadata["handoff"]


def tally(handoffs) -> dict:
    """Counts only. The caller turns them into rates beside their population."""
    out = {
        "n": 0, "fires": 0, "missing_canonical": 0, "no_reasoning_chain": 0,
        "not_a_dict": 0, "legacy_any": 0,
        **{f"legacy_{alias}": 0 for alias in HANDOFF_LEGACY_ALIASES},
    }
    for handoff in handoffs:
        out["n"] += 1
        if validate_handoff_schema(handoff) is not None:
            out["fires"] += 1
        if not isinstance(handoff, dict):
            out["not_a_dict"] += 1
            continue
        if any(f not in handoff for f in HANDOFF_CANONICAL_FIELDS):
            out["missing_canonical"] += 1
        if "reasoning_chain" not in handoff:
            out["no_reasoning_chain"] += 1
        found = [a for a in HANDOFF_LEGACY_ALIASES if a in handoff]
        if found:
            out["legacy_any"] += 1
            for alias in found:
                out[f"legacy_{alias}"] += 1
    return out


def rate(count: int, n: int) -> str:
    return f"{count:>6}  {count / n * 100:5.2f}%" if n else f"{count:>6}      --"


def report(name: str, unit: str, per_root: dict[str, dict]) -> dict:
    combined = {k: sum(t[k] for t in per_root.values()) for k in next(iter(per_root.values()))}
    print(f"\n{name}  (counting unit: {unit})")
    for root, t in per_root.items():
        print(f"  {root:<28} n={t['n']}")
    print(f"  {'COMBINED':<28} n={combined['n']}")
    print(f"\n  {'measure':<44}{'count':>8}   rate")
    rows = [
        (f"fires: missing >=1 of the {len(HANDOFF_REQUIRED_FIELDS)} REQUIRED "
         f"(shipped validator)", "fires"),
        (f"missing >=1 of the {len(HANDOFF_CANONICAL_FIELDS)} canonical "
         f"(shape, not the validator)", "missing_canonical"),
        ("reasoning_chain absent (recommended -- never fires)", "no_reasoning_chain"),
        ("present but NOT A DICT", "not_a_dict"),
        ("carries >=1 legacy spelling", "legacy_any"),
        *((f"  legacy `{a}` -> `{c}`", f"legacy_{a}") for a, c in HANDOFF_LEGACY_ALIASES.items()),
    ]
    for label, key in rows:
        print(f"  {label:<44}{rate(combined[key], combined['n'])}")
    return combined


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, default=Path.home(),
                        help="directory to scan for .claude* roots (default: $HOME)")
    args = parser.parse_args()

    assert_presence_only()
    roots = config_roots(args.home)
    if not roots:
        print(f"No .claude* roots under {args.home} -- nothing to census.")
        return 1

    print(f"Roots covered ({len(roots)}), and every rate below is scoped to them:")
    for root in roots:
        print(f"  {root}")

    journal = report("JOURNAL -- emitted/accepted handoffs", "events",
                     {r.name: tally(journal_handoffs(r)) for r in roots})
    task = report("TASK FILES -- submitted handoffs, no emission filter", "task files",
                  {r.name: tally(task_handoffs(r)) for r in roots})

    print("\nSTANDING TRIGGER -- the task-file instrument is the one entitled to answer.")
    if task["not_a_dict"]:
        print(f"  NOT-A-DICT HAS LEFT ZERO: {task['not_a_dict']} of {task['n']} submitted "
              f"handoffs. A refusing gate for the malformed shape was declined on a "
              f"measured zero; that premise no longer holds and the decision reopens.")
    else:
        print(f"  not-a-dict still 0 of {task['n']} submitted handoffs -- the premise "
              f"behind advisory-not-blocking still holds on this run's roots.")
    print(f"  legacy spellings in submitted handoffs: {task['legacy_any']} of {task['n']}. "
          f"The read-side alias map exists for the {journal['legacy_any']} in the "
          f"append-only journal, which never go away.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
