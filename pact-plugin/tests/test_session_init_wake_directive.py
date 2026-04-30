"""
Hook-side tests for the wake-arm directive emitted by session_init.py.

The lead-side wake-arm directive is appended to additionalContext on every
SessionStart fire (startup / resume / clear / compact) per #444's
"hook-emitted directives: unconditional > conditional" discipline. These
tests assert the directive's verbatim presence and idempotency across all
four sources.

Phantom-green mitigation: assertions use semantic anchors (skill slug,
operation name, timing-gap-closure phrase, idempotency phrase), not the
full sentence — an editing LLM reformatting whitespace must still pass.
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


def _run_session_init_main(monkeypatch, tmp_path: Path, source: str) -> str:
    """Invoke session_init.main() with the given SessionStart source value.

    Returns the additionalContext string from the hook output. Mocks all
    side-effecting helpers so the hook runs deterministically against
    tmp_path as $HOME.
    """
    from session_init import main

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    stdin_data = json.dumps({
        "session_id": "aabb1122-0000-0000-0000-000000000000",
        "source": source,
    })

    with patch("session_init.setup_plugin_symlinks", return_value=None), \
         patch("session_init.remove_stale_kernel_block", return_value=None), \
         patch("session_init.update_pact_routing", return_value=None), \
         patch("session_init.ensure_project_memory_md", return_value=None), \
         patch("session_init.check_pinned_staleness", return_value=None), \
         patch("session_init.update_session_info", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("session_init.restore_last_session", return_value=None), \
         patch("session_init.check_paused_state", return_value=None), \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
    output = json.loads(mock_stdout.getvalue())
    return output["hookSpecificOutput"]["additionalContext"]


class TestWakeArmDirectiveUnconditional:
    """Directive emits on EVERY SessionStart source — no LLM-self-diagnosis gate.

    Per #444 working-memory entry: hook-emitted directives use unconditional
    wording. Conditional emission ("if not loaded") requires LLM self-diagnosis,
    which is the failure mode the discipline closes.
    """

    @pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact"])
    def test_directive_emitted_for_source(self, source: str, monkeypatch, tmp_path):
        additional = _run_session_init_main(monkeypatch, tmp_path, source)
        assert 'Arm wake mechanism: invoke Skill("PACT:inbox-wake")' in additional, (
            f"Wake-arm directive missing for source={source!r} — emission must be unconditional"
        )


class TestWakeArmDirectiveSemanticAnchors:
    """Directive carries the load-bearing tokens.

    Each anchor protects against a specific drift:
    - skill slug: prevents rename-without-callsite-update
    - 'Arm' operation: prevents drift to a different operation name
    - 'before any teammate dispatch': lead-side timing-gap-closure (distinct
      from teammate-side 'before any tool call')
    - 'idempotent': prevents an editing LLM from adding a self-diagnosis guard
    """

    def test_directive_references_inbox_wake_skill_slug(self, monkeypatch, tmp_path):
        additional = _run_session_init_main(monkeypatch, tmp_path, "startup")
        assert 'Skill("PACT:inbox-wake")' in additional, (
            "Directive must reference exact skill slug Skill(\"PACT:inbox-wake\")"
        )

    def test_directive_references_arm_operation(self, monkeypatch, tmp_path):
        additional = _run_session_init_main(monkeypatch, tmp_path, "startup")
        assert "Arm operation" in additional, (
            "Directive must reference the Arm operation by name"
        )

    def test_directive_carries_lead_side_timing_phrase(self, monkeypatch, tmp_path):
        # Lead-side timing is "before any teammate dispatch" — distinct from
        # teammate-side "before any tool call". This anchor prevents copy-paste
        # of the teammate template into the lead site.
        additional = _run_session_init_main(monkeypatch, tmp_path, "startup")
        assert "before any teammate dispatch" in additional, (
            "Lead-side directive must use 'before any teammate dispatch' timing phrase"
        )

    def test_directive_carries_idempotency_phrase(self, monkeypatch, tmp_path):
        additional = _run_session_init_main(monkeypatch, tmp_path, "startup")
        # Anchor on the load-bearing word; the surrounding sentence may vary.
        assert "idempotent" in additional.lower(), (
            "Directive must carry an idempotency clause — guards against "
            "LLM-self-diagnosis re-introduction"
        )
