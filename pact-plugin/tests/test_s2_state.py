"""
Tests for shared/s2_state.py — Atomic read/write/update primitives for
.pact/s2-state.json, the S2 coordination state file.

Tests cover:
1. S2 state schema validation (default state structure)
2. Read/write round-trip (all field combinations)
3. Graceful degradation (missing file, corrupted file, empty file)
4. Concurrent writes via threading (3-5 threads)
5. Atomic rename safety (temp file cleanup on failure)
6. resolve_convention (last-per-key semantics)
7. check_boundary_overlap (overlapping, disjoint, prefix matching)
8. file_in_scope (matching, non-matching, edge cases)
9. write_s2_state timestamp auto-population
10. update_s2_state with missing file (creates from default)
"""
import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def worktree(tmp_path):
    """Create a worktree directory with .pact/ subdirectory."""
    pact_dir = tmp_path / ".pact"
    pact_dir.mkdir()
    return tmp_path


@pytest.fixture
def state_file(worktree):
    """Return the path to s2-state.json within the worktree."""
    return worktree / ".pact" / "s2-state.json"


def _make_state(
    *,
    boundaries=None,
    conventions=None,
    scope_claims=None,
    drift_alerts=None,
    session_team="pact-test",
    worktree_path="/test/worktree",
):
    """Factory for generating s2-state.json content."""
    return {
        "version": 1,
        "session_team": session_team,
        "worktree": worktree_path,
        "created_at": "2026-04-01T00:00:00+00:00",
        "last_updated": "2026-04-01T00:00:00+00:00",
        "created_by": "orchestrate",
        "boundaries": boundaries or {},
        "conventions": conventions or [],
        "scope_claims": scope_claims or {},
        "drift_alerts": drift_alerts or [],
    }


def _two_agent_disjoint():
    """Two agents with non-overlapping scopes."""
    return {
        "backend-coder": {
            "owns": ["src/server/", "src/api/"],
            "reads": ["src/types/"],
        },
        "frontend-coder": {
            "owns": ["src/client/", "src/ui/"],
            "reads": ["src/types/"],
        },
    }


def _two_agent_overlapping():
    """Two agents with overlapping 'owns' scopes."""
    return {
        "backend-coder": {
            "owns": ["src/server/", "src/shared/"],
            "reads": ["src/types/"],
        },
        "frontend-coder": {
            "owns": ["src/client/", "src/shared/"],
            "reads": ["src/types/"],
        },
    }


def _three_agent_chain_overlap():
    """Three agents where A overlaps B, B overlaps C, but A doesn't overlap C."""
    return {
        "agent-a": {"owns": ["src/server/", "src/shared/utils/"], "reads": []},
        "agent-b": {"owns": ["src/shared/"], "reads": []},
        "agent-c": {"owns": ["src/client/", "src/shared/types/"], "reads": []},
    }


# =============================================================================
# Schema Validation
# =============================================================================


class TestDefaultState:
    """Verify the default state schema structure."""

    def test_default_state_has_all_keys(self):
        from shared.s2_state import _DEFAULT_STATE

        expected_keys = {
            "version", "session_team", "worktree", "created_at",
            "last_updated", "created_by", "boundaries", "conventions",
            "scope_claims", "drift_alerts",
        }
        assert set(_DEFAULT_STATE.keys()) == expected_keys

    def test_default_state_version_is_1(self):
        from shared.s2_state import _DEFAULT_STATE

        assert _DEFAULT_STATE["version"] == 1

    def test_default_state_has_empty_collections(self):
        from shared.s2_state import _DEFAULT_STATE

        assert _DEFAULT_STATE["boundaries"] == {}
        assert _DEFAULT_STATE["conventions"] == []
        assert _DEFAULT_STATE["scope_claims"] == {}
        assert _DEFAULT_STATE["drift_alerts"] == []

    def test_default_state_string_fields_are_empty(self):
        from shared.s2_state import _DEFAULT_STATE

        for key in ("session_team", "worktree", "created_at", "last_updated", "created_by"):
            assert _DEFAULT_STATE[key] == ""


# =============================================================================
# Read/Write Round-Trip
# =============================================================================


