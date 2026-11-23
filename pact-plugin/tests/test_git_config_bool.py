"""
Location: pact-plugin/tests/test_git_config_bool.py
Summary: Holds shared/git_helpers.git_config_bool to git's own boolean parser,
value by value, and both callers (project_identity and project_scope) to it.
Used by: the test suite; no production code imports it.

Each expected answer is git's, asked through `git config --type=bool`, the
parser GIT_DISCOVERY_ACROSS_FILESYSTEM goes through (`git_env_bool` calls
`git_config_bool`). A value git refuses answers None here.
"""

import os
import random
import re
import subprocess

import pytest

from shared import project_scope
from shared.git_helpers import git_config_bool

# Spellings the two old parsers disagreed with git on (" 1", "0x1", "1k",
# "maybe"), the edges of git's integer rule, and the word forms.
_NAMED = [
    "", " ", "1", " 1", "1 ", "\t1", "\n0", "  0", "0x1", "0x0", "0X1F", "0x",
    "0x1g", "1k", "0k", "1K", "2m", "1g", "2g", "-2g", "1kk", "1kb", "k", "010",
    "07", "08", "00", "0", "+0", "-0", "-1", "+1", "+", "-", "1.0", "1_0",
    "2147483647", "2147483648", "-2147483647", "-2147483648", "2097151k",
    "2097152k", "2047m", "2048m", "0x7fffffff", "0x80000000", "9999999999999",
    "maybe", "tru", "true", " true", "true ", "TRUE", "On", "Yes", "yes ",
    "OFF", "fAlSe", "no", "\u00a01", "\uff34\uff32\uff35\uff25", "\u0661",
]


def _random_values(count: int) -> list:
    rng = random.Random(28)
    alphabet = " \t+-0123456789xXabcdefkKmMgGtrueyonsfl"
    return [
        "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6)))
        for _ in range(count)
    ]


def _git_answer(value: str):
    result = subprocess.run(
        ["git", "-c", f"pact.probe={value}", "config", "--type=bool", "pact.probe"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    answer = result.stdout.strip()
    if result.returncode == 0 and answer in ("true", "false"):
        return answer == "true"
    assert "bad boolean config value" in result.stderr, result.stderr
    return None


_AT_INT_MIN = re.compile(
    r"[ \t\n\v\f\r]*-(?:0[xX](?P<hex>[0-9a-fA-F]+)|(?P<oct>0[0-7]*)|(?P<dec>[1-9][0-9]*))"
    r"(?P<unit>[kKmMgG]?)"
)
_UNITS = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30}


def _at_int_min(value: str) -> bool:
    """Whether `value` is exactly -2^31, the one integer git versions
    disagree on: git 2.43 refuses it, a newer git accepts it."""
    match = _AT_INT_MIN.fullmatch(value)
    if match is None:
        return False
    number = (
        int(match["hex"], 16) if match["hex"] is not None
        else int(match["oct"], 8) if match["oct"] is not None
        else int(match["dec"])
    )
    return number * _UNITS[match["unit"].lower()] == 1 << 31


@pytest.mark.parametrize("value", _NAMED + _random_values(400))
def test_the_shared_parser_answers_as_git_does(value):
    """Every value the installed git agrees on with every supported git."""
    if _at_int_min(value):
        assert git_config_bool(value) is True, value
        return
    assert git_config_bool(value) is _git_answer(value)


@pytest.mark.parametrize("value", [
    "-2147483648", "-0x80000000", "-020000000000", "-2097152k", "-2097152K",
    "-2048m", "-2g", "-2G",
])
def test_minus_two_to_the_31_is_accepted_as_a_newer_git_accepts_it(value):
    """The parser accepts what any supported git accepts. RED WHEN the
    negative bound is git 2.43's -(2^31-1) rather than -2^31."""
    assert _at_int_min(value)
    assert git_config_bool(value) is True


@pytest.mark.parametrize("value", ["-2147483649", "-2097153k", "-3g", "2147483648", "2g"])
def test_beyond_a_c_int_is_refused_by_every_git(value):
    assert git_config_bool(value) is None


@pytest.mark.parametrize("value", [" 1", "0x1", "1k", "2m", "1 ", "maybe", "0"])
def test_both_callers_read_the_shared_answer(value, monkeypatch):
    """project_scope reads an unknown value as true (it counts more metadata,
    failing closed); project_identity reads it as false (git cannot answer
    the directory then, so the resolver declines it either way). A known
    value is git's answer in both."""
    from scripts.project_identity import _git_env_bool as identity_bool

    git = _git_answer(value)
    monkeypatch.setenv("PACT_PROBE_BOOL", value)
    assert identity_bool("PACT_PROBE_BOOL") is (git is True)
    assert project_scope._git_env_bool(value) is (True if git is None else git)
