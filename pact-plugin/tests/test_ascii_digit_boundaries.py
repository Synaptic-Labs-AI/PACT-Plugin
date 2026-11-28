"""
Location: pact-plugin/tests/test_ascii_digit_boundaries.py
Summary: Every place the identity, backlog and session-end code turns
untrusted text into a number accepts only ASCII digits and bounds the length
before `int()`, so a superscript, another script's digit or an over-long
value is refused rather than raised. `str.isdigit` accepts `²`, which
`int()` refuses; `int()` refuses a decimal string past 4300 digits.
Used by: the test suite; no production code imports it.
"""

from unittest.mock import patch

import pytest

from scripts import project_identity as pi
from scripts.database import is_canonical_id
from shared import backlog
from shared.git_helpers import git_config_bool

import session_end

# A superscript digit (isdigit, not decimal), another script's decimal digit
# (Arabic-Indic three) and a fullwidth one.
NON_ASCII_DIGITS = ["\u00b2", "\u0663", "\uff11"]
OVER_LONG = "1" * 5000


class TestTheRemotePort:
    @pytest.mark.parametrize("port", [*NON_ASCII_DIGITS, OVER_LONG])
    def test_a_port_that_is_not_ascii_digits_is_refused(self, port):
        assert pi._valid_port(port) is False

    @pytest.mark.parametrize("port", NON_ASCII_DIGITS)
    def test_a_remote_with_such_a_port_declines_without_raising(self, port):
        url = f"https://example.com:{port}/o/r"
        assert pi.normalize_remote_url(url) is None
        outcome, _value = pi.classify_remote_origin(url)
        assert outcome == pi.OUTCOME_DECLINE

    def test_control_an_ascii_port_is_kept(self):
        assert pi.normalize_remote_url("https://example.com:2/o/r") == "example.com:2/o/r"


class TestThePercentEscape:
    @pytest.mark.parametrize("digit", NON_ASCII_DIGITS)
    def test_a_non_ascii_hex_digit_is_refused(self, digit):
        assert pi._normalize_segment(f"a%{digit}3") is None

    def test_control_an_ascii_escape_decodes(self):
        assert pi._normalize_segment("a%2Db") == "a-b"


class TestTheCanonicalIdPort:
    @pytest.mark.parametrize("port", [*NON_ASCII_DIGITS, "1" * 200])
    def test_such_a_port_is_not_canonical(self, port):
        assert is_canonical_id(f"example.com:{port}/o/r") is False


class TestTheGitBoolean:
    @pytest.mark.parametrize("value", [*NON_ASCII_DIGITS, OVER_LONG, "-" + OVER_LONG])
    def test_such_a_value_is_refused(self, value):
        assert git_config_bool(value) is None

    def test_control_an_eleven_digit_value_is_out_of_range(self):
        assert git_config_bool("10000000000") is None
        assert git_config_bool("2147483647") is True


class TestThePullRequestNumber:
    @pytest.mark.parametrize("value", [*NON_ASCII_DIGITS, OVER_LONG])
    def test_such_a_number_is_none(self, value):
        assert session_end._as_pr_int(value) is None

    def test_control_an_ascii_number_converts(self):
        assert session_end._as_pr_int("42") == 42

    @pytest.mark.parametrize("digit", NON_ASCII_DIGITS)
    def test_a_handoff_link_with_such_a_number_names_no_pr(self, digit):
        tasks = [{"metadata": {"handoff": {"x": f"https://github.com/o/r/pull/{digit}"}}}]
        with patch("session_end.read_events", return_value=[]), patch(
            "session_end.check_pr_state",
            side_effect=AssertionError("a non-ASCII number reached the PR check"),
        ):
            assert session_end.check_unpaused_pr(tasks=tasks, project_slug="p") is None


class TestTheBacklogRef:
    @pytest.mark.parametrize("digit", NON_ASCII_DIGITS)
    def test_a_bare_ref_with_such_a_digit_is_no_issue_number(self, digit):
        kind = backlog._classify_ref("#" + digit)
        assert kind.number is None
        assert kind.kind == backlog._KIND_OPAQUE

    @pytest.mark.parametrize("digit", NON_ASCII_DIGITS)
    def test_a_github_link_with_such_a_digit_is_no_issue_number(self, digit):
        kind = backlog._classify_ref(f"https://github.com/o/r/issues/{digit}")
        assert kind.number is None

    def test_an_over_long_number_is_out_of_range(self):
        assert backlog._classify_ref("#" + "9" * 20).kind == backlog._KIND_OUT_OF_RANGE

    def test_control_an_ascii_bare_ref_is_a_number(self):
        assert backlog._classify_ref("#12").number == 12
