"""
Tests for environment drift convention in pact-s2-coordination.md.

Location: pact-plugin/tests/test_environment_drift_convention.py
Purpose: Validates that the S2 coordination protocol includes the
         environment drift detection convention for parallel agent work.
Related: pact-plugin/protocols/pact-s2-coordination.md

Tests cover:
1. Environment Drift Detection section exists
2. References file_tracker
3. Dispatch convention documented
"""
from pathlib import Path

import pytest


S2_PATH = Path(__file__).parent.parent / "protocols" / "pact-s2-coordination.md"


class TestEnvironmentDriftConvention:
    """Tests for environment drift convention in S2 coordination."""

    @pytest.fixture
    def s2_content(self):
        return S2_PATH.read_text(encoding="utf-8")

    def test_environment_drift_section_exists(self, s2_content):
        assert "### Environment Drift Detection" in s2_content

    def test_references_file_tracker(self, s2_content):
        assert "file_tracker" in s2_content or "file-edits.json" in s2_content

    def test_dispatch_convention_documented(self, s2_content):
        assert "environment delta" in s2_content.lower() or "Environment Delta" in s2_content
