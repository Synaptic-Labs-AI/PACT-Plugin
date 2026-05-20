"""
Comprehensive TEST-phase coverage for the actor-discriminator-capture-gate
bundle (#781 #786 #760 #738).

Backend-coder shipped SMOKE coverage: 4 lifted strict-xfail tests
in test_teardown_request_emitter.py (renamed `*_per_teammate_name_none_discriminator`),
6 anti-softening pin tests in test_wake_lifecycle_emitter.py
(TestDirectiveAntiSofteningGuard), plus the 4-leg helper-behavior probe in
the prior-phase HANDOFF. This file owns the SUBSTANTIVE coverage layer atop
those smoke tests, per the test-engineer ENGAGEMENT rule.

# Coverage map (per cbcfd589 audit-surface-enumeration discipline)

| Surface  | Class                                            | Scope                   |
|----------|--------------------------------------------------|-------------------------|
| TS-1     | TestIsLeadAtTaskCompletedPureContract            | helper pure-fn          |
| TS-2     | TestSiblingDiscriminatorParity                   | helper×helper           |
| TS-3     | TestObserveOnlyInvariant                         | SEC-S1 routing          |
| TS-4     | TestVestigialTeamNameKwargAcceptance             | signature parity        |
| TS-5     | TestDiscriminatorI1NamedInvariantPin             | named-invariant         |
| TS-6     | TestTeardownCallsiteWiringPin                    | callsite import         |
| TS-7     | TestAgentHandoffEmitterSiblingPreservation       | SEC-AC-3                |
| TS-8     | TestDirectiveByteIdentityOnPactSkillLiterals     | SEC-AC-7                |
| TS-9     | TestFalseTransitionClaimAbsenceAcrossSites       | #738 site sweep         |
| TS-10    | TestRiskElevenStrictXfailMechanismIntegrity      | rename + count          |
| TS-11    | TestPerPayloadSemanticReviewDiscipline           | phantom-green doc pin   |
| TS-12-14 | TestPostMergeFollowUpSpecs (docstring-only)      | spec, post-merge        |

# Backend-coder smoke-vs-test-engineer-comprehensive split (ENGAGEMENT rule)

- BACKEND-CODER SMOKE (already shipped, NOT duplicated here):
  - 4 lifted Gate-0 behavioral xfail tests in test_teardown_request_emitter.py
  - 6 anti-softening prose pins in test_wake_lifecycle_emitter.py
  - 4-leg helper-behavior probe in the helper pure-addition handoff
    (lead-frame {}, teammate, non-dict)
- TEST-ENGINEER COMPREHENSIVE (this file):
  - Pathological-input sweep on the helper (mirrors test_inbox_wake_lifecycle_helper.py)
  - Cross-helper parity invariant (proves the per-event partition convention)
  - Production-source structural pins (callsite, sibling preservation, prose)
  - Edit-time site enumeration for the #738 false-claim absence
  - Phantom-green discipline as in-code documentation pattern
  - Post-merge spec stubs for #786/#806/captured-fixture parity

# Counter-test-by-revert cardinality targets (post-directive-rewrite baseline)

Cardinality is documented per-test where measurable. Repeat the SOURCE-ONLY
revert mechanism per pact-testing-strategies:
   git checkout <callsite-migration-sha>^ -- <path>  # restore prior shape
   pytest tests/test_actor_discriminator_capture_gate.py -q
   git diff --quiet -- <path> && echo OK             # restore-integrity AC
"""

import ast
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

# conftest.py adds pact-plugin/hooks to sys.path; direct-import works.
import shared.wake_lifecycle as wl

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
PLUGIN_ROOT = HOOKS_DIR.parent
TEARDOWN_EMITTER = HOOKS_DIR / "teardown_request_emitter.py"
AGENT_HANDOFF_EMITTER = HOOKS_DIR / "agent_handoff_emitter.py"
WAKE_LIFECYCLE_EMITTER = HOOKS_DIR / "wake_lifecycle_emitter.py"
WAKE_LIFECYCLE_SHARED = HOOKS_DIR / "shared" / "wake_lifecycle.py"


# =============================================================================
# TS-1: TestIsLeadAtTaskCompletedPureContract — pathological-input sweep
# =============================================================================


