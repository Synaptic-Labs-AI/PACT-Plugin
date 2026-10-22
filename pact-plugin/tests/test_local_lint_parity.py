"""The local lint mirror must run CI's exact lint bytes.

WHAT THIS EXISTS TO STOP, stated as the defect rather than the rule
-------------------------------------------------------------------
``scripts/verify-local-lint-gate.sh`` is a COPY of the two ruff legs CI runs
before pytest (``.github/workflows/tests.yml``, "Import-hygiene lint"
step). Every copy rots: edit the workflow's flags, drop a leg, or reword the
script's lines, and the local gate keeps certifying an invocation nobody runs.
That green describes nothing — the same family as the escape this gate exists
to close, where every local lane certified the suite green while CI's lint
step was red from the first review commit: the gate you ran is not the gate
that decides.

This module byte-compares the script's ``ruff check`` lines against the
workflow's, so editing EITHER side alone reddens the merge gate itself. Leg 2
(the F821-only re-run) is DELIBERATE redundancy: it keeps F821 coverage
independent of leg 1's path list, so a dropped leg 2 in CI must redden here
too — the comparison pins BOTH lines, in order, not just one.

MEASURED, so the two-leg structure is not taken on faith: against a probe
carrying one unused import (F401), leg 2 alone passes clean — the F821-only
select cannot see an unused import — while leg 1 kills it. Against a probe
carrying one undefined name (F821), both legs kill it. Each leg is load-bearing
for a different finding class; neither subsumes the other's job, which is why
the parity comparison would rather go red on a MISSING line than accept a
subset.

PARSE FAILURE AND DRIFT ARE REPORTED SEPARATELY, ALWAYS
-------------------------------------------------------
Every parse below anchors its count before any comparison, with a distinct
message. The vacuous-pass hazard here is exact: two empty lists compare equal.
If either parser models the file wrongly (a refactor to a composite action, a
here-doc, a wrapper function), the counts go to zero FIRST and the failure
names the parser, not the parity. Convention followed from
``test_ci_collection_scope.py``, which parses the same workflow for its pytest
invocation — same line-oriented read, comments dropped first, same
skip-outside-a-checkout rule.
"""

from pathlib import Path

import pytest

# The gate script, relative to the repository root (the same root the workflow
# file is read from, so both arms of the comparison describe one tree).
_GATE_SCRIPT_REL = Path("scripts") / "verify-local-lint-gate.sh"


def _source_repo_root():
    """Repo root by a POSITIVE marker, or None when this is not a checkout.

    Deliberately NOT "did I find the workflow file?". "I cannot find it" and
    "it is wrong" must never produce the same outcome. The skip decision is
    made on an INDEPENDENT marker, which leaves the workflow's own absence
    free to be a FAILURE. Marker matches the convention in
    test_ci_collection_scope.py and test_memory_init.py.
    """
    for base in Path(__file__).resolve().parents:
        if (base / ".claude-plugin" / "marketplace.json").exists():
            return base
    return None


def _require_checkout():
    """Return (repo_root, workflow_text, script_text), or skip outside a checkout."""
    root = _source_repo_root()
    if root is None:
        pytest.skip(
            "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json above "
            f"{Path(__file__).resolve()}, so this is not a PACT source checkout. "
            "The expected case is an INSTALLED PLUGIN CACHE, which has no "
            ".github/ tree and no scripts/ tree by design. This skip is NO "
            "INFORMATION about local lint parity — never read it as a pass."
        )

    # INSIDE a checkout both artifacts are required, so absence is a FAILURE.
    workflow = root / ".github" / "workflows" / "tests.yml"
    assert workflow.exists(), (
        f"source checkout detected at {root} (marketplace.json present) but "
        f"{workflow} is missing. The lint-parity guard cannot run. This is a "
        f"FAILURE, not a skip: inside a checkout the CI workflow is required."
    )
    script = root / _GATE_SCRIPT_REL
    assert script.exists(), (
        f"source checkout detected at {root} but the local lint gate "
        f"{script} is missing. The mirror and the workflow it mirrors live in "
        f"the same tree; one without the other is a half-configured checkout, "
        f"not a parity question."
    )
    return root, workflow.read_text(encoding="utf-8"), script.read_text(encoding="utf-8")


