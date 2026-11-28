"""
Location: pact-plugin/tests/test_git_ceiling_one_parser.py
Summary: GIT_CEILING_DIRECTORIES has one parser
(shared.git_helpers.git_ceiling_directories), used by both marker walks
(shared.project_scope and the memory resolver), and it stops where git
stops. Each case is checked against git itself in a scratch repository.
Used by: pytest.
"""

from __future__ import annotations

import os
import subprocess

import pytest
from shared import project_scope
from shared.git_helpers import git_ceiling_directories

from scripts import project_identity

CASES = [
    "{r}/x", "{r}/z/../x", "{r}//x", "{r}/q/../x", "{r}/x/", "{r}/x/.", "{link}", "relative/x",
    ":{r}/x", ":{r}/z/../x", ":{r}//x", ":{r}/x/", ":{r}/x//", ":{r}/x/.", ":{link}",
]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "x" / "y").mkdir(parents=True)
    (root / "z").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (tmp_path / "link").symlink_to(root / "x")
    return root


@pytest.mark.parametrize("shape", CASES)
def test_the_one_parser_stops_where_git_stops(repo, shape, monkeypatch):
    """git discovers the repository from `<r>/x/y` unless the ceiling stops
    it at `<r>/x`; the parser holds `<r>/x` exactly when git stopped, and
    both walks read the same set. RED WHEN a parser normalises an entry git
    keeps as written, or keeps one git cannot resolve."""
    value = shape.format(r=repo, link=repo.parent / "link")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = value
    found = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=repo / "x" / "y",
        env=env, capture_output=True, text=True,
    ).returncode == 0
    ceilings = git_ceiling_directories(value)
    assert (os.path.realpath(repo / "x") in ceilings) is (not found), (value, found, ceilings)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", value)
    assert project_scope._git_ceiling_directories() == ceilings
    assert project_identity._git_ceiling_directories() == ceilings