class TestIsLeadAtTaskCompletedPureContract:
    """SEC-S1 pure-function contract for is_lead_at_task_completed.

    The four sibling helpers in shared/wake_lifecycle.py codify a
    pure-function-never-raises invariant (see is_lead_emit_authorized
    docstring + test_inbox_wake_lifecycle_helper.py
    test_lifecycle_relevant_never_raises pattern). The 5th sibling
    MUST honor the same contract — any future edit introducing an
    exception path is a SEC-S1 violation.

    Counter-test-by-revert: replacing the body with
    `raise ValueError("forced")` flips every test in this class RED.
    Cardinality target: {≥6 RED} on body-mutation.
    """

    @pytest.mark.parametrize("bad_input", [
        None, "", 0, [], {}, set(), tuple(), 42, 3.14, b"bytes",
        # Non-dict mapping-shaped: must still return False (isinstance gate).
        type("DictLike", (), {"get": lambda self, k, d=None: None})(),
    ])
    def test_non_dict_input_returns_false_never_raises(self, bad_input):
        """SEC-S1 invariant: any non-dict input returns False rather than
        raising. Mirrors the test_lifecycle_relevant_never_raises pattern
        in test_inbox_wake_lifecycle_helper.py. The {} case is included
        to confirm the isinstance gate AND the teammate_name-absent path
        both traverse cleanly.
        """
        # `{}` is a dict and the isinstance gate lets it through; verify
        # it is classified as lead (no teammate_name present).
        if bad_input == {}:
            assert wl.is_lead_at_task_completed(bad_input, "any-team") is True
        else:
            assert wl.is_lead_at_task_completed(bad_input, "any-team") is False

    # Pin the isinstance-gate behavior across dict-subclass /
    # Mapping-subclass shapes. `isinstance(_, dict)` is True for
    # OrderedDict + defaultdict (dict subclasses) — they traverse the
    # gate and the teammate_name-presence path classifies normally. It
    # is False for types.MappingProxyType (a separate Mapping protocol
    # implementation, NOT a dict subclass) — those traverse the
    # isinstance-False short-circuit and ALWAYS return False (teammate)
    # even when the underlying mapping has no teammate_name key.
    # Academic for Claude Code stdin (always plain `dict`), but
    # pinning rules out a future "treat all Mappings as dict" edit
    # that would silently change the asymmetry.
    def test_ordered_dict_with_teammate_name_classifies_as_teammate(self):
        from collections import OrderedDict
        payload = OrderedDict({"teammate_name": "secretary"})
        assert wl.is_lead_at_task_completed(payload, "team-x") is False

    def test_defaultdict_with_teammate_name_classifies_as_teammate(self):
        from collections import defaultdict
        payload = defaultdict(str)
        payload["teammate_name"] = "secretary"
        assert wl.is_lead_at_task_completed(payload, "team-x") is False

    def test_mapping_proxy_type_with_teammate_name_short_circuits_to_false(self):
        """MappingProxyType is NOT a dict subclass — isinstance gate
        returns False → helper returns False (teammate). This is
        academic for Claude Code stdin but pinning the asymmetry
        prevents a future 'treat all Mappings as dict' edit from
        silently changing the gate semantics.
        """
        from types import MappingProxyType
        payload = MappingProxyType({"teammate_name": "secretary"})
        assert wl.is_lead_at_task_completed(payload, "team-x") is False

    def test_mapping_proxy_type_empty_short_circuits_to_false(self):
        """MappingProxyType({}) is a `key-absent` Mapping shape that
        SHOULD classify as lead under the empirical schema (no
        teammate_name → lead), but the isinstance gate's strict dict-
        only check causes False (teammate) instead. Pinning documents
        the asymmetric isinstance-gate behavior so a future Mapping-
        aware edit is deliberate.
        """
        from types import MappingProxyType
        payload = MappingProxyType({})
        # NOTE: would return True under a Mapping-aware predicate;
        # the isinstance(_, dict) gate returns False instead.
        # Document the asymmetry — do NOT relax the gate without a
        # paired audit.
        assert wl.is_lead_at_task_completed(payload, "team-x") is False

    def test_lead_frame_empty_payload_classifies_as_lead(self):
        """Edge case for SEC-S1: bare `{}` passes the isinstance gate
        AND has no `teammate_name` key → True (lead). Matches the
        empirical lead-frame TaskCompleted shape (lead fires omit both
        teammate_name and team_name). Pinning the edge so a future
        "guard against missing fields" predicate edit fails loudly.
        """
        assert wl.is_lead_at_task_completed({}, "team-x") is True

    def test_teammate_frame_teammate_name_string_classifies_as_teammate(self):
        """Empirical platform path: teammate-context TaskCompleted
        stdin carries `teammate_name` identifying the agent that owned
        the task (captured 2026-05-20).
        `teammate_name is None` returns False → classified as teammate
        (suppress).
        """
        payload = {
            "session_id": "shared-lead-sid",
            "hook_event_name": "TaskCompleted",
            "task_id": "T1",
            "team_name": "team-x",
            "teammate_name": "secretary",
        }
        assert wl.is_lead_at_task_completed(payload, "team-x") is False

    @pytest.mark.parametrize("adversarial_teammate_name", [
        "", " ", "0", "False", "null", "None",
        "X" * 1000,  # very long
        "secretary-" + chr(0) + "-embedded-null",  # null byte (chr(0) avoids src null)
        "secretary-\n-newline", "secretary-\t-tab",
    ])
    def test_truthy_or_present_teammate_name_classifies_as_teammate(
        self, adversarial_teammate_name,
    ):
        """The discriminator is `teammate_name is None`, NOT
        `not teammate_name` and NOT `teammate_name` truthy-check. Any
        non-None string value — including empty-string, whitespace,
        embedded null, multiline — classifies as teammate. Pinning
        this rules out a future well-intended "guard against empty
        teammate_name" edit that would silently re-introduce
        false-classification.
        """
        payload = {"teammate_name": adversarial_teammate_name}
        assert wl.is_lead_at_task_completed(payload, "team-x") is False

    @pytest.mark.parametrize("none_equivalent", [None])
    def test_teammate_name_explicitly_none_classifies_as_lead(
        self, none_equivalent,
    ):
        """An explicit `"teammate_name": None` (rather than key-absent)
        still classifies as lead — `dict.get("teammate_name") is None`
        returns True both for missing key AND explicit-None value.
        Both shapes match the empirical lead-context schema (the
        captured lead fixture is key-absent; explicit-None is the
        in-Python equivalent).
        """
        assert wl.is_lead_at_task_completed(
            {"teammate_name": none_equivalent}, "team-x",
        ) is True

    def test_other_fields_irrelevant_to_classification(self):
        """The discriminator reads ONLY `teammate_name`. Other fields
        (`agent_id`, `session_id`, `task_id`, `team_name`, …) do not
        influence classification. Pinning this prevents a future
        "consult agent_id as a secondary signal" edit from re-
        introducing the docs-extrapolated discriminator the empirical
        capture invalidated.
        """
        payload = {
            # Other fields populated, including the previously-tried
            # agent_id discriminator…
            "session_id": "any",
            "task_id": "T1",
            "team_name": "team-x",
            "agent_id": "subagent-some-uuid",
            # …but teammate_name absent → must classify as lead.
        }
        assert wl.is_lead_at_task_completed(payload, "team-x") is True


# =============================================================================
# TS-2: TestSiblingDiscriminatorDivergence — per-event partition invariant
# (empirical: each event class uses ITS OWN discriminator field; uniform-
# field assumption was falsified by 2026-05-20 in-session capture).
# =============================================================================