class TestReadWriteRoundTrip:
    """Read and write operations preserve state correctly."""

    def test_write_then_read_empty_state(self, worktree):
        from shared.s2_state import read_s2_state, write_s2_state

        state = _make_state()
        assert write_s2_state(str(worktree), state) is True

        result = read_s2_state(str(worktree))
        assert result is not None
        assert result["version"] == 1
        assert result["session_team"] == "pact-test"

    def test_write_then_read_with_boundaries(self, worktree):
        from shared.s2_state import read_s2_state, write_s2_state

        state = _make_state(boundaries=_two_agent_disjoint())
        write_s2_state(str(worktree), state)

        result = read_s2_state(str(worktree))
        assert "backend-coder" in result["boundaries"]
        assert result["boundaries"]["backend-coder"]["owns"] == ["src/server/", "src/api/"]

    def test_write_then_read_with_conventions(self, worktree):
        from shared.s2_state import read_s2_state, write_s2_state

        conventions = [
            {"key": "naming", "value": "camelCase", "established_by": "coder-1",
             "established_at": "2026-04-01T00:00:00+00:00"},
        ]
        state = _make_state(conventions=conventions)
        write_s2_state(str(worktree), state)

        result = read_s2_state(str(worktree))
        assert len(result["conventions"]) == 1
        assert result["conventions"][0]["key"] == "naming"

    def test_write_then_read_with_scope_claims(self, worktree):
        from shared.s2_state import read_s2_state, write_s2_state

        claims = {
            "backend-coder": {
                "files_modified": ["/worktree/src/server/auth.ts"],
                "claimed_at": "2026-04-01T00:00:00+00:00",
            }
        }
        state = _make_state(scope_claims=claims)
        write_s2_state(str(worktree), state)

        result = read_s2_state(str(worktree))
        assert "backend-coder" in result["scope_claims"]

    def test_write_then_read_with_drift_alerts(self, worktree):
        from shared.s2_state import read_s2_state, write_s2_state

        alerts = [
            {"file": "/worktree/src/shared/utils.ts", "modified_by": "frontend-coder",
             "affects": ["backend-coder"], "timestamp": "2026-04-01T00:00:00+00:00"},
        ]
        state = _make_state(drift_alerts=alerts)
        write_s2_state(str(worktree), state)

        result = read_s2_state(str(worktree))
        assert len(result["drift_alerts"]) == 1
        assert result["drift_alerts"][0]["modified_by"] == "frontend-coder"

    def test_write_auto_populates_last_updated(self, worktree):
        from shared.s2_state import read_s2_state, write_s2_state

        state = _make_state()
        original_updated = state["last_updated"]
        write_s2_state(str(worktree), state)

        result = read_s2_state(str(worktree))
        # write_s2_state overrides last_updated with current time
        assert result["last_updated"] != original_updated

    def test_write_sets_created_at_if_empty(self, worktree):
        from shared.s2_state import read_s2_state, write_s2_state

        state = _make_state()
        state["created_at"] = ""
        write_s2_state(str(worktree), state)

        result = read_s2_state(str(worktree))
        assert result["created_at"] != ""
        assert result["created_at"] == result["last_updated"]

    def test_write_preserves_existing_created_at(self, worktree):
        from shared.s2_state import read_s2_state, write_s2_state

        state = _make_state()
        state["created_at"] = "2026-01-01T00:00:00+00:00"
        write_s2_state(str(worktree), state)

        result = read_s2_state(str(worktree))
        assert result["created_at"] == "2026-01-01T00:00:00+00:00"

    def test_write_creates_pact_dir_if_missing(self, tmp_path):
        from shared.s2_state import write_s2_state, read_s2_state

        # No .pact/ directory exists yet
        state = _make_state()
        assert write_s2_state(str(tmp_path), state) is True

        result = read_s2_state(str(tmp_path))
        assert result is not None


# =============================================================================
# Graceful Degradation
# =============================================================================