def _ruff_check_lines(text):
    """Every non-comment line that invokes `ruff check`, stripped, in order.

    Line-oriented on purpose, matching the collection-scope parser in this
    directory: both files discuss ruff in prose (the workflow's dependency
    notes, the script's header and refusal advice), and a parser that read
    comments would find `ruff check` strings in text that invokes nothing.
    Comments are dropped FIRST for that reason.

    The script's refusal message advises the exact CI legs as plain `ruff
    check ...` lines inside `printf` arguments; those lines begin with
    `printf`, not `ruff check`, so this parse cannot mistake advice for
    invocation. The two real invocation lines are bare in both files.
    """
    found = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if line.startswith("ruff check"):
            found.append(line)
    return found


def _anchored_legs(lines, source_name):
    """Exactly two legs, or a named parse failure — never a vacuous subset."""
    assert lines, (
        f"PARSE FAILURE, not parity drift: no `ruff check` line could be read "
        f"from {source_name}. The legs are known present — this module exists "
        f"because CI runs them and the local mirror copies them — so an empty "
        f"read means the parser is wrong (a refactor to a here-doc, a wrapper "
        f"variable, or a composite action defeats a line-oriented read). "
        f"RE-POINT THE PARSER — do NOT relax the byte-compare below, which "
        f"passes vacuously over two empty lists."
    )
    assert len(lines) == 2, (
        f"PARSE FAILURE, not parity drift: {len(lines)} `ruff check` lines were "
        f"read from {source_name} ({lines}). The lint contract is TWO legs — "
        f"F401+F821 then F821 alone — and leg 2 is deliberate redundancy this "
        f"module pins on purpose. If the leg count genuinely changed, the "
        f"workflow and the script must change TOGETHER and this message is how "
        f"you found out; update both and this anchor, never one."
    )
    return lines


def test_script_legs_byte_equal_workflow_legs():
    """The mirror's invocation lines equal the workflow's, byte for byte."""
    _, workflow_text, script_text = _require_checkout()
    workflow_legs = _anchored_legs(_ruff_check_lines(workflow_text), "the workflow")
    script_legs = _anchored_legs(_ruff_check_lines(script_text), "the gate script")
    assert script_legs == workflow_legs, (
        f"LOCAL LINT DRIFT: the gate script's legs and the workflow's lint "
        f"lines no longer byte-match.\n  workflow: {workflow_legs}\n"
        f"  script  : {script_legs}\n"
        f"A local green over different bytes than CI runs certifies nothing "
        f"about CI — that is the escape this gate exists to close. Reconcile "
        f"the two sides in one change; do not reword one to match the other "
        f"without deciding WHICH invocation is correct."
    )


def test_script_pins_repo_root_internally():
    """The script cd's to its own repo root before the root-relative legs.

    The CI lines are root-relative (`ruff check ... pact-plugin/`). A mirror
    that ran them from the caller's directory would silently re-narrow the
    sweep whenever it was invoked from inside pact-plugin/ — the
    top-level-file gap this gate exists to cover would reopen. The `cd` is
    structural, so it is pinned structurally, not by invocation.
    """
    _, _, script_text = _require_checkout()
    assert 'cd "$repo_root"' in script_text, (
        "the gate script no longer cd's to its resolved repository root before "
        "running the root-relative legs. Without the internal cd, the sweep "
        "depends on where the caller happens to stand — restore it."
    )


def test_script_refuses_when_ruff_absent():
    """A missing linter must refuse loudly, not read as a pass or a bare 127.

    The fail-closed contract has three mechanical parts: the availability
    probe, the operator-readable refusal, and the distinct exit. The probe and
    the message are pinned here as bytes; the exit code is exercised by the
    red-trial runbook (a PATH-stripped live run), because an exit code is a
    behavior, not a string.
    """
    _, _, script_text = _require_checkout()
    assert "command -v ruff" in script_text, (
        "the gate script no longer probes for ruff before linting. Without "
        "the probe a missing ruff surfaces as an opaque failure (or worse, a "
        "swallowed one) instead of a refusal — restore it."
    )
    assert "GATE REFUSED: ruff is not available" in script_text, (
        "the gate script's ruff-absent refusal message is gone or reworded "
        "past recognition. The refusal is the operator-facing half of "
        "fail-closed: it must name the problem and the remedy."
    )
