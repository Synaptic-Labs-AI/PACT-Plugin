"""Row 8 -- the malformed-settings.json self-check (check_settings_well_formed).

Claude Code silently drops a malformed settings.json WHOLESALE in headless mode,
taking the whole `env` block (every persisted PACT_* option) with it. The
self-check surfaces that so the user learns why their env-block config had no
effect. Two layers:

- TestCheckSettingsWellFormed (unit): drives the resolution through the SANCTIONED
  seam -- monkeypatch CLAUDE_CONFIG_DIR to a tmp dir (get_claude_config_dir honors
  it) and plant settings.json variants. It does NOT patch get_claude_config_dir
  itself: that helper's own docstring warns that injecting there is a vacuous
  green (the DI-seam trap). Core invariants: malformed -> warn (NO raise);
  well-formed -> None; absent -> None; the total contract swallows a read error.
  Load-bearing counter-test: the warn fires ONLY on malformed (well-formed and
  absent both return None), so the malformed-detection branch -- not an
  unconditional emit -- is what produces the warning.

- TestSelfCheckWiring (integration): drives the REAL session_init.main() with a
  malformed settings.json on disk and asserts the warn reaches systemMessage on a
  lead frame, and is gated OUT on a teammate frame (the 4d lead-only wiring).
"""
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import lead_frame_qualified, teammate_frame

import session_init
from session_init import check_settings_well_formed

_PROJECT_DIR = "/Users/example/Sites/test-project"
_MALFORMED = '{ "env": { "PACT_PR_GREEDY_FIX": "1"  '   # missing closing braces
_WELL_FORMED = '{ "env": { "PACT_PR_GREEDY_FIX": "1" } }'


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    """A tmp CLAUDE_CONFIG_DIR the resolver honors (sanctioned seam)."""
    d = tmp_path / "claude_cfg"
    d.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(d))
    return d


class TestCheckSettingsWellFormed:
    def test_malformed_returns_warn_without_raising(self, cfg_dir):
        (cfg_dir / "settings.json").write_text(_MALFORMED, encoding="utf-8")
        result = check_settings_well_formed()   # must NOT raise
        assert isinstance(result, str)
        assert "settings.json" in result
        assert "not valid JSON" in result

    def test_well_formed_returns_none(self, cfg_dir):
        (cfg_dir / "settings.json").write_text(_WELL_FORMED, encoding="utf-8")
        assert check_settings_well_formed() is None

    def test_absent_returns_none(self, cfg_dir):
        # No settings.json planted.
        assert check_settings_well_formed() is None

    def test_invalid_utf8_returns_warn_without_raising(self, cfg_dir):
        # read_text(encoding="utf-8") raises UnicodeDecodeError (ValueError
        # subclass) -> caught by the total contract -> warn, never propagate.
        (cfg_dir / "settings.json").write_bytes(b'\xff\xfe{ "env": ')
        result = check_settings_well_formed()
        assert isinstance(result, str) and "settings.json" in result

    def test_guard_is_load_bearing_only_malformed_warns(self, cfg_dir):
        # The load-bearing counter-test: the SAME call returns a warn on malformed
        # and None on well-formed -- so the warning is produced BY the malformed
        # branch, not unconditionally. A guard that emitted always (or never)
        # would fail one of these two poles.
        sp = cfg_dir / "settings.json"
        sp.write_text(_MALFORMED, encoding="utf-8")
        malformed_result = check_settings_well_formed()
        sp.write_text(_WELL_FORMED, encoding="utf-8")
        wellformed_result = check_settings_well_formed()
        assert malformed_result is not None
        assert wellformed_result is None


def _run_main_output(frame, monkeypatch, tmp_path, cfg_dir):
    """Drive real session_init.main(); return the full parsed output dict."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    stdin_data = json.dumps({"source": "startup", **frame})
    with patch("session_init.setup_plugin_symlinks", return_value=None), \
         patch("session_init.ensure_project_memory_md", return_value=None), \
         patch("session_init.check_pinned_staleness", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("session_init.restore_last_session", return_value=None), \
         patch("session_init.build_context_cache",
               return_value=(Path("/tmp/ctx.json"), {})), \
         patch("session_init.persist_context", return_value=None), \
         patch("session_init.append_event"), \
         patch("session_init.update_session_info", return_value=None), \
         patch("session_init.check_resume_state", return_value=None), \
         patch("session_init._registry_resolve", return_value=None), \
         patch("session_init.get_peer_context", return_value=None), \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    raw = mock_stdout.getvalue().strip()
    return json.loads(raw) if raw else {}


class TestSelfCheckWiring:
    def test_lead_frame_surfaces_warn_in_system_message(self, monkeypatch, tmp_path, cfg_dir):
        (cfg_dir / "settings.json").write_text(_MALFORMED, encoding="utf-8")
        out = _run_main_output(lead_frame_qualified(), monkeypatch, tmp_path, cfg_dir)
        assert "settings.json" in out.get("systemMessage", ""), (
            "malformed settings.json warn must reach systemMessage on a lead frame (4d wiring)"
        )

    def test_well_formed_produces_no_settings_warn(self, monkeypatch, tmp_path, cfg_dir):
        (cfg_dir / "settings.json").write_text(_WELL_FORMED, encoding="utf-8")
        out = _run_main_output(lead_frame_qualified(), monkeypatch, tmp_path, cfg_dir)
        assert "not valid JSON" not in out.get("systemMessage", "")

    def test_teammate_frame_gated_no_settings_warn(self, monkeypatch, tmp_path, cfg_dir):
        # Even with a malformed file present, a teammate frame must NOT receive the
        # config-health warn -- it lives inside the same lead-only gate as the
        # injection block.
        (cfg_dir / "settings.json").write_text(_MALFORMED, encoding="utf-8")
        out = _run_main_output(teammate_frame("pact-backend-coder"),
                               monkeypatch, tmp_path, cfg_dir)
        assert "not valid JSON" not in out.get("systemMessage", "")