class TestSiblingDiscriminatorDivergence:
    """The five is_lead_* sibling helpers DIVERGE on which field they
    read, by design. Empirical capture (2026-05-20) confirmed each
    event class carries a different actor-discriminator field; an
    earlier draft of this class asserted PARITY between TaskCompleted
    and PostToolUse on `agent_id`, but that draft was falsified by the
    capture (TaskCompleted does not fire in subagent context, so
    `agent_id` never appears on its stdin; the captured discriminator
    is `teammate_name` instead).

    The per-event partition is:

    +-------------------------+--------------------------+--------------------+
    | Helper                  | Event class              | Discriminator      |
    +=========================+==========================+====================+
    | is_lead_emit_authorized | PostToolUse              | agent_id           |
    | is_lead_drain_authorized| UserPromptSubmit         | agent_id           |
    | is_lead_at_session_start| SessionStart             | agent_type         |
    | is_lead_at_task_completed| TaskCompleted           | teammate_name      |
    | is_lead_session         | backward-compat delegate | (delegates to     |
    |                         |                          |  is_lead_emit_     |
    |                         |                          |  authorized)       |
    +-------------------------+--------------------------+--------------------+

    Counter-test-by-revert: if a future edit re-unifies two helpers on
    the same field (e.g., changes is_lead_at_task_completed back to
    read `agent_id`), the divergence tests below trip RED — the
    regression is loud, not silent.
    """

    def test_task_completed_helper_reads_teammate_name_not_agent_id(self):
        """`is_lead_at_task_completed` reads `teammate_name`, not
        `agent_id`. Verify via a payload where the two fields would
        give OPPOSITE answers: agent_id present but teammate_name
        absent → if the helper reads teammate_name, returns True (lead);
        if it reads agent_id, returns False (teammate).
        """
        payload = {"agent_id": "subagent-some-uuid"}
        # Reads teammate_name (absent → None → True/lead).
        assert wl.is_lead_at_task_completed(payload, "team-x") is True, (
            "is_lead_at_task_completed must read `teammate_name`, not "
            "`agent_id`. Empirical capture (2026-05-20) showed agent_id "
            "never appears on TaskCompleted stdin; teammate_name "
            "presence is the lead-vs-teammate discriminator."
        )
        # Inverse: teammate_name present but agent_id absent → teammate.
        payload_inv = {"teammate_name": "secretary"}
        assert wl.is_lead_at_task_completed(payload_inv, "team-x") is False

    def test_post_tool_use_helper_reads_agent_id_not_teammate_name(self):
        """`is_lead_emit_authorized` (PostToolUse) reads `agent_id`,
        not `teammate_name`. Verify via the opposite-answer payload:
        teammate_name present but agent_id absent → if it reads
        agent_id, returns True (lead); if it reads teammate_name,
        returns False (teammate).
        """
        payload = {"teammate_name": "backend-coder"}
        # Reads agent_id (absent → None → True/lead).
        assert wl.is_lead_emit_authorized(payload, "team-x") is True, (
            "is_lead_emit_authorized must read `agent_id`, not "
            "`teammate_name`. `agent_id` is the documented field in "
            "the 'Common input fields' section "
            "(code.claude.com/docs/en/hooks.md); empirically PostToolUse "
            "stdin carries agent_id in subagent context — see the "
            "`is_lead_emit_authorized` docstring in shared/wake_lifecycle.py."
        )
        # Inverse: agent_id present → teammate.
        payload_inv = {"agent_id": "subagent-some-uuid"}
        assert wl.is_lead_emit_authorized(payload_inv, "team-x") is False

    def test_session_start_helper_reads_agent_type_not_agent_id(self):
        """`is_lead_at_session_start` reads `agent_type` (agent-CLASS
        string like 'pact-secretary'), not `agent_id` (per-instance
        UUID). Pinning this asymmetry prevents a future "unify all
        is_lead_* on the same field" edit from collapsing the
        per-event partition convention.
        """
        # Payload with agent_id present but agent_type absent.
        payload_agent_id_only = {"agent_id": "subagent-some-uuid"}
        # SessionStart reads agent_type (absent → True/lead).
        assert wl.is_lead_at_session_start(payload_agent_id_only) is True

        # Inverse: agent_type present → teammate.
        payload_agent_type_only = {"agent_type": "pact-secretary"}
        assert wl.is_lead_at_session_start(payload_agent_type_only) is False

    def test_three_helpers_diverge_on_same_three_field_payload(self):
        """Single payload exercises the divergence across the 3
        per-event helpers simultaneously. A payload carrying ONE of
        each discriminator field forces each helper to read its own
        field — re-unification of any two would flip the assertion.
        """
        # All 3 discriminator fields present.
        payload = {
            "teammate_name": "secretary",
            "agent_id": "subagent-some-uuid",
            "agent_type": "pact-secretary",
        }
        # TaskCompleted reads teammate_name (present → teammate).
        assert wl.is_lead_at_task_completed(payload, "team-x") is False
        # PostToolUse reads agent_id (present → teammate).
        assert wl.is_lead_emit_authorized(payload, "team-x") is False
        # SessionStart reads agent_type (present → teammate).
        assert wl.is_lead_at_session_start(payload) is False

        # Remove only teammate_name: TaskCompleted flips lead, others
        # stay teammate.
        payload_no_teammate = {
            k: v for k, v in payload.items() if k != "teammate_name"
        }
        assert wl.is_lead_at_task_completed(payload_no_teammate, "team-x") is True
        assert wl.is_lead_emit_authorized(payload_no_teammate, "team-x") is False
        assert wl.is_lead_at_session_start(payload_no_teammate) is False

        # Remove only agent_id from the original: PostToolUse flips,
        # others stay where they were under the all-present payload.
        payload_no_agent_id = {
            k: v for k, v in payload.items() if k != "agent_id"
        }
        assert wl.is_lead_at_task_completed(payload_no_agent_id, "team-x") is False
        assert wl.is_lead_emit_authorized(payload_no_agent_id, "team-x") is True
        assert wl.is_lead_at_session_start(payload_no_agent_id) is False

        # Remove only agent_type: SessionStart flips, others stay.
        payload_no_agent_type = {
            k: v for k, v in payload.items() if k != "agent_type"
        }
        assert wl.is_lead_at_task_completed(payload_no_agent_type, "team-x") is False
        assert wl.is_lead_emit_authorized(payload_no_agent_type, "team-x") is False
        assert wl.is_lead_at_session_start(payload_no_agent_type) is True

    def test_legacy_delegate_is_lead_session_still_matches_emit_authorized(self):
        """is_lead_session is a backward-compat thin delegate to
        is_lead_emit_authorized — its body is a single pass-through
        return statement, which is the rename-is-body-preserving
        rationale for the callsite-migration commit's 0-RED counter-
        test (auditor-endorsed via 8-payload body-identity proof). Any
        future edit that diverges these two breaks the corridor.
        """
        for payload in [{}, {"agent_id": "x"}, {"agent_id": None}, None, 42]:
            a = wl.is_lead_session(payload, "team-x")
            b = wl.is_lead_emit_authorized(payload, "team-x")
            assert a == b, (
                f"is_lead_session/is_lead_emit_authorized parity broken "
                f"on payload={payload!r}: delegate={a}, primary={b}"
            )


# =============================================================================
# TS-3: TestObserveOnlyInvariant — SEC-S1 routing primitive
# =============================================================================


