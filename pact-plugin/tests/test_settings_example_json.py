"""
Shape validation for templates/settings.example.json.

Under v4.0.0 the user-facing per-project entry point is
`.claude/settings.json` with `"agent": "PACT:pact-orchestrator"`. The plugin
ships a sample at `pact-plugin/templates/settings.example.json` for users to
copy from. This test guards the shape so the example stays canonical
through future changes.

C2 lands xfail-strict; C10 flips.
"""
import json
from pathlib import Path

import pytest


SETTINGS_EXAMPLE_PATH = (
    Path(__file__).parent.parent / "templates" / "settings.example.json"
)

EXPECTED_AGENT_VALUE = "PACT:pact-orchestrator"


@pytest.mark.xfail(strict=True, reason="v4.0.0 — flips in C10")
def test_settings_example_file_exists():
    assert SETTINGS_EXAMPLE_PATH.exists(), (
        f"settings.example.json missing at {SETTINGS_EXAMPLE_PATH}"
    )


@pytest.mark.xfail(strict=True, reason="v4.0.0 — flips in C10")
def test_settings_example_is_valid_json():
    text = SETTINGS_EXAMPLE_PATH.read_text()
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        pytest.fail(f"settings.example.json is not valid JSON: {e}")


@pytest.mark.xfail(strict=True, reason="v4.0.0 — flips in C10")
def test_settings_example_has_agent_field():
    data = json.loads(SETTINGS_EXAMPLE_PATH.read_text())
    assert "agent" in data, (
        "settings.example.json must contain an `agent` field — "
        "this is the canonical user-facing convention under v4.0.0"
    )


@pytest.mark.xfail(strict=True, reason="v4.0.0 — flips in C10")
def test_settings_example_agent_value_is_pact_orchestrator():
    data = json.loads(SETTINGS_EXAMPLE_PATH.read_text())
    assert data.get("agent") == EXPECTED_AGENT_VALUE, (
        f"settings.example.json `agent` must be {EXPECTED_AGENT_VALUE!r}, "
        f"got {data.get('agent')!r}"
    )


@pytest.mark.xfail(strict=True, reason="v4.0.0 — flips in C10")
def test_settings_example_top_level_is_object():
    data = json.loads(SETTINGS_EXAMPLE_PATH.read_text())
    assert isinstance(data, dict), (
        "settings.example.json top-level must be a JSON object"
    )
