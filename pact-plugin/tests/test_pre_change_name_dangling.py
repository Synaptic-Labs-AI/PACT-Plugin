"""
Location: pact-plugin/tests/test_pre_change_name_dangling.py
Summary: A checkout whose `.git` names a git dir that no longer exists still
counts as a rival when it could have written the bucket. Its name is read
from the `.git` text, which is the name the old resolver gave it while the
git dir existed; the folder-name id covers the old resolver's fallback when
git failed.
Used by: pytest; exercises scripts.database.bucket_evidence with real files.
"""

from __future__ import annotations

import os
from scripts.database import sqlite3

import pytest

from fixtures.identity_git import assert_outside_repository, make_repo
from scripts import database
from scripts.database import bucket_evidence

B = "widget"


def _dangling_gitfile(root, gone):
    (root / ".git").write_text(f"gitdir: {gone / '.git'}\n", encoding="utf-8")


def _dangling_commondir(root, gone):
    admin = gone.parent.parent / "admin" / root.name / "worktrees" / "wt"
    admin.mkdir(parents=True)
    (admin / "commondir").write_text(f"{gone / '.git'}\n", encoding="utf-8")
    (root / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")


def _dangling_symlink(root, gone):
    (root / ".git").symlink_to(gone / ".git")


LAYOUTS = [_dangling_gitfile, _dangling_commondir, _dangling_symlink]


def _evidence(mine, row):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.init_schema(conn)
    for index, path in enumerate((mine / "a.py", row)):
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
            (f"f{index}", os.fspath(path), B),
        )
    return bucket_evidence(conn, B, [str(mine.resolve())])


def _mine(tmp_path):
    assert_outside_repository(tmp_path)
    mine = make_repo(tmp_path / "mine" / B)
    (mine / "a.py").write_text("x", encoding="utf-8")
    return mine


@pytest.mark.parametrize("layout", LAYOUTS)
def test_a_rival_named_like_the_bucket_with_a_dangling_git_stays_foreign(layout, tmp_path):
    """The dangling target's parent ('elsewhere') is not the bucket, but the
    rival's own folder name is, and git failing there made the old resolver
    fall back to that folder name."""
    mine = _mine(tmp_path)
    rival = tmp_path / "theirs" / B
    rival.mkdir(parents=True)
    layout(rival, tmp_path / "gone" / "elsewhere")
    (rival / "a.py").write_text("x", encoding="utf-8")
    evidence = _evidence(mine, rival / "a.py")
    assert evidence["contained_rows"] == 1, evidence
    assert evidence["foreign_checkout_rows"] == 1, evidence


@pytest.mark.parametrize("layout", LAYOUTS)
def test_an_ancestor_whose_dangling_git_names_the_bucket_stays_foreign(layout, tmp_path):
    """The named-directory rule: an ancestor checkout whose `.git` names a
    deleted git dir under a folder called the bucket wrote that bucket while
    the git dir existed. Treating a dangling `.git` as unnamed would drop it
    from the rule and make this row neutral."""
    mine = _mine(tmp_path)
    outer = tmp_path / "outer" / "x"
    inner = make_repo(outer / "inner")
    (inner / "a.py").write_text("x", encoding="utf-8")
    layout(outer, tmp_path / "gone" / B)
    evidence = _evidence(mine, inner / "a.py")
    assert evidence["foreign_checkout_rows"] == 1, evidence
