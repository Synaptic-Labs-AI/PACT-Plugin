"""
Location: pact-plugin/tests/test_merge_guard_mint_parity.py
Summary: STANDING mint-parity regression suite (permanent, not a one-arc cert) —
         guards the CARDINAL direction: a currently-minting faithful spelling must
         NEVER stop minting (a spelling the read floor gates but the mint refuses is
         gated-but-unmintable = a faithful click permanently blocked). One row per
         currently-minting spelling; each row asserts the FULL production round-trip
         on the live tree:

           clicked option (canonical backtick-wrapped AskUserQuestion shape)
             -> _mint_context_from_bundle mints a context with op + target
             -> _token_matches_command({"context": ctx}, command) is True

         i.e. the operator's click mints a token AND that token authorizes the
         executed command. Any future recognition/extraction change that breaks
         either half of the round-trip for any op-class fails here.

         LOAD-BEARING DISCRIMINATORS:
           - `bash -c 'gh pr merge 5'` / `sh -c "gh pr merge 5"`: classify None on
             the executed-surface view (quoted payload masked) and survive ONLY via
             detect's raw-fallback pass. A future "simplify detect to view-only"
             edit fails exactly on these two rows.
           - quoted CLI target (`git branch -D "feat/x"`): extraction stays RAW —
             a view-side extraction would blank the target (gated-but-unmintable).
           - quoted API URL (`gh api 'repos/…/git/refs/…' -X DELETE`): the API-URL
             detect arms stay RAW in both passes for the same reason.

         Destructive verbs are assembled at runtime so this file stays inert to the
         live guard.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import merge_guard_pre  # noqa: E402
from merge_guard_post import _mint_context_from_bundle, _target_value  # noqa: E402

# --- currently-minting faithful spellings, one per op-class + the pinned
#     discriminator spellings (assembled at runtime) ---
MINTING_SPELLINGS = [
    ("merge", "gh pr " + "mer" + "ge 42"),
    ("merge-admin", "gh pr " + "mer" + "ge 5 --admin"),
    ("close-delete-branch", "gh pr close 42 " + "--delete-" + "branch"),
    ("force-push", "git push " + "--for" + "ce origin main"),
    ("push-to-main", "git push origin main"),
    ("lease-push", "git push " + "--force-with-" + "lease origin main"),
    ("branch-delete-single", "git branch " + "-D victim"),
    ("branch-delete-set", "git branch " + "-D feat1 feat2"),
    ("branch-delete-quoted-target", 'git branch ' + '-D "feat/x"'),
    ("remote-ref-delete", "git push origin " + ":feature"),
    ("remote-mass-delete", "git push " + "--prune origin"),
    (
        "branch-protection",
        "gh api -X " + "DEL" + "ETE repos/o/r/branches/main/protection",
    ),
    (
        "quoted-api-url-refs-delete",
        "gh api 'repos/o/r/git/refs/heads/x' -X " + "DEL" + "ETE",
    ),
    # global-flag spelling (`gh -R o/r api …`): the canonical minting form is the
    # branch-protection endpoint (matches the standing completeness pin). The
    # git/refs global-flag variant is a PRE-EXISTING non-minting residual (target
    # extraction abstains identically at base and HEAD — verified, not this arc's).
    (
        "global-flag-gh-api",
        "gh -R o/r api -X " + "DEL" + "ETE repos/o/r/branches/main/protection",
    ),
    ("api-merge-endpoint", "gh api -X PUT /repos/o/r/pulls/42/" + "mer" + "ge"),
    # V2 raw-fallback discriminators (see module docstring).
    ("wrapped-bash-c-merge", "bash -c 'gh pr " + "mer" + "ge 5'"),
    ("wrapped-sh-c-merge", 'sh -c "gh pr ' + "mer" + 'ge 5"'),
    # line-continuation spelling: the shared normalizer joins the lines.
    ("line-continuation-close", "gh pr close 5 \\\n" + "--delete-" + "branch"),
]


class TestMintParityRoundTrip:
    @pytest.mark.parametrize(
        "label,cmd", MINTING_SPELLINGS, ids=[r[0] for r in MINTING_SPELLINGS]
    )
    def test_faithful_click_mints_and_token_authorizes(self, label, cmd):
        question = {
            "question": "Proceed?",
            "options": [{"label": "Yes, do it", "description": "Run `%s` now" % cmd}],
            "multiSelect": False,
        }
        ctx, refusal = _mint_context_from_bundle([question], {"Proceed?": "Yes, do it"})
        assert ctx is not None, (
            "faithful click STOPPED MINTING (%s): %r — gated-but-unmintable is a "
            "cardinal over-block" % (refusal, cmd)
        )
        assert ctx.get("operation_type") is not None
        assert _target_value(ctx) is not None, "minted context lost its target: %r" % cmd
        assert merge_guard_pre._token_matches_command({"context": ctx}, cmd) is True, (
            "the minted token no longer authorizes its own command: %r" % cmd
        )
