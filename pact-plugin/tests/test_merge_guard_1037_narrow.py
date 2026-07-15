"""
Location: pact-plugin/tests/test_merge_guard_1037_narrow.py
Summary: Regression canaries for the NARROW gh-comment carrier-strip (#1037) — the
         doubly-anchored step-7 `_gh_carrier_span` extension that adds
         `gh issue comment` + `gh pr comment` to the carrier verb alternation.
         Locks in: gh-comment --title/--body/-t/-b prose that NAMES a dangerous op
         now ALLOWs; every executing-op vector still BLOCKs; create/edit unchanged.
         The general HYBRID suppressor approach was abandoned (a single-line regex
         cannot model bash grammar — it shipped under-blocks); this narrow
         carrier-strip is the safe partial fix for the gh-comment over-block.
Used by: pytest (merge-guard suite).

Non-vacuity:
  * ALLOW canaries (proof-class P): the comment-verb membership is what flips them.
    Proven dynamically by VERB-DISCRIMINATION (a carrier verb ALLOWs an inert
    --body; the SAME --body under a non-carrier sibling verb `gh pr review` BLOCKs)
    + a mechanism assertion (_strip blanks the --body value to STRIPPED, so the
    dangerous literal never reaches DANGEROUS_PATTERNS). Build-time source-revert
    MEASUREMENT (remove the comment verbs from `_gh_carrier_span`): {5 of 5 ALLOW
    flip to BLOCK}. `_gh_carrier_span` is FUNCTION-LOCAL to
    _strip_non_executable_content, so this is a measured/documented counter-test
    (the discrimination is the runnable equivalent), not an in-test monkeypatch.
  * BLOCK canaries (proof-class C): a broaden/neuter flips a preserved-block to
    ALLOW, OR (span-stop cases) a discrimination shows the op moves outside the
    stripped value — see each test's inline note for the EXACT proven mechanism.

Dangerous literals are authored IN-FILE only (never on a Bash command line — the
installed merge-guard hook false-positives on them). Backslash and single-quote
are built from char vars so the escaped-quote vectors are unambiguous.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import pytest

_BS = "\\"  # one literal backslash
_SQ = "'"


class TestNarrowGhCommentCarrierStripAllow:
    """The fix: `gh issue|pr comment --body/-b` prose that NAMES a dangerous op is
    now ALLOWED (was a false-positive BLOCK). NEGATIVE assertions — non-vacuity via
    the verb-discrimination + mechanism assertion below."""

    ALLOW = [
        'gh pr comment 5 --body "see gh pr merge 5 for context"',
        'gh issue comment 12 --body "do not git push --force origin main"',
        'gh pr comment 5 -b "ship after gh pr merge 9"',
        "gh issue comment 7 --body 'note: gh pr merge 3 later'",  # single-quoted body
    ]

    @pytest.mark.parametrize("command", ALLOW)
    def test_comment_body_naming_dangerous_op_now_allows(self, command):
        from merge_guard_pre import (
            is_dangerous_command,
            is_compound_destructive_command,
        )

        assert not is_dangerous_command(command)
        assert not is_compound_destructive_command(command)

    def test_carrier_strip_neutralizes_the_body(self):
        # mechanism (exclusion-guard, #933): the comment carrier-strip BLANKS the
        # --body value to STRIPPED, so the dangerous literal never reaches the scan.
        from merge_guard_pre import _strip_non_executable_content

        stripped = _strip_non_executable_content('gh pr comment 5 --body "gh pr merge 5"')
        assert "gh pr merge" not in stripped
        assert "STRIPPED" in stripped
        assert stripped.startswith("gh pr comment 5")

    @pytest.mark.parametrize(
        "carrier,sibling",
        [
            (
                'gh pr comment 5 --body "ref gh pr merge 5"',
                'gh pr review 5 --body "ref gh pr merge 5"',
            ),
            (
                'gh pr comment 8 --body "ref git push --force origin main"',
                'gh pr review 8 --body "ref git push --force origin main"',
            ),
        ],
    )
    def test_comment_verb_membership_is_load_bearing(self, carrier, sibling):
        # non-vacuity (proof-class P): the comment verb being IN the alternation is
        # what flips these. The identical --body under a NON-carrier sibling verb
        # (`gh pr review`, absent from the pr alternation — #1129 R2 added `pr edit`
        # to the carrier set, so `review` is now the discriminating non-carrier sibling)
        # is NOT stripped and BLOCKs.
        # This is the runnable equivalent of reverting the comment verbs from
        # `_gh_carrier_span` (build-time measured: {5 of 5 ALLOW flip to BLOCK}).
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(carrier)  # carrier verb -> stripped -> ALLOW
        assert is_dangerous_command(sibling)  # non-carrier verb -> survives -> BLOCK


class TestNarrowGhCommentCarrierStripBlock:
    """No under-block: every executing-op vector still BLOCKs. The carrier-strip is
    DOUBLY-ANCHORED (carrier verb + value DIRECTLY after --title/--body/-t/-b) and
    the span stops at the first UNQUOTED separator, so an op outside the carrier
    value always survives into DANGEROUS_PATTERNS (the authority)."""

    def test_op_after_body_unquoted_separator_blocks(self):
        # non-vacuity (proof-class C, span-stop discrimination): the op INSIDE the
        # body ALLOWs; the SAME op after an UNQUOTED `;` BLOCKs (the span stops at
        # the separator, the op falls OUTSIDE the stripped value and survives).
        # Honest-mistake ≥2-narrowing: the post-separator `gh pr merge 5` is ONE
        # destructive leg (the carrier `gh pr comment` is benign), so it is NOT
        # >=2-compound — but it is STILL is_dangerous-gated (the carrier strip
        # preserves the op, the single-op gate catches it), so it STILL BLOCKS.
        from merge_guard_pre import (
            is_dangerous_command,
            is_compound_destructive_command,
            _strip_non_executable_content,
        )

        inside = 'gh pr comment 5 --body "x gh pr merge 5"'
        outside = 'gh pr comment 5 --body "x" ; gh pr merge 5'
        assert not is_dangerous_command(inside)  # op inside the body -> ALLOW
        assert is_dangerous_command(outside)  # op after `;` survives the strip -> BLOCK (single-op gate)
        assert not is_compound_destructive_command(outside)  # one destructive leg -> not >=2-compound
        assert "gh pr merge 5" in _strip_non_executable_content(outside)  # op survives

    def test_op_after_body_andand_blocks(self):
        # Honest-mistake ≥2: `gh pr comment 5 --body "x" && gh pr merge 9` is ONE
        # destructive leg (merge 9; the comment carrier is benign) → NOT >=2-compound,
        # but the merge survives the carrier strip and STILL BLOCKS via the single-op
        # is_dangerous gate.
        from merge_guard_pre import is_dangerous_command, is_compound_destructive_command

        cmd = 'gh pr comment 5 --body "x" && gh pr merge 9'
        assert is_dangerous_command(cmd)  # op after && -> BLOCK (single-op gate)
        assert not is_compound_destructive_command(cmd)  # one destructive leg

    def test_command_substitution_in_body_blocks(self, monkeypatch):
        # non-vacuity (proof-class C): the dq inner-strip PRESERVES a $()/backtick
        # body via `_has_command_substitution` (it executes inside double quotes).
        # Neuter that guard -> the body is blanked -> ALLOW (MEASURED flip), proving
        # the preserve guard is the load-bearing reason this stays BLOCK.
        from merge_guard_pre import is_dangerous_command

        cmd = 'gh pr comment 5 --body "$(gh pr merge 5)"'
        assert is_dangerous_command(cmd)
        # is_dangerous_command + its _has_command_substitution helper are promoted to
        # shared (GAP1); neuter the SHARED definition (pre re-exports the same object).
        monkeypatch.setattr("shared.merge_guard_common._has_command_substitution", lambda q: False)
        assert not is_dangerous_command(cmd)  # flips ALLOW under the neuter

    def test_escaped_quote_outside_carrier_flag_blocks(self):
        # an escaped quote NOT after a carrier flag does not open a carrier-flag
        # value, so the op after the (real, unquoted) separator survives the strip.
        # non-vacuity (mechanism): the op literal is present in the _strip output.
        from merge_guard_pre import is_dangerous_command, _strip_non_executable_content

        cmd = "gh pr comment 5 --foo a" + _BS + _SQ + " ; gh pr merge 5 " + _BS + _SQ + "b"
        assert is_dangerous_command(cmd)
        assert "gh pr merge 5" in _strip_non_executable_content(cmd)

    def test_close_delete_branch_blocks(self):
        # `gh pr close` is NOT a carrier verb (absent from the alternation BY
        # CONSTRUCTION); `--delete-branch` is the deny trigger -> BLOCK.
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close 5 --delete-branch")

    def test_python_dash_c_blocks(self):
        # python -c EXECUTES its arg; not a carrier verb -> not stripped -> BLOCK.
        # `print('...')` style per the security ratification (an `x='...'` form would
        # be removed by the pre-existing var-assignment carrier and is a weaker canary).
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('python3 -c "print(' + _SQ + "gh pr merge 42" + _SQ + ')"')

    def test_general_grep_papercut_now_closed(self):
        # The `grep "...op..."` over-block papercut this #1037 NARROW fix deliberately left
        # OUT OF SCOPE (only gh-comment) is CLOSED by the #1178 general inert-default positional
        # strip — "the proper fix" this pin previously guarded against a suppressor faking. grep
        # is an unrecognized head, so its quoted pattern is POSIX-argv-inert (never executed);
        # carrier 10 strips it exactly like the echo carrier-strip -> ALLOW is CORRECT (a
        # faithful `grep "gh pr merge 5" file` no longer gates). Flipped from block to allow when
        # #1178 landed; the general strip is the general fix the papercut awaited.
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('grep "gh pr merge 5" file')


class TestNarrowGhCommentInertAllow:
    """Ratified INERT-ALLOW: an escaped quote INSIDE a dq --body value is a literal
    char in bash, so the entire dq is ONE inert --body value (the op is never a shell
    token). ALLOW is CORRECT, NOT an under-block."""

    def test_escaped_quote_inside_body_is_inert_allow(self):
        # `gh pr comment 5 --body "a\' ; gh pr merge 5 \'b"` — the `\'` is literal
        # inside the dq; the whole dq string is the --body API value; the op cannot
        # execute. The carrier-strip blanks the whole inert value -> ALLOW. This is
        # NOT an under-block: in bash the op never becomes a command.
        from merge_guard_pre import is_dangerous_command, _strip_non_executable_content

        cmd = (
            "gh pr comment 5 --body " + '"' + "a" + _BS + _SQ
            + " ; gh pr merge 5 " + _BS + _SQ + "b" + '"'
        )
        assert not is_dangerous_command(cmd)
        assert "gh pr merge" not in _strip_non_executable_content(cmd)


class TestNarrowGhCarrierCreateEditUnchanged:
    """Regression guard: the narrow change only ADDED comment to the alternation;
    create/edit carrier behavior is unchanged."""

    CREATE_EDIT = [
        'gh issue create --title "regression: gh pr merge 5 in title"',
        'gh pr create --body "do not git push --force origin main"',
        'gh issue edit 4 --body "ref gh pr merge 2"',
    ]

    @pytest.mark.parametrize("command", CREATE_EDIT)
    def test_create_edit_carrier_still_allows(self, command):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(command)


class TestNarrowGhCommentSecurityAnchoringCanaries:
    """Security re-probe (#36 SAFE 42-case differential) anchoring canaries directed
    at the COMMENT carrier — the two classes that bit the ABANDONED general
    suppressor and that the narrow's FLAG-ANCHORED inner strip (blanks ONLY the
    value DIRECTLY after --title/--body/-t/-b) neutralizes. Highest-value guards:
    they flip RED if the inner strip were ever loosened from FLAG-ANCHORED to
    WHOLE-SEGMENT (blank any quoted region in the span) — the precise mutation that
    distinguishes the SAFE narrow carrier-strip from the abandoned general
    suppressor (which blanked ALL quoted literals in a segment, so it under-blocked).

    Non-vacuity: each canary asserts BLOCK + the op LITERAL SURVIVES the strip
    (present in _strip output, so DANGEROUS_PATTERNS catches it — proving the
    flag-anchoring did NOT blank it). The distinguishing mutation is MEASURED at
    build time: loosening the inner-strip flag-anchor
    (`(?:--title|--body|-t|-b)\\s+` -> `\\s*`, i.e. blank ANY quoted region) flips
    {4 of 4} flag-anchoring canaries (2 CLASS-1 non-carrier-flag + 2 CLASS-2
    `<(...)`) BLOCK->ALLOW = under-block, while the `>(...)` output-procsub stays
    BLOCK (defense-in-depth: the whole-command process-sub-to-shell guard skips the
    strip entirely). The inner strip is FUNCTION-LOCAL to
    _strip_non_executable_content, so the loosen is a documented measurement and
    op-survives-the-strip is the runnable equivalent.
    """

    # CLASS-1: a dangerous op in a QUOTED value NOT directly after a carrier flag
    # (flag-anchored strip leaves it; a whole-segment strip would blank it).
    CLASS1_FLAG_ANCHORED = [
        'gh pr comment 5 --foo "gh pr merge 5"',
        'gh pr comment 5 --body "ok" --foo "gh pr merge 9"',
    ]
    # CLASS-2: process-substitution running its OWN command (incl --admin / the
    # #1042 bypass and git push --force); the `<(...)` op survives the strip.
    CLASS2_PROCSUB = [
        'gh pr comment 5 --body "x" <(bash -c "gh pr merge 5 --admin")',
        'gh pr comment 5 --body "x" <(bash -c "git push --force origin main")',
    ]

    @pytest.mark.parametrize("command", CLASS1_FLAG_ANCHORED + CLASS2_PROCSUB)
    def test_flag_anchoring_canary_blocks_and_op_survives(self, command):
        # BLOCK + the op SURVIVES the strip (flag-anchoring did NOT blank it). The
        # whole-segment-loosen mutation flips these {4 of 4} -> ALLOW (measured).
        from merge_guard_pre import (
            is_dangerous_command,
            is_compound_destructive_command,
            _strip_non_executable_content,
        )

        assert is_dangerous_command(command) or is_compound_destructive_command(command)
        stripped = _strip_non_executable_content(command)
        assert ("gh pr merge" in stripped) or ("git push" in stripped)  # op survives

    def test_escaped_quote_then_real_op_blocks(self):
        # CLASS-1 escaped-quote-then-real-op (span-stop): a `\'` desync ends the sq
        # (bash sq has no escapes), so the op after the real `;` is BARE and OUTSIDE
        # the carrier span -> survives -> BLOCK. (Flips under a different mutation,
        # span-consume-past-separator, not the whole-segment-loosen.)
        from merge_guard_pre import (
            is_dangerous_command,
            is_compound_destructive_command,
            _strip_non_executable_content,
        )

        cmd = "gh pr comment 5 --body " + _SQ + "a" + _BS + _SQ + " ; gh pr merge 5"
        assert is_dangerous_command(cmd) or is_compound_destructive_command(cmd)
        assert "gh pr merge 5" in _strip_non_executable_content(cmd)

    def test_op_after_body_with_admin_blocks(self):
        # #1042 bypass shape (--admin) chained after the comment body -> BLOCK.
        # Honest-mistake ≥2: ONE destructive leg (merge 5 --admin; the comment carrier
        # is benign) → NOT >=2-compound, but the privileged merge survives the carrier
        # strip and STILL BLOCKS via the single-op is_dangerous gate.
        from merge_guard_pre import (
            is_dangerous_command,
            is_compound_destructive_command,
            _strip_non_executable_content,
        )

        cmd = 'gh pr comment 5 --body "ok" ; gh pr merge 5 --admin'
        assert is_dangerous_command(cmd)  # op after `;` -> BLOCK (single-op gate)
        assert not is_compound_destructive_command(cmd)  # one destructive leg
        assert "gh pr merge 5 --admin" in _strip_non_executable_content(cmd)

    def test_output_procsub_blocks_defense_in_depth(self):
        # `>(bash -c ...)` output process-sub: BLOCK via DEFENSE-IN-DEPTH — the
        # whole-command process-sub-to-shell guard skips the carrier-strip entirely
        # AND the op is outside any flag-anchored value. Stays BLOCK under the
        # whole-segment-loosen alone (a combined procsub-guard-neuter is needed too).
        from merge_guard_pre import is_dangerous_command, _strip_non_executable_content

        cmd = 'gh pr comment 5 --body "x" >(bash -c "gh pr merge 9")'
        assert is_dangerous_command(cmd)
        assert "gh pr merge 9" in _strip_non_executable_content(cmd)