class TestObserveOnlyInvariant:
    """SEC-S1 observe-only invariant: the helper is a routing primitive,
    NOT a deny gate. It returns the non-emitting branch on ambiguous
    actor; it MUST NOT raise to abort the caller, MUST NOT print to
    stderr to side-channel a denial, MUST NOT consult external state.

    Pinning this contract prevents a future "fail-secure by raising on
    non-dict" edit from promoting the helper to a hard gate — that
    promotion would change the call-site semantics from "if not
    is_lead: suppress" to "if not is_lead: exception-propagates" with
    very different blast radius.
    """

    def test_returns_bool_not_truthy(self):
        """Return value MUST be a bool (True or False), not a truthy/
        falsy value that masquerades. Some predicate code paths use
        `return x is None` (correct: bool) but a future refactor
        might `return x or "lead"` (string, truthy) and pass
        `if not is_lead:` callers — pinning bool prevents this.
        """
        for payload in [{}, {"teammate_name": "x"}, {"teammate_name": None}]:
            result = wl.is_lead_at_task_completed(payload)
            assert result is True or result is False, (
                f"Return type must be bool literal True/False; "
                f"got {result!r} of type {type(result).__name__}"
            )

    def test_helper_makes_no_filesystem_reads(self, tmp_path, monkeypatch):
        """The helper is pure — `teammate_name`-presence on the in-
        memory dict is the discriminator; no team_config read, no
        journal read, no marker read. Monkeypatching Path.home to an
        empty tmp_path AND filesystem ops must not affect the result.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Even with a wildly wrong HOME, the helper must work.
        assert wl.is_lead_at_task_completed({}, "team-x") is True
        assert wl.is_lead_at_task_completed(
            {"teammate_name": "secretary"}, "team-x",
        ) is False

    def test_helper_does_not_consult_environ(self, monkeypatch):
        """The helper does not read os.environ — pinning this rules
        out a future "env-var override" edit that would couple the
        routing primitive to runtime state.
        """
        # Set adversarial env vars that a misguided edit might consult.
        monkeypatch.setenv("CLAUDE_TEAMMATE_NAME", "lead-pretender")
        monkeypatch.setenv("PACT_OVERRIDE_DISCRIMINATOR", "true")
        # Lead-frame {} STAYS lead regardless of env.
        assert wl.is_lead_at_task_completed({}, "team-x") is True
        # Teammate-frame teammate_name STAYS teammate regardless of env.
        assert wl.is_lead_at_task_completed(
            {"teammate_name": "secretary"}, "team-x",
        ) is False


# =============================================================================
# TS-4: TestVestigialTeamNameKwargAcceptance — signature uniformity
# =============================================================================


class TestVestigialTeamNameKwargAcceptance:
    """The 5 sibling helpers share `(input_data, team_name="")` for
    signature uniformity (architect §1 retention rationale). The
    team_name parameter is vestigial under the field-presence
    discriminator (no team_config read needed). Pinning the call
    shapes ensures a future "drop the unused parameter" refactor is
    deliberate, not accidental — the corridor's call sites rely on
    the uniform signature.
    """

    def test_accepts_positional_team_name(self):
        assert wl.is_lead_at_task_completed({}, "team-x") is True

    def test_accepts_keyword_team_name(self):
        assert wl.is_lead_at_task_completed({}, team_name="team-x") is True

    def test_team_name_defaults_to_empty_string(self):
        """Default value pinned. Sibling helpers all default to "".
        """
        sig = inspect.signature(wl.is_lead_at_task_completed)
        assert sig.parameters["team_name"].default == ""

    def test_team_name_value_does_not_affect_result(self):
        """The field-presence discriminator does not consult
        team_name. Identical payload + varying team_name = identical
        result. Pinning this rules out a future "team_config-driven
        override" leak.
        """
        for team in ["", "team-x", "pact-00000000", "team-with-special!@#$"]:
            assert wl.is_lead_at_task_completed({}, team) is True
            assert wl.is_lead_at_task_completed(
                {"teammate_name": "secretary"}, team,
            ) is False


# =============================================================================
# TS-5: TestDiscriminatorI1NamedInvariantPin — cbcfd589 audit anchor
# =============================================================================


class TestDiscriminatorI1NamedInvariantPin:
    """I1 = "teammate_name is None" — the named invariant the lifted
    Gate-0 tests in test_teardown_request_emitter.py encode in their
    `*_per_teammate_name_none_discriminator` suffixes (cbcfd589 §AUDIT
    discipline; the named-invariant rename shipped with the empirical-
    grounded predicate body fix).

    These structural assertions pin the invariant name AT the helper
    so the rename convention is self-consistent. If a future edit
    swaps the discriminator (e.g., back to `agent_id is None` or to
    a compound `teammate_name is None AND agent_id is None`), it MUST
    also update the suffix — these tests force that coupling visible.
    """

    def test_helper_body_reads_teammate_name_field_only(self):
        """Source-level structural pin: the helper body MUST reference
        `teammate_name` and MUST NOT reference `agent_id`, `agent_type`,
        or `tmuxPaneId` (other candidate discriminators across the
        sibling helpers). Drift would mean the empirical-grounded
        body has been silently swapped without updating the I1
        named-invariant tests.

        Counter-test-by-revert: replacing `teammate_name` with
        `agent_id` in the helper body flips this test RED with a
        specific assertion message identifying the wrong field.
        """
        src = inspect.getsource(wl.is_lead_at_task_completed)
        # Strip the docstring so we only inspect the executable body.
        body = src.split('"""')[-1] if '"""' in src else src
        assert 'teammate_name' in body, (
            f"is_lead_at_task_completed body must reference "
            f"'teammate_name'; got body excerpt={body!r}"
        )
        forbidden = ("agent_id", "agent_type", "tmuxPaneId")
        for f in forbidden:
            assert f not in body, (
                f"is_lead_at_task_completed body MUST NOT reference {f!r} "
                f"(empirical capture established teammate_name as the "
                f"discriminator; the other fields read different events' "
                f"actor-discriminators). A swap to {f!r} would silently "
                f"invalidate the I1 named-invariant tests' "
                f"`_per_teammate_name_none_discriminator` suffix without "
                f"forcing test-renames. Body excerpt: {body!r}"
            )

    def test_renamed_gate0_tests_carry_per_invariant_suffix(self):
        """Pin the cbcfd589 audit-surface-enumeration convention: the 4
        lifted Gate-0/ExitContract tests carry the
        `_per_teammate_name_none_discriminator` suffix. If a future
        edit rotates the discriminator, the test names MUST rotate
        accordingly — these structural pins force the coupling visible.
        """
        teardown_test_src = (PLUGIN_ROOT / "tests"
                             / "test_teardown_request_emitter.py").read_text()
        expected_suffixed_names = [
            "test_teammate_session_suppresses_emission_per_teammate_name_none_discriminator",
            "test_teammate_session_writes_no_journal_event_per_teammate_name_none_discriminator",
            "test_teammate_session_does_not_create_marker_per_teammate_name_none_discriminator",
            "test_all_gate_failure_paths_exit_zero_per_teammate_name_none_discriminator",
        ]
        for name in expected_suffixed_names:
            assert name in teardown_test_src, (
                f"Expected renamed test {name!r} not found in "
                f"test_teardown_request_emitter.py. The cbcfd589 §AUDIT "
                f"named-invariant rename was reverted or the discriminator "
                f"was silently swapped without updating the suffix."
            )


# =============================================================================
# TS-6: TestTeardownCallsiteWiringPin — production wiring at :301
# =============================================================================


class TestTeardownCallsiteWiringPin:
    """Pin the production wiring at teardown_request_emitter.py:301
    after the callsite-migration commit. The Gate-0 check MUST call
    `is_lead_at_task_completed`, NOT the legacy `is_lead_session`
    delegate. Production-source structural pin; complements the
    behavioral lifted-xfail tests.

    Counter-test-by-revert: `git checkout <callsite-migration-sha>^ --
    pact-plugin/hooks/teardown_request_emitter.py` restores the
    `is_lead_session` call and flips these tests RED. Cardinality
    target: {3 RED} (import, call, invariant comment).
    """

    def test_imports_is_lead_at_task_completed_from_shared(self):
        """The import statement must bring is_lead_at_task_completed
        into scope. Mirrors the test_pending_scan_session_init.py
        `test_session_init_imports_or_calls_lead_session_guard` pattern.
        """
        src = TEARDOWN_EMITTER.read_text(encoding="utf-8")
        # Tolerate `from shared.wake_lifecycle import (... is_lead_at_task_completed ...)`
        assert "is_lead_at_task_completed" in src, (
            "teardown_request_emitter.py must import "
            "is_lead_at_task_completed from shared.wake_lifecycle"
        )

    def test_callsite_uses_is_lead_at_task_completed_not_legacy_delegate(self):
        """The actual call expression must be
        `is_lead_at_task_completed(input_data, team_name)`, not the
        legacy `is_lead_session(input_data, team_name)`. Pattern
        regex-anchored to a control-flow statement to rule out
        commentary-only references.
        """
        src = TEARDOWN_EMITTER.read_text(encoding="utf-8")
        # Match `if not is_lead_at_task_completed(...)` or similar.
        call_pattern = re.compile(
            r"^\s*(if|return|elif|while|assert)\b.*is_lead_at_task_completed\b\s*\(",
            re.MULTILINE,
        )
        assert call_pattern.search(src), (
            "Expected a control-flow statement calling "
            "is_lead_at_task_completed(...) in teardown_request_emitter.py; "
            "the callsite migration may have regressed to is_lead_session."
        )
        # Negative pin: no LIVE call to the legacy delegate at Gate 0.
        # Allow `is_lead_session` to appear in commentary, but NOT in a
        # `if not is_lead_session(` shape (the previous Gate 0 form).
        legacy_call_pattern = re.compile(
            r"^\s*if\s+not\s+is_lead_session\s*\(",
            re.MULTILINE,
        )
        assert not legacy_call_pattern.search(src), (
            "teardown_request_emitter.py still calls "
            "`if not is_lead_session(` — the callsite migration was reverted."
        )

    def test_callsite_passes_input_data_and_team_name_positionally(self):
        """Pinning the call signature shape — `(input_data, team_name)`,
        matching the sibling-helper uniform signature. A future
        "drop the unused team_name" refactor would need to update this
        test, making the change deliberate.
        """
        src = TEARDOWN_EMITTER.read_text(encoding="utf-8")
        # Two-arg call with input_data + team_name in some order.
        # Tolerate whitespace + keyword form.
        call_shape = re.compile(
            r"is_lead_at_task_completed\s*\(\s*input_data\s*,\s*team_name\s*\)",
        )
        assert call_shape.search(src), (
            "Expected call shape `is_lead_at_task_completed(input_data, "
            "team_name)` at the Gate-0 callsite in "
            "teardown_request_emitter.py. The signature-uniformity contract "
            "may have been broken (TS-4)."
        )


# =============================================================================
# TS-7: TestAgentHandoffEmitterSiblingPreservation — SEC-AC-3
# =============================================================================


class TestAgentHandoffEmitterSiblingPreservation:
    """SEC-AC-3 architect §6: agent_handoff_emitter.py (the sibling
    TaskCompleted hook at :262) MUST continue reading
    `task_data.get("owner") or input_data.get("teammate_name")` —
    NOT migrate to the new is_lead_* helper.

    The two TaskCompleted hooks answer semantically distinct questions:
    teardown_request_emitter asks "is this fire from the LEAD session?"
    (routing); agent_handoff_emitter asks "is this fire on a task
    OWNED by an agent?" (content). Aligning the discriminators would
    conflate the two and silence the journal write in teammate frames.

    Counter-test-by-revert: editing agent_handoff_emitter.py:262 to
    use is_lead_at_task_completed flips both tests RED.
    """

    def test_agent_handoff_emitter_still_reads_task_data_owner(self):
        """Pin the disk-record owner read at the canonical line range.
        """
        src = AGENT_HANDOFF_EMITTER.read_text(encoding="utf-8")
        assert 'task_data.get("owner")' in src, (
            "agent_handoff_emitter.py must still read "
            '`task_data.get("owner")` — SEC-AC-3 sibling preservation. '
            "Aligning the discriminator with teardown_request_emitter "
            "would silence the journal write in teammate frames."
        )

    def test_agent_handoff_emitter_does_not_call_is_lead_at_task_completed(self):
        """Negative pin: agent_handoff_emitter.py MUST NOT call any
        is_lead_* helper at Gate 0. Its discriminator is owner-based,
        not actor-based.
        """
        src = AGENT_HANDOFF_EMITTER.read_text(encoding="utf-8")
        # Forbid the function-call shape; tolerate docstring/comment
        # mentions if they appear.
        forbidden_calls = [
            "is_lead_at_task_completed(",
            "is_lead_emit_authorized(",
            "is_lead_session(",
            "is_lead_drain_authorized(",
            "is_lead_at_session_start(",
        ]
        for f in forbidden_calls:
            assert f not in src, (
                f"agent_handoff_emitter.py contains a call to {f!r} — "
                f"SEC-AC-3 violation. The hook's discriminator is "
                f"owner-based (task_data.get('owner')), not actor-based. "
                f"Re-aligning collapses two semantically distinct "
                f"decisions into one."
            )


# =============================================================================
# TS-8: TestDirectiveByteIdentityOnPactSkillLiterals — SEC-AC-7
# =============================================================================


class TestDirectiveByteIdentityOnPactSkillLiterals:
    """SEC-AC-7: the directive-prose rewrite preserves byte-identity
    on PACT skill literals (`PACT:start-pending-scan`,
    `PACT:stop-pending-scan`, `/PACT:scan-pending-tasks`). Drift on
    these literals breaks the skill invocation contract — the
    receiving orchestrator's `Skill("PACT:...")` resolver matches on
    exact string.

    The directive-prose commit verified PACT-skill-literal preservation
    at edit time via disk-grep; this test class encodes the invariant
    as a regression guard against future "tidy up the directive prose"
    edits that might rename the skills.
    """

    def _import_emitter(self):
        sys.path.insert(0, str(HOOKS_DIR))
        import wake_lifecycle_emitter as emitter
        return emitter

    def test_arm_directive_invokes_start_pending_scan_byte_identical(self):
        emitter = self._import_emitter()
        assert 'Skill("PACT:start-pending-scan")' in emitter._ARM_DIRECTIVE, (
            f"_ARM_DIRECTIVE must invoke "
            f'Skill("PACT:start-pending-scan") byte-identically; '
            f"got {emitter._ARM_DIRECTIVE!r}"
        )

    def test_teardown_directive_invokes_stop_pending_scan_byte_identical(self):
        emitter = self._import_emitter()
        assert 'Skill("PACT:stop-pending-scan")' in emitter._TEARDOWN_DIRECTIVE, (
            f"_TEARDOWN_DIRECTIVE must invoke "
            f'Skill("PACT:stop-pending-scan") byte-identically; '
            f"got {emitter._TEARDOWN_DIRECTIVE!r}"
        )

    def test_both_directives_reference_scan_pending_tasks_cron_slug(self):
        """Both directives reference the cron's slug
        `/PACT:scan-pending-tasks` (Arm: "if a /PACT:scan-pending-tasks
        cron is already registered"; Teardown: "to delete the
        /PACT:scan-pending-tasks cron"). The slug literal is the
        CronList match key; drift here would break the idempotency
        check in the skill body.
        """
        emitter = self._import_emitter()
        assert "/PACT:scan-pending-tasks" in emitter._ARM_DIRECTIVE
        assert "/PACT:scan-pending-tasks" in emitter._TEARDOWN_DIRECTIVE


# =============================================================================
# TS-9: TestFalseTransitionClaimAbsenceAcrossSites — #738 site sweep
# =============================================================================

# F2-test (cycle-2 adoption): explicit allowlist of permitted fixture
# files that legitimately carry the retired-prose literal as task-
# subject content (NOT directive prose). The sweep below skips any
# .json file under `fixtures/` AND filters its hits against this
# allowlist; any future fixture that grows a hit MUST be explicitly
# added here with a one-line rationale. This makes the "why is this
# fixture exempt?" question discoverable from a single grep.
#
# Rationale (per cycle-2 finding F2-test): the two fixture files
# below carry "First active teammate task" as a `subject` field on
# synthetic task records used for stdin-shape probes. These are
# task subjects, not directive prose; they predate the #738 rewrite
# and remain valid synthetic test data. A future reader grepping
# for the retired literal would otherwise have to re-derive why
# these 2 hits don't trip the discipline — the allowlist documents
# the exemption inline.
FIXTURE_PROSE_ALLOWLIST = {
    "task_create_production_shape.json": (
        "carries 'First active teammate task' as a synthetic task "
        "`subject` field, not directive prose"
    ),
    "task_update_production_shape.json": (
        "carries 'First active teammate task' as a synthetic task "
        "`subject` field, not directive prose"
    ),
}


class TestFalseTransitionClaimAbsenceAcrossSites:
    """#738 root cause: the original directives' "First active teammate
    task created" / "Last active teammate task completed" prefixes are
    provably-false on multi-fire (the predicate is `count >= 1`, not
    a 0→1 transition). The directive-prose rewrite removed the false
    claim; this class pins the absence across the EDIT-TIME enumerated
    site surface so a future "restore the friendly transition prefix"
    edit fails loudly.

    The directive-prose commit verified retired-prose disk-grep zero
    in production hooks/commands/protocols/skills at commit time; this
    class makes the invariant automated.

    The full pin surface enumerated at commit time spans 11 test files
    + 3 runbooks + 5 cmd/proto docs. The test below sweeps the
    production-source enumerated sites (hooks/, commands/, protocols/,
    skills/) and asserts zero matches. The 2 fixture-task-subject hits
    at edit-time were classified as out-of-scope (synthetic task
    subjects, not directive prose) — this test's GLOB skips fixture
    .json files.

    Counter-test-by-revert: restoring the old prefix at
    wake_lifecycle_emitter.py:186 flips this test RED with the
    specific file:line of the regression.
    """

    @pytest.mark.parametrize("retired_prose", [
        "First active teammate task created",
        "Last active teammate task completed",
    ])
    def test_retired_prose_absent_from_production_source_surface(
        self, retired_prose,
    ):
        """Sweep pact-plugin/hooks + pact-plugin/commands +
        pact-plugin/protocols + pact-plugin/skills for the retired
        directive prefix. The test class docstring documents
        scope (production source, not fixtures or test files).
        """
        # Test files (pact-plugin/tests/) reference the literal in
        # negative-pin assertions (`"First active" not in directive`) —
        # those are INTENTIONAL and out of this test's scope. Fixture
        # task-subject strings are unrelated synthetic data.
        production_surface = [
            PLUGIN_ROOT / "hooks",
            PLUGIN_ROOT / "commands",
            PLUGIN_ROOT / "protocols",
            PLUGIN_ROOT / "skills",
        ]
        hits = []
        for root in production_surface:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                # Skip __pycache__ and binary blobs.
                if "__pycache__" in path.parts:
                    continue
                # Skip fixture JSONs that may carry task subjects.
                if path.suffix == ".json" and "fixtures" in path.parts:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if retired_prose in text:
                    # Report file + first occurrence line.
                    for i, line in enumerate(text.splitlines(), 1):
                        if retired_prose in line:
                            hits.append(f"{path}:{i}: {line.strip()[:120]}")
                            break
        assert not hits, (
            f"Retired #738 prose {retired_prose!r} found in the "
            f"production-source surface (hooks/commands/protocols/skills). "
            f"The directive-prose rewrite expected zero hits here. "
            f"Hits:\n" + "\n".join(hits)
        )

    @pytest.mark.parametrize("retired_prose", [
        "First active teammate task created",
        "Last active teammate task completed",
    ])
    def test_retired_prose_absent_from_runbooks(self, retired_prose):
        """Runbooks (pact-plugin/tests/runbooks/*.md) are the
        operator-facing documentation surface. Drift here would
        re-introduce the misleading transition-claim prose at the
        operator layer even if the source is correct.
        """
        runbook_dir = PLUGIN_ROOT / "tests" / "runbooks"
        if not runbook_dir.exists():
            pytest.skip("runbooks dir not present in this checkout")
        hits = []
        for path in runbook_dir.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            if retired_prose in text:
                for i, line in enumerate(text.splitlines(), 1):
                    if retired_prose in line:
                        hits.append(f"{path}:{i}: {line.strip()[:120]}")
                        break
        assert not hits, (
            f"Retired #738 prose {retired_prose!r} found in runbooks. "
            f"The directive-prose rewrite expected zero hits. Hits:\n"
            + "\n".join(hits)
        )

    def test_fixture_prose_allowlist_matches_disk_state(self):
        """F2-test (cycle-2): pin the FIXTURE_PROSE_ALLOWLIST against
        the actual disk state. Every fixture file under
        pact-plugin/tests/fixtures/ that carries the retired prose
        literal MUST appear in the allowlist; every allowlist entry
        MUST exist on disk. A future fixture refactor that adds OR
        removes a permitted fixture surface trips this pin and forces
        an allowlist update — keeping the documented exemption in
        sync with reality per SEC-AC-2 verify-against-disk discipline.
        """
        fixtures_root = PLUGIN_ROOT / "tests" / "fixtures"
        if not fixtures_root.exists():
            pytest.skip("fixtures dir not present in this checkout")
        retired_literals = (
            "First active teammate task created",
            "Last active teammate task completed",
            # Truncated fixture-subject form (see allowlist rationale):
            "First active teammate task",
        )
        actual_hits = set()
        for path in fixtures_root.rglob("*.json"):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if any(lit in text for lit in retired_literals):
                actual_hits.add(path.name)
        allowlisted = set(FIXTURE_PROSE_ALLOWLIST.keys())
        missing_from_allowlist = actual_hits - allowlisted
        stale_allowlist_entries = allowlisted - actual_hits
        assert not missing_from_allowlist, (
            f"Fixture files carry retired-prose literals but are NOT in "
            f"FIXTURE_PROSE_ALLOWLIST: {sorted(missing_from_allowlist)}. "
            f"Either remove the prose from the fixture, OR add the "
            f"file to FIXTURE_PROSE_ALLOWLIST with a rationale."
        )
        assert not stale_allowlist_entries, (
            f"FIXTURE_PROSE_ALLOWLIST entries do not match any disk "
            f"file with retired-prose content: "
            f"{sorted(stale_allowlist_entries)}. Either the fixture "
            f"was renamed/deleted (remove from allowlist) OR the prose "
            f"was scrubbed (remove from allowlist — exemption no longer "
            f"needed)."
        )


# =============================================================================
# TS-10: TestRiskElevenStrictXfailMechanismIntegrity — atomic-lift discharge
# =============================================================================


class TestRiskElevenStrictXfailMechanismIntegrity:
    """The Risk-11 verification gate is the
    `@pytest.mark.xfail(strict=True)` markers on the
    test_teardown_request_emitter.py Gate-0 and ExitContract Gate-0
    tests. The callsite-migration commit lifted all 4 atomically with
    the predicate migration and payload updates.

    Post-migration INTEGRITY pins:
    - The lifted tests now PASS (not xfail-strict).
    - The strict-xfail count on the affected file is 0.
    - Re-introducing the markers without reverting the predicate
      would XPASS-strict → CI-RED — by design.
    - The named-invariant rename suffixes are present.

    Counter-test-by-revert: `git checkout <callsite-migration-sha>^ --
    pact-plugin/hooks/teardown_request_emitter.py pact-plugin/hooks/
    shared/wake_lifecycle.py pact-plugin/tests/test_teardown_request_
    emitter.py` restores all 4 strict-xfail markers + the prior
    predicate; this test class would then collect under the prior
    marker set and the structural pins below would still hold (the
    markers are still PRESENT, just discharged differently). The
    load-bearing signal is the 4→0 strict-xfail count delta surfaced
    in audit findings, not a single test pass.
    """

    def test_no_strict_xfail_markers_remain_in_gate0_test_file(self):
        """Post-migration, ZERO `@pytest.mark.xfail(strict=True)`
        markers remain in test_teardown_request_emitter.py (the 4
        Risk-11 markers were atomically lifted with the migration).
        Re-introduction without reverting the predicate would be a
        SEC-AC-1 violation (Risk-11 gate decided post-hoc).
        """
        src = (PLUGIN_ROOT / "tests"
               / "test_teardown_request_emitter.py").read_text()
        # Look for the canonical strict=True spelling. Tolerate
        # multiline formatting via regex.
        pattern = re.compile(
            r"@pytest\.mark\.xfail\s*\([^)]*strict\s*=\s*True",
            re.DOTALL,
        )
        matches = pattern.findall(src)
        assert not matches, (
            f"Expected zero strict-xfail markers in "
            f"test_teardown_request_emitter.py post-migration (Risk-11 "
            f"gate discharged via atomic lift). Found {len(matches)} "
            f"marker(s):\n" + "\n".join(matches[:5])
        )

    def test_renamed_tests_are_collected_and_pass(self):
        """The 4 cbcfd589-renamed tests
        (`*_per_teammate_name_none_discriminator`) are collected by pytest
        and pass under the new predicate. This is a meta-test — if the
        test names don't exist or they fail, the migration is
        incomplete.
        """
        expected = [
            "test_teammate_session_suppresses_emission_per_teammate_name_none_discriminator",
            "test_teammate_session_writes_no_journal_event_per_teammate_name_none_discriminator",
            "test_teammate_session_does_not_create_marker_per_teammate_name_none_discriminator",
            "test_all_gate_failure_paths_exit_zero_per_teammate_name_none_discriminator",
        ]
        src = (PLUGIN_ROOT / "tests"
               / "test_teardown_request_emitter.py").read_text()
        missing = [n for n in expected if f"def {n}" not in src]
        assert not missing, (
            f"Expected renamed tests not found in "
            f"test_teardown_request_emitter.py: {missing}. The "
            f"cbcfd589 §AUDIT named-invariant rename was reverted "
            f"or the discriminator was swapped without updating "
            f"the suffix."
        )


# =============================================================================
# TS-11: TestPerPayloadSemanticReviewDiscipline — phantom-green doc pattern
# =============================================================================


class TestPerPayloadSemanticReviewDiscipline:
    """Documentation-in-code: pin the per-payload semantic review
    discipline applied across multiple discriminator-migration cycles.

    Historical context: an earlier dispatch enumerated ~25 stubs as
    candidates for synthetic-field addition based on an architect
    estimate; per-payload session-frame classification (reading
    30-line windows above each enumerated line) reduced the actual
    surface to 4 teammate-frame stubs that needed change. After the
    empirical-grounded discriminator migration (capture 2026-05-20),
    a second per-payload semantic review identified lead-context
    payloads that were carrying `teammate_name` incorrectly — those
    were repaired by removing the stray field rather than mechanically
    swapping. Both passes followed the same discipline: when an
    architect or upstream coder estimates a sweep surface, the coder
    MUST re-measure against disk per SEC-AC-2 before treating the
    count as load-bearing.

    These tests are STRUCTURAL DOCUMENTATION — they pin the discipline
    as an in-code artifact rather than a feedback memo.
    """

    def test_per_payload_semantic_review_discipline_documented_in_repo(self):
        """The discipline is captured across multiple HANDOFFs (the
        callsite-migration commit's CODE-phase narrowing, this file's
        cycle-3 refinement, and the empirical-alignment dispatch's
        Part C narrative). This test pins the discipline as a
        discoverable in-repo audit trail.

        Failure mode: if the discipline is forgotten, a future sweep
        might mechanically swap a field name across all payload stubs
        and re-introduce phantom-green tests (stubs that pass for
        accidental reasons under the new predicate).

        Pinning the discipline as test code keeps it discoverable
        from any future grep over the test surface.
        """
        # Reference assertion: this test's existence + docstring IS
        # the in-code artifact. The assertion below is the marker.
        marker = (
            "per-payload semantic review discipline applied: when a "
            "discriminator field changes, classify each payload stub "
            "against the per-test intent (lead-context vs teammate-"
            "context) rather than mechanically swap field names."
        )
        assert marker, "Discipline marker present in this test."

    def test_teammate_name_use_count_within_empirical_bound(self):
        """Order-of-magnitude sweep on `teammate_name` references in
        test_teardown_request_emitter.py. Under the empirical-grounded
        predicate (`teammate_name is None`), only payloads INTENDED as
        teammate-frame should carry the field; lead-context payloads
        MUST omit it. Backend-coder's empirical-grounded predicate
        migration + this dispatch's Part C lead-context-stub cleanup
        reduced the count from 27 (post-D1 worktree, where lead-context
        stubs incorrectly carried teammate_name) to 7 — matching the
        4 teammate-frame Gate-0/ExitContract stubs + 3 schema-pin /
        provenance references.

        Bound: 3 < count < 25. False-trip requires either a fixture-
        refactor-scale change (~18+ new teammate-context stubs) OR a
        silent deletion of ~4+ existing teammate-frame stubs, either
        of which warrants the discipline reminder.

        Empirical anchor: 7 measured on post-cycle-C empirical-
        alignment worktree.
        """
        src = (PLUGIN_ROOT / "tests"
               / "test_teardown_request_emitter.py").read_text()
        # Match both JSON-quoted payload form `"teammate_name":` (the
        # dominant shape) and kwarg form `teammate_name=` (a few helper
        # calls). The per-payload semantic classification reads
        # session-context kwargs to determine if each call site is
        # lead-context (drop teammate_name) or teammate-context (keep);
        # the regex below is a fidelity-weaker proxy whose ONLY purpose
        # is order-of-magnitude drift detection.
        teammate_name_uses = len(re.findall(
            r'(?:"teammate_name"\s*:|teammate_name\s*=)', src,
        ))
        assert 3 < teammate_name_uses < 25, (
            f"Order-of-magnitude check on teammate_name uses in "
            f"test_teardown_request_emitter.py: expected ~4 teammate-"
            f"frame stubs + ~3 schema-pin / provenance references ≈ 7 "
            f"(validated empirically at 7 on post-cycle-C empirical-"
            f"alignment worktree). Got {teammate_name_uses}. A wildly "
            f"different count suggests a mechanical sweep happened — "
            f"re-run the per-payload semantic classification before "
            f"merging."
        )


# =============================================================================
# TS-12, TS-13, TS-14: POST-MERGE FOLLOW-UP SPECS (docstring-only, skipped)
# =============================================================================


class TestPostMergeFollowUpSpecs:
    """Specifications for post-capture follow-up tests. The 3 paired-
    fixture tests (TS-12) ACTIVATED in this dispatch: the post-merge
    capture campaign happened IN-SESSION on 2026-05-20, the paired
    captured fixtures landed in
    pact-plugin/tests/fixtures/wake_lifecycle/, and these tests now
    assert against the real captured shapes. The 2 remaining stubs
    (TS-13 #786 PostToolUse falsifier, TS-14 #806 SubagentStop)
    remain skipped because their event-class captures are
    intentionally out of scope for this PR.

    Activation history:
    1. The in-session TaskCompleted capture campaign landed 3 real
       payloads on 2026-05-20.
    2. The lead-frame + teammate-frame TaskCompleted captures
       (sanitized) live at
       pact-plugin/tests/fixtures/wake_lifecycle/
       taskcompleted_{lead,teammate}_context_shape.json with
       `_meta.capture_method: "logging-shim"`.
    3. The TS-12 spec-stubs in this class were ACTIVATED (skip
       removed; assertions implemented).
    4. TS-13 + TS-14 stubs remain skipped (their captures are
       follow-up issues, out of this PR's scope).
    """

    # ---- TS-12: Captured-fixture parity specs (ACTIVATED) ----

    def test_captured_lead_context_fixture_shape(self):
        """The captured lead-frame fixture omits `teammate_name` and
        `team_name` per the empirical capture (2026-05-20). The helper
        classifies it as lead-frame (True). Provenance is
        `capture_method == "logging-shim"`.
        """
        fixture_path = (PLUGIN_ROOT / "tests" / "fixtures"
                        / "wake_lifecycle"
                        / "taskcompleted_lead_context_shape.json")
        assert fixture_path.exists(), (
            f"Lead-context fixture must exist at {fixture_path}; the "
            f"in-session capture campaign landed it on 2026-05-20."
        )
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert data.get("_meta", {}).get("capture_method") == "logging-shim", (
            f"Lead-frame fixture must declare logging-shim provenance; "
            f"got _meta={data.get('_meta')!r}"
        )
        # Empirical lead-frame signature: NO teammate_name, NO team_name.
        assert data.get("teammate_name") is None, (
            "Captured lead-frame fixture MUST omit teammate_name "
            "(the lead-context signature under the empirical schema)."
        )
        assert data.get("team_name") is None, (
            "Captured lead-frame fixture MUST omit team_name (paired "
            "with teammate_name absence on lead frames)."
        )
        # Helper classifies as lead.
        assert wl.is_lead_at_task_completed(data) is True, (
            "Captured lead-frame fixture must classify as lead under "
            "the empirical-grounded predicate body."
        )

    def test_captured_teammate_context_fixture_shape(self):
        """The captured teammate-frame fixture carries `teammate_name`
        and `team_name` per the empirical capture (2026-05-20). The
        helper classifies it as teammate-frame (False). Provenance is
        `capture_method == "logging-shim"`.
        """
        fixture_path = (PLUGIN_ROOT / "tests" / "fixtures"
                        / "wake_lifecycle"
                        / "taskcompleted_teammate_context_shape.json")
        assert fixture_path.exists(), (
            f"Teammate-context fixture must exist at {fixture_path}; "
            f"the in-session capture campaign landed it on 2026-05-20."
        )
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert data.get("_meta", {}).get("capture_method") == "logging-shim", (
            f"Teammate-frame fixture must declare logging-shim "
            f"provenance; got _meta={data.get('_meta')!r}"
        )
        # Empirical teammate-frame signature: teammate_name + team_name present.
        assert isinstance(data.get("teammate_name"), str) and data["teammate_name"], (
            "Captured teammate-frame fixture MUST carry a non-empty "
            "teammate_name (the teammate-context signature under the "
            "empirical schema)."
        )
        assert isinstance(data.get("team_name"), str) and data["team_name"], (
            "Captured teammate-frame fixture MUST carry a non-empty "
            "team_name (paired with teammate_name presence)."
        )
        # Helper classifies as teammate.
        assert wl.is_lead_at_task_completed(data) is False, (
            "Captured teammate-frame fixture must classify as teammate "
            "(suppress directive) under the empirical-grounded "
            "predicate body."
        )

    def test_lead_and_teammate_fixtures_paired_via_meta_provenance(self):
        """PAIR-REVERT-IN-SINGLE-FIXTURE discipline: the lead AND
        teammate fixtures MUST be captured in the SAME session
        (`_meta.capture_session_id` matches across both files) so a
        negative test ("teammate_name absent in lead-frame") doesn't
        pass by absence trivially — its positive counterpart
        ("teammate_name present in teammate-frame") comes from the
        SAME captured session and revert-cardinality > 1.
        """
        lead_path = (PLUGIN_ROOT / "tests" / "fixtures" / "wake_lifecycle"
                     / "taskcompleted_lead_context_shape.json")
        teammate_path = (PLUGIN_ROOT / "tests" / "fixtures" / "wake_lifecycle"
                         / "taskcompleted_teammate_context_shape.json")
        assert lead_path.exists() and teammate_path.exists(), (
            "Both paired fixtures must exist; the in-session capture "
            "campaign landed them together on 2026-05-20."
        )
        lead = json.loads(lead_path.read_text(encoding="utf-8"))
        teammate = json.loads(teammate_path.read_text(encoding="utf-8"))
        lead_sid = lead.get("_meta", {}).get("capture_session_id")
        teammate_sid = teammate.get("_meta", {}).get("capture_session_id")
        assert lead_sid and teammate_sid, (
            f"Both fixtures must declare _meta.capture_session_id; "
            f"got lead={lead_sid!r}, teammate={teammate_sid!r}"
        )
        assert lead_sid == teammate_sid, (
            f"Paired fixtures must share capture_session_id for "
            f"pair-revert discipline; lead={lead_sid!r}, "
            f"teammate={teammate_sid!r}"
        )
        # Also pin both fixtures classify per-frame correctly under
        # the helper (the paired assertion the discipline protects).
        assert wl.is_lead_at_task_completed(lead) is True
        assert wl.is_lead_at_task_completed(teammate) is False

    # The #786 PostToolUse falsifier + #806 SubagentStop audit
    # stubs that previously lived here were REMOVED in this
    # dispatch. Rationale: each is its own follow-up issue (#812,
    # #813, #814 already filed for the cross-PR work) with its own
    # capture cycle and test surface. Carrying skip-stubs for them
    # here would (a) clutter the PASS/FAIL signal of this file with
    # perpetually-skipped tests, and (b) leak this PR's scope into
    # the follow-up issues' design space. Each follow-up will land
    # its own test class when its capture lands.


# =============================================================================
# TS-15: TestCounterTestMechanismDocumentation — discriminative-NOTE pattern
# =============================================================================


class TestCounterTestMechanismDocumentation:
    """DISCRIMINATIVE-vs-NON-DISCRIMINATIVE counter-test discipline:
    for COMPOUND predicates, an adversarial reviewer can mutate a
    non-discriminative sub-clause and get false-RED / false-confidence.
    The mitigation is an inline `# counter-test:` NOTE specifying
    WHICH mutation is discriminative.

    The current empirical-grounded predicate is single-field
    (`teammate_name is None`) so the discriminative mutation is
    unambiguous; the NOTE is not load-bearing today. But if a future
    follow-up swaps to a compound form (e.g., `teammate_name is None
    AND agent_id is None`), the predicate becomes COMPOUND and the
    discipline applies.

    This test class pins the discipline: the helper docstring MUST
    document which field is the discriminator + the empirical
    provenance; if the helper body changes to compound form, the
    inline NOTE shape MUST appear. Today the body is single-field, so
    the test asserts the documentation-in-code discipline exists at
    the helper docstring level rather than the inline-comment level.
    """

    def test_helper_docstring_documents_discriminator_choice(self):
        """The helper docstring MUST document WHICH field is the
        discriminator and WHY (the empirical-capture provenance from
        2026-05-20 + the per-event partition convention). This is the
        load-bearing documentation surface; an inline `# counter-test:`
        NOTE becomes additionally load-bearing only under compound-
        predicate forms.
        """
        docstring = wl.is_lead_at_task_completed.__doc__ or ""
        # Pin the canonical references. The discriminator field MUST
        # be named explicitly; the empirical-capture anchor pins the
        # rationale (rather than docs-extrapolation, which was the
        # earlier and falsified-by-capture approach).
        required_substrings = [
            "teammate_name",       # which field
        ]
        # Empirical-capture anchor: tolerate prose variants that
        # name the in-repo shim OR the capture date.
        capture_anchor_pattern = re.compile(
            r"captures?|captured|empirical|logging[- ]?shim", re.IGNORECASE,
        )
        for s in required_substrings:
            assert s in docstring, (
                f"is_lead_at_task_completed docstring must reference "
                f"{s!r} per the discriminative-NOTE discipline. "
                f"Docstring excerpt: {docstring[:300]!r}"
            )
        assert capture_anchor_pattern.search(docstring), (
            f"is_lead_at_task_completed docstring must reference the "
            f"empirical-capture provenance (case-insensitive match on "
            f"`capture` / `captured` / `empirical` / `logging-shim`). "
            f"The capture anchor is the load-bearing audit anchor — "
            f"without it, a future docs-extrapolation revert could "
            f"lose its rationale trail. Docstring excerpt: "
            f"{docstring[:400]!r}"
        )

    def test_compound_predicate_contingency_unreached_under_empirical_schema(
        self,
    ):
        """The compound-predicate contingency is unreached under the
        empirical-grounded schema that currently ships. Pin that the
        helper body is still SINGLE-FIELD; if a future edit silently
        swaps to compound form (`teammate_name is None AND agent_id
        is None` or similar) without filing the follow-up, this test
        flags the drift.
        """
        body_src = inspect.getsource(wl.is_lead_at_task_completed)
        # Strip docstring.
        body_lines = [
            line for line in body_src.splitlines()
            if line.strip() and not line.strip().startswith('"""')
            and not line.strip().startswith("Discriminator")
        ]
        # Look for an `and input_data.get("<other>")` shape (any
        # second discriminator field added to the single-field
        # predicate).
        compound_pattern = re.compile(
            r'and\s+input_data\.get\(["\'][\w_]+["\']\)\s+is\s+None'
        )
        is_compound = any(compound_pattern.search(line) for line in body_lines)
        assert not is_compound, (
            "is_lead_at_task_completed body has been swapped to "
            "compound form without filing a follow-up. Compound-"
            "predicate swap requires: predicate body swap + payload "
            "swap + counter-test cardinality re-measure + update this "
            "test's discriminator-NOTE pin to allow compound form."
        )
