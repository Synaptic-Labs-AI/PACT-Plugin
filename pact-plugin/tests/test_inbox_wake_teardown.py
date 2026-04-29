"""
Static structural tests for the inbox-wake TEARDOWN surface.

Covers:
  - Sentinel-pair presence + ordering at every TEARDOWN_FILE.

Byte-equivalence of the captured Teardown block body against
`pact-plugin/tests/fixtures/inbox-wake-canonical/teardown-block.txt`
is enforced separately by the verify-script subprocess test.
"""
import pytest

from fixtures.inbox_wake import (
    TEARDOWN_FILES, COMMANDS_DIR,
    TEARDOWN_START, TEARDOWN_END,
    _read,
)


class TestTeardownFilesContainCanonicalBlocks:
    @pytest.mark.parametrize("name", TEARDOWN_FILES)
    def test_contains_teardown_sentinel_pair(self, name):
        text = _read(COMMANDS_DIR / name)
        assert TEARDOWN_START in text, f"{name} missing Teardown (start) sentinel"
        assert TEARDOWN_END in text, f"{name} missing Teardown (end) sentinel"
        assert text.index(TEARDOWN_START) < text.index(TEARDOWN_END), (
            f"{name} Teardown sentinels are out of order"
        )