class TestGracefulDegradation:
    """read_s2_state returns None when file is missing, corrupted, or empty."""

    def test_read_missing_file(self, worktree):
        from shared.s2_state import read_s2_state

        result = read_s2_state(str(worktree))
        assert result is None

    def test_read_empty_file(self, worktree, state_file):
        from shared.s2_state import read_s2_state

        state_file.write_text("")
        result = read_s2_state(str(worktree))
        assert result is None

    def test_read_whitespace_only_file(self, worktree, state_file):
        from shared.s2_state import read_s2_state

        state_file.write_text("   \n  \n")
        result = read_s2_state(str(worktree))
        assert result is None

    def test_read_corrupted_json(self, worktree, state_file):
        from shared.s2_state import read_s2_state

        state_file.write_text("{corrupted json!!!")
        result = read_s2_state(str(worktree))
        assert result is None

    def test_read_non_dict_json(self, worktree, state_file):
        from shared.s2_state import read_s2_state

        state_file.write_text('"just a string"')
        # read_s2_state returns whatever json.loads returns — a string is valid JSON
        result = read_s2_state(str(worktree))
        assert result == "just a string"

    def test_read_array_json(self, worktree, state_file):
        from shared.s2_state import read_s2_state

        state_file.write_text('[1, 2, 3]')
        result = read_s2_state(str(worktree))
        assert result == [1, 2, 3]

    def test_write_returns_false_on_permission_error(self, worktree):
        from shared.s2_state import write_s2_state

        with patch("shared.s2_state._ensure_pact_dir", side_effect=OSError("Permission denied")):
            result = write_s2_state(str(worktree), _make_state())
            assert result is False

    def test_update_returns_false_on_error(self, worktree):
        from shared.s2_state import update_s2_state

        with patch("shared.s2_state._ensure_pact_dir", side_effect=OSError("Permission denied")):
            result = update_s2_state(str(worktree), lambda s: s)
            assert result is False


# =============================================================================
# update_s2_state
# =============================================================================


class TestUpdateS2State:
    """Atomic read-modify-write cycle."""

    def test_update_creates_from_default_when_missing(self, worktree):
        from shared.s2_state import update_s2_state, read_s2_state

        def add_boundary(state):
            state["boundaries"]["test-agent"] = {"owns": ["src/"], "reads": []}
            return state

        assert update_s2_state(str(worktree), add_boundary) is True

        result = read_s2_state(str(worktree))
        assert result is not None
        assert "test-agent" in result["boundaries"]

    def test_update_modifies_existing_state(self, worktree):
        from shared.s2_state import write_s2_state, update_s2_state, read_s2_state

        initial = _make_state(boundaries=_two_agent_disjoint())
        write_s2_state(str(worktree), initial)

        def add_convention(state):
            state["conventions"].append({
                "key": "naming",
                "value": "snake_case",
                "established_by": "backend-coder",
                "established_at": "2026-04-01T01:00:00+00:00",
            })
            return state

        assert update_s2_state(str(worktree), add_convention) is True

        result = read_s2_state(str(worktree))
        assert len(result["conventions"]) == 1
        assert result["conventions"][0]["value"] == "snake_case"
        # Boundaries preserved
        assert "backend-coder" in result["boundaries"]

    def test_update_sets_last_updated(self, worktree):
        from shared.s2_state import write_s2_state, update_s2_state, read_s2_state

        initial = _make_state()
        write_s2_state(str(worktree), initial)

        old_updated = read_s2_state(str(worktree))["last_updated"]

        # Small delay to ensure timestamp differs
        time.sleep(0.01)

        update_s2_state(str(worktree), lambda s: s)

        new_updated = read_s2_state(str(worktree))["last_updated"]
        assert new_updated != old_updated

    def test_update_with_corrupted_file_uses_default(self, worktree, state_file):
        from shared.s2_state import update_s2_state, read_s2_state

        state_file.write_text("{broken json")

        def add_data(state):
            state["session_team"] = "pact-recovered"
            return state

        assert update_s2_state(str(worktree), add_data) is True

        result = read_s2_state(str(worktree))
        assert result["session_team"] == "pact-recovered"
        assert result["version"] == 1  # From default


# =============================================================================
# Concurrent Writes (Threading)
# =============================================================================


