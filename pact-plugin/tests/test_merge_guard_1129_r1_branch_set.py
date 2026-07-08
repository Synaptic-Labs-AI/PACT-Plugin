"""
Location: pact-plugin/tests/test_merge_guard_1129_r1_branch_set.py
Summary: Verification tests for #1129 R1 — the additive multi-branch FORCE-delete
         mint widening. A faithful `git branch -D A B C` (>=2 positionals) was
         gated-on-read but UNMINTABLE (#1064 class): detect classifies it
         branch-delete and is_dangerous=True, but the scalar _extract_branch_name
         refuses >1 positional, so no target key minted -> the read side's
         _both_present_equal(None, None) REFUSED the faithful click (an
         over-block). R1 adds a canonical sorted+deduped+quote-stripped `branch_set`
         identity string (mirroring the mass_target #1062b precedent) so the
         multi-branch click mints and the read side matches it via set-EQUALITY.
Used by: pytest (merge-guard suite).

Scope: VERIFICATION (not the exhaustive/adversarial cert — that is the TEST phase's
job). Confirms: multi-branch mints+self-matches (every force spelling), the
single-branch scalar path is unchanged, cross-cardinality set-equality closes the
#1032 under-block (subset/superset/disjoint/scalar REFUSE; reorder MATCHES), lowercase
`-d`/`--delete` stays ungated, and the identity is a JSON-round-trip-safe STRING.

The FORCE flag and `git branch` verb are assembled at runtime (D = "-"+"D"), so this
file carries no raw force-delete literal — mirrors the sibling probe-harness convention
and keeps the file inert to any literal-scanning tool.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import pytest  # noqa: E402

from shared.merge_guard_common import (  # noqa: E402
    is_dangerous_command,
    detect_command_operation_type,
    extract_command_context,
    _extract_branch_delete_set,
)
from merge_guard_pre import _token_matches_command  # noqa: E402

# FORCE flag assembled at runtime — no raw `git branch -D` literal in the source.
_D = "-" + "D"


def _cmd(flags: str, names: list[str]) -> str:
    """Build a `git branch <flags> <names...>` force-delete command string."""
    return "git branch " + flags + " " + " ".join(names)


def _token_for(cmd: str) -> dict:
    """A minted token carrying the shared-SSOT context for `cmd`, JSON-round-tripped
    exactly as a real persisted token is (tuple<->list drift would surface here)."""
    return json.loads(json.dumps({"context": extract_command_context(cmd)}))


def _matches(token_cmd: str, exec_cmd: str) -> bool:
    return _token_matches_command(_token_for(token_cmd), exec_cmd)


# The five FORCE spellings detect already classifies as branch-delete (#1094).
_FORCE_SPELLINGS = [_D, "-Df", "-fD", "--delete --force", "--force --delete"]


@pytest.mark.parametrize("flags", _FORCE_SPELLINGS)
def test_multi_branch_force_delete_mints_branch_set_and_self_matches(flags):
    cmd = _cmd(flags, ["A", "B", "C"])
    ctx = extract_command_context(cmd)
    assert ctx.get("operation_type") == "branch-delete"
    # The additive key is populated with the canonical sorted identity, and the
    # scalar `branch` key stays ABSENT (mutual exclusivity).
    assert ctx.get("branch_set") == "A,B,C"
    assert "branch" not in ctx
    # The faithful click now mints+matches (the over-block cure).
    assert _matches(cmd, cmd) is True


@pytest.mark.parametrize("flags", [_D, "-Df", "--delete --force"])
def test_single_branch_scalar_path_unchanged(flags):
    cmd = _cmd(flags, ["feature"])
    ctx = extract_command_context(cmd)
    # Scalar path byte-identical: `branch` set, `branch_set` never populated.
    assert ctx.get("branch") == "feature"
    assert "branch_set" not in ctx
    assert _matches(cmd, cmd) is True


def test_set_equality_reorder_matches_but_cardinality_mismatch_refuses():
    tok = _cmd(_D, ["A", "B"])
    # Reorder MATCHES (order-independent canonical identity).
    assert _matches(tok, _cmd(_D, ["B", "A"])) is True
    # Superset / subset / disjoint all REFUSE (the #1032 under-block closed).
    assert _matches(tok, _cmd(_D, ["A", "B", "C"])) is False
    assert _matches(tok, _cmd(_D, ["A"])) is False
    assert _matches(tok, _cmd(_D, ["C", "D"])) is False


def test_scalar_token_does_not_authorize_a_set_command_and_vice_versa():
    # A single-branch token must not authorize a multi-branch delete (distinct key).
    assert _matches(_cmd(_D, ["only"]), _cmd(_D, ["A", "B"])) is False
    # And a set token must not authorize a single-branch delete.
    assert _matches(_cmd(_D, ["A", "B"]), _cmd(_D, ["only"])) is False


def test_duplicate_names_dedup_to_same_set():
    # Deleting {A, A, B} is the set {A, B}; a {A,B} token authorizes it.
    assert _matches(_cmd(_D, ["A", "B"]), _cmd(_D, ["A", "A", "B"])) is True


def test_slashed_branch_names_mint_and_match():
    cmd = _cmd(_D, ["feature/x", "feature/y"])
    assert extract_command_context(cmd).get("branch_set") == "feature/x,feature/y"
    assert _matches(cmd, cmd) is True


@pytest.mark.parametrize("flags", ["-d", "--delete"])
def test_lowercase_delete_stays_ungated(flags):
    # Non-force merged-branch delete is NOT gated at HEAD and must stay so —
    # R1 must not start gating/minting it.
    cmd = _cmd(flags, ["A", "B", "C"])
    assert is_dangerous_command(cmd) is False
    assert detect_command_operation_type(cmd) is None
    assert "branch_set" not in extract_command_context(cmd)


def test_branch_set_is_order_independent_canonical_string():
    a = _extract_branch_delete_set(_cmd(_D, ["A", "B", "C"]))
    b = _extract_branch_delete_set(_cmd(_D, ["C", "B", "A"]))
    assert a == b == "A,B,C"
    assert isinstance(a, str)  # STRING, never a tuple/list (JSON round-trip safety)
    # Fewer than two positionals -> None -> defers to the scalar `branch` path.
    assert _extract_branch_delete_set(_cmd(_D, ["solo"])) is None
