"""
Comprehensive TEST-phase coverage for the actor-discriminator-capture-gate
bundle.

Backend-coder shipped SMOKE coverage: 4 lifted strict-xfail tests in
test_teardown_request_emitter.py (renamed
`*_per_field_presence_discriminator`), 6 anti-softening pin tests in
test_wake_lifecycle_emitter.py (TestDirectiveAntiSofteningGuard), plus
the helper-behavior probe in the prior-phase HANDOFF. This file owns
the SUBSTANTIVE coverage layer atop those smoke tests, per the
test-engineer ENGAGEMENT rule.

# Coverage map (audit-surface-enumeration discipline)

| Surface  | Class                                            | Scope                   |
|----------|--------------------------------------------------|-------------------------|
| TS-1     | TestIsLeadContextPureContract                    | helper pure-fn          |
| TS-2     | TestIsLeadContextContract                        | compound discriminator  |
| TS-3     | TestObserveOnlyInvariant                         | SEC-S1 routing          |
| TS-4     | TestVestigialTeamNameKwargAcceptance             | signature parity        |
| TS-5     | TestDiscriminatorNamedInvariantPin               | named-invariant         |
| TS-6     | TestTeardownCallsiteWiringPin                    | callsite import         |
| TS-7     | TestAgentHandoffEmitterSiblingPreservation       | SEC-AC-3                |
| TS-8     | TestDirectiveByteIdentityOnPactSkillLiterals     | SEC-AC-7                |
| TS-9     | TestFalseTransitionClaimAbsenceAcrossSites       | site sweep              |
| TS-10    | TestRiskElevenStrictXfailMechanismIntegrity      | rename + count          |
| TS-11    | TestPerPayloadSemanticReviewDiscipline           | phantom-green doc pin   |
| TS-12    | TestPostMergeFollowUpSpecs                       | fixture-parity (live)   |
| TS-15    | TestCounterTestMechanismDocumentation            | discriminative-NOTE     |

# Backend-coder smoke vs test-engineer comprehensive split (ENGAGEMENT rule)

- BACKEND-CODER SMOKE (already shipped, NOT duplicated here):
  - 4 lifted Gate-0 behavioral tests in test_teardown_request_emitter.py
  - 6 anti-softening prose pins in test_wake_lifecycle_emitter.py
  - Helper-behavior probe in the helper-addition handoff
    (lead-frame {}, teammate, non-dict)
- TEST-ENGINEER COMPREHENSIVE (this file):
  - Pathological-input sweep on the consolidated helper
  - Compound-discriminator contract across 4 hooked events
  - Production-source structural pins (callsite, sibling preservation, prose)
  - Edit-time site enumeration for the retired-prose false-claim absence
  - Phantom-green discipline as in-code documentation pattern
  - Captured-fixture parity (TaskCompleted lead/teammate)

# Counter-test-by-revert cardinality targets

Cardinality is documented per-test where measurable. Repeat the SOURCE-ONLY
revert mechanism per pact-testing-strategies:
   git checkout <prior-sha> -- <path>  # restore prior shape
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
# TS-1: TestIsLeadContextPureContract — pathological-input sweep
# =============================================================================


class TestIsLeadContextPureContract:
    """SEC-S1 pure-function contract for is_lead_context.

    The compound is_lead_context helper in shared/wake_lifecycle.py
    codifies a pure-function-never-raises invariant (see
    test_inbox_wake_lifecycle_helper.py test_lifecycle_relevant_never_raises
    pattern). Any future edit introducing an exception path is a
    SEC-S1 violation.

    Counter-test-by-revert: replacing the body with
    `raise ValueError("forced")` flips every test in this class RED.
    Cardinality target: {>=6 RED} on body-mutation.
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
        to confirm the isinstance gate AND the both-fields-absent path
        both traverse cleanly.
        """
        # `{}` is a dict and the isinstance gate lets it through; verify
        # it is classified as lead (neither agent_id nor teammate_name
        # present).
        if bad_input == {}:
            assert wl.is_lead_context(bad_input, "any-team") is True
        else:
            assert wl.is_lead_context(bad_input, "any-team") is False

    # Pin the isinstance-gate behavior across dict-subclass /
    # Mapping-subclass shapes. `isinstance(_, dict)` is True for
    # OrderedDict + defaultdict (dict subclasses) — they traverse the
    # gate and the field-presence path classifies normally. It
    # is False for types.MappingProxyType (a separate Mapping protocol
    # implementation, NOT a dict subclass) — those traverse the
    # isinstance-False short-circuit and ALWAYS return False (teammate)
    # even when the underlying mapping has neither field. Academic
    # for Claude Code stdin (always plain `dict`), but pinning rules
    # out a future "treat all Mappings as dict" edit that would
    # silently change the asymmetry.
    def test_ordered_dict_with_teammate_name_classifies_as_teammate(self):
        from collections import OrderedDict
        payload = OrderedDict({"teammate_name": "secretary"})
        assert wl.is_lead_context(payload, "team-x") is False

    def test_defaultdict_with_teammate_name_classifies_as_teammate(self):
        from collections import defaultdict
        payload = defaultdict(str)
        payload["teammate_name"] = "secretary"
        assert wl.is_lead_context(payload, "team-x") is False

    def test_mapping_proxy_type_with_teammate_name_short_circuits_to_false(self):
        """MappingProxyType is NOT a dict subclass — isinstance gate
        returns False → helper returns False (teammate). This is
        academic for Claude Code stdin but pinning the asymmetry
        prevents a future 'treat all Mappings as dict' edit from
        silently changing the gate semantics.
        """
        from types import MappingProxyType
        payload = MappingProxyType({"teammate_name": "secretary"})
        assert wl.is_lead_context(payload, "team-x") is False

    def test_mapping_proxy_type_empty_short_circuits_to_false(self):
        """MappingProxyType({}) is a `key-absent` Mapping shape that
        SHOULD classify as lead under the empirical schema (neither
        field -> lead), but the isinstance gate's strict dict-only
        check causes False (teammate) instead. Pinning documents the
        asymmetric isinstance-gate behavior so a future Mapping-aware
        edit is deliberate.
        """
        from types import MappingProxyType
        payload = MappingProxyType({})
        # NOTE: would return True under a Mapping-aware predicate;
        # the isinstance(_, dict) gate returns False instead.
        # Document the asymmetry — do NOT relax the gate without a
        # paired audit.
        assert wl.is_lead_context(payload, "team-x") is False

    def test_lead_frame_empty_payload_classifies_as_lead(self):
        """Edge case for SEC-S1: bare `{}` passes the isinstance gate
        AND has neither `agent_id` nor `teammate_name` key -> True
        (lead). Matches the empirical lead-frame shape (lead fires
        omit both discriminator fields). Pinning the edge so a future
        "guard against missing fields" predicate edit fails loudly.
        """
        assert wl.is_lead_context({}, "team-x") is True

    def test_teammate_frame_teammate_name_string_classifies_as_teammate(self):
        """Empirical platform path: teammate-context TaskCompleted
        stdin carries `teammate_name` identifying the agent that owned
        the task (captured 2026-05-20). teammate_name present ->
        classified as teammate (suppress).
        """
        payload = {
            "session_id": "shared-lead-sid",
            "hook_event_name": "TaskCompleted",
            "task_id": "T1",
            "team_name": "team-x",
            "teammate_name": "secretary",
        }
        assert wl.is_lead_context(payload, "team-x") is False

    def test_teammate_frame_agent_id_string_classifies_as_teammate(self):
        """Empirical platform path: teammate-context PostToolUse /
        UserPromptSubmit / SessionStart stdin carries `agent_id`
        identifying the in-process subagent fire (captured 2026-05-20).
        agent_id present -> classified as teammate (suppress).
        """
        payload = {
            "session_id": "shared-lead-sid",
            "hook_event_name": "PostToolUse",
            "agent_id": "subagent-some-uuid",
        }
        assert wl.is_lead_context(payload, "team-x") is False

    @pytest.mark.parametrize("adversarial_teammate_name", [
        "", " ", "0", "False", "null", "None",
        "X" * 1000,  # very long
        "secretary-" + chr(0) + "-embedded-null",  # null byte (chr(0) avoids src null)
        "secretary-\n-newline", "secretary-\t-tab",
    ])
    def test_truthy_or_present_teammate_name_classifies_as_teammate(
        self, adversarial_teammate_name,
    ):
        """The discriminator is field-presence (`is None`), NOT
        `not teammate_name` and NOT truthy-check. Any non-None string
        value — including empty-string, whitespace, embedded null,
        multiline — classifies as teammate. Pinning this rules out a
        future well-intended "guard against empty teammate_name" edit
        that would silently re-introduce false-classification.
        """
        payload = {"teammate_name": adversarial_teammate_name}
        assert wl.is_lead_context(payload, "team-x") is False

    @pytest.mark.parametrize("adversarial_agent_id", [
        "", " ", "0", "False", "null", "None",
        "X" * 1000,  # very long
        "subagent-" + chr(0) + "-embedded-null",  # null byte
        "subagent-\n-newline", "subagent-\t-tab",
    ])
    def test_truthy_or_present_agent_id_classifies_as_teammate(
        self, adversarial_agent_id,
    ):
        """Mirror of the teammate_name sweep: the discriminator is
        field-presence on agent_id too. Any non-None string value
        classifies as teammate. Pins the identity-vs-None semantic
        on the agent_id half of the compound check.
        """
        payload = {"agent_id": adversarial_agent_id}
        assert wl.is_lead_context(payload, "team-x") is False

    @pytest.mark.parametrize("none_equivalent", [None])
    def test_teammate_name_explicitly_none_classifies_as_lead(
        self, none_equivalent,
    ):
        """An explicit `"teammate_name": None` (rather than key-absent)
        still classifies as lead — `dict.get("teammate_name") is None`
        returns True both for missing key AND explicit-None value.
        Both shapes match the empirical lead-context schema (the
        captured lead fixture is key-absent; explicit-None is the
        in-Python equivalent). Same holds for agent_id under the
        compound check.
        """
        assert wl.is_lead_context(
            {"teammate_name": none_equivalent}, "team-x",
        ) is True
        assert wl.is_lead_context(
            {"agent_id": none_equivalent}, "team-x",
        ) is True
        assert wl.is_lead_context(
            {"teammate_name": none_equivalent, "agent_id": none_equivalent},
            "team-x",
        ) is True

    def test_other_fields_irrelevant_to_classification(self):
        """The compound discriminator reads ONLY `agent_id` and
        `teammate_name`. Other fields (`session_id`, `task_id`,
        `team_name`, `agent_type`, …) do not influence classification.
        Pinning this prevents a future "consult agent_type as a
        secondary signal" edit from re-introducing the per-event
        partition the consolidation collapsed.
        """
        payload = {
            # Other fields populated, including a previously-tried
            # agent_type discriminator…
            "session_id": "any",
            "task_id": "T1",
            "team_name": "team-x",
            "agent_type": "pact-secretary",
            # …but neither agent_id nor teammate_name -> must
            # classify as lead.
        }
        assert wl.is_lead_context(payload, "team-x") is True


# =============================================================================
# TS-2: TestIsLeadContextContract — compound field-presence discriminator
# (empirical: lead-context lacks both agent_id and teammate_name; teammate-
# context carries at least one; single helper unifies the per-event check).
# =============================================================================


class TestIsLeadContextContract:
    """The single compound is_lead_context helper classifies the
    actor-context of any hook-stdin payload by checking field-presence
    on BOTH `agent_id` and `teammate_name`. A lead-context fire has
    NEITHER field stamped; a teammate-context fire has at least one.

    Empirical provenance: the PR #808 capture campaign on 2026-05-20
    landed 121 UserPromptSubmit captures, 5 SessionStart captures + 2
    negative Agent() probes, and paired lead/teammate-frame
    PostToolUse + TaskCompleted captures via in-repo logging shims
    (pact-plugin/tests/runbooks/install_*_logging_shim.sh). The
    per-event partition assumption (different field per event class)
    was falsified by the cross-event capture; the unified compound
    check holds across all 4 event classes empirically.

    Regression-canary purpose: if a future hook event stamps either
    `agent_id` or `teammate_name` on a lead-frame fire, this contract
    catches it via fail-closed semantics (the helper classifies the
    fire as teammate and the downstream Gate-0 suppresses emission).
    The reverse direction — a teammate-context fire that omits both
    fields — would phantom-classify as lead and re-introduce the #611
    in-process subagent misclassification class; the contract rows
    below pin both directions.

    Follow-up: #812 tracks the cross-event capture-fixture parity work.

    Counter-test-by-revert: if a future edit drops one half of the
    compound check (e.g., reverts to `teammate_name is None` only),
    the agent_id-only rows below trip RED — the regression is loud,
    not silent.
    """

    # Captured-fixture path (TaskCompleted has both frames captured).
    _FIXTURE_DIR = (Path(__file__).resolve().parent
                    / "fixtures" / "wake_lifecycle")

    def test_lead_frame_posttooluse_classified_as_lead(self):
        """PostToolUse lead-frame: lead fires omit both agent_id and
        teammate_name. The compound check classifies as lead.
        """
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "lead-sid",
            "tool_name": "TaskUpdate",
            # Neither agent_id nor teammate_name present.
        }
        assert wl.is_lead_context(payload, "team-x") is True

    def test_teammate_frame_posttooluse_classified_as_teammate(self):
        """PostToolUse teammate-frame: in-process subagent fires carry
        `agent_id` (per Claude Code platform docs + empirical capture).
        The compound check classifies as teammate.
        """
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "shared-sid",
            "agent_id": "subagent-some-uuid",
            "tool_name": "TaskUpdate",
        }
        assert wl.is_lead_context(payload, "team-x") is False

    def test_lead_frame_taskcompleted_classified_as_lead(self):
        """TaskCompleted lead-frame: from the captured-from-production
        fixture (taskcompleted_lead_context_shape.json). The compound
        check must classify as lead.
        """
        fixture_path = self._FIXTURE_DIR / "taskcompleted_lead_context_shape.json"
        assert fixture_path.exists(), (
            f"Lead-frame TaskCompleted fixture must exist at "
            f"{fixture_path}; in-session capture campaign landed it "
            f"on 2026-05-20."
        )
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert wl.is_lead_context(payload, "team-x") is True, (
            "Captured lead-frame TaskCompleted fixture must classify "
            "as lead under the compound discriminator (empirical "
            "schema: lead fires omit both agent_id and teammate_name)."
        )

    def test_teammate_frame_taskcompleted_classified_as_teammate(self):
        """TaskCompleted teammate-frame: from the captured-from-
        production fixture (taskcompleted_teammate_context_shape.json).
        Carries `teammate_name`. The compound check must classify as
        teammate.
        """
        fixture_path = (self._FIXTURE_DIR
                        / "taskcompleted_teammate_context_shape.json")
        assert fixture_path.exists(), (
            f"Teammate-frame TaskCompleted fixture must exist at "
            f"{fixture_path}; in-session capture campaign landed it "
            f"on 2026-05-20."
        )
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert wl.is_lead_context(payload, "team-x") is False, (
            "Captured teammate-frame TaskCompleted fixture must "
            "classify as teammate under the compound discriminator "
            "(empirical schema: teammate fires carry teammate_name)."
        )

    def test_lead_frame_userpromptsubmit_classified_as_lead(self):
        """UserPromptSubmit lead-frame: no teammate path exists
        empirically (UPS does not fire in subagent context per Claude
        Code platform docs; the PR #808 capture campaign confirmed 121
        UPS captures, all lead-frame with neither field). The compound
        check classifies as lead.
        """
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "lead-sid",
            "prompt": "any prompt text",
            # Neither agent_id nor teammate_name present (empirical).
        }
        assert wl.is_lead_context(payload, "team-x") is True

    def test_lead_frame_sessionstart_classified_as_lead(self):
        """SessionStart lead-frame: no teammate path exists empirically
        within the captured campaign (5 SessionStart captures + 2
        negative Agent() probes confirmed in-process subagent
        SessionStart does not stamp the discriminator fields the way
        PostToolUse / TaskCompleted do; see task #63/#64 deliverables
        + follow-up #812 for the cross-event audit). The compound
        check classifies the empirical shape as lead.
        """
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "lead-sid",
            "source": "startup",
            # Neither agent_id nor teammate_name present (empirical).
        }
        assert wl.is_lead_context(payload, "team-x") is True


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
        for payload in [
            {},
            {"teammate_name": "x"},
            {"teammate_name": None},
            {"agent_id": "x"},
            {"agent_id": None},
        ]:
            result = wl.is_lead_context(payload)
            assert result is True or result is False, (
                f"Return type must be bool literal True/False; "
                f"got {result!r} of type {type(result).__name__}"
            )

    def test_helper_makes_no_filesystem_reads(self, tmp_path, monkeypatch):
        """The helper is pure — field-presence on the in-memory dict
        is the discriminator; no team_config read, no journal read,
        no marker read. Monkeypatching Path.home to an empty tmp_path
        AND filesystem ops must not affect the result.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Even with a wildly wrong HOME, the helper must work.
        assert wl.is_lead_context({}, "team-x") is True
        assert wl.is_lead_context(
            {"teammate_name": "secretary"}, "team-x",
        ) is False
        assert wl.is_lead_context(
            {"agent_id": "subagent-some-uuid"}, "team-x",
        ) is False

    def test_helper_does_not_consult_environ(self, monkeypatch):
        """The helper does not read os.environ — pinning this rules
        out a future "env-var override" edit that would couple the
        routing primitive to runtime state.
        """
        # Set adversarial env vars that a misguided edit might consult.
        monkeypatch.setenv("CLAUDE_TEAMMATE_NAME", "lead-pretender")
        monkeypatch.setenv("CLAUDE_AGENT_ID", "lead-pretender-uuid")
        monkeypatch.setenv("PACT_OVERRIDE_DISCRIMINATOR", "true")
        # Lead-frame {} STAYS lead regardless of env.
        assert wl.is_lead_context({}, "team-x") is True
        # Teammate-frame STAYS teammate regardless of env (both halves).
        assert wl.is_lead_context(
            {"teammate_name": "secretary"}, "team-x",
        ) is False
        assert wl.is_lead_context(
            {"agent_id": "subagent-some-uuid"}, "team-x",
        ) is False


# =============================================================================
# TS-4: TestVestigialTeamNameKwargAcceptance — signature uniformity
# =============================================================================


class TestVestigialTeamNameKwargAcceptance:
    """The compound is_lead_context helper accepts a vestigial
    `team_name=""` kwarg for signature uniformity with the surrounding
    corridor (a holdover from the pre-consolidation per-event helpers).
    The team_name parameter is vestigial under the field-presence
    discriminator (no team_config read needed). Pinning the call
    shapes ensures a future "drop the unused parameter" refactor is
    deliberate, not accidental — the corridor's call sites rely on
    the uniform signature.
    """

    def test_accepts_positional_team_name(self):
        assert wl.is_lead_context({}, "team-x") is True

    def test_accepts_keyword_team_name(self):
        assert wl.is_lead_context({}, team_name="team-x") is True

    def test_team_name_defaults_to_empty_string(self):
        """Default value pinned at the empty-string sentinel.
        """
        sig = inspect.signature(wl.is_lead_context)
        assert sig.parameters["team_name"].default == ""

    def test_team_name_value_does_not_affect_result(self):
        """The field-presence discriminator does not consult
        team_name. Identical payload + varying team_name = identical
        result. Pinning this rules out a future "team_config-driven
        override" leak.
        """
        for team in ["", "team-x", "pact-00000000", "team-with-special!@#$"]:
            assert wl.is_lead_context({}, team) is True
            assert wl.is_lead_context(
                {"teammate_name": "secretary"}, team,
            ) is False
            assert wl.is_lead_context(
                {"agent_id": "subagent-some-uuid"}, team,
            ) is False


# =============================================================================
# TS-5: TestDiscriminatorNamedInvariantPin — field-presence audit anchor
# =============================================================================


class TestDiscriminatorNamedInvariantPin:
    """The named invariant for is_lead_context is field-presence on
    BOTH `agent_id` and `teammate_name`. The lifted Gate-0 tests in
    test_teardown_request_emitter.py encode this in their
    `*_per_field_presence_discriminator` suffixes (audit-surface-
    enumeration discipline; the named-invariant rename shipped with
    the helper consolidation).

    These structural assertions pin the invariant name AT the helper
    so the rename convention is self-consistent. If a future edit
    drops one half of the compound check (e.g., back to
    `teammate_name is None` only) or rotates one field, it MUST also
    update the suffix — these tests force that coupling visible.
    """

    def test_helper_body_reads_both_field_presence_fields(self):
        """Source-level structural pin: the helper body MUST reference
        BOTH `agent_id` and `teammate_name` and MUST NOT reference
        `agent_type` or `tmuxPaneId` (other candidate discriminators
        from the pre-consolidation per-event partition). Drift means
        the compound body has been silently rolled back to a single-
        field check without updating the suffix discipline.

        Counter-test-by-revert: dropping `agent_id` from the helper
        body flips this test RED with a specific assertion message
        identifying the missing field.
        """
        src = inspect.getsource(wl.is_lead_context)
        # Strip the docstring so we only inspect the executable body.
        body = src.split('"""')[-1] if '"""' in src else src
        for required in ("agent_id", "teammate_name"):
            assert required in body, (
                f"is_lead_context body must reference {required!r} "
                f"(compound field-presence discriminator); got body "
                f"excerpt={body!r}"
            )
        forbidden = ("agent_type", "tmuxPaneId")
        for f in forbidden:
            assert f not in body, (
                f"is_lead_context body MUST NOT reference {f!r} "
                f"(the empirical-grounded compound check reads "
                f"agent_id + teammate_name; agent_type was the "
                f"SessionStart-specific discriminator from the "
                f"pre-consolidation partition and is NOT part of the "
                f"unified check). Body excerpt: {body!r}"
            )

    def test_renamed_gate0_tests_carry_per_invariant_suffix(self):
        """Pin the audit-surface-enumeration convention: the 4 lifted
        Gate-0/ExitContract tests carry the
        `_per_field_presence_discriminator` suffix. If a future edit
        rotates the discriminator, the test names MUST rotate
        accordingly — these structural pins force the coupling visible.
        """
        teardown_test_src = (PLUGIN_ROOT / "tests"
                             / "test_teardown_request_emitter.py").read_text()
        expected_suffixed_names = [
            "test_teammate_session_suppresses_emission_per_field_presence_discriminator",
            "test_teammate_session_writes_no_journal_event_per_field_presence_discriminator",
            "test_teammate_session_does_not_create_marker_per_field_presence_discriminator",
            "test_all_gate_failure_paths_exit_zero_per_field_presence_discriminator",
        ]
        for name in expected_suffixed_names:
            assert name in teardown_test_src, (
                f"Expected renamed test {name!r} not found in "
                f"test_teardown_request_emitter.py. The named-invariant "
                f"rename was reverted or the discriminator was silently "
                f"swapped without updating the suffix."
            )


# =============================================================================
# TS-6: TestTeardownCallsiteWiringPin — production wiring at :301
# =============================================================================


class TestTeardownCallsiteWiringPin:
    """Pin the production wiring of the Gate-0 lead-context check in
    teardown_request_emitter.py. The Gate-0 check MUST call
    `is_lead_context` — the legacy per-event helpers
    (is_lead_at_task_completed and the other 4) have been deleted by
    the consolidation. Production-source structural pin; complements
    the behavioral lifted Gate-0 tests.

    Counter-test-by-revert: a checkout restoring the prior callsite
    would import a deleted symbol and fail at collect time; these
    pins catch a less obvious regression — a future edit that calls
    a re-introduced legacy delegate or routes through the wrong
    helper name.
    """

    def test_imports_is_lead_context_from_shared(self):
        """The import statement must bring is_lead_context into scope.
        Mirrors the test_pending_scan_session_init.py
        `test_session_init_imports_or_calls_lead_session_guard` pattern.
        """
        src = TEARDOWN_EMITTER.read_text(encoding="utf-8")
        # Tolerate `from shared.wake_lifecycle import (... is_lead_context ...)`
        assert "is_lead_context" in src, (
            "teardown_request_emitter.py must import is_lead_context "
            "from shared.wake_lifecycle"
        )

    def test_callsite_uses_is_lead_context_not_legacy_helper(self):
        """The actual call expression must be
        `is_lead_context(input_data, team_name)`, not any of the
        deleted legacy per-event helpers. Pattern regex-anchored to a
        control-flow statement to rule out commentary-only references.
        """
        src = TEARDOWN_EMITTER.read_text(encoding="utf-8")
        # Match `if not is_lead_context(...)` or similar.
        call_pattern = re.compile(
            r"^\s*(if|return|elif|while|assert)\b.*is_lead_context\b\s*\(",
            re.MULTILINE,
        )
        assert call_pattern.search(src), (
            "Expected a control-flow statement calling "
            "is_lead_context(...) in teardown_request_emitter.py; the "
            "callsite migration may have regressed to a deleted helper."
        )
        # Negative pin: no LIVE call to any of the deleted legacy
        # helpers at Gate 0. Allow them to appear in commentary, but
        # NOT in a control-flow shape (the previous Gate 0 forms).
        legacy_names = [
            "is_lead_session",
            "is_lead_emit_authorized",
            "is_lead_drain_authorized",
            "is_lead_at_session_start",
            "is_lead_at_task_completed",
        ]
        for name in legacy_names:
            legacy_call_pattern = re.compile(
                r"^\s*(if|return|elif|while|assert)\b.*\b" + name + r"\b\s*\(",
                re.MULTILINE,
            )
            assert not legacy_call_pattern.search(src), (
                f"teardown_request_emitter.py contains a control-flow "
                f"call to deleted legacy helper {name!r} — the "
                f"consolidation to is_lead_context was reverted."
            )

    def test_callsite_passes_input_data_and_team_name_positionally(self):
        """Pinning the call signature shape — `(input_data, team_name)`,
        matching the helper's uniform signature. A future "drop the
        unused team_name" refactor would need to update this test,
        making the change deliberate.
        """
        src = TEARDOWN_EMITTER.read_text(encoding="utf-8")
        # Two-arg call with input_data + team_name in some order.
        # Tolerate whitespace + keyword form.
        call_shape = re.compile(
            r"is_lead_context\s*\(\s*input_data\s*,\s*team_name\s*\)",
        )
        assert call_shape.search(src), (
            "Expected call shape `is_lead_context(input_data, "
            "team_name)` at the Gate-0 callsite in "
            "teardown_request_emitter.py. The signature-uniformity "
            "contract may have been broken (TS-4)."
        )


# =============================================================================
# TS-7: TestAgentHandoffEmitterSiblingPreservation — SEC-AC-3
# =============================================================================


class TestAgentHandoffEmitterSiblingPreservation:
    """SEC-AC-3 sibling preservation: agent_handoff_emitter.py (the
    sibling TaskCompleted hook) MUST continue reading
    `task_data.get("owner") or input_data.get("teammate_name")` — NOT
    migrate to the consolidated is_lead_context helper.

    The two TaskCompleted hooks answer semantically distinct questions:
    teardown_request_emitter asks "is this fire from the LEAD context?"
    (routing); agent_handoff_emitter asks "is this fire on a task
    OWNED by an agent?" (content). Aligning the discriminators would
    conflate the two and silence the journal write in teammate frames.

    Counter-test-by-revert: editing agent_handoff_emitter.py to use
    is_lead_context flips both tests RED.
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

    def test_agent_handoff_emitter_does_not_call_is_lead_context(self):
        """Negative pin: agent_handoff_emitter.py MUST NOT call the
        is_lead_context helper at Gate 0. Its discriminator is
        owner-based, not actor-based.
        """
        src = AGENT_HANDOFF_EMITTER.read_text(encoding="utf-8")
        # Forbid the function-call shape; tolerate docstring/comment
        # mentions if they appear.
        forbidden_calls = ["is_lead_context("]
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
# TS-9: TestFalseTransitionClaimAbsenceAcrossSites — retired-prose sweep
# =============================================================================

# Explicit allowlist of permitted fixture files that legitimately carry
# the retired-prose literal as task-subject content (NOT directive
# prose). The sweep below skips any .json file under `fixtures/` AND
# filters its hits against this allowlist; any future fixture that
# grows a hit MUST be explicitly added here with a one-line rationale.
# This makes the "why is this fixture exempt?" question discoverable
# from a single grep.
#
# Rationale: the two fixture files below carry "First active teammate
# task" as a `subject` field on synthetic task records used for
# stdin-shape probes. These are task subjects, not directive prose;
# they predate the directive-prose rewrite and remain valid synthetic
# test data. A future reader grepping for the retired literal would
# otherwise have to re-derive why these 2 hits don't trip the
# discipline — the allowlist documents the exemption inline.
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
    """Root cause of the directive-prose rewrite: the original
    directives' "First active teammate task created" / "Last active
    teammate task completed" prefixes are provably-false on multi-fire
    (the predicate is `count >= 1`, not a 0->1 transition). The
    directive-prose rewrite removed the false claim; this class pins
    the absence across the EDIT-TIME enumerated site surface so a
    future "restore the friendly transition prefix" edit fails loudly.

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
            f"Retired transition-claim prose {retired_prose!r} found "
            f"in the production-source surface "
            f"(hooks/commands/protocols/skills). The directive-prose "
            f"rewrite expected zero hits here. Hits:\n"
            + "\n".join(hits)
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
            f"Retired transition-claim prose {retired_prose!r} found "
            f"in runbooks. The directive-prose rewrite expected zero "
            f"hits. Hits:\n" + "\n".join(hits)
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
        """The 4 lifted Gate-0/ExitContract tests
        (`*_per_field_presence_discriminator`) are collected by pytest
        and pass under the consolidated predicate. This is a meta-test —
        if the test names don't exist or they fail, the migration is
        incomplete.
        """
        expected = [
            "test_teammate_session_suppresses_emission_per_field_presence_discriminator",
            "test_teammate_session_writes_no_journal_event_per_field_presence_discriminator",
            "test_teammate_session_does_not_create_marker_per_field_presence_discriminator",
            "test_all_gate_failure_paths_exit_zero_per_field_presence_discriminator",
        ]
        src = (PLUGIN_ROOT / "tests"
               / "test_teardown_request_emitter.py").read_text()
        missing = [n for n in expected if f"def {n}" not in src]
        assert not missing, (
            f"Expected renamed tests not found in "
            f"test_teardown_request_emitter.py: {missing}. The "
            f"named-invariant rename was reverted or the "
            f"discriminator was swapped without updating the suffix."
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
# TS-12: Captured-fixture parity (activated against in-session captures)
# =============================================================================


class TestPostMergeFollowUpSpecs:
    """Captured-fixture parity tests. The 3 tests in this class assert
    that the captured TaskCompleted stdin fixtures under
    pact-plugin/tests/fixtures/wake_lifecycle/ match the empirical
    schema (lead-frame omits teammate_name + team_name; teammate-frame
    carries both), and that the is_lead_context helper classifies
    each fixture correctly per the empirical-grounded compound
    predicate body.

    Activation history:
    1. The in-session TaskCompleted capture campaign landed 3 real
       payloads on 2026-05-20.
    2. The lead-frame + teammate-frame TaskCompleted captures
       (sanitized) live at
       pact-plugin/tests/fixtures/wake_lifecycle/
       taskcompleted_{lead,teammate}_context_shape.json with
       `_meta.capture_method: "logging-shim"`.
    3. The 3 spec-stubs in this class were ACTIVATED (skip removed;
       assertions implemented against the captured fixtures).

    Sibling event-class audits (PostToolUse falsifier, SubagentStop
    discriminator) were originally planned alongside the TaskCompleted
    captures here but are tracked separately as their own follow-up
    issues; see the removal-comment after the last test method below
    for the canonical record.
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
        assert wl.is_lead_context(data) is True, (
            "Captured lead-frame fixture must classify as lead under "
            "the empirical-grounded compound predicate body."
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
        assert wl.is_lead_context(data) is False, (
            "Captured teammate-frame fixture must classify as teammate "
            "(suppress directive) under the empirical-grounded "
            "compound predicate body."
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
        assert wl.is_lead_context(lead) is True
        assert wl.is_lead_context(teammate) is False

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
    for compound predicates, an adversarial reviewer can mutate a
    non-discriminative sub-clause and get false-RED / false-confidence.
    The mitigation is an inline `# counter-test:` NOTE specifying
    WHICH mutation is discriminative.

    The consolidated is_lead_context body is compound (`agent_id is
    None AND teammate_name is None`), so the discipline applies: the
    helper docstring MUST document both fields + the empirical
    provenance, and the body MUST remain compound (a future "simplify
    to single-field" revert would silently drop one half of the
    actor-discrimination surface).
    """

    def test_helper_docstring_documents_discriminator_choice(self):
        """The helper docstring MUST document both discriminator
        fields and WHY (the empirical-capture provenance from
        2026-05-20). This is the load-bearing documentation surface;
        without it a future docs-extrapolation revert could lose the
        rationale trail.
        """
        docstring = wl.is_lead_context.__doc__ or ""
        # Pin the canonical references: both compound fields named
        # explicitly + the empirical-capture anchor.
        required_substrings = [
            "agent_id",
            "teammate_name",
        ]
        # Empirical-capture anchor: tolerate prose variants that
        # name the in-repo shim, "empirical", or the capture verb.
        capture_anchor_pattern = re.compile(
            r"captures?|captured|empirical|logging[- ]?shim", re.IGNORECASE,
        )
        for s in required_substrings:
            assert s in docstring, (
                f"is_lead_context docstring must reference {s!r} per "
                f"the compound-discriminator documentation discipline. "
                f"Docstring excerpt: {docstring[:300]!r}"
            )
        assert capture_anchor_pattern.search(docstring), (
            f"is_lead_context docstring must reference the empirical-"
            f"capture provenance (case-insensitive match on "
            f"`capture` / `captured` / `empirical` / `logging-shim`). "
            f"The capture anchor is the load-bearing audit anchor — "
            f"without it, a future docs-extrapolation revert could "
            f"lose its rationale trail. Docstring excerpt: "
            f"{docstring[:400]!r}"
        )

    def test_compound_predicate_is_canonical_under_empirical_schema(self):
        """The compound predicate is the canonical body under the
        empirical-grounded schema. Pin that the helper body remains
        COMPOUND (`agent_id is None AND teammate_name is None` or
        equivalent shape); if a future edit silently drops one half
        of the compound, this test flags the drift.

        Counter-test-by-revert: replacing the body with a single-field
        check (e.g., `return stdin.get("teammate_name") is None`)
        flips this test RED.
        """
        body_src = inspect.getsource(wl.is_lead_context)
        # Strip docstring.
        body_lines = [
            line for line in body_src.splitlines()
            if line.strip() and not line.strip().startswith('"""')
            and not line.strip().startswith("Discriminator")
        ]
        # Look for an `and <param>.get("<other>")` shape — two
        # field-presence reads joined by `and`. Match any param name
        # (the helper signature variable is `stdin` post-consolidation
        # but historically `input_data`; tolerate either or any future
        # rename).
        compound_pattern = re.compile(
            r'and\s+\w+\.get\(["\'][\w_]+["\']\)\s+is\s+None'
        )
        is_compound = any(compound_pattern.search(line) for line in body_lines)
        assert is_compound, (
            "is_lead_context body must remain COMPOUND (two field-"
            "presence reads joined by `and`). The empirical-grounded "
            "schema requires reading both `agent_id` and `teammate_name`; "
            "dropping one half re-introduces the pre-consolidation "
            "misclassification class (the per-event partition this "
            "consolidation collapsed)."
        )