class TestConcurrentWrites:
    """Verify concurrent writes don't corrupt the state file."""

    def test_concurrent_updates_all_succeed(self, worktree):
        """Multiple threads updating different keys should all succeed."""
        from shared.s2_state import write_s2_state, update_s2_state, read_s2_state

        initial = _make_state()
        write_s2_state(str(worktree), initial)

        errors = []
        num_threads = 5

        def update_worker(thread_id):
            try:
                def add_convention(state):
                    state["conventions"].append({
                        "key": f"convention-{thread_id}",
                        "value": f"value-{thread_id}",
                        "established_by": f"agent-{thread_id}",
                        "established_at": "2026-04-01T00:00:00+00:00",
                    })
                    return state

                result = update_s2_state(str(worktree), add_convention)
                if not result:
                    errors.append(f"Thread {thread_id} failed")
            except Exception as e:
                errors.append(f"Thread {thread_id}: {e}")

        threads = [
            threading.Thread(target=update_worker, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Thread errors: {errors}"

        # File should be valid JSON
        result = read_s2_state(str(worktree))
        assert result is not None

    def test_concurrent_writes_produce_valid_json(self, worktree):
        """After concurrent writes, the file must be valid JSON."""
        from shared.s2_state import write_s2_state, update_s2_state, read_s2_state

        initial = _make_state()
        write_s2_state(str(worktree), initial)

        num_threads = 3

        def boundary_updater(thread_id):
            def updater(state):
                state["boundaries"][f"agent-{thread_id}"] = {
                    "owns": [f"src/module-{thread_id}/"],
                    "reads": [],
                }
                return state
            update_s2_state(str(worktree), updater)

        threads = [
            threading.Thread(target=boundary_updater, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # The file must be valid JSON (not partially written)
        result = read_s2_state(str(worktree))
        assert result is not None
        assert isinstance(result, dict)
        assert "boundaries" in result


# =============================================================================
# resolve_convention
# =============================================================================


class TestResolveConvention:
    """Last-per-key semantics for convention resolution."""

    def test_returns_none_for_empty_list(self):
        from shared.s2_state import resolve_convention

        assert resolve_convention([], "naming") is None

    def test_returns_none_for_missing_key(self):
        from shared.s2_state import resolve_convention

        conventions = [
            {"key": "naming", "value": "camelCase"},
        ]
        assert resolve_convention(conventions, "formatting") is None

    def test_returns_single_value(self):
        from shared.s2_state import resolve_convention

        conventions = [
            {"key": "naming", "value": "camelCase"},
        ]
        assert resolve_convention(conventions, "naming") == "camelCase"

    def test_last_entry_wins(self):
        from shared.s2_state import resolve_convention

        conventions = [
            {"key": "naming", "value": "camelCase"},
            {"key": "naming", "value": "snake_case"},
            {"key": "naming", "value": "PascalCase"},
        ]
        assert resolve_convention(conventions, "naming") == "PascalCase"

    def test_different_keys_are_independent(self):
        from shared.s2_state import resolve_convention

        conventions = [
            {"key": "naming", "value": "camelCase"},
            {"key": "formatting", "value": "prettier"},
            {"key": "naming", "value": "snake_case"},
        ]
        assert resolve_convention(conventions, "naming") == "snake_case"
        assert resolve_convention(conventions, "formatting") == "prettier"

    def test_handles_entries_without_value(self):
        from shared.s2_state import resolve_convention

        conventions = [
            {"key": "naming"},  # No "value" key
        ]
        assert resolve_convention(conventions, "naming") is None

    def test_handles_entries_without_key(self):
        from shared.s2_state import resolve_convention

        conventions = [
            {"value": "camelCase"},  # No "key" key
        ]
        assert resolve_convention(conventions, "naming") is None


# =============================================================================
# check_boundary_overlap
# =============================================================================


class TestCheckBoundaryOverlap:
    """Detect overlapping 'owns' scopes between agents."""

    def test_empty_boundaries_returns_empty(self):
        from shared.s2_state import check_boundary_overlap

        assert check_boundary_overlap({}) == []

    def test_single_agent_returns_empty(self):
        from shared.s2_state import check_boundary_overlap

        boundaries = {
            "agent-a": {"owns": ["src/server/"], "reads": ["src/types/"]},
        }
        assert check_boundary_overlap(boundaries) == []

    def test_disjoint_scopes_returns_empty(self):
        from shared.s2_state import check_boundary_overlap

        assert check_boundary_overlap(_two_agent_disjoint()) == []

    def test_overlapping_scopes_detected(self):
        from shared.s2_state import check_boundary_overlap

        overlaps = check_boundary_overlap(_two_agent_overlapping())
        assert len(overlaps) == 1
        assert set(overlaps[0]["overlapping_paths"]) == {"src/shared/"}

    def test_prefix_overlap_detected(self):
        """If agent A owns 'src/' and agent B owns 'src/server/', that's an overlap."""
        from shared.s2_state import check_boundary_overlap

        boundaries = {
            "agent-a": {"owns": ["src/"], "reads": []},
            "agent-b": {"owns": ["src/server/"], "reads": []},
        }
        overlaps = check_boundary_overlap(boundaries)
        assert len(overlaps) == 1
        # The shorter path is reported as the overlap
        assert "src/server/" in overlaps[0]["overlapping_paths"] or \
               "src/" in overlaps[0]["overlapping_paths"]

    def test_three_agents_pairwise_overlaps(self):
        from shared.s2_state import check_boundary_overlap

        overlaps = check_boundary_overlap(_three_agent_chain_overlap())
        # agent-a overlaps agent-b (src/shared/utils/ is prefix of src/shared/)
        # agent-b overlaps agent-c (src/shared/ is prefix of src/shared/types/)
        assert len(overlaps) >= 2

    def test_overlap_includes_both_agent_names(self):
        from shared.s2_state import check_boundary_overlap

        overlaps = check_boundary_overlap(_two_agent_overlapping())
        overlap = overlaps[0]
        agents = {overlap["agent_a"], overlap["agent_b"]}
        assert agents == {"backend-coder", "frontend-coder"}

    def test_no_overlap_on_reads_only(self):
        """'reads' scope should not trigger overlap detection."""
        from shared.s2_state import check_boundary_overlap

        boundaries = {
            "agent-a": {"owns": ["src/server/"], "reads": ["src/shared/"]},
            "agent-b": {"owns": ["src/client/"], "reads": ["src/shared/"]},
        }
        assert check_boundary_overlap(boundaries) == []

    def test_missing_owns_key_handled(self):
        from shared.s2_state import check_boundary_overlap

        boundaries = {
            "agent-a": {"reads": ["src/"]},
            "agent-b": {"owns": ["src/server/"], "reads": []},
        }
        # agent-a has no "owns" — should not crash
        assert check_boundary_overlap(boundaries) == []

    def test_identical_scopes_detected(self):
        from shared.s2_state import check_boundary_overlap

        boundaries = {
            "agent-a": {"owns": ["src/shared/"], "reads": []},
            "agent-b": {"owns": ["src/shared/"], "reads": []},
        }
        overlaps = check_boundary_overlap(boundaries)
        assert len(overlaps) == 1
        assert "src/shared/" in overlaps[0]["overlapping_paths"]


# =============================================================================
# file_in_scope
# =============================================================================


class TestFileInScope:
    """Check if a file path falls within scope paths."""

    def test_file_in_scope(self):
        from shared.s2_state import file_in_scope

        assert file_in_scope("src/server/auth.ts", ["src/server/"]) is True

    def test_file_not_in_scope(self):
        from shared.s2_state import file_in_scope

        assert file_in_scope("src/client/app.ts", ["src/server/"]) is False

    def test_empty_scope_list(self):
        from shared.s2_state import file_in_scope

        assert file_in_scope("src/server/auth.ts", []) is False

    def test_multiple_scopes_matches_any(self):
        from shared.s2_state import file_in_scope

        scopes = ["src/server/", "src/api/"]
        assert file_in_scope("src/api/routes.ts", scopes) is True

    def test_nested_path_matches_parent_scope(self):
        from shared.s2_state import file_in_scope

        assert file_in_scope("src/server/auth/middleware.ts", ["src/server/"]) is True

    def test_partial_name_no_match(self):
        """'src/server-utils/' should not match 'src/server/' scope."""
        from shared.s2_state import file_in_scope

        # startswith means "src/server-utils/foo.ts".startswith("src/server/") is False
        assert file_in_scope("src/server-utils/foo.ts", ["src/server/"]) is False

    def test_exact_directory_match(self):
        from shared.s2_state import file_in_scope

        assert file_in_scope("src/server/", ["src/server/"]) is True

    def test_file_at_scope_root(self):
        from shared.s2_state import file_in_scope

        assert file_in_scope("src/server/index.ts", ["src/server/"]) is True


# =============================================================================
# Path Helpers
# =============================================================================


class TestPathHelpers:
    """Test internal path helper functions."""

    def test_s2_state_path(self):
        from shared.s2_state import _s2_state_path

        result = _s2_state_path("/my/worktree")
        assert result == Path("/my/worktree/.pact/s2-state.json")

    def test_ensure_pact_dir_creates_directory(self, tmp_path):
        from shared.s2_state import _ensure_pact_dir

        result = _ensure_pact_dir(str(tmp_path))
        assert result.exists()
        assert result.is_dir()
        assert result == tmp_path / ".pact"

    def test_ensure_pact_dir_idempotent(self, worktree):
        from shared.s2_state import _ensure_pact_dir

        # .pact/ already exists
        result = _ensure_pact_dir(str(worktree))
        assert result.exists()

    def test_now_iso_returns_utc(self):
        from shared.s2_state import _now_iso

        result = _now_iso()
        assert "+" in result or "Z" in result  # Has timezone info
