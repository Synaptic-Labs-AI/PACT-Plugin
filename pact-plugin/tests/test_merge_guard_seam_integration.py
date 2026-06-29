"""S1 — non-mocked post→pre token-seam integration test (== the convention lint).

This is the ONE test that exercises the real on-disk authorization token as a
genuine integration SEAM: a real mint writes a token file to a temp dir, and the
real read (`check_merge_authorization`) reads it back — no mock or monkeypatch of
the seam itself. The only injected value is `token_dir`, a PRODUCTION parameter
both functions already expose for redirection (NOT a stub of the resolution under
test). It is also the convention LINT: the approval bundle is built from the
documented merge-approval template in `commands/peer-review.md`, read from the
ACTUAL rendered source (no hardcoded copy), so a drift between the documented
template and the guard's behavior turns this test RED — keeping doc and behavior
in lockstep (architect §7 S1; the lint and the seam e2e are ONE test, not two).

Why a non-mocked seam test (the mock-hid-the-seam trap): a fully-mocked suite can
pass while the post→pre token handoff is broken in live operation, because the
one broken seam is the seam every mocked test stubs. The merge guards are a
SACROSANCT security control, so the canonical post→pre handoff over a real token
file is exercised here for real.

Levels (architect §7):
  L-A  function-level real seam — extract_command_context → write_token(tmp) →
       check_merge_authorization(tmp). token_dir injected (production param).
       Only STABLE entry points (no net-new symbol), so the file collects even
       under a source-revert counter-test.
  L-B  in-process main()→main() — patched TOKEN_DIR + sys.stdin, subprocess
       count = 0 — drives the full bundle mint + the JSON-envelope boundary L-A
       cannot see.

Mapped from COVERED_L2 in test_hook_infra_classifier.py for BOTH merge_guard_pre
and merge_guard_post (the on-disk token IS their integration seam).

NON-VACUITY (revert-cardinality gate). The L-B conforming bundles place the
command ONLY in the affirmative option's description (the mandatory template
location); the question carries no PR number or command, so the OLD question-only
mint cannot recover the target. A SOURCE-ONLY revert of C3 (merge_guard_post.py
mint rewire, 5e3d2436) therefore re-mints an op-only / target-less token, the
read side denies the command, and the L-B conforming cases flip RED. Verified
cardinality: `git checkout 5e3d2436^ -- pact-plugin/hooks/merge_guard_post.py`
then this file → {3 failed} (the 2 `test_main_to_main_conforming_authorizes`
params + `test_main_to_main_echo_authorizes`). The L-A write→read cases stay
green under that revert (they bypass the bundle mint) — their non-vacuity is the
non-mocked-seam property itself: a regression in the post→pre token handoff
turns them RED where a mocked-seam test would not.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from merge_guard_post import main as _post_main, write_token
from merge_guard_pre import check_merge_authorization, main as _pre_main
from shared.merge_guard_common import extract_command_context

# ─────────────────── parse the documented template (anti-drift) ──────────────

_PEER_REVIEW = Path(__file__).parent.parent / "commands" / "peer-review.md"
# Capture an option line:  - **"<label>"** (description: "<desc>") → ...
_OPTION_RE = re.compile(r'\*\*"(?P<label>[^"]+)"\*\*\s*\(description:\s*"(?P<desc>[^"]+)"\)')
# A backtick-quoted command literal inside a description.
_CMD_SPAN_RE = re.compile(r"`([^`]+)`")
# The runtime placeholder for the PR number in the documented template.
_PLACEHOLDER = "<N>"


def _load_documented_merge_option() -> tuple[str, str, str]:
    """Return (label, description_template, command_template) for the affirmative
    merge option, parsed from the LIVE peer-review.md. The affirmative option is
    the one whose description embeds a `gh pr merge ...` command literal. Raises
    (failing the test) if the documented convention is absent — that IS the
    anti-drift alarm."""
    text = _PEER_REVIEW.read_text(encoding="utf-8")
    for m in _OPTION_RE.finditer(text):
        desc = m.group("desc")
        for span in _CMD_SPAN_RE.findall(desc):
            if span.startswith("gh pr merge"):
                return m.group("label"), desc, span
    raise AssertionError(
        "peer-review.md no longer documents a merge option whose description "
        "embeds a `gh pr merge <N>` command literal — the merge-approval "
        "convention drifted from the guard's contract (S1 anti-drift alarm)."
    )


# Parsed ONCE at import; the values flow into every case below.
_LABEL, _DESC_TEMPLATE, _CMD_TEMPLATE = _load_documented_merge_option()


def _concrete(template: str, pr: str) -> str:
    return template.replace(_PLACEHOLDER, pr)


def _conforming_bundle(pr: str) -> tuple[list, dict]:
    """The documented convention: the affirmative option carries the command
    literal in its description (the MANDATORY template location). The question is
    a plain merge prompt with NO PR number and NO command — so the target is
    recoverable ONLY from the selected option, which is what makes the L-B
    conforming cases revert-provable against C3."""
    description = _concrete(_DESC_TEMPLATE, pr)
    question = "Merge this pull request now? The reviewers have signed off — proceed?"
    options = [
        {"label": _LABEL, "description": description},
        {"label": "Continue reviewing", "description": "Keep reviewing"},
        {"label": "Pause work for now", "description": "Save and pause"},
    ]
    return [{"question": question, "options": options, "multiSelect": False}], {question: _LABEL}


def _echo_bundle(pr: str) -> tuple[list, dict]:
    """Template-conforming ECHO: the command appears in BOTH the question and the
    selected option (the same PR) — the >=2-raw-regions / 1-distinct-pair case
    the SACROSANCT multiplicity gate must MINT (counting occurrences would refuse
    100% of conforming approvals)."""
    command = _concrete(_CMD_TEMPLATE, pr)
    description = _concrete(_DESC_TEMPLATE, pr)
    question = f"Merge this PR now? On approval the team runs {command}"
    options = [{"label": _LABEL, "description": description}]
    return [{"question": question, "options": options, "multiSelect": False}], {question: _LABEL}


# ─────────────────────── convention LINT (doc presence) ──────────────────────

class TestConventionDocumented:
    def test_merge_option_documents_command_literal(self):
        """The convention is present: an affirmative option whose description
        embeds `gh pr merge <N>`."""
        assert _LABEL
        assert "gh pr merge" in _CMD_TEMPLATE
        assert _PLACEHOLDER in _CMD_TEMPLATE

    def test_placeholder_not_a_hardcoded_pr_number(self):
        """`<N>` is a placeholder, not a concrete PR number (planning-artifact /
        placeholder convention — the doc must not pin a real number)."""
        assert not re.search(r"gh pr merge\s+\d", _CMD_TEMPLATE)


# ───────────────────────── L-A — function-level write→read seam ───────────────

class TestSeamFunctionLevel:
    @pytest.mark.parametrize("pr", ["252", "1029", "1234"])
    def test_documented_command_round_trips(self, tmp_path, pr):
        """The command extracted from the documented template, written to a real
        token file, is authorized when run — the real mint→read handoff over a
        real on-disk token (token_dir injected, nothing stubbed)."""
        command = _concrete(_CMD_TEMPLATE, pr)
        context = extract_command_context(command)
        assert context.get("operation_type") == "merge"
        assert context.get("pr_number") == pr

        token_path = write_token(context, token_dir=tmp_path)
        assert token_path is not None
        assert Path(token_path).exists()

        assert check_merge_authorization(command, token_dir=tmp_path) is None

    def test_token_does_not_authorize_a_different_pr(self, tmp_path):
        """Binding integrity over the real seam: a token minted for one PR denies
        a different PR."""
        context = extract_command_context(_concrete(_CMD_TEMPLATE, "252"))
        write_token(context, token_dir=tmp_path)
        assert check_merge_authorization("gh pr merge 999", token_dir=tmp_path) is not None

    def test_no_token_holds_the_merge(self, tmp_path):
        """The fail-closed default over the real seam: with no token on disk the
        merge is held."""
        assert check_merge_authorization("gh pr merge 252", token_dir=tmp_path) is not None


# ───────────────────────── L-B — in-process main()→main() ─────────────────────

class TestSeamMainEntryPoints:
    def _post_envelope(self, questions, answers) -> str:
        return json.dumps({
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": questions},
            "tool_response": {"answers": answers},
            "session_id": "test-seam",
        })

    def _pre_envelope(self, command) -> str:
        return json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": "test-seam",
        })

    def _run_post(self, questions, answers, tmp) -> int:
        with patch("merge_guard_post.TOKEN_DIR", tmp), \
             patch("sys.stdin", io.StringIO(self._post_envelope(questions, answers))):
            with pytest.raises(SystemExit) as exc:
                _post_main()
        return exc.value.code

    def _run_pre(self, command, tmp) -> tuple[int, str]:
        out = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp), \
             patch("sys.stdin", io.StringIO(self._pre_envelope(command))), \
             patch("sys.stdout", out):
            with pytest.raises(SystemExit) as exc:
                _pre_main()
        return exc.value.code, out.getvalue()

    @pytest.mark.parametrize("pr", ["252", "1029"])
    def test_main_to_main_conforming_authorizes(self, tmp_path, pr):
        """Real stdin envelope → post.main() mints a token file from the SELECTED
        option's command → pre.main() reads it and allows the matching command
        (exit 0, no deny JSON). Subprocess count = 0. Revert-provable against C3
        (the old question-only mint cannot reach the option's command)."""
        questions, answers = _conforming_bundle(pr)
        assert self._run_post(questions, answers, tmp_path) == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 1

        code, out = self._run_pre(_concrete(_CMD_TEMPLATE, pr), tmp_path)
        assert code == 0
        assert '"permissionDecision": "deny"' not in out

    def test_main_to_main_echo_authorizes(self, tmp_path):
        """Template echo (command in BOTH question and option, same PR) → ONE
        distinct pair → mints → authorizes through the full envelope seam."""
        questions, answers = _echo_bundle("777")
        assert self._run_post(questions, answers, tmp_path) == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 1
        code, out = self._run_pre("gh pr merge 777", tmp_path)
        assert code == 0
        assert '"permissionDecision": "deny"' not in out

    def test_main_to_main_divergent_question_mints_clicked_residual(self, tmp_path):
        """RESIDUAL-CANARY (floor): the clicked option names PR 252, the QUESTION
        names a divergent PR 253. The minimal floor scans ONLY the clicked option for
        multiplicity (the question prose is never cross-checked), so it MINTS the
        clicked 252 and IGNORES the question's 253 — a documented wrong-TARGET
        ACCEPTED RESIDUAL. Through the full main()→main() seam: a token mints,
        `gh pr merge 252` authorizes, and the divergent `gh pr merge 253` is NOT
        authorized (only the clicked command binds).
        NON-VACUITY: re-widen the Step-3 multiplicity scan to include the question
        text → two distinct pairs → refuse → 0 tokens → RED."""
        description = _concrete(_DESC_TEMPLATE, "252")
        question = f"Merge this PR now? On approval the team runs {_concrete(_CMD_TEMPLATE, '253')}"
        options = [{"label": _LABEL, "description": description}]
        assert self._run_post(
            [{"question": question, "options": options, "multiSelect": False}],
            {question: _LABEL}, tmp_path,
        ) == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 1
        # the divergent QUESTION pr is not authorized (never minted — only the click)
        code253, out253 = self._run_pre("gh pr merge 253", tmp_path)
        assert code253 == 2
        assert '"permissionDecision": "deny"' in out253
        # the clicked option's pr authorizes
        code252, out252 = self._run_pre("gh pr merge 252", tmp_path)
        assert code252 == 0
        assert '"permissionDecision": "deny"' not in out252

    def test_main_to_main_non_conforming_blocks(self, tmp_path):
        """A non-conforming approval through main() (no command in question or
        option) mints no token → pre.main() blocks the merge (exit 2, deny)."""
        question = "Ready?"
        options = [{"label": "Yes, merge", "description": "No command here"}]
        assert self._run_post(
            [{"question": question, "options": options, "multiSelect": False}],
            {question: "Yes, merge"}, tmp_path,
        ) == 0
        assert list(tmp_path.glob("merge-authorized-*")) == []

        code, out = self._run_pre("gh pr merge 252", tmp_path)
        assert code == 2
        assert '"permissionDecision": "deny"' in out


# ════════════════════════════════════════════════════════════════════════════
# #1052 — self-teaching OBSERVER-STYLE no-mint advisory. When an AskUserQuestion
# approval is issued for a COMMAND-BEARING bundle but mints NO token, main emits a
# `hookSpecificOutput.additionalContext` advisory (exit 0) that NAMES the failing
# gate and points to the canonical peer-review.md template. It is OBSERVER-ONLY:
# it never writes a token, so the pre-hook still DENIES — authorization isolation.
# Benign, non-destructive AskUserQuestions stay SILENT (suppressOutput), because
# the advisory is gated by _bundle_has_command (this hook matches EVERY
# AskUserQuestion). Driven over the real main() stdin→stdout seam, no token mock.
# ════════════════════════════════════════════════════════════════════════════

class TestNoMintAdvisory1052:
    def _post_envelope(self, questions, answers) -> str:
        return json.dumps({
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": questions},
            "tool_response": {"answers": answers},
            "session_id": "test-advisory",
        })

    def _run_post_capture(self, questions, answers, tmp) -> tuple[int, str]:
        """Drive merge_guard_post.main() over a real stdin envelope, capturing
        STDOUT (the advisory / suppressOutput payload). TOKEN_DIR redirected to a
        real temp dir; nothing else stubbed."""
        out = io.StringIO()
        with patch("merge_guard_post.TOKEN_DIR", tmp), \
             patch("sys.stdin", io.StringIO(self._post_envelope(questions, answers))), \
             patch("sys.stdout", out):
            with pytest.raises(SystemExit) as exc:
                _post_main()
        return exc.value.code, out.getvalue()

    def _pre_envelope(self, command) -> str:
        return json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": "test-advisory",
        })

    def _run_pre(self, command, tmp) -> tuple[int, str]:
        out = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp), \
             patch("sys.stdin", io.StringIO(self._pre_envelope(command))), \
             patch("sys.stdout", out):
            with pytest.raises(SystemExit) as exc:
                _pre_main()
        return exc.value.code, out.getvalue()

    @staticmethod
    def _additional_context(stdout: str) -> str | None:
        """Extract hookSpecificOutput.additionalContext from a captured stdout
        payload, or None when the payload is a plain suppressOutput (silent)."""
        payload = json.loads(stdout)
        return payload.get("hookSpecificOutput", {}).get("additionalContext")

    # (reason_code, bundle, answers, distinctive_diagnostic_substring). Each bundle
    # is COMMAND-BEARING and refuses for exactly the named gate; the substring is an
    # INDEPENDENT literal (NOT derived from _REFUSAL_DIAGNOSTICS) so the oracle does
    # not share the implementation's wording source — a wording regression on that
    # specific reason flips only its own row.
    # The floor's LIVE refusal reasons (post-#22). The removed decline-veto +
    # label-op-consistency reasons (decline_veto / label_op_mismatch) are gone — those
    # are now ACCEPTED RESIDUALS that MINT (see the auth_symmetry residual-canaries),
    # so there is no no-mint advisory for them. option_not_anchored is UNREACHABLE
    # under the floor (Step 3 scans only the clicked options, so anchoring holds by
    # construction — a command in the question with a generic clicked option yields
    # `no_command`, not option_not_anchored). Two NEW floor reasons are added:
    # not_dangerous (GAP1 is_dangerous write-gate) and compound_command (GAP5).
    _REASON_CASES = [
        ("no_options",
         [{"question": "Run `gh pr merge 42`?"}],
         {"Run `gh pr merge 42`?": "yes please"},
         "no clickable options"),
        ("label_mismatch",
         [{"question": "Merge now?",
           "options": [{"label": "Yes, merge", "description": "Run `gh pr merge 42`"}],
           "multiSelect": False}],
         {"Merge now?": "Some answer matching no label"},
         "did not exactly match an option label"),
        ("no_command",
         [{"question": "Merge `gh pr merge 42`?",
           "options": [{"label": "Yes", "description": "Confirmed, proceed"}],
           "multiSelect": False}],
         {"Merge `gh pr merge 42`?": "Yes"},
         "no recognized merge/close/force-push/branch-delete command"),
        ("multiple_commands",
         [{"question": "Do these?",
           "options": [{"label": "Yes", "description": "Run `gh pr merge 42` and `gh pr merge 99`"}],
           "multiSelect": False}],
         {"Do these?": "Yes"},
         "more than one distinct"),
        ("not_dangerous",
         [{"question": "Close it?",
           "options": [{"label": "Yes", "description": "Run `gh pr close 7`"}],
           "multiSelect": False}],
         {"Close it?": "Yes"},
         "is not one the merge guard gates"),
        # Honest-mistake ≥2 model: compound_command fires only for >=2 DESTRUCTIVE
        # legs. (Was `gh pr merge 42 && rm -rf /` — now ONE destructive leg, `rm` is
        # outside the gh/git surface, so no longer compound; that gh-op-&&-rm case is
        # the lead's held accept-vs-catch decision. Use a genuine TWO-destructive
        # compound here so the advisory's compound_command reason is still exercised.)
        ("compound_command",
         [{"question": "Run it?",
           "options": [{"label": "Yes", "description": "Run `gh pr merge 42 && gh pr close 7 --delete-branch`"}],
           "multiSelect": False}],
         {"Run it?": "Yes"},
         "chained multiple commands with a shell separator"),
    ]

    @pytest.mark.parametrize(
        "reason, questions, answers, diagnostic",
        _REASON_CASES, ids=[c[0] for c in _REASON_CASES],
    )
    def test_advisory_fires_with_reason_diagnostic(
        self, tmp_path, reason, questions, answers, diagnostic
    ):
        """A command-bearing bundle that refuses for `reason` → main emits the
        observer advisory naming THAT gate + pointing at peer-review.md, mints NO
        token, exits 0.

        NON-VACUITY (broaden/remove-mutation): delete the no-mint advisory else-branch
        in `main` (the `elif refusal_reason is not None and _bundle_has_command(...)`
        block) → stdout becomes a plain suppressOutput → additionalContext is None →
        every row RED. Verified {6 failed} (one per live floor reason)."""
        code, out = self._run_post_capture(questions, answers, tmp_path)
        assert code == 0
        # No token minted on any refusal path.
        assert list(tmp_path.glob("merge-authorized-*")) == []
        ctx = self._additional_context(out)
        assert ctx is not None, f"advisory did not fire for {reason}"
        # Names the specific failing gate (independent literal, not SSOT-derived).
        assert diagnostic in ctx, f"advisory for {reason} missing its diagnostic"
        # Points at the canonical approval template.
        assert "peer-review.md" in ctx

    def test_benign_nondestructive_question_stays_silent(self, tmp_path):
        """A benign, NON-destructive AskUserQuestion (no command anywhere) that does
        not mint stays SILENT — plain suppressOutput, no additionalContext, no token.
        The advisory is gated by _bundle_has_command so this hook (which matches
        EVERY AskUserQuestion) does not spam ordinary questions.

        BROADEN-MUTATION proof: drop the `and _bundle_has_command(questions)` guard on
        main's advisory else-branch (fire on any refusal). Then this benign question
        emits an advisory → additionalContext becomes non-None → this silence
        assertion flips RED. Verified {1 fail}."""
        questions = [{
            "question": "Which environment should we deploy to?",
            "options": [{"label": "Staging", "description": "Deploy to staging"},
                        {"label": "Production", "description": "Deploy to production"}],
            "multiSelect": False,
        }]
        code, out = self._run_post_capture(questions, {"Which environment should we deploy to?": "Staging"}, tmp_path)
        assert code == 0
        assert list(tmp_path.glob("merge-authorized-*")) == []
        assert self._additional_context(out) is None, (
            "benign non-destructive question wrongly emitted the no-mint advisory"
        )

    def test_authorization_isolation_advisory_never_authorizes(self, tmp_path):
        """AUTHORIZATION ISOLATION — the headline #1052 safety property, RE-POINTED to
        a genuine FLOOR no-mint scenario. A command-bearing bundle whose CLICKED option
        carries NO command (the destructive `gh pr merge 42` is only in the QUESTION
        prose, never the clicked option) refuses with `no_command` → it emits the
        advisory AND writes NO token AND the pre-hook STILL DENIES the merge. The
        observer advisory is structurally incapable of authorizing. (The old scenario —
        a decline-LABELED option CARRYING the command — now MINTS as an accepted
        residual under the floor, so it is no longer a no-mint case; this re-points to
        the clicked-option-with-no-command no-mint, which the floor still refuses.)

        Three conjuncts asserted separately:
          (a) the advisory fired (operator gets the self-teaching nudge),
          (b) NO token on disk (additionalContext is not a token), and
          (c) the pre-hook denies `gh pr merge 42` (exit 2 + deny JSON) over the real
              token seam — the merge stays HELD.

        MAKE-ADVISORY-MINT mutation proof: have the advisory else-branch call
        write_token(...) before emitting (or fall through into the mint). Then a token
        appears → conjunct (b) flips RED (and (c) would authorize). That mutation is the
        exact authorization leak the observer-only design forbids."""
        questions = [{
            "question": "Approve the merge `gh pr merge 42`?",
            "options": [{"label": "Yes", "description": "Confirmed, proceed"},
                        {"label": "Cancel", "description": "Abort"}],
            "multiSelect": False,
        }]
        code, out = self._run_post_capture(questions, {"Approve the merge `gh pr merge 42`?": "Yes"}, tmp_path)
        assert code == 0
        # (a) advisory fired
        ctx = self._additional_context(out)
        assert ctx is not None and "peer-review.md" in ctx
        # (b) no token minted by the advisory path
        assert list(tmp_path.glob("merge-authorized-*")) == []
        # (c) the pre-hook still denies the merge over the real seam
        pre_code, pre_out = self._run_pre("gh pr merge 42", tmp_path)
        assert pre_code == 2
        assert '"permissionDecision": "deny"' in pre_out
