"""
Static structural tests for the inbox-wake ARMING surface.

Covers (per #591):
  - Sentinel-pair presence + ordering at every ARMING_FILE.
  - Cron schedule literal pinning (twin grep-gate per memory bb101a99).
  - STATE_FILE inline-prose glue between Monitor (end) and Cron (start).

The byte-equivalence of the captured canonical block bodies against
fixtures is enforced by `scripts/verify-protocol-extracts.sh` (see
test_inbox_wake_canonical_mirror.py). These tests verify the structural
surface — sentinel pairs are present, ordered, and the inline-prose
glue between them references the registry file.
"""
import re

import pytest

from fixtures.inbox_wake import (
    ARMING_FILES, COMMANDS_DIR, FIXTURES_DIR,
    MONITOR_START, MONITOR_END, CRON_START, CRON_END,
    _read, _between,
)


class TestArmingFilesContainCanonicalBlocks:
    """Each ARMING_FILE must contain both Monitor and Cron sentinel pairs.

    Byte-equivalence is enforced separately by the verify-script subprocess
    test. These tests verify the structural surface.
    """

    @pytest.mark.parametrize("name", ARMING_FILES)
    def test_contains_monitor_sentinel_pair(self, name):
        text = _read(COMMANDS_DIR / name)
        assert MONITOR_START in text, f"{name} missing Monitor (start) sentinel"
        assert MONITOR_END in text, f"{name} missing Monitor (end) sentinel"
        assert text.index(MONITOR_START) < text.index(MONITOR_END), (
            f"{name} Monitor sentinels are out of order"
        )

    @pytest.mark.parametrize("name", ARMING_FILES)
    def test_contains_cron_sentinel_pair(self, name):
        text = _read(COMMANDS_DIR / name)
        assert CRON_START in text, f"{name} missing Cron (start) sentinel"
        assert CRON_END in text, f"{name} missing Cron (end) sentinel"
        assert text.index(CRON_START) < text.index(CRON_END), (
            f"{name} Cron sentinels are out of order"
        )

    @pytest.mark.parametrize("name", ARMING_FILES)
    def test_monitor_precedes_cron(self, name):
        """Arm-step ordering: Monitor block precedes Cron block at every
        ARMING_FILE. The STATE_FILE write step lives between them."""
        text = _read(COMMANDS_DIR / name)
        assert text.index(MONITOR_END) < text.index(CRON_START), (
            f"{name} Cron block must follow Monitor block"
        )


class TestCronLiteralPinning:
    """The cron schedule literal `*/4 * * * *` is pinned across all ARMING_FILES.

    Twin grep-gate per memory bb101a99: forbid-literal scoped to refactor
    closure (`*/3 * * * *` and `*/5 * * * *` are explicitly excluded as
    common drift candidates) + require-literal at the canonical surface.
    """

    REQUIRED = "*/4 * * * *"
    FORBIDDEN = ["*/3 * * * *", "*/5 * * * *"]

    @pytest.mark.parametrize("name", ARMING_FILES)
    def test_required_cadence_present(self, name):
        text = _read(COMMANDS_DIR / name)
        assert self.REQUIRED in text, (
            f"{name} missing required cron cadence '{self.REQUIRED}' — "
            "the canonical Cron block must use 4-minute off-prime cadence"
        )

    @pytest.mark.parametrize("name", ARMING_FILES)
    @pytest.mark.parametrize("forbidden", FORBIDDEN)
    def test_forbidden_cadence_absent(self, name, forbidden):
        text = _read(COMMANDS_DIR / name)
        assert forbidden not in text, (
            f"{name} contains forbidden cron cadence '{forbidden}' — "
            "common drift candidate from the canonical 4-minute cadence"
        )

    def test_required_cadence_in_fixture(self):
        text = _read(FIXTURES_DIR / "cron-block.txt")
        assert self.REQUIRED in text, (
            f"cron-block.txt missing canonical cadence '{self.REQUIRED}'"
        )


class TestStateFileWritePhrase:
    """CF7 inline-prose glue: each ARMING_FILE writes the STATE_FILE between
    Monitor (end) and Cron (start). The STATE_FILE write step is NOT under
    canonical-mirror sentinels — drift across callsites would only be
    caught by manual review or this regression. Pins:
      (a) the file path appears in the inline-prose region, AND
      (b) the v=1 schema marker appears in that region.
    """

    @pytest.mark.parametrize("name", ARMING_FILES)
    def test_state_file_path_between_monitor_and_cron(self, name):
        text = _read(COMMANDS_DIR / name)
        between = _between(text, MONITOR_END, CRON_START)
        assert "inbox-wake-state.json" in between, (
            f"{name} missing STATE_FILE write phrase between Monitor (end) "
            "and Cron (start) sentinels — CF7 inline-prose glue"
        )

    @pytest.mark.parametrize("name", ARMING_FILES)
    def test_state_file_schema_version_present(self, name):
        text = _read(COMMANDS_DIR / name)
        between = _between(text, MONITOR_END, CRON_START)
        # CF7 schema is {"v":1,"monitor_task_id":...,"cron_job_id":...,...}.
        # Pin the v=1 marker so a future schema bump is caught. Permissive
        # whitespace match — accepts `"v":1`, `"v": 1`, `"v" : 1`, etc.;
        # rejects nothing semantically valid.
        assert re.search(r'"v"\s*:\s*1', between), (
            f"{name} STATE_FILE write phrase missing v=1 schema marker "
            "between Monitor and Cron sentinels"
        )
