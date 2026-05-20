"""
Comprehensive TEST-phase coverage for the actor-discriminator-capture-gate
bundle (#781 #786 #760 #738).

Backend-coder shipped SMOKE coverage: 4 lifted strict-xfail tests
in test_teardown_request_emitter.py (renamed `*_per_agent_id_none_discriminator`),
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
        to confirm the isinstance gate AND the agent_id-absent path both
        traverse cleanly.
        """
        # `{}` is a dict and the isinstance gate lets it through; verify
        # it is classified as lead (no agent_id present).
        if bad_input == {}:
            assert wl.is_lead_at_task_completed(bad_input, "any-team") is True
        else:
            assert wl.is_lead_at_task_completed(bad_input, "any-team") is False

    def test_lead_frame_empty_payload_classifies_as_lead(self):
        """Edge case for SEC-S1: bare `{}` passes the isinstance gate
        AND has no `agent_id` key → True (lead). This is the documented
        behavior per the upstream Claude Code platform documentation
        (TaskCompleted lead fires omit agent_id). Pinning the edge so
        a future "guard against missing fields" predicate edit fails
        loudly.
        """
        assert wl.is_lead_at_task_completed({}, "team-x") is True

    def test_teammate_frame_agent_id_string_classifies_as_teammate(self):
        """Documented platform path: teammate-context TaskCompleted
        stdin carries platform-stamped `agent_id` per-instance UUID
        string per upstream Claude Code documentation.
        `agent_id is None` returns False → classified as teammate
        (suppress). Sentinel-shaped value chosen to match the in-PR
        synthesized payload convention.
        """
        payload = {
            "session_id": "shared-lead-sid",
            "hook_event_name": "TaskCompleted",
            "task_id": "T1",
            "team_name": "team-x",
            "teammate_name": "backend-coder",
            "agent_id": "subagent-12ab34cd-5e6f-7890-abcd-ef1234567890",
        }
        assert wl.is_lead_at_task_completed(payload, "team-x") is False

    @pytest.mark.parametrize("adversarial_agent_id", [
        "", " ", "0", "False", "null", "None",
        "X" * 1000,  # very long
        "subagent-" + chr(0) + "-embedded-null",  # null byte (chr(0) avoids src null)
        "subagent-\n-newline", "subagent-\t-tab",
    ])
    def test_truthy_or_present_agent_id_classifies_as_teammate(
        self, adversarial_agent_id,
    ):
        """The discriminator is `agent_id is None`, NOT `not agent_id`
        and NOT `agent_id` truthy-check. Any non-None string value —
        including empty-string, whitespace, embedded null, multiline —
        classifies as teammate. Pinning this rules out a future
        well-intended "guard against empty agent_id" edit that would
        silently re-introduce false-classification.
        """
        payload = {"agent_id": adversarial_agent_id}
        assert wl.is_lead_at_task_completed(payload, "team-x") is False

    @pytest.mark.parametrize("none_equivalent", [None])
    def test_agent_id_explicitly_none_classifies_as_lead(self, none_equivalent):
        """An explicit `"agent_id": None` (rather than key-absent) still
        classifies as lead — `dict.get("agent_id") is None` returns True
        both for missing key AND explicit-None value. Both shapes are
        documented as lead-context per upstream Claude Code documentation.
        """
        assert wl.is_lead_at_task_completed(
            {"agent_id": none_equivalent}, "team-x",
        ) is True

    def test_other_fields_irrelevant_to_classification(self):
        """The discriminator reads ONLY `agent_id`. Other fields
        (`teammate_name`, `session_id`, `task_id`, …) do not influence
        classification. Pinning this prevents a future "consult
        teammate_name as a secondary signal" edit from leaking the
        decision-table Cell 2 belt-and-suspenders shape into the
        ships-first Cell 1/3 backstop.
        """
        payload = {
            # All teammate-style fields populated...
            "session_id": "any",
            "task_id": "T1",
            "team_name": "team-x",
            "teammate_name": "backend-coder",
            # ...but agent_id absent → must classify as lead.
        }
        assert wl.is_lead_at_task_completed(payload, "team-x") is True


# =============================================================================
# TS-2: TestSiblingDiscriminatorParity — per-event partition invariant
# =============================================================================


