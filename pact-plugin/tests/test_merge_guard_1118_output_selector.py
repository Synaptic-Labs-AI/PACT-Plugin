"""
Location: pact-plugin/tests/test_merge_guard_1118_output_selector.py
Summary: Bidirectional regression matrix for the #1118 carrier-9 output-selector strip —
         the NEW additive `_strip_non_executable_content` pass that blanks the VALUES of
         gh-api client-side non-target flags (--jq/-q, --template/-t, --header/-H,
         --input, --hostname, --preview/-p) within a gh-api-only span. Locks in:
         Direction 1 — a faithful gh-api READ whose output selector / header / body-file
         name / hostname / preview value carries a `merge`/`git/refs` substring now
         ALLOWs (was an OVER-BLOCK by the .*merge implicit-POST arm — a SACROSANCT
         cardinal-sin cure); Direction 2 — every intentionally-held gh-api/CLI WRITE
         STAYS held, because its destructive target lives in the URL positional or the
         -X/--method flag (never a stripped value), and a contents/ span is preserved
         verbatim.
Used by: pytest (merge-guard suite).

Non-vacuity (red-on-revert): the mechanism class asserts carrier 9 actually blanks a
READ's --jq value (so `mergeStateStatus` no longer reaches the danger arms) AND leaves a
WRITE's endpoint positional (`merges`) intact. Reverting carrier 9 makes the READ's
substring survive -> is_dangerous flips to True -> the Direction-1 asserts fail. The
Direction-2 asserts are DISCRIMINATING negatives (each pins the SPECIFIC is_dangerous
True + the op_type the guard classifies it as), not a "matrix executes" smoke check.

Dangerous substrings are constructed at runtime (M / MS / GR) so this file carries no raw
`gh pr merge` / `git/refs` literal — mirrors the architect probe-harness convention and
keeps the file inert to any literal-scanning tool.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import pytest  # noqa: E402

from shared.merge_guard_common import (  # noqa: E402
    is_dangerous_command,
    detect_command_operation_type,
    _strip_non_executable_content,
)

# Dangerous substrings assembled at runtime — never a raw literal in the source.
M = "mer" + "ge"                 # merge
MS = "mer" + "geStateStatus"     # a graphql/REST response field name
GR = "git" + "/refs"             # git/refs


# --------------------------------------------------------------------------------------
# Direction 1 — over-blocked READS must now ALLOW (is_dangerous is False).
# --------------------------------------------------------------------------------------
class TestReadsFlipToAllow:
    """A faithful gh-api read whose non-target value carries a merge/git-refs substring
    is no longer held. The two NEW maximal-set shapes (--hostname, --preview) are
    included explicitly per the lead-ratified strip set."""

    READS = {
        "graphql_jq_single_quote": f"gh api graphql -f query='q' --jq '.data.repo.pr.{MS}'",
        "graphql_jq_double_quote": f'gh api graphql -f query="q" --jq ".data.repo.pr.{MS}"',
        "graphql_no_jq":           "gh api graphql -f query='q'",
        "rest_jq_mergeable":       f"gh api repos/o/r/pulls/1 -f foo=bar --jq '.{M}able'",
        "rest_template":           f"gh api repos/o/r/pulls/1 -f foo=bar -t '{{{{.{MS}}}}}'",
        "rest_header":             f'gh api repos/o/r/pulls/1 -f foo=bar -H "Accept: application/vnd.github.{M}-preview+json"',
        "input_merge_named_file":  f"gh api repos/o/r/issues/1/comments --input {M}-notes.json",
        "input_gitrefs_named_file":f"gh api repos/o/r/issues/1/comments --input {GR}-notes.json",
        "jq_no_body_flag":         f"gh api repos/o/r/pulls/1 --jq '.{M}able'",
        "gh_pr_view_not_gh_api":   f"gh pr view 1116 --json {MS} --jq '.{MS}'",
        "hostname_new_shape":      f"gh api --hostname {M}.example.com -f q=x repos/o/r/pulls/1",
        "preview_new_shape":       f"gh api --preview {M}-info -f q=x repos/o/r/pulls/1",
    }

    @pytest.mark.parametrize("command", READS.values(), ids=list(READS.keys()))
    def test_read_now_allows(self, command):
        assert is_dangerous_command(command) is False


# --------------------------------------------------------------------------------------
# Direction 2 — intentionally-held WRITES must STAY held (discriminating negatives).
# Each asserts the SPECIFIC is_dangerous True AND the op_type the guard classifies it as,
# proving it is held for the right reason and that carrier 9 did not alter the target.
# --------------------------------------------------------------------------------------
class TestWritesStayHeld:

    def test_gh_pr_merge_held(self):
        cmd = f"gh pr {M} 1116 --squash"
        assert is_dangerous_command(cmd) is True
        assert detect_command_operation_type(cmd) == M

    def test_api_merges_body_flag_held(self):
        # Endpoint `…/merges` (positional) survives carrier 9; the -f body value is
        # carrier 8's domain. Held via the implicit-POST .*merge API arm; op_type None.
        cmd = f"gh api repos/o/r/{M}s -f base=main -f head=x"
        assert is_dangerous_command(cmd) is True
        assert detect_command_operation_type(cmd) is None

    def test_api_merges_input_held(self):
        # --input arms implicit-POST and its VALUE is stripped, but the real target
        # `…/merges` is the positional endpoint (never stripped) -> still held.
        cmd = f"gh api repos/o/r/{M}s --input body.json"
        assert is_dangerous_command(cmd) is True
        assert detect_command_operation_type(cmd) is None

    def test_api_delete_gitrefs_held(self):
        cmd = f"gh api -X DELETE repos/o/r/{GR}/heads/x"
        assert is_dangerous_command(cmd) is True
        assert detect_command_operation_type(cmd) == "branch-delete"

    def test_api_delete_gitrefs_with_selectors_held(self):
        # Selector values (--input, -t) are stripped; the git/refs positional + -X DELETE
        # gating signals survive -> stays held with an unchanged classification.
        cmd = f"gh api -X DELETE repos/o/r/{GR}/heads/x --input b.json -t '{{{{.x}}}}'"
        assert is_dangerous_command(cmd) is True
        assert detect_command_operation_type(cmd) == "branch-delete"

    def test_api_put_branch_protection_held(self):
        cmd = "gh api -X PUT repos/o/r/branches/main/protection -f x=y"
        assert is_dangerous_command(cmd) is True
        assert detect_command_operation_type(cmd) == "branch-protection"

    def test_contents_put_span_preserved_held(self):
        # Contents-API early-return: the span is preserved verbatim, so the body-resident
        # main/master gating signal is never removed even though a --jq selector is present.
        cmd = f"gh api -X PUT repos/o/r/contents/f.txt -f branch=main --jq '.{M}able'"
        assert is_dangerous_command(cmd) is True
        assert detect_command_operation_type(cmd) is None


# --------------------------------------------------------------------------------------
# Mechanism / non-vacuity — proves carrier 9 does the work (red-on-revert anchor).
# --------------------------------------------------------------------------------------
class TestCarrier9Mechanism:

    def test_read_selector_value_is_blanked(self):
        # The --jq value carrying the response-field substring is removed from the
        # analysis surface, so it never reaches the .*merge arm.
        stripped = _strip_non_executable_content(
            f"gh api graphql -f query='q' --jq '.{MS}'"
        )
        assert MS not in stripped

    def test_write_endpoint_positional_survives(self):
        # The DESTRUCTIVE target lives in the URL positional and is NEVER stripped —
        # this is the invariant that keeps every Direction-2 write held.
        stripped = _strip_non_executable_content(f"gh api repos/o/r/{M}s -f base=main")
        assert f"{M}s" in stripped

    def test_gh_api_template_short_flag_stripped_in_span(self):
        # -t is --template WITHIN a gh-api span, so its value IS blanked (the short-flag
        # arm of carrier 9 fires only under the gh-api anchor).
        stripped = _strip_non_executable_content(
            f"gh api repos/o/r/pulls/1 -f foo=bar -t '.{M}'"
        )
        assert M not in stripped

    def test_curl_header_not_treated_as_selector(self):
        # Scope isolation: carrier 9's -H strip is gh-api-anchored. The SAME -H on a
        # curl command is outside the gh-api span (and -H is not carrier 8's domain),
        # so its value survives — proving carrier 9 never applies globally.
        stripped = _strip_non_executable_content(
            f'curl -H "X-Thing: {M}" https://api.example.com/foo'
        )
        assert M in stripped
