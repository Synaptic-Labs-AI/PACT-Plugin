"""
Location: pact-plugin/tests/test_remediation_identity_parser.py
Summary: Arms for the repository-identity parser (`project_identity.py`): which
         origin value is read and from which config, how a remote string is
         classified, how submodule and redirect shapes are recognised, and
         what the override accepts. Each arm builds real git repositories with
         fake origin URLs; no network is contacted.
Used by: pytest. Shares the real-git helpers in `fixtures/identity_git.py`.
"""

from __future__ import annotations

from pathlib import Path

from fixtures.identity_git import assert_outside_repository, make_repo, run_git

from scripts import project_identity as pi

CANONICAL = pi.OUTCOME_CANONICAL
NO_PORTABLE = pi.OUTCOME_NO_PORTABLE_ORIGIN
DECLINE = pi.OUTCOME_DECLINE


def _origin_id(repo: Path):
    """The (outcome, id) the classifier gives for what `read_origin` returns."""
    origin = pi.read_origin(repo)
    assert origin.state == pi.ORIGIN_PRESENT, origin
    return pi.classify_remote_origin(origin.value)


def _add_url(repo: Path, url: str) -> None:
    run_git("config", "--add", "remote.origin.url", url, cwd=repo)


class TestTheOriginIsTheUrlGitFetches:
    """The origin is the first `remote.origin.url` in the repository's own
    config, includes followed. git fetches from the first of several urls;
    the last one is a push-only url."""

    def test_the_first_of_two_urls_wins(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="git@first.example:o/first.git")
        _add_url(repo, "git@second.example:o/second.git")

        assert _origin_id(repo) == (CANONICAL, "first.example/o/first")

    def test_a_url_set_only_through_an_include_is_present(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        included = tmp_path / "included.cfg"
        included.write_text(
            '[remote "origin"]\n\turl = git@included.example:o/inc.git\n',
            encoding="utf-8",
        )
        run_git("config", "include.path", str(included), cwd=repo)

        assert _origin_id(repo) == (CANONICAL, "included.example/o/inc")

    def test_an_empty_value_clears_the_urls_before_it(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="git@old.example:o/old.git")
        _add_url(repo, "")
        _add_url(repo, "git@new.example:o/new.git")

        assert _origin_id(repo) == (CANONICAL, "new.example/o/new")

    def test_an_empty_value_with_nothing_after_it_is_absent(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="git@old.example:o/old.git")
        _add_url(repo, "")

        assert pi.read_origin(repo) == pi.OriginRead(pi.ORIGIN_ABSENT, None)

    def test_a_value_holding_a_newline_is_read_as_one_value(self, tmp_path):
        """A line-oriented reader would take the text before the newline as a
        complete, valid remote."""
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        with open(repo / ".git" / "config", "a", encoding="utf-8") as handle:
            handle.write(
                '[remote "origin"]\n'
                '\turl = "git@a.example:o/a.git\\ngit@evil.example:x/y"\n'
            )

        origin = pi.read_origin(repo)

        assert origin == pi.OriginRead(
            pi.ORIGIN_PRESENT, "git@a.example:o/a.git\ngit@evil.example:x/y"
        )


class TestMachineLocalConfigIsNotAnIdentityInput:
    """Global, system and environment config, and `config.worktree`, would
    give one repository different ids on different machines or worktrees."""

    def _origin_less_repo(self, tmp_path) -> Path:
        assert_outside_repository(tmp_path)
        return make_repo(tmp_path / "repo")

    def test_a_global_url_does_not_set_the_identity(self, tmp_path, monkeypatch):
        repo = self._origin_less_repo(tmp_path)
        global_config = tmp_path / "global.cfg"
        global_config.write_text(
            '[remote "origin"]\n\turl = git@global.example:o/g.git\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))

        assert pi.read_origin(repo).state == pi.ORIGIN_ABSENT

    def test_environment_config_does_not_set_the_identity(self, tmp_path, monkeypatch):
        repo = self._origin_less_repo(tmp_path)
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "remote.origin.url")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "git@env.example:o/e.git")
        monkeypatch.setenv(
            "GIT_CONFIG_PARAMETERS", "'remote.origin.url'='git@param.example:o/p.git'"
        )

        assert pi.read_origin(repo).state == pi.ORIGIN_ABSENT

    def test_an_exported_git_config_file_neither_sets_nor_breaks_the_identity(
        self, tmp_path, monkeypatch
    ):
        """`GIT_CONFIG` names a file for `git config` only; git fetch ignores
        it. Honouring it lets the environment choose the identity, and passing
        it to a `--local` read makes git refuse ("only one config file at a
        time"), which would decline every memory operation."""
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="git@local.example:o/l.git")
        other = tmp_path / "other.cfg"
        other.write_text(
            '[remote "origin"]\n\turl = git@other.example:o/x.git\n', encoding="utf-8"
        )
        monkeypatch.setenv("GIT_CONFIG", str(other))

        assert _origin_id(repo) == (CANONICAL, "local.example/o/l")

    def test_a_worktree_scoped_url_does_not_set_the_identity(self, tmp_path):
        repo = self._origin_less_repo(tmp_path)
        run_git("config", "extensions.worktreeConfig", "true", cwd=repo)
        run_git("config", "--worktree", "remote.origin.url",
                "git@worktree.example:o/w.git", cwd=repo)

        assert pi.read_origin(repo).state == pi.ORIGIN_ABSENT