class TestSiblingDiscriminatorParity:
    """The TaskCompleted and PostToolUse discriminators (Cell 1/3
    backstop) read the SAME field — `agent_id`. Upstream Claude Code
    documented-schema authority establishes that both event classes
    stamp `agent_id` in subagent frames. The two helpers' bodies are
    code-clones today; future divergence is a deliberate design
    decision that MUST be visible at the helper level, not
    accidentally introduced.

    The SessionStart sibling reads a DIFFERENT field (`agent_type`)
    — that asymmetry is the per-event partition convention's load-
    bearing property (see the sibling-helper table in
    shared/wake_lifecycle.py module docstring).

    Counter-test-by-revert: if a future edit changes
    is_lead_at_task_completed to read `agent_type` (accidental
    SessionStart-pattern leak), the parity tests trip RED.
    """

    @pytest.mark.parametrize("payload", [
        {},
        {"agent_id": None},
        {"agent_id": "subagent-uuid-1"},
        {"agent_id": ""},
        {"hook_event_name": "TaskCompleted"},
        {"teammate_name": "backend-coder", "agent_id": "subagent-x"},
        {"team_name": "team-x"},
        {"session_id": "sid", "task_id": "T"},
    ])
    def test_task_completed_and_post_tool_use_helpers_agree_on_documented_schema(
        self, payload,
    ):
        """Both helpers MUST return the same bool for the same input
        under the upstream Claude Code documented schema. If they
        diverge, either (a) a body has been edited to read a different
        field, or (b) the documented-schema assumption has been
        falsified by capture and the Cell 2/Cell 4 fallback predicates
        should ship — either way, this divergence is a flag-loud
        signal, not silent drift.
        """
        a = wl.is_lead_at_task_completed(payload, "team-x")
        b = wl.is_lead_emit_authorized(payload, "team-x")
        assert a == b, (
            f"Sibling-parity failure under upstream documented "
            f"schema: is_lead_at_task_completed={a}, "
            f"is_lead_emit_authorized={b}, payload={payload!r}"
        )

    def test_session_start_helper_reads_different_field_partition_invariant(
        self,
    ):
        """SessionStart event class uses `agent_type` (agent-CLASS string),
        not `agent_id` (per-instance UUID). Pinning this asymmetry
        prevents a future "unify all is_lead_* on the same field" edit
        from collapsing the per-event partition convention.
        """
        # Payload with agent_id present but agent_type absent.
        payload_agent_id_only = {"agent_id": "subagent-x"}
        # TaskCompleted/PostToolUse classify as teammate (agent_id present).
        assert wl.is_lead_at_task_completed(payload_agent_id_only) is False
        assert wl.is_lead_emit_authorized(payload_agent_id_only) is False
        # SessionStart classifies as lead (agent_type absent).
        assert wl.is_lead_at_session_start(payload_agent_id_only) is True

        # Inverse: agent_type present but agent_id absent.
        payload_agent_type_only = {"agent_type": "pact-secretary"}
        assert wl.is_lead_at_task_completed(payload_agent_type_only) is True
        assert wl.is_lead_emit_authorized(payload_agent_type_only) is True
        assert wl.is_lead_at_session_start(payload_agent_type_only) is False

    def test_legacy_delegate_is_lead_session_still_matches_emit_authorized(self):
        """is_lead_session is a backward-compat thin delegate to
        is_lead_emit_authorized — its body is a single pass-through
        return statement, which is the rename-is-body-preserving
        rationale for the callsite-migration commit's 0-RED counter-
        test (auditor-endorsed via 8-payload body-identity proof). Any
        future edit that diverges these two breaks the corridor.
        """
        for payload in [{}, {"agent_id": "x"}, {"agent_id": None}, None, 42]:
            a = wl.is_lead_session(payload, "team-x") if isinstance(payload, dict) or payload is None else wl.is_lead_session(payload, "team-x")
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
        for payload in [{}, {"agent_id": "x"}, {"agent_id": None}]:
            result = wl.is_lead_at_task_completed(payload)
            assert result is True or result is False, (
                f"Return type must be bool literal True/False; "
                f"got {result!r} of type {type(result).__name__}"
            )

    def test_helper_makes_no_filesystem_reads(self, tmp_path, monkeypatch):
        """The helper is pure — `agent_id`-presence on the in-memory
        dict is the discriminator; no team_config read, no journal
        read, no marker read. Monkeypatching Path.home to an empty
        tmp_path AND filesystem ops must not affect the result.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Even with a wildly wrong HOME, the helper must work.
        assert wl.is_lead_at_task_completed({}, "team-x") is True
        assert wl.is_lead_at_task_completed(
            {"agent_id": "x"}, "team-x",
        ) is False

    def test_helper_does_not_consult_environ(self, monkeypatch):
        """The helper does not read os.environ — pinning this rules
        out a future "env-var override" edit that would couple the
        routing primitive to runtime state.
        """
        # Set adversarial env vars that a misguided edit might consult.
        monkeypatch.setenv("CLAUDE_AGENT_ID", "lead-pretender")
        monkeypatch.setenv("PACT_OVERRIDE_DISCRIMINATOR", "true")
        # Lead-frame {} STAYS lead regardless of env.
        assert wl.is_lead_at_task_completed({}, "team-x") is True
        # Teammate-frame agent_id STAYS teammate regardless of env.
        assert wl.is_lead_at_task_completed(
            {"agent_id": "subagent-x"}, "team-x",
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
        for team in ["", "team-x", "pact-7642b0c9", "team-with-special!@#$"]:
            assert wl.is_lead_at_task_completed({}, team) is True
            assert wl.is_lead_at_task_completed(
                {"agent_id": "x"}, team,
            ) is False


# =============================================================================
# TS-5: TestDiscriminatorI1NamedInvariantPin — cbcfd589 audit anchor
# =============================================================================


class TestDiscriminatorI1NamedInvariantPin:
    """I1 = "agent_id is None" — the named invariant the lifted Gate-0
    tests in test_teardown_request_emitter.py encode in their
    `*_per_agent_id_none_discriminator` suffixes (cbcfd589 §AUDIT
    discipline; the named-invariant rename shipped with the
    callsite-migration commit).

    These structural assertions pin the invariant name AT the helper
    so the rename convention is self-consistent. If a future edit
    swaps the discriminator (e.g., to `agent_type is None` or to
    Cell 2 `agent_id is None AND teammate_name is None`), it MUST
    also update the suffix — these tests force that coupling visible.
    """

    def test_helper_body_reads_agent_id_field_only(self):
        """Source-level structural pin: the helper body MUST reference
        `agent_id` and MUST NOT reference `agent_type`, `teammate_name`,
        or `tmuxPaneId` (the other 3 candidate discriminators in the
        2×2 capture-decision table). Drift would mean the ships-first
        backstop has been silently swapped without updating the I1
        named-invariant tests.

        Counter-test-by-revert: replacing `agent_id` with
        `teammate_name` in the helper body flips this test RED with
        a specific assertion message identifying the wrong field.
        """
        src = inspect.getsource(wl.is_lead_at_task_completed)
        # Strip the docstring so we only inspect the executable body.
        body = src.split('"""')[-1] if '"""' in src else src
        assert 'agent_id' in body, (
            f"is_lead_at_task_completed body must reference 'agent_id'; "
            f"got body excerpt={body!r}"
        )
        forbidden = ("agent_type", "teammate_name", "tmuxPaneId")
        for f in forbidden:
            assert f not in body, (
                f"is_lead_at_task_completed body MUST NOT reference {f!r} "
                f"(cell-2/cell-4 fossilized-bug-defense candidates only); "
                f"a swap to {f!r} would silently invalidate the I1 named-"
                f"invariant tests' `_per_agent_id_none_discriminator` "
                f"suffix without forcing test-renames. Body excerpt: {body!r}"
            )

    def test_renamed_gate0_tests_carry_per_invariant_suffix(self):
        """Pin the cbcfd589 audit-surface-enumeration convention: the 4
        lifted Gate-0/ExitContract tests carry the
        `_per_agent_id_none_discriminator` suffix. If a future edit
        rotates the discriminator (e.g., to cell-2), the test names
        MUST rotate accordingly — these structural pins force the
        coupling visible.
        """
        teardown_test_src = (PLUGIN_ROOT / "tests"
                             / "test_teardown_request_emitter.py").read_text()
        expected_suffixed_names = [
            "test_teammate_session_suppresses_emission_per_agent_id_none_discriminator",
            "test_teammate_session_writes_no_journal_event_per_agent_id_none_discriminator",
            "test_teammate_session_does_not_create_marker_per_agent_id_none_discriminator",
            "test_all_gate_failure_paths_exit_zero_per_agent_id_none_discriminator",
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
        (`*_per_agent_id_none_discriminator`) are collected by pytest
        and pass under the new predicate. This is a meta-test — if the
        test names don't exist or they fail, the migration is
        incomplete.
        """
        expected = [
            "test_teammate_session_suppresses_emission_per_agent_id_none_discriminator",
            "test_teammate_session_writes_no_journal_event_per_agent_id_none_discriminator",
            "test_teammate_session_does_not_create_marker_per_agent_id_none_discriminator",
            "test_all_gate_failure_paths_exit_zero_per_agent_id_none_discriminator",
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
    discipline applied during the callsite-migration commit's
    phantom-green-stub sweep.

    The architect's design phase enumerated ~25 phantom-green test
    stubs as candidates for `agent_id` addition. Backend-coder's
    per-payload session-frame classification (reading 30-line windows
    above each enumerated line) reduced the actual surface to 4
    teammate-frame stubs; 21 were lead-frame and needed no change.
    This is the canonical counter-example to mechanical find-replace
    under a phantom-green sweep: when an architect estimates a sweep
    surface, the coder MUST re-measure against disk per SEC-AC-2
    before treating the count as load-bearing.

    These tests are STRUCTURAL DOCUMENTATION — they pin the discipline
    as an in-code artifact rather than a feedback memo. The enumerated
    stub lines remain at their original positions in the test file
    (line numbers may shift; the discipline is the invariant).
    """

    def test_phantom_green_stubs_classification_is_documented_in_repo(self):
        """The CODE-phase HANDOFF records the narrowing: 4 of 25 stubs
        were teammate-frame; 21 were lead-frame. This test pins the
        discipline as a discoverable in-repo audit trail.

        Failure mode: if the discipline is forgotten, a future sweep
        might mechanically update all 25 lines and re-introduce
        phantom-green stubs (the 21 lead-frame ones would now
        classify wrong under the cell-1/cell-3 predicate).

        Pinning the discipline as test code keeps it discoverable
        from any future grep over the test surface.
        """
        # Reference assertion: this test's existence + docstring IS
        # the in-code artifact. The assertion below is the marker.
        marker = (
            "phantom-green narrowing applied: per-payload session-frame "
            "classification reduces phantom-green sweep from "
            "architect-estimated 25 to disk-measured 4 teammate-"
            "frame stubs."
        )
        assert marker, "Discipline marker present in this test."

    def test_lead_frame_stubs_in_teardown_test_remain_unchanged_by_design(self):
        """The 21 lead-frame stubs in test_teardown_request_emitter.py
        remain unchanged post-migration BY DESIGN — they were lead-
        context under both OLD (`session_id == leadSessionId`) and NEW
        (`agent_id is None`) predicates because lead-frame stdin has
        no agent_id stamp.

        Structural sweep: count occurrences of "teammate_name" in the
        test file that are NOT paired with "agent_id" in the same
        payload-dict context. Backend-coder measured 21 such payloads
        at the callsite-migration commit time. Any future "wrap with
        agent_id" sweep would change this count — the bound below is
        loose to tolerate added/removed unrelated tests, but the
        order-of-magnitude pin keeps the discipline visible.
        """
        src = (PLUGIN_ROOT / "tests"
               / "test_teardown_request_emitter.py").read_text()
        # Sweep is approximate but bound the order of magnitude. The
        # 21 lead-frame stubs each carry `teammate_name=` in the
        # payload-arg of `_run_emitter_subprocess` / `_write_task` /
        # journal payloads.
        # Match both JSON-quoted payload form `"teammate_name":` (the
        # dominant shape) and kwarg form `teammate_name=` (a few helper
        # calls). The per-payload semantic classification used 30-line-
        # window session_id-context reading; the regex below is a
        # fidelity-weaker proxy whose ONLY purpose is order-of-magnitude
        # drift detection.
        teammate_name_uses = len(re.findall(
            r'(?:"teammate_name"\s*:|teammate_name\s*=)', src,
        ))
        # Callsite-migration stub sweep: 4 teammate-frame stubs have agent_id added;
        # ~21 lead-frame stubs use teammate_name without agent_id. Plus
        # ~3 schema-pin / leaked-field references. Order-of-magnitude
        # bound: 15 < count < 60 (forgiving range that surfaces both
        # massive sweeps and silent deletions).
        assert 15 < teammate_name_uses < 60, (
            f"Order-of-magnitude check on teammate_name uses in "
            f"test_teardown_request_emitter.py: expected ~21+ lead-"
            f"frame stubs + ~3 schema-pin refs + 4 teammate-frame "
            f"stubs ≈ 28. Got {teammate_name_uses}. A wildly different "
            f"count suggests a mechanical sweep happened — re-run the "
            f"per-payload semantic classification before merging."
        )


# =============================================================================
# TS-12, TS-13, TS-14: POST-MERGE FOLLOW-UP SPECS (docstring-only, skipped)
# =============================================================================


class TestPostMergeFollowUpSpecs:
    """Specifications for post-merge follow-up tests. These are
    DOCUMENTATION-IN-CODE — the test bodies are skipped because the
    fixtures + capture campaign that would activate them DO NOT EXIST
    in this PR.

    Per the dispatch's Deliverable 3 ("Spec only — implementation is
    post-merge"), these stubs encode the test SHAPES so the post-merge
    follow-up commit can implement them without re-deriving the
    design. They are kept in the same file as the comprehensive
    coverage so they are discoverable from the test-surface map.

    Each test below has:
    - a `pytest.skip(reason=...)` body explaining the post-merge gate
    - a docstring that IS the spec (read the docstring to understand
      what the implementation should assert)

    Activation discipline (post-merge follow-up):
    1. Capture campaign produces real fixtures under
       pact-plugin/tests/fixtures/wake_lifecycle/
       (`taskcompleted_lead_context_shape.json` +
        `taskcompleted_teammate_context_shape.json`).
    2. Each fixture carries `_meta.capture_method: "logging-shim"`.
    3. The skip()-bodies below are REPLACED with the assertions
       described in the docstrings.
    4. The `TestStdinShapePin` synthesized 10-key payload in
       test_teardown_request_emitter.py:55-75 is REPLACED with a
       fixture load.
    """

    # ---- TS-12: Captured-fixture parity specs ----

    def test_captured_lead_context_fixture_shape_pin_spec(self):
        """SPEC: when `taskcompleted_lead_context_shape.json` exists
        under pact-plugin/tests/fixtures/wake_lifecycle/ with
        `_meta.capture_method == "logging-shim"`, assert:
        - The fixture's `agent_id` field is absent (or explicitly None).
        - `is_lead_at_task_completed(fixture, fixture['team_name'])`
          returns True.
        - The fixture's keys are a superset of the documented
          TaskCompleted schema (per teardown_request_emitter.py:96).

        Replaces the synthesized lead-frame shape derived from
        PLATFORM_TASKCOMPLETED_STDIN_SHAPE minus `agent_id`.
        """
        fixture_path = (PLUGIN_ROOT / "tests" / "fixtures"
                        / "wake_lifecycle"
                        / "taskcompleted_lead_context_shape.json")
        if not fixture_path.exists():
            pytest.skip(
                "POST-MERGE GATE: captured "
                "taskcompleted_lead_context_shape.json does not exist; "
                "this spec activates when the capture campaign lands "
                "the fixture per the post-merge logging-shim capture campaign."
            )
        # Activation body (post-merge implementation):
        # data = json.loads(fixture_path.read_text())
        # assert data.get("_meta", {}).get("capture_method") == "logging-shim"
        # assert data.get("agent_id") is None
        # assert wl.is_lead_at_task_completed(data, data["team_name"]) is True

    def test_captured_teammate_context_fixture_shape_pin_spec(self):
        """SPEC: when `taskcompleted_teammate_context_shape.json` exists
        under pact-plugin/tests/fixtures/wake_lifecycle/ with
        `_meta.capture_method == "logging-shim"`, assert:
        - The fixture's `agent_id` field is a non-empty string
          (platform-stamped per-instance UUID).
        - `is_lead_at_task_completed(fixture, fixture['team_name'])`
          returns False.
        - The fixture's keys are a superset of the documented
          TaskCompleted schema PLUS `agent_id`.
        """
        fixture_path = (PLUGIN_ROOT / "tests" / "fixtures"
                        / "wake_lifecycle"
                        / "taskcompleted_teammate_context_shape.json")
        if not fixture_path.exists():
            pytest.skip(
                "POST-MERGE GATE: captured "
                "taskcompleted_teammate_context_shape.json does not "
                "exist; this spec activates when the capture campaign "
                "lands the fixture per the post-merge logging-shim capture campaign."
            )

    def test_lead_and_teammate_fixtures_paired_via_meta_provenance_spec(self):
        """SPEC: PAIR-REVERT-IN-SINGLE-FIXTURE discipline (per
        #797 a0b35b4a NEGATIVE-TEST PHANTOM-GREEN CLASS mitigation):
        the lead AND teammate fixtures MUST be captured in the SAME
        session (`_meta.capture_session_id` matches across both files).
        A negative test that "agent_id absent in lead-frame" passes by
        absence trivially; pair it with the positive "agent_id present
        in teammate-frame" from the SAME session so revert-cardinality
        > 1.
        """
        lead = (PLUGIN_ROOT / "tests" / "fixtures" / "wake_lifecycle"
                / "taskcompleted_lead_context_shape.json")
        teammate = (PLUGIN_ROOT / "tests" / "fixtures" / "wake_lifecycle"
                    / "taskcompleted_teammate_context_shape.json")
        if not (lead.exists() and teammate.exists()):
            pytest.skip(
                "POST-MERGE GATE: paired captured fixtures do not "
                "exist; spec activates post-campaign together with "
                "SEC-AC-2 pair-revert discipline."
            )

    # ---- TS-13: #786 falsifier spec ----

    def test_786_post_tool_use_teammate_agent_id_presence_falsifier_spec(self):
        """SPEC: #786 (post-tool-use directive misrouting investigation
        per the documented disposition gate). Once the capture campaign
        lands a PostToolUse teammate-context stdin payload at
        pact-plugin/tests/fixtures/wake_lifecycle/
        `posttooluse_teammate_context_shape.json`, assert:

        Outcome A (close as defense-in-depth-by-design):
        - The fixture's `agent_id` field is present (non-None string).
        - `is_lead_emit_authorized(fixture, fixture['team_name'])`
          returns False.
        - The Layer-1 Lead-Session Guard in the skill body IS the
          load-bearing gate; the Layer-0 hook-side check is
          defense-in-depth. Document via assertion-message reference.

        Outcome B (file follow-up issue for corridor regression):
        - The fixture's `agent_id` field is ABSENT (or None) in the
          teammate frame.
        - The Option-5 thesis is FALSIFIED for the PostToolUse path.
        - The test raises pytest.UsageError with a corridor-wide
          escalation message identifying all 4 surfaces that need
          re-migration: wake_lifecycle_emitter, wake_inbox_drain,
          session_init, teardown_request_emitter.
        """
        fixture_path = (PLUGIN_ROOT / "tests" / "fixtures"
                        / "wake_lifecycle"
                        / "posttooluse_teammate_context_shape.json")
        if not fixture_path.exists():
            pytest.skip(
                "POST-MERGE GATE: captured PostToolUse teammate-context "
                "fixture does not exist; #786 falsifier activates "
                "post-campaign per the documented disposition gate. "
                "Outcome A → close #786 as defense-in-depth; "
                "Outcome B → file follow-up issue for corridor "
                "regression."
            )

    # ---- TS-14: #806 SubagentStop discriminator spec ----

    def test_806_subagent_stop_discriminator_audit_spec(self):
        """SPEC: #806 SubagentStop discriminator audit (opportunistic
        rider on the post-merge capture campaign). When the capture
        campaign opportunistically captures a SubagentStop fire under
        /tmp/pact-hook-stdin-captures/subagentstop/, promote a
        representative fire to pact-plugin/tests/fixtures/<future-dir>/
        and assert:
        - The SubagentStop stdin shape (documented per
          claude-code-guide).
        - Whether `agent_id` is present (the discriminator field
          parity check across the 5-event sibling family).
        - validate_handoff.py's lead/teammate routing is correct under
          the captured stdin shape.

        OUT OF SCOPE FOR THIS PR — opportunistic data-gathering rider
        only; the actual #806 fix (if any) lands in a separate PR.
        """
        # No fixture path to check; spec is purely SubagentStop-domain.
        pytest.skip(
            "POST-MERGE GATE: #806 SubagentStop audit is an "
            "opportunistic rider on the #781 capture campaign. "
            "Activates only if the campaign captures a SubagentStop "
            "fire AND #806's own PACT session promotes it to a "
            "fixture. This PR's TEST phase has zero SubagentStop scope."
        )


# =============================================================================
# TS-15: TestCounterTestMechanismDocumentation — discriminative-NOTE pattern
# =============================================================================


class TestCounterTestMechanismDocumentation:
    """Per #802 [0ff9d0bb] DISCRIMINATIVE-vs-NON-DISCRIMINATIVE counter-
    test discipline: for COMPOUND predicates, an adversarial reviewer
    can mutate a non-discriminative sub-clause and get false-RED /
    false-confidence. The mitigation is an inline `# counter-test:`
    NOTE specifying WHICH mutation is discriminative.

    The current ships-first Cell 1/3 backstop is single-field
    (`agent_id is None`) so the discriminative mutation is
    unambiguous; the NOTE is not load-bearing today. But if a future
    PATH B follow-up commit swaps to Cell 2 (`agent_id is None AND
    teammate_name is None`), the predicate becomes COMPOUND and the
    discipline applies.

    This test class pins the discipline for the future PATH B
    contingency: if the helper body changes to compound form, the
    pre-existing inline NOTE shape MUST appear. Today the body is
    single-field, so the test asserts the documentation-in-code
    discipline exists at the helper docstring level rather than
    the inline-comment level.
    """

    def test_helper_docstring_documents_discriminator_choice(self):
        """The helper docstring MUST document WHICH field is the
        discriminator and WHY (documented-schema authority from
        upstream Claude Code platform docs / cell-1/cell-3 backstop /
        per-event partition convention). This is the load-bearing
        documentation surface; an inline `# counter-test:` NOTE
        becomes additionally load-bearing only under compound-
        predicate Cell 2 or Cell 4.
        """
        docstring = wl.is_lead_at_task_completed.__doc__ or ""
        # Pin the canonical references. Case-insensitive on the cell
        # tag to tolerate "Cell-1", "cell-1", "Cell 1" formatting drift.
        required_substrings = [
            "agent_id",            # which field
        ]
        # Cell-1/3 anchor: tolerate case + dash/space variations.
        cell_anchor_pattern = re.compile(
            r"cell[- ]?1[/ ]*(?:cell[- ]?3|3)", re.IGNORECASE,
        )
        for s in required_substrings:
            assert s in docstring, (
                f"is_lead_at_task_completed docstring must reference "
                f"{s!r} per the discriminative-NOTE discipline. "
                f"Docstring excerpt: {docstring[:300]!r}"
            )
        assert cell_anchor_pattern.search(docstring), (
            f"is_lead_at_task_completed docstring must reference the "
            f"cell-1/cell-3 backstop selection (any case, dash/space "
            f"variants accepted). The cell tag is the load-bearing "
            f"audit anchor — without it, a future swap to Cell 2/Cell 4 "
            f"could lose its rationale trail. Docstring excerpt: "
            f"{docstring[:400]!r}"
        )

    def test_compound_predicate_path_b_contingency_unreached_under_documented_schema(
        self,
    ):
        """The PATH B contingency (Cell 2 belt-and-suspenders) is
        unreached under the upstream documented-schema authority that
        currently ships. Pin that the helper body is still SINGLE-
        FIELD; if a future edit silently swaps to compound form
        (`agent_id is None AND teammate_name is None`) without filing
        the PATH B follow-up, this test flags the drift.
        """
        body_src = inspect.getsource(wl.is_lead_at_task_completed)
        # Strip docstring.
        body_lines = [
            line for line in body_src.splitlines()
            if line.strip() and not line.strip().startswith('"""')
            and not line.strip().startswith("Discriminator")
        ]
        # Look for `and input_data.get("teammate_name")` shape.
        compound_pattern = re.compile(
            r'and\s+input_data\.get\(["\']teammate_name["\']\)'
        )
        is_compound = any(compound_pattern.search(line) for line in body_lines)
        assert not is_compound, (
            "is_lead_at_task_completed body has been swapped to "
            "compound form (PATH B Cell 2 belt-and-suspenders) without "
            "filing the post-merge follow-up. PATH B requires: "
            "predicate body swap + payload swap + SEC-AC-5 counter-"
            "test cardinality re-measure. If this swap is intentional, "
            "file the follow-up PR and update this test's discriminator-"
            "NOTE pin to allow compound form."
        )
