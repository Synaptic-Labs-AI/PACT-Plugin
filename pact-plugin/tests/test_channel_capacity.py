"""
Tests for pact-channel-capacity.md protocol content.

Tests cover:
1. Channel capacity signal format (NOMINAL/ELEVATED/CRITICAL)
2. Batch protocol fields present
3. Back-pressure signal format
4. Capacity indicator table present
5. Shannon's theorem attribution
"""
from pathlib import Path

import pytest

PROTOCOLS_DIR = Path(__file__).parent.parent / "protocols"
CHANNEL_CAPACITY = PROTOCOLS_DIR / "pact-channel-capacity.md"


@pytest.fixture
def capacity_content():
    return CHANNEL_CAPACITY.read_text(encoding="utf-8")


# =============================================================================
# Protocol structure
# =============================================================================


class TestChannelCapacityStructure:
    """Verify pact-channel-capacity.md has required sections."""

    def test_file_exists(self):
        assert CHANNEL_CAPACITY.exists()

    def test_has_main_heading(self, capacity_content):
        assert "Channel Capacity" in capacity_content

    def test_has_shannon_attribution(self, capacity_content):
        """Should reference Shannon's Channel Capacity Theorem."""
        assert "Shannon" in capacity_content

    def test_has_context_window_as_channel(self, capacity_content):
        """Maps context window to communication channel."""
        lower = capacity_content.lower()
        assert "context window" in lower

    def test_has_capacity_indicators_section(self, capacity_content):
        assert "Capacity Indicator" in capacity_content or "Indicator" in capacity_content


# =============================================================================
# Capacity signal format
# =============================================================================


class TestCapacitySignalFormat:
    """Validate capacity signal levels and format."""

    def test_three_signal_levels(self, capacity_content):
        """Must define NOMINAL, ELEVATED, CRITICAL levels."""
        assert "NOMINAL" in capacity_content
        assert "ELEVATED" in capacity_content
        assert "CRITICAL" in capacity_content

    def test_nominal_means_healthy(self, capacity_content):
        lower = capacity_content.lower()
        # NOMINAL should be associated with healthy/normal state
        assert "nominal" in lower and "healthy" in lower

    def test_elevated_means_approaching_limits(self, capacity_content):
        lower = capacity_content.lower()
        assert "elevated" in lower and ("approaching" in lower or "limit" in lower)

    def test_critical_means_capacity_exceeded(self, capacity_content):
        lower = capacity_content.lower()
        assert "critical" in lower and ("exceed" in lower or "pause" in lower)

    def test_signal_has_current_load(self, capacity_content):
        lower = capacity_content.lower()
        assert "current load" in lower or "current_load" in lower

    def test_signal_has_trend(self, capacity_content):
        lower = capacity_content.lower()
        assert "trend" in lower

    def test_signal_has_recommended_action(self, capacity_content):
        lower = capacity_content.lower()
        assert "recommended action" in lower or "recommended_action" in lower


# =============================================================================
# Batch protocol
# =============================================================================


class TestBatchProtocol:
    """Validate batch protocol section."""

    def test_has_batch_section(self, capacity_content):
        assert "Batch Protocol" in capacity_content or "Batch" in capacity_content

    def test_has_batching_strategies(self, capacity_content):
        lower = capacity_content.lower()
        assert "batch" in lower and ("strateg" in lower or "combine" in lower)

    def test_has_lossless_priority(self, capacity_content):
        """Batch protocol should mention prioritizing lossless fields."""
        lower = capacity_content.lower()
        assert "lossless" in lower


# =============================================================================
# Back-pressure
# =============================================================================


class TestBackPressure:
    """Validate back-pressure section."""

    def test_has_back_pressure_section(self, capacity_content):
        lower = capacity_content.lower()
        assert "back-pressure" in lower or "backpressure" in lower

    def test_elevated_triggers_back_pressure(self, capacity_content):
        """ELEVATED should trigger back-pressure mechanisms."""
        lower = capacity_content.lower()
        assert "elevated" in lower
