"""
Location: pact-plugin/tests/test_lead_address_gap_pins.py
Summary: Pins for two #1671 guards that the final test review (task #109)
found had no failing arm under mutation:
  - MB1: plugin_manifest's banner fails CLOSED ("<unsafe>") when the
    is_safe_plugin_root import fails (mutant: echo the root, stayed green).
  - MB2: build_context_cache still reads the recorded context when only
    lead_address is unset (mutant: drop that clause from needs_carry, stayed
    green; a caller threading process_key and harness then got the env
    address instead of the recorded one).
Used by: the full-suite merge gate (`cd pact-plugin && python3 -m pytest -q`).
"""

from __future__ import annotations

import builtins
import json

import shared.pact_context as pact_context

SESSION_ID = "9f8e7d6c-5b4a-3f2e-1d0c-9b8a7f6e5d4c"


def test_banner_fails_closed_when_the_allowlist_import_fails(monkeypatch):
    from shared import plugin_manifest

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "shared.session_state":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/a/$(id)")
    banner = plugin_manifest.format_plugin_banner()
    assert banner == "PACT plugin: unknown (root: <unsafe>)"
    assert "$(id)" not in banner


def test_lead_address_carries_when_only_it_is_unset(tmp_path, monkeypatch):
    """process_key and harness are threaded, lead_address is not: the value
    recorded for THIS session is still carried. The environment is local
    (conftest scrubs CLAUDE_CODE_REMOTE), so the env fallback would give
    team-lead and the recorded main is observable."""
    path = tmp_path / "pact-session-context.json"
    path.write_text(json.dumps({"session_id": SESSION_ID, "lead_address": "main"}),
                    encoding="utf-8")
    monkeypatch.setattr(pact_context, "_context_path", path)
    result = pact_context.build_context_cache(
        "session-9f8e7d6c", SESSION_ID, "/proj", "/root",
        process_key="pk", harness="tty")
    assert result is not None
    assert result[1]["lead_address"] == "main"
