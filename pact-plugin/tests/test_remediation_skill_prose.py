"""
Agent-facing prose that must agree with what the pact-memory code emits.

Location: pact-plugin/tests/test_remediation_skill_prose.py

The pact-memory SKILL.md and the secretary's agent file are loaded into agent
sessions as instructions. When they describe an envelope field or an exit code
the code does not produce, an agent looks for a key that is not there or reads
a failure as something else. Each arm here binds one load-bearing clause.

HOW THE ARMS READ THE PROSE. Markdown heading lines are dropped before any
search, so a heading cannot satisfy its own clause. Backticks are removed and
whitespace is collapsed on both sides, so a re-wrap or a backtick change does
not red an arm, while a deleted or inverted clause does.
"""

import functools
import re
from pathlib import Path

import pytest

from scripts.project_identity import DECLINE_REASONS

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SKILL = PLUGIN_ROOT / "skills" / "pact-memory" / "SKILL.md"
SECRETARY = PLUGIN_ROOT / "agents" / "pact-secretary.md"
SCRIPTS = PLUGIN_ROOT / "skills" / "pact-memory" / "scripts"

# The decline causes `project-id show` and `status` can carry in
# `decline_reason.reason`, taken from the code's own registry so a cause
# added there reds the row pin until SKILL.md names it.
DECLINE_TOKENS = tuple(sorted(DECLINE_REASONS))


def _flatten(text: str) -> str:
    """Backticks removed and whitespace collapsed."""
    return re.sub(r"\s+", " ", text.replace("`", "")).strip()


def _body_lines(path: Path) -> list:
    """The file's lines with every markdown heading line removed."""
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def _table_row(path: Path, first_cell: str) -> str:
    """The one table row whose first cell is `first_cell`, flattened."""
    rows = [
        line
        for line in _body_lines(path)
        if line.startswith("|")
        and _flatten(line.split("|")[1]) == first_cell
    ]
    assert len(rows) == 1, (
        f"expected exactly one table row whose first cell is {first_cell!r}, "
        f"found {len(rows)}"
    )
    return _flatten(rows[0])


def _body(path: Path) -> str:
    return _flatten("\n".join(_body_lines(path)))


class TestDeclineReasonIsDocumentedAsEmitted:
    """The `project-id show` row names the shape and every cause."""

    def test_the_row_states_the_emitted_shape(self):
        row = _table_row(SKILL, "project-id show")
        assert (
            "decline_reason is {source, reason, override_path} whenever "
            "resolution declined for a named cause, and null otherwise"
        ) in row

    def test_the_retired_shape_is_gone(self):
        assert "{reason, override}" not in _body(SKILL).lower()

    @pytest.mark.parametrize("token", DECLINE_TOKENS)
    def test_each_cause_is_named_in_the_row(self, token):
        assert token in _table_row(SKILL, "project-id show")

    REASON_LIST_START = "reason is one of "
    REASON_LIST_END = ", and override_path is"

    def _documented_reasons(self) -> set:
        row = _table_row(SKILL, "project-id show")
        start = row.index(self.REASON_LIST_START) + len(self.REASON_LIST_START)
        listed = row[start:row.index(self.REASON_LIST_END, start)]
        return {n.strip() for n in listed.replace(" or ", ", ").split(",") if n.strip()}

    def test_the_documented_causes_are_the_code_registry(self):
        """The row's list is exactly the registry: a stale or extra name reds."""
        assert self._documented_reasons() == set(DECLINE_REASONS)


class TestExitCodeThreeCoversBothVerbs:
    def test_row_three_names_project_file_and_rekey(self):
        row = _table_row(SKILL, "3")
        assert "project-file: this session's project memory file is unavailable" in row
        assert "project-id rekey: an operational failure; nothing was moved" in row

    def test_the_rekey_one_and_two_meanings_are_stated(self):
        assert (
            "project-id rekey exits 1 when it refuses \u2014 --to is omitted and no "
            "canonical identity resolves, or --to is omitted and --from is not "
            "this checkout's own legacy candidate that is neither excluded nor "
            "unproven, or --db-path names a store that is not present \u2014 and 2 "
            "for bad input"
        ) in _body(SKILL)


class TestSecretaryProjectionKey:
    """The secretary's Working Memory step names the key the store uses. Each
    case its sentences name is run on a real layout, and the identity the
    resolver gives there is the one the sentence states; a short phrase of
    the sentence ties each case to its prose, so a sentence that drops a case
    or a resolver that changes one fails here."""

    _CANONICAL = "github.com/acme/widget"
    _OVERRIDE = "override.example.com/team/pinned"

    @staticmethod
    def _layout(case, root, monkeypatch):
        """Build `case` under `root`, enter it, and return the folder whose
        name a folder-name key would use."""
        import subprocess

        from fixtures.identity_git import make_repo, write_common_dir_override

        canonical = "https://github.com/acme/widget.git"
        checkout = root / "checkout"
        if case == "override file":
            write_common_dir_override(make_repo(checkout, remote=canonical),
                                      TestSecretaryProjectionKey._OVERRIDE)
        elif case == "origin remote":
            make_repo(checkout, remote=canonical)
        elif case == "no origin":
            make_repo(checkout)
        elif case == "local-path origin":
            make_repo(checkout, remote=str(root / "elsewhere"))
        elif case == "no repository":
            (checkout / ".claude").mkdir(parents=True)
        elif case == "submodule":
            source = make_repo(root / "source")
            make_repo(root / "super")
            added = subprocess.run(
                ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "sub"],
                cwd=root / "super", capture_output=True, text=True, timeout=60, check=False,
            )
            assert added.returncode == 0, added.stderr
            checkout = root / "super" / "sub"
        elif case == "unrepresentable origin":
            make_repo(checkout, remote="https://host/owner/re po")
        elif case == "broken .git":
            checkout.mkdir()
            (checkout / ".git").write_text("not a gitdir line\n", encoding="utf-8")
        elif case == "bare .git":
            checkout.mkdir()
            subprocess.run(["git", "init", "-q", "--bare", str(checkout / ".git")], check=True, timeout=30)
        elif case in ("home checkout", "home checkout git cannot see"):
            make_repo(checkout, remote=canonical)
            monkeypatch.setattr(Path, "home", classmethod(lambda cls: checkout))
            if case == "home checkout git cannot see":
                monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(checkout))
                (checkout / "sub").mkdir()
                monkeypatch.chdir(checkout / "sub")
                return checkout
        else:
            raise AssertionError(case)
        monkeypatch.chdir(checkout)
        return checkout

    # (phrase of the sentence naming the case, case, a user setting, declared,
    # the identity it states: a literal, "folder" for the folder's name, or
    # None for a decline)
    CASES = [
        ("from the repository's override file", "override file", None, False, _OVERRIDE),
        ("or its origin remote", "origin remote", None, False, _CANONICAL),
        ("the origin is absent or a local path", "no origin", None, False, "folder"),
        ("the origin is absent or a local path", "local-path origin", None, False, "folder"),
        ("folder name when there is no override file", "no repository", None, False, "folder"),
        ("except in a submodule", "submodule", None, False, None),
        ("home directory is keyed by its folder name", "home checkout", None, False, "folder"),
        ("home directory is keyed by its folder name", "home checkout", "my-setting", False, "folder"),
        ("home directory is keyed by its folder name", "home checkout", None, True, "folder"),
        ("no project directory is declared and no repository is found", "no repository", "my-setting", False,
         "my-setting"),
        ("no project directory is declared and no repository is found", "no repository", "my-setting", True,
         "folder"),
        ("when git cannot see a home-directory checkout", "home checkout git cannot see", "my-setting", False,
         "my-setting"),
        ("when git cannot see a home-directory checkout", "home checkout git cannot see", None, False, "folder"),
        ("a present origin that cannot be represented", "unrepresentable origin", None, False, None),
        ("a broken, redirected or bare .git", "broken .git", None, False, None),
        ("a broken, redirected or bare .git", "bare .git", None, False, None),
    ]

    @pytest.mark.parametrize("phrase, case, setting, declared, stated", CASES)
    def test_each_case_the_sentence_names_resolves_as_it_states(
            self, tmp_path, monkeypatch, phrase, case, setting, declared, stated):
        import json
        import os

        from fixtures.identity_git import assert_outside_repository
        from scripts.memory_api import PACTMemory

        assert phrase in " ".join(_body(SECRETARY).split()), phrase
        assert_outside_repository(tmp_path)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)
        folder = self._layout(case, tmp_path, monkeypatch)
        if setting is not None:
            memory_dir = Path(os.environ["PACT_TEST_MEMORY_DIR"])
            memory_dir.mkdir(parents=True, exist_ok=True)
            (memory_dir / "config.json").write_text(
                json.dumps({"project_id": setting}), encoding="utf-8")
        if declared:
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(folder))
        project_id, _source = PACTMemory._detect_project_id_with_source()
        assert project_id == (folder.name if stated == "folder" else stated), (case, project_id)

    def test_every_case_the_projection_sentence_names_is_run(self):
        """Control: the rows cover every clause of the two sentences, so a
        clause added to the prose without a row fails here."""
        body = " ".join(_body(SECRETARY).split())
        start = body.index("The projection is keyed by")
        passage = body[start:body.index("nothing is projected.", start)]
        clauses = {phrase for phrase, *_rest in self.CASES}
        assert all(phrase in passage for phrase in clauses), clauses
        assert passage.count("keys it instead") == 1 and passage.count("declines instead") == 1, passage

    @pytest.mark.parametrize("launched_in, counts", [("", True), ("sub", False)], ids=["root", "subfolder"])
    def test_the_legacy_bucket_is_read_only_on_evidence(self, tmp_path, launched_in, counts):
        """A session that wrote into the legacy bucket counts as evidence
        when it was launched at the repository's root, and not when it was
        launched in a subfolder, as the sentence states; its phrases tie the
        rule to the prose, and the retired "until it is rekeyed" stays gone."""
        from datetime import datetime, timezone

        from fixtures.identity_git import make_repo, seed_session_evidence
        from scripts.database import db_connection, ensure_initialized

        from scripts import memory_api

        body = _body(SECRETARY)
        for phrase in ("only when a file row places it under this repository or its linked worktrees",
                       "a session launched at the root of this repository or of one of its linked worktrees "
                       "wrote into it",
                       "and no row in it was written by a session recorded outside this repository and its "
                       "linked worktrees",
                       "An admitted bucket is shared with every checkout whose root folder has that name"):
            assert phrase in body, phrase
        assert "until it is rekeyed" not in body.lower()
        memory_api._PRUNED_SESSION_INDEX.clear()
        repo = make_repo(tmp_path / "proj", remote="https://github.com/o/proj.git")
        (repo / "sub").mkdir()
        store = tmp_path / "store.db"
        now = datetime.now(timezone.utc).isoformat()
        with db_connection(store) as conn:
            ensure_initialized(conn)
            conn.execute("INSERT INTO memories (id, context, project_id, session_id, created_at, updated_at) "
                         "VALUES (?, 'x', 'proj', 'writer', ?, ?)", ("a" * 32, now, now))
            conn.commit()
        seed_session_evidence(tmp_path / "home" / ".claude", repo / launched_in if launched_in else repo, "writer")
        with db_connection(store) as conn:
            assert memory_api._legacy_session_evidence(conn, "proj", [str(repo)], repo / ".git") is counts

    def test_the_retired_folder_name_key_is_gone(self):
        assert "keyed by the basename of the project root" not in _body(SECRETARY).lower()


class TestOverrideAcceptsCanonicalIdsOnly:
    """The override bullet states the canonical-only rule the reader enforces."""

    def test_only_a_canonical_id_is_accepted(self):
        assert (
            "only a canonical host/owner/repo id is accepted: a folder-name "
            "value is reported as override_ignored with reason not_canonical"
        ) in _body(SKILL)

    def test_the_copied_store_route_is_an_explicit_rekey(self):
        assert (
            "To move a copied store's folder-name bucket, use project-id "
            "rekey --from=<old> --to=<canonical>"
        ) in _body(SKILL)

    def test_the_retired_folder_name_pin_is_gone(self):
        assert "legacy folder name are both accepted" not in _body(SKILL).lower()

    def test_the_documented_reason_is_one_the_reader_emits(self):
        source = (SCRIPTS / "project_identity.py").read_text(encoding="utf-8")
        assert '"not_canonical"' in source


class TestPercentEncodedIdsAreDocumented:
    """The percent-encoded and Azure DevOps forms the canonicaliser produces."""

    EXAMPLE = "dev.azure.com/fabrikam/My%20Project/_git/repo"

    def test_the_note_names_the_percent_encoded_form(self):
        assert (
            "A project id keeps its remote's percent-encoding, so it can "
            "contain an uppercase %XX, for example " + self.EXAMPLE
        ) in _body(SKILL)

    def test_the_example_is_a_canonical_id(self):
        from scripts.project_identity import is_canonical

        assert is_canonical(self.EXAMPLE)

    def test_the_path_case_split_is_documented_and_measured(self):
        from scripts.project_identity import normalize_remote_url

        assert "On every other host the path keeps its case" in _body(SKILL)
        github = {normalize_remote_url("https://GitHub.com/Owner/Repo"),
                  normalize_remote_url("https://github.com/owner/repo")}
        assert github == {"github.com/owner/repo"}
        other = normalize_remote_url("https://gitlab.com/Owner/Repo")
        assert other == "gitlab.com/Owner/Repo"
        assert other != normalize_remote_url("https://gitlab.com/owner/repo")

    def test_the_scp_relative_absolute_collision_is_documented_and_measured(self):
        from scripts.project_identity import normalize_remote_url

        assert (
            "A relative and an absolute scp path are one id "
            "(git@host:srv/repo and git@host:/srv/repo both give "
            "host/srv/repo), so two different repositories at ~/srv/repo and "
            "/srv/repo on one plain ssh server share a bucket."
        ) in _body(SKILL)
        assert normalize_remote_url("git@host:srv/repo") == "host/srv/repo"
        assert normalize_remote_url("git@host:/srv/repo") == "host/srv/repo"

    def test_the_login_strip_collision_is_documented_and_measured(self):
        from scripts.project_identity import normalize_remote_url

        assert (
            "The login is dropped too, so alice@host:repo and bob@host:repo "
            "are one id even when each names a repository in its own home "
            "directory."
        ) in _body(SKILL)
        assert normalize_remote_url("alice@host:repo") == "host/repo"
        assert normalize_remote_url("bob@host:repo") == "host/repo"

    def test_the_azure_spellings_share_one_id(self):
        assert (
            "all of them resolve to that one "
            "dev.azure.com/<org>/<project>/_git/<repo> id"
        ) in _body(SKILL)

    @pytest.mark.parametrize(
        "url",
        (
            "https://dev.azure.com/fabrikam/My%20Project/_git/repo",
            "git@ssh.dev.azure.com:v3/fabrikam/My%20Project/repo",
            "https://fabrikam.visualstudio.com/My%20Project/_git/repo",
        ),
    )
    def test_each_azure_spelling_canonicalises_to_the_documented_id(self, url):
        from scripts.project_identity import normalize_remote_url

        assert normalize_remote_url(url) == self.EXAMPLE


class TestOverridePathIsNullWhenNoPinCanRemedy:
    """The `override_path` clause and the redirect remedy beside it."""

    def _clause(self) -> str:
        row = _table_row(SKILL, "project-id show")
        start = row.index("override_path is the")
        return row[start:start + 1400]

    def test_null_means_no_override_file_can_remedy(self):
        assert (
            "or null in two cases. First, when no override file can remedy the "
            "decline"
        ) in self._clause()

    def test_the_retired_condition_is_gone(self):
        assert "null when no repository was found" not in _body(SKILL).lower()

    def test_the_redirect_remedy_is_the_code_remedy(self):
        from scripts.memory_api import _REDIRECT_REMEDY

        clause = self._clause()
        assert "For gitfile_redirect: " + _flatten(_REDIRECT_REMEDY) in clause

    def test_only_the_user_writes_the_separate_git_dir_pin(self):
        clause = self._clause()
        assert (
            "Such a layout is recovered by a pin at <dir>/.git/pact-project-id, "
            "in the git directory its .git file names, but only the user writes "
            "it, after confirming that directory is theirs."
        ) in clause

    def test_an_agent_asks_the_user_instead_of_writing_the_pin(self):
        clause = self._clause()
        assert (
            "An agent never writes this pin and asks the user instead: a forged "
            ".git file naming another repository's git directory has the same "
            "shape, and the pin would re-pin that repository."
        ) in clause
        assert "write its canonical id to <dir>/.git/pact-project-id instead" not in clause


class TestLegacyScopeStatesAreDocumented:
    """The `legacy_scope` key set and its three exclusive states."""

    KEY_LIST_START = "legacy_scope carries the same keys in every state:"
    KEY_LIST_END = ". It is null only when no identity resolves"

    def _documented_keys(self) -> set:
        body = _body(SKILL)
        start = body.index(self.KEY_LIST_START) + len(self.KEY_LIST_START)
        end = body.index(self.KEY_LIST_END, start)
        names = body[start:end].replace(" and ", ", ").split(",")
        return {name.strip() for name in names if name.strip()}

    def test_the_documented_keys_are_the_code_key_set(self):
        from scripts.memory_api import LEGACY_SCOPE_KEYS

        assert self._documented_keys() == set(LEGACY_SCOPE_KEYS)

    def test_exactly_one_state_is_true_when_a_candidate_is_set(self):
        assert (
            "When candidate is set, exactly one of included, excluded and "
            "unproven is true"
        ) in _body(SKILL)

    def test_included_unverified_rests_on_session_evidence(self):
        assert (
            "included_unverified means no file row places it but a recorded "
            "session from this repository wrote into it (session_evidence is true)"
        ) in _body(SKILL)

    def test_a_recorded_session_counts_only_before_the_description_ctime(self):
        body = _body(SKILL)
        assert (
            "A recorded session counts only when it was launched at the root of "
            "this repository or of one of its linked worktrees (a session "
            "launched in a subfolder is not read), and <git-common-dir>/description is a "
            "regular file whose ctime is earlier than the session's start, or the "
            "repository has no description file."
        ) in body
        assert (
            "A session folder that was cleaned up counts the same way through "
            "the line recorded for it when it was removed."
        ) in body
        assert "inode" not in body.lower()

    def test_sessions_removed_before_the_ledger_read_as_unproven(self):
        assert (
            "A session folder that an earlier version of the plugin removed "
            "left no line, so a project whose writing sessions were all "
            "removed that way reads as unproven until its rows are moved with "
            "that rekey command and --apply, which needs the user's explicit "
            "approval."
        ) in _body(SKILL)

    def test_a_copied_or_recloned_repository_reads_as_unproven(self):
        assert (
            "A repository that was moved, copied, restored or cloned again, and "
            "a folder that is not a repository, read as unproven too."
        ) in _body(SKILL)

    def test_the_moved_repository_residual_is_stated(self):
        assert (
            "a result can contain rows from another checkout that shares the "
            "folder name, including one that used this exact folder before "
            "this repository was moved into it."
        ) in _body(SKILL)

    def test_excluded_is_not_read(self):
        assert (
            "excluded: its absolute file rows place it in another repository, "
            "and it is not read"
        ) in _body(SKILL)

    def test_unproven_is_not_read_and_names_its_command(self):
        assert (
            "unproven: it is not read, and its rekey command is printed once "
            "per process, with the cause."
        ) in _body(SKILL)

    def test_an_unattributed_row_is_read_with_a_warning(self):
        assert (
            "an included bucket that holds such rows is read with a warning, "
            "once per process, that gives their count (sessions.unattributed) "
            "and the rekey command."
        ) in _body(SKILL)

    def test_a_foreign_row_keeps_either_kind_out(self):
        assert (
            "Neither kind is read when any of its rows was written by a session "
            "recorded outside this repository and its linked worktrees: it is "
            "then unproven."
        ) in _body(SKILL)

    def test_the_suppression_reason_is_documented_only_there(self):
        from scripts.project_identity import DECLINE_REASONS

        body = _body(SKILL)
        assert (
            "suppressed is set for a submodule checkout, as {source: "
            "submodule_admin_dir, reason: repository_chosen_name, "
            "override_path: null, memory_count: N}"
        ) in body
        assert (
            "The reason repository_chosen_name appears only here, never in "
            "decline_reason"
        ) in body
        assert "repository_chosen_name" not in DECLINE_REASONS

    def test_the_retired_unverified_admission_is_gone(self):
        assert "the gate includes the bucket as unverified" not in _body(SKILL).lower()


class TestRekeyRemediesNameTheirTarget:
    """The rekey rules an agent must follow before an irreversible move."""

    def test_apply_needs_explicit_approval_and_never_crosses_projects(self):
        assert (
            "Run that command with --apply ONLY with the user's explicit "
            "approval, and NEVER for an excluded bucket or for another "
            "repository's canonical id"
        ) in _body(SKILL)

    def test_the_documented_command_names_its_target(self):
        assert (
            "project-id rekey --from=<id> --to=<canonical> --db-path=<store>"
        ) in _body(SKILL)

    def test_the_documented_command_form_is_the_rendered_form(self):
        from scripts.memory_api import render_rekey_command

        rendered = render_rekey_command("OLD", "host/o/r", db_path="/s.db")
        assert "--from=OLD" in rendered and "--to=host/o/r" in rendered
        assert "--db-path=/s.db" in rendered

    def test_no_targetless_command_form_remains(self):
        body = _body(SKILL)
        assert "rekey --from <id>" not in body
        assert "follow the candidate it names" not in body.lower()

    def test_the_census_row_shape_and_which_rows_carry_a_command(self):
        row = _table_row(SKILL, "project-id show")
        assert (
            "Each candidate row is {id, memory_count, relation, rekey}, with "
            "relation one of current, legacy_candidate, case_variant or other; "
            "only a legacy_candidate row that is not excluded and a "
            "case_variant row carry a rekey command"
        ) in row

    def test_a_defaulted_target_is_only_for_the_own_candidate(self):
        assert (
            "An omitted --to is accepted only when --from is this checkout's "
            "own legacy candidate and it is neither excluded nor unproven; "
            "otherwise the move is refused with REKEY_REFUSED"
        ) in _body(SKILL)

    def test_a_named_absent_store_is_refused_not_reported_absent(self):
        row = _table_row(SKILL, "project-id rekey --from=<id> [--to=<canonical>] [--dry-run] [--apply], or project-id rekey --candidates alone")
        assert (
            "DB_PATH_NOT_FOUND when --db-path names a store that is not "
            "present, and nothing is created"
        ) in row
        assert "A store path that does not exist answers store_absent" not in row

    def test_the_envelope_field_sentences_are_present(self):
        row = _table_row(SKILL, "project-id rekey --from=<id> [--to=<canonical>] [--dry-run] [--apply], or project-id rekey --candidates alone")
        assert (
            "When the default store does not exist yet, the command answers "
            "store_absent: true and creates nothing; a --from bucket with no rows answers "
            "from_bucket_empty: true. The envelope also carries "
            "target_defaulted and legacy_verdict."
        ) in row

    @pytest.mark.parametrize(
        "field",
        ("store_absent", "from_bucket_empty", "target_defaulted", "legacy_verdict"),
    )
    def test_each_documented_envelope_field_is_one_the_cli_emits(self, field):
        cli = (SCRIPTS / "cli.py").read_text(encoding="utf-8")
        assert f'"{field}"' in cli

    def test_links_repointed_is_described_as_merged_rows_only(self):
        body = _body(SKILL)
        assert (
            "links_repointed counts the links on file rows merged into an "
            "existing target row"
        ) in body
        assert "links_repointed counts old links examined" not in body


class TestRepositorySessionsNeverWriteAFolderNameBucket:
    """The write rule that keeps a legacy bucket's evidence from being made."""

    def test_the_rule_names_both_refused_writes(self):
        assert (
            "A session whose detected project id is a repository identity "
            "never writes into a folder-name bucket: save and update refuse a "
            "payload project_id that is not a canonical host/owner/repo id, "
            "and update refuses a session_id change on a row stored under a "
            "folder-name bucket. Both exit 2 and write nothing."
        ) in _body(SKILL)

    def test_the_remedy_is_the_one_the_refusal_prints(self):
        source = (SCRIPTS / "memory_api.py").read_text(encoding="utf-8")
        remedy = "project-id rekey --from=FOLDER_NAME --to=host/owner/repo"
        assert (
            "To move rows, run " + remedy + ", with the folder-name bucket and "
            "the canonical id filled in; to write a folder-name bucket, save "
            "from a folder whose project id is that name (project-id show "
            "reports it)."
        ) in _body(SKILL)
        assert "save from that non-repository folder" not in _body(SKILL)
        assert remedy in source.replace("`", "")
        assert "--to=<canonical id>" not in source


class TestCalibrationCountReadsTheWholeScope:
    """The secretary's gate count binds every id `sync` read under."""

    def _sql_block(self) -> str:
        text = SECRETARY.read_text(encoding="utf-8")
        blocks = re.findall(r"```sql\n(.*?)```", text, flags=re.S)
        matches = [b for b in blocks if "json_each(COALESCE" in b]
        assert len(matches) == 1, "expected exactly one calibration SQL block"
        return _flatten(matches[0])

    def test_the_count_binds_the_scope_array(self):
        assert (
            "WHERE project_id IN (SELECT value FROM json_each(:scope))"
        ) in self._sql_block()

    def test_the_single_id_binding_is_gone(self):
        block = self._sql_block()
        assert "project_id = ?2" not in block
        assert "project_id = :scope" not in block
        assert "?1" not in block and "?2" not in block

    BINDING_LEAD = "bind a mapping, not a tuple:"

    def _binding_literal(self) -> str:
        text = SECRETARY.read_text(encoding="utf-8")
        start = text.index(self.BINDING_LEAD) + len(self.BINDING_LEAD)
        literal = re.search(r"`(\{[^`]*\})`", text[start:start + 200])
        assert literal, "no backticked binding mapping after the binding lead"
        return literal.group(1)

    @staticmethod
    def _build_binding(literal: str, read_scope: list) -> dict:
        """The prose's mapping, built from its AST without evaluating code.

        Each value must be a string constant or `json.dumps(read_scope)`;
        any other shape fails the arm, so the binding the prose shows is the
        binding executed.
        """
        import ast
        import json

        node = ast.parse(literal, mode="eval").body
        assert isinstance(node, ast.Dict), "the binding is not a mapping"
        binding = {}
        for key, value in zip(node.keys, node.values):
            assert isinstance(key, ast.Constant) and isinstance(key.value, str)
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                binding[key.value] = value.value
            elif (
                isinstance(value, ast.Call)
                and ast.unparse(value.func) == "json.dumps"
                and [ast.unparse(arg) for arg in value.args] == ["read_scope"]
                and not value.keywords
            ):
                binding[key.value] = json.dumps(read_scope)
            else:
                raise AssertionError(
                    f"unsupported binding value {ast.unparse(value)!r}"
                )
        return binding

    def test_the_binding_is_a_mapping_over_the_sync_read_scope(self):
        assert (
            "Run it with Python's sqlite3 module and bind a mapping, not a "
            "tuple: {\"name\": \"orchestration_calibration\", \"scope\": "
            "json.dumps(read_scope)}, where read_scope is the array from this "
            "briefing's sync envelope."
        ) in _body(SECRETARY)

    def test_the_documented_sql_and_binding_count_both_buckets(self, tmp_path):
        """Executes the prose's SQL with the prose's own binding, warnings fatal."""
        import json
        import sqlite3
        import warnings

        store = tmp_path / "memory.db"
        conn = sqlite3.connect(store)
        conn.execute(
            "CREATE TABLE memories (id TEXT, project_id TEXT, entities TEXT)"
        )
        conn.executemany(
            "INSERT INTO memories VALUES (?, ?, ?)",
            [
                ("a", "github.com/o/r",
                 json.dumps([{"name": "orchestration_calibration"}])),
                ("b", "legacy", json.dumps(["orchestration_calibration"])),
                ("c", "legacy", json.dumps([{"name": "other"}])),
                ("d", "unrelated",
                 json.dumps([{"name": "orchestration_calibration"}])),
            ],
        )
        conn.commit()
        conn.close()

        read_scope = ["github.com/o/r", "legacy"]
        binding = self._build_binding(self._binding_literal(), read_scope)
        sql = [
            block
            for block in re.findall(
                r"```sql\n(.*?)```",
                SECRETARY.read_text(encoding="utf-8"),
                flags=re.S,
            )
            if "json_each(COALESCE" in block
        ][0]
        ro = sqlite3.connect(f"file:{store}?mode=ro&immutable=1", uri=True)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                counted = ro.execute(sql, binding).fetchone()[0]
        finally:
            ro.close()
        assert counted == 2

    def test_no_scope_means_not_determined(self):
        assert (
            "If sync did not run in this briefing, or its read_scope is empty, "
            "do not run the count and report the gate state not determined."
        ) in _body(SECRETARY)

    def test_the_sync_row_documents_read_scope(self):
        row = _table_row(SKILL, "sync")
        assert '"read_scope": [...]' in row
        assert (
            "read_scope lists every project id the records were read under, "
            "and is [] when no identity resolves"
        ) in row


class TestUserSettingIsReadFromTheUserScope:
    """The user `project_id` setting lives in the user memory directory."""

    def test_the_bullet_names_the_user_directory_and_not_the_store(self):
        assert (
            "config.json \u2192 \"project_id\" in the user memory directory, the "
            "directory that holds the default memory.db named under Storage \u2014 "
            "a USER-scoped setting, read there whatever store a command names: "
            "--db-path does not move it."
        ) in _body(SKILL)

    def test_the_store_relative_location_is_gone(self):
        assert "<memory_dir>/config.json" not in _body(SKILL)

    def test_only_the_user_sets_the_user_setting(self):
        assert (
            "Only the user sets it: it changes the identity of every session "
            "it answers for, so an agent asks the user instead of writing it."
        ) in _body(SKILL)

    def test_only_the_user_writes_the_repository_pin(self):
        assert (
            "a REPOSITORY-scoped pin. Only the user writes it: it decides which "
            "bucket every checkout of the repository reads and writes, so an "
            "agent that meets a remedy naming this file asks the user for the "
            "id instead of writing it."
        ) in _body(SKILL)

    def test_the_code_reads_the_user_directory_under_a_store_scope(
        self, monkeypatch, tmp_path
    ):
        from pathlib import Path as _Path

        from scripts import config

        monkeypatch.delenv("PACT_TEST_MEMORY_DIR", raising=False)
        with config.store_scope(tmp_path / "elsewhere" / "memory.db"):
            assert config.get_user_memory_dir() == (
                _Path.home() / ".claude" / "pact-memory"
            )


class TestUpdateCascadeQuarantineIsDocumented:
    """A re-key that cannot move a link reports it instead of aborting."""

    def test_the_quarantine_sentence_is_present(self):
        assert (
            "A file link whose recorded path cannot be re-tracked stays on the "
            "old project, is counted in the update envelope's "
            "last_cascade_skipped, and does not stop the rest of the re-key."
        ) in _body(SKILL)

    def test_the_field_is_on_the_update_envelope(self):
        cli = (SCRIPTS / "cli.py").read_text(encoding="utf-8")
        assert '"last_cascade_skipped": memory.last_cascade_skipped' in cli


@pytest.fixture(scope="module")
def null_rig(tmp_path_factory):
    """The layout cells of `TestNullOverridePathIsMeasured`, built once, with
    the CLI runner, the origin-read shim and git's outside-store measurement.
    """
    import os
    import types
    import shutil
    import subprocess
    import sys

    base = tmp_path_factory.mktemp("null_override_matrix")
    home, mem = base / "home", base / "mem"
    home.mkdir()
    mem.mkdir()
    store = mem / "memory.db"
    real_git = shutil.which("git")
    shim = base / "shim"
    shim.mkdir()
    # Only the origin read fails; every other git call is the real one.
    # No layout makes `git config --get-all remote.origin.url` fail while
    # `rev-parse` succeeds, so this is the one way to reach that cause.
    (shim / "git").write_text(
        "#!/bin/sh\n"
        'case "$*" in *"--get-all remote.origin.url"*) exit 2;; esac\n'
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    os.chmod(shim / "git", 0o755)
    cli_path = SCRIPTS / "cli.py"
    plain_path = os.pathsep.join((os.path.dirname(real_git), "/usr/bin", "/bin"))

    def run(argv, cwd, declared, shimmed):
        env = {
            "PATH": (str(shim) + os.pathsep + plain_path) if shimmed else plain_path,
            "HOME": str(home),
            "PACT_TEST_MEMORY_DIR": str(mem),
        }
        if declared is not None:
            env["CLAUDE_PROJECT_DIR"] = str(declared)
        return subprocess.run(
            [sys.executable, str(cli_path), *argv, "--db-path", str(store)],
            cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
        )

    def points_at_outside_store(cwd):
        """Whether the checkout's `.git` names a git dir outside the checkout
        that is neither a linked worktree's nor a submodule's admin dir.

        Measured by git itself: the git dir lies outside the checkout, it is
        its own common dir (a linked worktree's admin dir is not), and the
        checkout has no superproject (a submodule's admin dir has one).
        """
        out = subprocess.run(
            [
                real_git, "-C", str(cwd), "rev-parse", "--absolute-git-dir",
                "--path-format=absolute", "--git-common-dir",
                "--show-superproject-working-tree",
            ],
            capture_output=True, text=True, timeout=30,
            env={"PATH": plain_path, "HOME": str(home)},
        )
        if out.returncode != 0:
            return False
        lines = out.stdout.splitlines()
        git_dir = os.path.realpath(lines[0])
        common = os.path.realpath(lines[1])
        superproject = lines[2] if len(lines) > 2 else ""
        root = os.path.realpath(cwd)
        outside = os.path.commonpath([git_dir, root]) != root
        return outside and git_dir == common and not superproject

    run(["setup"], base, None, False)
    layouts = TestNullOverridePathIsMeasured._layouts(base)
    return types.SimpleNamespace(
        base=base, home=home, run=run, layouts=layouts,
        shim_path=str(shim) + os.pathsep + plain_path,
        outside={cell: points_at_outside_store(cwd) for cell, (cwd, _, _) in layouts.items()},
    )


@pytest.fixture(scope="module")
def matrix(null_rig):
    """{(cause, layout): (emitted reason, emitted override_path, outside)},
    from the `project-id show` envelope with CLAUDE_PROJECT_DIR set."""
    import json

    result = {}
    for (cause, layout), (cwd, declared, shimmed) in null_rig.layouts.items():
        out = null_rig.run(["project-id", "show"], cwd, cwd if declared is None else declared, shimmed)
        record = json.loads(out.stdout)["result"]["decline_reason"] or {}
        result[(cause, layout)] = (
            record.get("reason"), record.get("override_path"), null_rig.outside[(cause, layout)],
        )
    return result


@pytest.fixture(scope="module")
def routed_matrix(null_rig):
    """{(cause, layout, route): (reason, override_path, outside)} for every
    resolver route that reaches the cell, measured in-process.

    Every route in `identity_routes.ROUTES` is pointed at every cell. A route
    reaches a cell when its own rung answered. A declaration cell names a
    missing directory, so a route that must enter it cannot address it and is
    skipped. Any other raise is recorded in `errors`, a route whose rung did
    not answer in `unreached`, and a reached route that bound instead of
    declining in `bound`, with whether git, run as the route ran it, finds no
    repository (the route then cannot see the cause it was pointed at).
    """
    import os
    import subprocess

    from fixtures import identity_routes as routes

    cells, errors, unreached, bound = {}, {}, {}, {}
    runs = 0
    for (cause, layout), (cwd, declared, shimmed) in null_rig.layouts.items():
        target = cwd if declared is None else declared
        if declared is None:
            (target / "sub").mkdir(exist_ok=True)
        for route, (fn, sources) in routes.ROUTES.items():
            runs += 1
            elsewhere = null_rig.base / "elsewhere" / f"r{runs}"
            elsewhere.mkdir(parents=True)
            with pytest.MonkeyPatch.context() as mp:
                routes._clear(mp)
                mp.setenv("HOME", str(null_rig.home))
                if shimmed:
                    mp.setenv("PATH", null_rig.shim_path)
                try:
                    detected = fn(mp, target, elsewhere)
                except Exception as exc:  # recorded and asserted on, not hidden
                    unaddressable = isinstance(exc, FileNotFoundError) and not os.path.exists(target)
                    if not (declared is not None and unaddressable):
                        errors[(cause, layout, route)] = repr(exc)
                    continue
                looked = subprocess.run(
                    ["git", "rev-parse", "--git-dir"], cwd=os.getcwd(), env=dict(os.environ),
                    capture_output=True, text=True, timeout=30,
                )
            _, rung = routes._answer(detected)
            if rung not in sources:
                unreached[(cause, layout, route)] = rung
                continue
            record = detected.decline_reason or {}
            if not record:
                bound[(cause, layout, route)] = (
                    not os.path.exists(target) or looked.returncode != 0
                )
                continue
            cells[(cause, layout, route)] = (
                record.get("reason"), record.get("override_path"),
                null_rig.outside[(cause, layout)],
            )
    return {"cells": cells, "errors": errors, "unreached": unreached, "bound": bound}


@pytest.fixture(scope="module")
def null_cells(routed_matrix):
    return routed_matrix["cells"]



class TestNullOverridePathIsMeasured:
    """When `override_path` is null is MEASURED, never restated.

    Every cause the code defines (`DECLINE_REASONS`) is produced in every
    layout that can produce it, and every resolver route in
    `identity_routes.ROUTES` is pointed at each cell. The detected decline
    decides: a cause that is null in every cell belongs in the SKILL's cause
    list, and a cell that nulls a cause which elsewhere carries a path is a
    location route the SKILL clause must name. A new null route, a new cause,
    or a dropped route therefore reds an arm here without anyone editing a
    list, and a route added to `ROUTES` is measured here without an edit. A
    resolver rung added without a route reds the rung-parity test's call-site
    census; a route input added without a route (an environment variable, a
    record reader) reds TestEveryRouteEntryConditionIsARoute. The `project-id
    show` envelope anchors the in-process measurement on the
    CLAUDE_PROJECT_DIR route.
    """

    BAD_ORIGIN = "https://host/own%ZZr/repo.git"

    # The SKILL clause states the location route as a PROPERTY of the cell,
    # measured per cell by git (see `points_at_outside_store`).
    OUTSIDE_PHRASE = (
        "when the checkout's .git points at a git directory outside it that is "
        "neither a linked worktree's nor a submodule's admin directory"
    )

    CLAUSE_START = (
        "First, when no override file can remedy the decline: that is the case for"
    )

    @staticmethod
    def _git(*args, cwd=None):
        import subprocess

        subprocess.run(
            [
                "git", "-c", "protocol.file.allow=always", "-c", "user.name=x",
                "-c", "user.email=x@x", *args,
            ],
            cwd=cwd, check=True, capture_output=True, timeout=30,
        )

    @classmethod
    def _repo(cls, path, origin=None, commit=False, bare=False):
        cls._git("init", "-q", *(["--bare"] if bare else []), str(path))
        if origin:
            cls._git("-C", str(path), "remote", "add", "origin", origin)
        if commit:
            cls._git("-C", str(path), "commit", "-q", "--allow-empty", "-m", "i")
        return path

    @classmethod
    def _layouts(cls, base):
        """{(cause, layout): (cwd, declared_dir, shim_origin_read_failure)}."""
        cells = {}
        bad = cls.BAD_ORIGIN
        good = "https://github.com/acme/{}.git"
        own = base / "own"
        own.mkdir()
        cells[("origin_unrepresentable", "own")] = (cls._repo(own / "o1", bad), None, False)
        cells[("origin_unreadable", "own")] = (
            cls._repo(own / "o2", good.format("o2")), None, True,
        )
        (own / "o3").mkdir()
        (own / "o3" / ".git").write_text("garbage\n", encoding="utf-8")
        cells[("broken_git_marker", "own")] = (own / "o3", None, False)
        cells[("no_working_tree", "own")] = (
            cls._repo(own / "o4.git", good.format("o4"), bare=True), None, False,
        )
        (own / "cwd").mkdir()
        cells[("declaration_unresolved", "own")] = (own / "cwd", own / "missing", False)

        wt = base / "wt"
        wt.mkdir()
        main = cls._repo(wt / "main", bad, commit=True)
        cls._git("-C", str(main), "worktree", "add", "-q", str(wt / "linked"))
        cells[("origin_unrepresentable", "worktree")] = (wt / "linked", None, False)
        main2 = cls._repo(wt / "main2", good.format("w2"), commit=True)
        cls._git("-C", str(main2), "worktree", "add", "-q", str(wt / "linked2"))
        cells[("origin_unreadable", "worktree")] = (wt / "linked2", None, True)

        sub = base / "sub"
        sub.mkdir()
        for name, origin, shim, cause in (
            ("s1", None, False, "submodule_checkout"),
            ("s2", bad, False, "origin_unrepresentable"),
            ("s3", good.format("s3"), True, "origin_unreadable"),
        ):
            source = cls._repo(sub / f"{name}src", None, commit=True)
            superproject = cls._repo(sub / name, good.format(name), commit=True)
            cls._git("-C", str(superproject), "submodule", "add", "-q", str(source), "mod")
            if origin:
                cls._git("-C", str(superproject / "mod"), "remote", "set-url", "origin", origin)
            cells[(cause, "submodule")] = (superproject / "mod", None, shim)

        red = base / "redirect"
        red.mkdir()
        victim = cls._repo(red / "victim", good.format("victim"))
        (red / "forged").mkdir()
        (red / "forged" / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")
        cells[("gitfile_redirect", "redirect")] = (red / "forged", None, False)

        ext = base / "external"
        (ext / "stores").mkdir(parents=True)
        for name, origin, shim, cause in (
            ("e1", bad, False, "origin_unrepresentable"),
            ("e2", good.format("e2"), True, "origin_unreadable"),
        ):
            cls._git("init", "-q", f"--separate-git-dir={ext / 'stores' / (name + '.git')}", str(ext / name))
            cls._git("-C", str(ext / name), "remote", "add", "origin", origin)
            cells[(cause, "external")] = (ext / name, None, shim)
        cls._repo(ext / "stores" / "e3.git", good.format("e3"), bare=True)
        (ext / "e3").mkdir()
        (ext / "e3" / ".git").write_text(
            f"gitdir: {ext / 'stores' / 'e3.git'}\n", encoding="utf-8"
        )
        cells[("no_working_tree", "external")] = (ext / "e3", None, False)

        sym = base / "symlink"
        (sym / "stores").mkdir(parents=True)
        for name, origin, shim, cause in (
            ("y1", bad, False, "origin_unrepresentable"),
            ("y2", good.format("y2"), True, "origin_unreadable"),
        ):
            cls._repo(sym / name, origin)
            (sym / name / ".git").rename(sym / "stores" / f"{name}.git")
            (sym / name / ".git").symlink_to(sym / "stores" / f"{name}.git")
            cells[(cause, "symlink")] = (sym / name, None, shim)
        victim2 = cls._repo(sym / "victim", good.format("victim2"))
        (sym / "linked").mkdir()
        (sym / "linked" / ".git").symlink_to(victim2 / ".git")
        cells[("gitfile_redirect", "symlink")] = (sym / "linked", None, False)

        swt = base / "sepworktree"
        (swt / "stores").mkdir(parents=True)
        for name, origin, shim, cause in (
            ("w1", bad, False, "origin_unrepresentable"),
            ("w2", good.format("sw2"), True, "origin_unreadable"),
        ):
            cls._git("init", "-q", f"--separate-git-dir={swt / 'stores' / (name + '.git')}", str(swt / name))
            cls._git("-C", str(swt / name), "remote", "add", "origin", origin)
            cls._git("-C", str(swt / name), "commit", "-q", "--allow-empty", "-m", "i")
            cls._git("-C", str(swt / name), "worktree", "add", "-q", str(swt / f"{name}-linked"))
            cells[(cause, "sepworktree")] = (swt / f"{name}-linked", None, shim)
        return cells

    def _clause(self) -> str:
        row = _table_row(SKILL, "project-id show")
        start = row.index("override_path is the")
        return row[start:row.index("For gitfile_redirect:", start)]

    def _documented_causes(self) -> set:
        clause = self._clause()
        start = clause.index(self.CLAUSE_START) + len(self.CLAUSE_START)
        listed = clause[start:clause.index(".", start)]
        return {n.strip() for n in listed.replace(" and ", ", ").split(",") if n.strip()}

    @staticmethod
    def _always_null(matrix) -> set:
        causes = {cell[0] for cell in matrix}
        return {
            cause for cause in causes
            if all(path is None for cell, (_, path, _) in matrix.items() if cell[0] == cause)
        }

    def _location_nulls(self, matrix) -> dict:
        """Cells nulled by location: a cause that is pathed elsewhere."""
        always = self._always_null(matrix)
        return {
            cell: outside for cell, (_, path, outside) in matrix.items()
            if path is None and cell[0] not in always
        }

    def _location_routes(self, matrix) -> set:
        return {cell[1] for cell in self._location_nulls(matrix)}

    def test_no_route_raises_on_any_cell(self, routed_matrix):
        assert routed_matrix["errors"] == {}

    def test_every_route_reaches_every_cell(self, routed_matrix):
        """A route whose rung does not answer measures nothing here."""
        assert routed_matrix["unreached"] == {}

    def test_a_route_binds_only_where_git_finds_no_repository(self, routed_matrix):
        """A reached route that binds instead of declining is judged only
        where the route cannot see the repository it was pointed at."""
        seen = {cell for cell, hidden in routed_matrix["bound"].items() if not hidden}
        assert seen == set(), f"bound although git finds the repository: {seen}"

    def test_every_route_and_every_cell_is_measured(self, null_rig, null_cells):
        from fixtures import identity_routes as routes

        assert {cell[2] for cell in null_cells} == set(routes.ROUTES)
        assert {cell[:2] for cell in null_cells} == set(null_rig.layouts)

    def test_the_envelope_reports_what_the_detection_set(self, matrix, null_cells):
        detected = {cell[:2]: value for cell, value in null_cells.items() if cell[2] == "CLAUDE_PROJECT_DIR"}
        assert matrix == detected

    def test_each_cell_declines_for_the_cause_it_was_built_for(self, null_cells):
        wrong = {cell: got for cell, (got, _, _) in null_cells.items() if got != cell[0]}
        assert wrong == {}, f"a layout builder produced another cause: {wrong}"

    def test_every_cause_the_code_defines_is_measured(self, null_cells):
        from scripts.project_identity import DECLINE_REASONS

        assert {cell[0] for cell in null_cells} == set(DECLINE_REASONS)

    def test_the_documented_causes_are_the_always_null_causes(self, null_cells):
        assert self._documented_causes() == self._always_null(null_cells)

    def test_every_location_route_is_named(self, null_cells):
        """Every location null is a cell that points at an outside store, and
        the clause names that route."""
        nulls = self._location_nulls(null_cells)
        unnamed = {cell for cell, outside in nulls.items() if not outside}
        assert unnamed == set(), f"override_path is null by a route the SKILL does not name: {unnamed}"
        if nulls:
            assert self.OUTSIDE_PHRASE in self._clause()

    def test_the_outside_clause_holds_in_every_outside_cell(self, null_cells):
        """The clause claims EVERY outside-store cell is null for a pathed
        cause; such a cell that prints a path falsifies it."""
        if self.OUTSIDE_PHRASE not in self._clause():
            return
        always = self._always_null(null_cells)
        printed = {
            cell: path for cell, (_, path, outside) in null_cells.items()
            if outside and cell[0] not in always and path is not None
        }
        assert printed == {}, f"outside store, yet a path is printed: {printed}"

    def test_no_unmeasured_location_route_is_claimed(self, null_cells):
        if not self._location_nulls(null_cells):
            assert self.OUTSIDE_PHRASE not in self._clause(), "documented but measures no null"

    def test_the_withheld_path_is_the_users_decision(self):
        assert (
            "a pin there may remedy the decline, but the path is withheld "
            "because a forged pointer looks the same, so pinning that git "
            "directory is the user's decision"
        ) in self._clause()

    DOC_LEAD = "It is None for "

    def test_the_decline_record_docstring_lists_the_always_null_causes(self, null_cells):
        from scripts.project_identity import decline_record

        doc = _flatten(decline_record.__doc__ or "")
        assert doc.count(self.DOC_LEAD) == 1, "the null-cause sentence must appear once"
        start = doc.index(self.DOC_LEAD) + len(self.DOC_LEAD)
        listed = doc[start:doc.index(".", start)]
        names = {n.strip() for n in listed.replace(" and ", ", ").split(",") if n.strip()}
        assert names == self._always_null(null_cells)
        assert "when no repository was found" not in doc

    DOC_LOCATION = (
        "It is also None for every other cause when the checkout's .git "
        "points at a git directory outside the checkout"
    )

    def test_the_decline_record_docstring_names_the_location_route(self, null_cells):
        from scripts.project_identity import decline_record

        doc = _flatten(decline_record.__doc__ or "")
        if "external" in self._location_routes(null_cells):
            assert self.DOC_LOCATION in doc
        else:
            assert self.DOC_LOCATION not in doc

    def test_a_redirect_decline_can_never_carry_a_path(self):
        from scripts.project_identity import decline_record

        with pytest.raises(ValueError):
            decline_record("gitfile_redirect", "/r/.git/pact-project-id")


@pytest.fixture(scope="module")
def override_read_matrix(tmp_path_factory):
    """{(shape, route): cell} for every shape x every resolver route.

    The routes and the hostile shapes come from `fixtures.identity_routes`,
    which the rung-parity test checks against the code's rung call sites and
    TestEveryRouteEntryConditionIsARoute checks against the resolver's route
    inputs, so a route or shape added there is measured here without an edit. The shapes
    below add the outcomes the skill sentence also covers: resolved
    checkouts, decline causes, a home-directory root and a no-working-tree
    repository. Each cell plants an UNUSABLE override at the common dir git
    reports for the target, runs the route in-process, and records:
    `reached` (the route's own rung answered, by the route's source set),
    `read` (an `override_ignored` record was set), and the never-read
    properties measured by git and the environment on the same target. A
    shared shape that documents a HOME (`identity_routes.home_for`) runs with
    HOME set to it, and its cells record the HOME they ran with (`home`), the
    one the shape documents (`home_expected`) and the target.
    """
    import os
    import shutil
    import subprocess

    from fixtures import identity_routes as routes

    T = TestNullOverridePathIsMeasured
    base_root = tmp_path_factory.mktemp("override_read_matrix")
    real_git = shutil.which("git")
    shim = base_root / "shim"
    shim.mkdir()
    (shim / "git").write_text(
        "#!/bin/sh\n"
        'case "$*" in *"--get-all remote.origin.url"*) exit 2;; esac\n'
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    os.chmod(shim / "git", 0o755)
    good = "https://github.com/acme/{}.git"

    def with_sub(target):
        (target / "sub").mkdir(exist_ok=True)
        return target

    def own_unrepresentable(base):
        return with_sub(T._repo(base / "target", T.BAD_ORIGIN))

    def own_origin_unreadable(base):
        return with_sub(T._repo(base / "target", good.format("unreadable")))

    def own_symlinked_store(base):
        T._repo(base / "target", good.format("symstore"))
        (base / "target" / ".git").rename(base / "symstore.git")
        (base / "target" / ".git").symlink_to(base / "symstore.git")
        return with_sub(base / "target")

    def own_external_store(base):
        T._git("init", "-q", f"--separate-git-dir={base / 'store.git'}", str(base / "target"))
        T._git("-C", str(base / "target"), "remote", "add", "origin", good.format("extstore"))
        return with_sub(base / "target")

    def own_external_unrepresentable(base):
        T._git("init", "-q", f"--separate-git-dir={base / 'store.git'}", str(base / "target"))
        T._git("-C", str(base / "target"), "remote", "add", "origin", T.BAD_ORIGIN)
        return with_sub(base / "target")

    def own_store_worktree(base):
        T._git("init", "-q", f"--separate-git-dir={base / 'store.git'}", str(base / "main"))
        T._git("-C", str(base / "main"), "remote", "add", "origin", good.format("storewt"))
        T._git("-C", str(base / "main"), "commit", "-q", "--allow-empty", "-m", "i")
        T._git("-C", str(base / "main"), "worktree", "add", "-q", str(base / "target"))
        return with_sub(base / "target")

    def own_store_named_dotgit(base):
        (base / "store").mkdir()
        T._git("init", "-q", f"--separate-git-dir={base / 'store' / '.git'}", str(base / "target"))
        T._git("-C", str(base / "target"), "remote", "add", "origin", good.format("dotgit"))
        return with_sub(base / "target")

    def own_submodule_local_origin(base):
        src = T._repo(base / "src", None, commit=True)
        sup = T._repo(base / "super", good.format("super"), commit=True)
        T._git("-C", str(sup), "submodule", "add", "-q", str(src), "mod")
        return with_sub(sup / "mod")

    def own_bare_repository(base):
        return with_sub(T._repo(base / "target.git", good.format("bare"), bare=True))

    def home_root(base):
        return with_sub(T._repo(base / "target", good.format("home")))

    # name -> (builder, environment the cell runs under, given the target)
    extra = {
        "own_unrepresentable": (own_unrepresentable, None),
        "own_origin_unreadable": (
            own_origin_unreadable, lambda target: {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"},
        ),
        "own_symlinked_store": (own_symlinked_store, None),
        "own_external_store": (own_external_store, None),
        "own_external_unrepresentable": (own_external_unrepresentable, None),
        "own_store_worktree": (own_store_worktree, None),
        "own_store_named_dotgit": (own_store_named_dotgit, None),
        "own_submodule_local_origin": (own_submodule_local_origin, None),
        "own_bare_repository": (own_bare_repository, None),
        "home_root": (home_root, lambda target: {"HOME": str(target)}),
    }
    def home_env(name):
        """The environment a shared shape runs under: its documented HOME."""
        if getattr(routes.SHAPES[name], "home", None) is None:
            return None
        return lambda target: {"HOME": str(routes.home_for(name, target))}

    shapes = {name: (build, home_env(name)) for name, build in routes.SHAPES.items()}
    shapes.update(extra)

    def git_out(target, *args):
        out = subprocess.run(
            [real_git, "-C", str(target), *args], capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip() if out.returncode == 0 else None

    def properties(target, home):
        common = git_out(target, "rev-parse", "--path-format=absolute", "--git-common-dir")
        git_dir = git_out(target, "rev-parse", "--absolute-git-dir")
        unlinked_admin = False
        if common and git_dir and os.path.realpath(git_dir) != os.path.realpath(common):
            back = os.path.join(git_dir, "gitdir")
            linked = open(back, encoding="utf-8").read().strip() if os.path.isfile(back) else ""
            unlinked_admin = os.path.realpath(linked) != os.path.realpath(os.path.join(target, ".git"))
        toplevel = git_out(target, "rev-parse", "--show-toplevel")
        # The session's checkout is HOME when the target is, or when git's
        # worktree toplevel for it is (a stray `.git` git skips).
        return {
            "bare": git_out(target, "rev-parse", "--is-bare-repository") == "true",
            "unlinked_admin": unlinked_admin,
            "home_root": os.path.realpath(target) == os.path.realpath(home) or (
                toplevel is not None and os.path.realpath(toplevel) == os.path.realpath(home)
            ),
        }

    cells = {}
    for shape, (build, env_for) in shapes.items():
        for route, (run, sources) in routes.ROUTES.items():
            base = base_root / f"c{len(cells)}"
            base.mkdir()
            target = build(base)
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            pin = routes.plant_unusable_pin(target)
            env = env_for(target) if env_for else {}
            with pytest.MonkeyPatch.context() as mp:
                routes._clear(mp)
                for key, value in env.items():
                    mp.setenv(key, value)
                try:
                    detected = run(mp, target, elsewhere)
                except Exception as exc:  # noqa: BLE001 - a raising route is a result
                    cells[(shape, route)] = {"reached": False, "error": repr(exc)}
                    continue
                home = os.environ["HOME"]
            _answer, rung = routes._answer(detected)
            documented = routes.home_for(shape, target) if shape in routes.SHAPES else None
            toplevel = git_out(target, "rev-parse", "--show-toplevel")
            cells[(shape, route)] = {
                "error": None,
                "reached": rung in sources,
                "placeable": pin is not None,
                "home": os.path.realpath(home),
                "home_expected": os.path.realpath(documented) if documented else None,
                "target": os.path.realpath(target),
                "toplevel": os.path.realpath(toplevel) if toplevel else None,
                "read": routes._override(detected) is not None,
                **properties(target, home),
            }
    # The one decline that is a property of the route, not of a shape.
    base = base_root / "declaration_unresolved"
    (base / "elsewhere").mkdir(parents=True)
    with pytest.MonkeyPatch.context() as mp:
        routes._clear(mp)
        mp.chdir(base / "elsewhere")
        mp.setenv("CLAUDE_PROJECT_DIR", str(base / "missing"))
        detected = routes._detect()
    cells[("declaration_unresolved", "CLAUDE_PROJECT_DIR")] = {
        "error": None, "reached": True, "placeable": False,
        "read": routes._override(detected) is not None,
        "bare": False, "unlinked_admin": False, "home_root": False,
    }
    return cells


@pytest.fixture(scope="module")
def override_cli_anchors(tmp_path_factory):
    """{route: (in-process override, CLI-envelope override)} for the routes a
    plain subprocess can take, on one read shape and one never-read shape."""
    import json
    import os
    import subprocess
    import sys

    from fixtures import identity_routes as routes

    base_root = tmp_path_factory.mktemp("override_cli_anchors")
    mem = base_root / "mem"
    mem.mkdir()
    store = mem / "memory.db"
    env0 = {"PATH": os.environ["PATH"], "HOME": str(base_root), "PACT_TEST_MEMORY_DIR": str(mem)}
    subprocess.run([sys.executable, str(SCRIPTS / "cli.py"), "setup", "--db-path", str(store)],
                   cwd=base_root, env=env0, capture_output=True, timeout=60)
    # route -> (cwd, CLAUDE_PROJECT_DIR, GIT_CEILING_DIRECTORIES), given target/elsewhere
    plain = {
        "git root": lambda t, e: (t, None, None),
        "git root (subdirectory)": lambda t, e: (t / "sub", None, None),
        "CLAUDE_PROJECT_DIR": lambda t, e: (e, t, None),
        "CLAUDE_PROJECT_DIR (subdirectory)": lambda t, e: (e, t / "sub", None),
        "cwd marker": lambda t, e: (t / "sub", None, t),
    }
    anchors = {}
    for shape in ("foreign_external_store", "bare_target"):
        for route, place in plain.items():
            run, _sources = routes.ROUTES[route]
            base = base_root / f"{shape}-{len(anchors)}"
            base.mkdir()
            target = routes.SHAPES[shape](base)
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            routes.plant_unusable_pin(target)
            with pytest.MonkeyPatch.context() as mp:
                routes._clear(mp)
                inproc = routes._override(run(mp, target, elsewhere))
            cwd, declared, ceiling = place(target, elsewhere)
            env = dict(env0)
            if declared is not None:
                env["CLAUDE_PROJECT_DIR"] = str(declared)
            if ceiling is not None:
                env["GIT_CEILING_DIRECTORIES"] = str(ceiling)
            out = subprocess.run(
                [sys.executable, str(SCRIPTS / "cli.py"), "project-id", "show", "--db-path", str(store)],
                cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
            )
            ignored = json.loads(out.stdout)["result"].get("override_ignored")
            cli = (ignored.get("reason"), ignored.get("path") is None) if ignored else None
            anchors[(shape, route)] = (inproc, cli)
    return anchors


class TestOverrideIsReadOnEveryRouteItIsSaidToBe:
    """Where an unusable override file is READ (and so reported) is measured,
    over every shape x every resolver route.

    `read` is whether the detection set `override_ignored`; the properties
    that name each never-read route are measured by git and the environment
    on the same target. Only cells whose own rung answered are judged (a route
    that hands a shape to another rung measures that rung, not itself). Every
    reached placeable cell that is not read must carry a property the SKILL
    names, every reached cell carrying one must not be read, the SKILL names
    a property exactly when some reached cell measures it, and every route
    and every shape is reached somewhere. Cross-route AGREEMENT of the
    override disclosure is the rung-parity test's assertion; this class owns
    the skill sentence.
    """

    # Each measured property and the SKILL phrase that names its route.
    NEVER_READ = {
        "bare": "when git gives no working tree for the repository",
        "unlinked_admin": (
            "when the checkout's .git names a linked worktree's admin directory "
            "that does not link back to it"
        ),
        "home_root": (
            "when the checkout the session is in is the user's home directory "
            "(a linked worktree included)"
        ),
    }
    NO_GIT_DIR = (
        "or when no git directory is found (a broken .git marker or a "
        "declaration that cannot be resolved)"
    )

    def _sentence(self) -> str:
        body = _body(SKILL)
        start = body.index("The file is never read, so nothing is reported,")
        return body[start:body.index(".", body.index("resolved)", start)) + 1]

    @staticmethod
    def _named(cell) -> set:
        return {prop for prop in TestOverrideIsReadOnEveryRouteItIsSaidToBe.NEVER_READ if cell[prop]}

    @staticmethod
    def _reached(matrix) -> dict:
        return {key: cell for key, cell in matrix.items() if cell["reached"]}

    def test_no_route_raises_on_any_shape(self, override_read_matrix):
        raised = {key: cell["error"] for key, cell in override_read_matrix.items() if cell["error"]}
        assert raised == {}, f"a resolver route raised instead of answering: {raised}"

    def test_every_route_and_every_shape_is_reached(self, override_read_matrix):
        from fixtures.identity_routes import ROUTES

        reached = self._reached(override_read_matrix)
        routes = {route for _shape, route in reached}
        shapes = {shape for shape, _route in override_read_matrix}
        assert set(ROUTES) <= routes, f"a resolver route no cell reaches: {set(ROUTES) - routes}"
        unreached = shapes - {shape for shape, _route in reached}
        assert unreached == set(), f"a shape no route reaches: {unreached}"

    def test_every_unread_placeable_cell_is_on_a_named_route(self, override_read_matrix):
        unnamed = {
            key for key, cell in self._reached(override_read_matrix).items()
            if cell["placeable"] and not cell["read"] and not self._named(cell)
        }
        assert unnamed == set(), f"an unusable override is silently not read on: {unnamed}"

    def test_no_named_route_reads_the_file(self, override_read_matrix):
        read_anyway = {
            key: sorted(self._named(cell))
            for key, cell in self._reached(override_read_matrix).items()
            if cell["placeable"] and cell["read"] and self._named(cell)
        }
        assert read_anyway == {}, f"a route the SKILL calls never-read reads the file: {read_anyway}"

    def test_each_home_shape_is_measured_at_its_home(self, override_read_matrix):
        """A shared shape that documents a HOME runs every route at it, and
        answers as the skill says: where the target (the checkout the session
        is in) IS that HOME, a linked worktree included, the cell is a
        home-directory root and never reads the file, the user-setting route
        included; where the target is a worktree outside a home main
        checkout, it is not one and reads the file. The expected verdict is
        derived from the shape's own documented HOME, not typed per shape."""
        from fixtures import identity_routes as routes

        home_shapes = {
            name for name, build in routes.SHAPES.items() if getattr(build, "home", None) is not None
        }
        assert home_shapes, "the shared fixture documents no home shape"
        cells = {key: cell for key, cell in override_read_matrix.items() if key[0] in home_shapes}
        assert {shape for shape, _route in cells} == home_shapes
        not_at_home = {
            key: (cell.get("error"), cell.get("home"), cell.get("home_expected"))
            for key, cell in cells.items()
            if cell.get("error") or cell["home"] != cell["home_expected"]
        }
        assert not_at_home == {}, f"a home shape ran away from its documented HOME: {not_at_home}"
        wrong = {}
        for key, cell in cells.items():
            at_home = cell["target"] == cell["home_expected"] or cell["toplevel"] == cell["home_expected"]
            expected = (True, False) if at_home else (False, True)
            if (cell["home_root"], cell["read"]) != expected or not cell["reached"]:
                wrong[key] = {"home_root": cell["home_root"], "read": cell["read"], "reached": cell["reached"]}
        assert wrong == {}, f"a home shape's verdict differs from the skill's: {wrong}"
        assert any(cell["target"] == cell["home_expected"] for cell in cells.values()), (
            "no shared home shape has its target at HOME"
        )

    def test_the_skill_names_exactly_the_measured_routes(self, override_read_matrix):
        sentence = self._sentence()
        reached = self._reached(override_read_matrix).values()
        for prop, phrase in self.NEVER_READ.items():
            measured = any(cell[prop] and cell["placeable"] for cell in reached)
            assert (phrase in sentence) == measured, prop

    def test_the_no_git_dir_route_is_named_when_measured(self, override_read_matrix):
        unplaceable = [
            key for key, cell in self._reached(override_read_matrix).items()
            if not cell["placeable"]
        ]
        assert unplaceable, "no cell measures a decline that finds no git directory"
        assert all(not override_read_matrix[key]["read"] for key in unplaceable)
        assert self.NO_GIT_DIR in self._sentence()

    def test_the_cli_envelope_reports_what_the_detection_set(self, override_cli_anchors):
        """The in-process value is the one `project-id show` emits, on every
        route a plain subprocess can take, for a read and a never-read shape."""
        differ = {key: pair for key, pair in override_cli_anchors.items() if pair[0] != pair[1]}
        assert differ == {}, f"the CLI envelope and the detection disagree: {differ}"
        assert any(pair[0] is not None for pair in override_cli_anchors.values())
        assert any(pair[0] is None for pair in override_cli_anchors.values())

    def test_the_read_rule_is_stated(self):
        assert (
            "an unusable file is reported as override_ignored and resolution "
            "continues without it."
        ) in _body(SKILL)


# --- every route entry condition is a route ---------------------------------
# The resolver chooses its route from a small set of inputs: an environment
# variable it reads, a record or setting a helper returns, the working
# directory. Each one is read here from the code of the detection path,
# helper modules included, and each must be driven by a route in
# `identity_routes.ROUTES` (so the matrices above measure it) or be listed in
# NOT_A_ROUTE with the reason no route needs to set it. A route added through
# an EXISTING rung (a new variable handed to the declared rung) adds no rung
# call site, so only this census sees it.

DETECTION_MODULES = ("memory_api", "project_identity")
DETECTION_ENTRY = ("memory_api", "PACTMemory._detect_project_identity_with_legacy")
# The pass the detection path runs inside: its setup and the raw git-result
# pass it opens, each read from its `def` (the join branch included), and every
# place a memory script opens one (`_pass_openings`).
PASS_SETUPS = (("memory_api", "detection_pass"), ("shared.git_helpers", "git_results_pass"))
# Where a module the detection path imports is looked up, by dotted name.
_PROJECT_ROOTS = (SCRIPTS, PLUGIN_ROOT / "hooks")
# Mapping methods whose first argument names the variable they read.
_ENV_KEY_METHODS = frozenset({"get", "setdefault", "pop", "__getitem__", "__contains__"})


def _env_anchors() -> tuple:
    """(mappings, functions): the qualified names by which code reaches the
    process environment, read from the os module itself. The mappings are
    `os.environ*`; the functions are `os.getenv*`, `os.putenv`, `os.unsetenv`
    (a variable name first) and `os.path.expandvars` (a template)."""
    import os

    mappings = frozenset(f"os.{name}" for name in dir(os) if name.startswith("environ"))
    functions = frozenset(
        f"os.{name}" for name in dir(os)
        if name.startswith(("getenv", "putenv", "unsetenv")) and callable(getattr(os, name))
    ) | {"os.path.expandvars"}
    return mappings, functions


_ENV_MAPPINGS, _ENV_FUNCTIONS = _env_anchors()


def _script_module(name: str) -> str:
    """`name` with the `scripts.` package prefix taken off when it names a
    memory script: the scripts import one another both relatively
    (`from .config import ...`) and through the package
    (`from scripts.memory_api import PACTMemory` in `cli`), and the census
    names each script by its file stem."""
    head, _, rest = name.partition(".")
    if head == "scripts" and rest and (SCRIPTS / (rest.split(".")[0] + ".py")).is_file():
        return rest
    return name


class _DetectionGraph:
    """The project modules the detection path reaches, parsed on demand: each
    module's top-level functions and methods, its import aliases and its
    string constants."""

    def __init__(self):
        self.defs, self.aliases, self.constants, self._files = {}, {}, {}, {}
        # {module: names a relative import binds to a module that cannot be
        # found}; a use of one fails closed in `route_inputs`.
        self.unresolved = {}
        # {module: names a relative import binds}, and {module: names its
        # top-level statements assign}.
        self.relative, self.globals = {}, {}
        # {module: {top-level name: the value it is assigned}}, {module:
        # {class name: its ClassDef}}, and {module: top-level names bound to
        # the process environment} (`_ENV = os.environ`).
        self.values, self.classes, self.env_aliases = {}, {}, {}
        # {module: {simple name: {qualified name: ClassDef}}} for every class
        # the module defines, at any depth (`_class_names`).
        self.class_names = {}
        # {module: {module-level name: every value bound to it, None for a
        # binding that has no plain value}} (`_module_bindings`).
        self.bindings = {}
        # {module: the code the module runs when imported, as a function}
        # (`_import_time_code`).
        self.import_time = {}

    @staticmethod
    def _path(name: str):
        for root in _PROJECT_ROOTS:
            for candidate in (root / (name.replace(".", "/") + ".py"),
                              root / name.replace(".", "/") / "__init__.py"):
                if candidate.is_file():
                    return candidate
        return None

    def load(self, module: str) -> bool:
        import ast

        from fixtures.identity_git import import_aliases

        if module in self._files:
            return self._files[module] is not None
        path = self._path(module)
        self._files[module] = path
        if path is None:
            return False
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = {bound: _script_module(target) for bound, target in import_aliases(tree).items()}
        # A relative import names a module in the importing module's own
        # package (`from .paths import f` in `shared.project_scope` is
        # `shared.paths.f`), which `import_aliases` records without its dots.
        package = module.split(".")[:-1] if path.name != "__init__.py" else module.split(".")
        self.unresolved[module] = set()
        self.relative[module] = set()
        self.values[module] = {
            target.id: node.value for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        self.bindings[module] = _module_bindings(tree)
        self.import_time[module] = _import_time_code(tree)
        self.globals[module] = set(self.values[module]) | {
            name for name, values in self.bindings[module].items() if any(v is not None for v in values)
        }
        self.classes[module] = {
            node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
        }
        self.class_names[module] = _class_names(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.level):
                continue
            base = package[: len(package) - (node.level - 1)] if node.level - 1 <= len(package) else None
            for alias in node.names:
                bound = alias.asname or alias.name
                if base is None:
                    self.unresolved[module].add(bound)
                    continue
                owner = ".".join([*base, *([node.module] if node.module else [])])
                target = f"{owner}.{alias.name}" if owner else alias.name
                aliases[bound] = target
                if self._path(owner or alias.name) is None and self._path(target) is None:
                    self.unresolved[module].add(bound)
                else:
                    self.relative[module].add(bound)
        self.aliases[module] = aliases
        bound = set()
        changed = True
        while changed:
            changed = False
            for name, values in self.bindings[module].items():
                if name not in bound and any(
                    value is not None and _evaluates_to_env(value, aliases, bound) for value in values
                ):
                    bound.add(name)
                    changed = True
        self.env_aliases[module] = bound
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.defs[(module, node.name)] = node
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.defs[(module, f"{node.name}.{item.name}")] = item
        from test_identity_rung_parity import module_string_constants

        # every module-level binding of a name, by any binding form, assigns this string
        self.constants[module] = module_string_constants(tree)
        return True

    def follow(self, qualified: str, depth: int = 0):
        """The (module, def) a dotted name refers to, through re-exports."""
        owner, _, name = _script_module(qualified).rpartition(".")
        if not owner or depth > 8 or not self.load(owner):
            return None
        if (owner, name) in self.defs:
            return owner, name
        again = self.aliases[owner].get(name)
        return self.follow(again, depth + 1) if again else None

    def target(self, call, module: str):
        """The (module, def) a call reaches in a project module: a function by
        name or through an import (re-exports followed), a method through its
        class, `self` or `cls`, or a dotted path through an imported package
        (`shared.project_scope.f()` after `import shared.project_scope`).
        None for anything else."""
        return self.refers_to(call.func, module)

    def plain_class(self, func, module: str, depth: int = 0):
        """(owning module, class name) when `func`, as `module` sees it, names
        a top-level project class whose building runs only code the census
        reads: no class keyword (`metaclass=`), no decorator, no
        `__new__`, `__init_subclass__` or `__class_getitem__`, and every base
        either from outside the project (`Exception`,
        `sqlite3.OperationalError`; `NamedTuple` is left to the record-class
        reason) or itself a plain class. Building one runs the `__init__`
        `init_of` names, if any, and nothing else of the project's. None for
        anything else."""
        import ast

        if not isinstance(func, (ast.Name, ast.Attribute)):
            return None
        head = func.id if isinstance(func, ast.Name) else None
        owned = self.project_name(head, module) if head else None
        if owned is None and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = self.aliases[module].get(func.value.id, "")
            owned = (base, func.attr) if base and self._path(base) is not None else None
        if owned is None or not owned[1] or depth > 8 or not self.load(owned[0]):
            return None
        node = self.classes.get(owned[0], {}).get(owned[1])
        if node is None or node.keywords or node.decorator_list:
            return None
        for base in node.bases:
            text = ast.unparse(base)
            root = base.id if isinstance(base, ast.Name) else (
                base.value.id if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) else None)
            if root is None or text in ("NamedTuple", "typing.NamedTuple"):
                return None
            if self.project_name(root, owned[0]) is not None and self.plain_class(base, owned[0], depth + 1) is None:
                return None
        if any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
               and item.name in ("__new__", "__init_subclass__", "__class_getitem__") for item in node.body):
            return None
        return owned

    def project_bases(self, owned) -> list:
        """(module, class) of each base of the class `owned` that is a project
        class (`plain_class` of the base, in the class's own module)."""
        node = self.classes.get(owned[0], {}).get(owned[1])
        return [found for found in (self.plain_class(base, owned[0]) for base in (node.bases if node else []))
                if found is not None] if node is not None else []

    def init_of(self, owned):
        """The (module, `<class>.__init__`) a build of the `plain_class`
        `owned` runs: its own `__init__`, else the first project base's, in
        base order. None when no class of the chain defines one."""
        key = (owned[0], f"{owned[1]}.__init__")
        if key in self.defs:
            return key
        for base in self.project_bases(owned):
            found = self.init_of(base)
            if found is not None:
                return found
        return None

    def callee(self, call, module: str, function=None):
        """The (module, def) a call runs, as `target`, and also the
        `__init__` of a `plain_class` it builds and an `instance_target`.
        Kept apart from `target`, which the standard-library typing uses: a
        build's result is the instance, not what `__init__` returns."""
        found = self.target(call, module)
        if found is not None:
            return found
        built = self.plain_class(call.func, module)
        if built is not None and self.init_of(built) is not None:
            return self.init_of(built)
        return self.instance_target(call, module, function)

    def instance_target(self, call, module: str, function=None):
        """The (module, method) a method call on a built object runs, when the
        census can type the object as a build of one `plain_class`
        (`value_class`) and that class defines the method: a build
        (`PACTMemory(...).save()`), a local of `function` whose every
        binding is such a build (`memory = PACTMemory(); memory.save()`), or
        a parameter of a top-level function whose every call the census
        finds passes such a value (`parameter_class`). None otherwise (a
        method on an attribute of `self`, on an untyped parameter, or on a
        local bound any other way)."""
        import ast

        func = call.func
        if not isinstance(func, ast.Attribute):
            return None
        owned = self.value_class(func.value, module, function)
        if owned is None:
            return None
        method = (owned[0], f"{owned[1]}.{func.attr}")
        return method if method in self.defs else None

    def value_class(self, value, module: str, function=None, depth: int = 0):
        """The (module, class) of the one `plain_class` `value` is a build
        of: a call building it, a local of `function` whose every binding
        (`_bindings`) is a call building that same class, or a parameter
        typed by `parameter_class`. None for anything else."""
        import ast

        if isinstance(value, ast.Call):
            return self.plain_class(value.func, module)
        if not isinstance(value, ast.Name) or function is None:
            return None
        found = self._bindings(function, name=value.id)
        if found and all(kind == "param" for kind, _expression in found):
            return self.parameter_class(function, value.id, depth)
        if not found or not all(kind == "value" and isinstance(expression, ast.Call)
                                for kind, expression in found):
            return None
        classes = {self.plain_class(expression.func, module) for _kind, expression in found}
        return classes.pop() if len(classes) == 1 and None not in classes else None

    def parameter_class(self, function, name: str, depth: int = 0):
        """The (module, class) parameter `name` of the top-level function
        `function` holds: the one `plain_class` every call of `function` in
        the loaded modules passes there (`value_class` at the caller, a
        caller's own parameter followed the same way, to a depth of four),
        by position or keyword. None for a method's parameter, a variadic
        function, a function no loaded code calls, a call that passes
        nothing there by name or position or unpacks arguments at or before
        it, or callers that pass anything else."""
        import ast

        if depth > 3:
            return None
        if getattr(self, "_callers_size", None) != len(self.defs):
            # {def key: [(caller module, caller def, call)]} and {id(def): key},
            # rebuilt whenever a load adds a def.
            self._callers, self._keys, self._parameter_classes = {}, {}, {}
            for (owner, qualified), node in list(self.defs.items()):
                self._keys[id(node)] = (owner, qualified)
                for call in ast.walk(node):
                    if isinstance(call, ast.Call):
                        found = self.target(call, owner)
                        if found is not None:
                            self._callers.setdefault(found, []).append((owner, node, call))
            self._callers_size = len(self.defs)
        key = self._keys.get(id(function))
        if key is None or "." in key[1]:
            return None
        if (key, name, depth) in self._parameter_classes:
            return self._parameter_classes[(key, name, depth)]
        self._parameter_classes[(key, name, depth)] = None  # a call cycle types nothing
        positional = [argument.arg for argument in function.args.posonlyargs + function.args.args]
        keywords = positional + [argument.arg for argument in function.args.kwonlyargs]
        if name not in keywords or function.args.vararg is not None or function.args.kwarg is not None:
            return None
        index = positional.index(name) if name in positional else None
        classes = set()
        for owner, node, call in self._callers.get(key, ()):
            value = next((keyword.value for keyword in call.keywords if keyword.arg == name), None)
            if value is None and index is not None and index < len(call.args) and not any(
                    isinstance(argument, ast.Starred) for argument in call.args[:index + 1]):
                value = call.args[index]
            classes.add(None if value is None else self.value_class(value, owner, node, depth + 1))
        found = classes.pop() if len(classes) == 1 and None not in classes else None
        self._parameter_classes[(key, name, depth)] = found
        return found

    def refers_to(self, func, module: str):
        """The (module, def) the expression `func` names, as `target`."""
        import ast

        from fixtures.identity_git import qualified_name

        if isinstance(func, ast.Name):
            if (module, func.id) in self.defs:
                return module, func.id
            return self.follow(self.aliases[module].get(func.id, ""))
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = func.value.id
            for (owner, qualified) in list(self.defs):
                cls, _, method = qualified.partition(".")
                if owner == module and method == func.attr and base in (cls, "self", "cls"):
                    return owner, qualified
            other = self.aliases[module].get(base, "")
            if other:
                return self.follow(f"{other}.{func.attr}")
        if isinstance(func, ast.Attribute):
            dotted = qualified_name(func, self.aliases[module])
            return self.follow(dotted) if dotted else None
        return None

    def project_name(self, name: str, module: str):
        """(owning module, name) when `name`, as `module` sees it, is one of
        a project module's own names: a top-level function or assigned
        object of `module`, a class it defines at any depth (`_class_names`),
        or a name it imports from a project module. None for anything else
        (the standard library, a local)."""
        if (module, name) in self.defs or name in self.globals.get(module, ()) or (
            name in self.class_names.get(module, {})
        ):
            return module, name
        imported = self.aliases[module].get(name)
        if not imported:
            return None
        if self._path(imported) is not None:
            return imported, ""
        owner, _, leaf = imported.rpartition(".")
        if owner and self._path(owner) is not None:
            return owner, leaf
        return None

    def _is_data(self, owner: str, name: str) -> bool:
        """EVERY module-level binding of `name` in `owner` (`_module_bindings`)
        is a LITERAL that `ast.literal_eval` accepts, so it holds constants
        only (a display holding a call or a name, which runs at import, is
        not). A name bound in any other way as well (a call, an augmented
        assignment, a loop target, an import, an `except ... as` or `match`
        capture, a `global` assignment in a function) is not data, whichever
        binding comes last. A built object (a compiled pattern, a logger, a
        context variable, a class instance) is never data: a method called on
        one is reported."""
        import ast

        if not self.load(owner):
            return False
        values = self.bindings.get(owner, {}).get(name, [])

        def literal(value):
            try:
                ast.literal_eval(value)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                return False
            return True

        return bool(values) and all(value is not None and literal(value) for value in values)

    def _owner_class(self, module: str, function):
        """The name of the class of `module` whose body defines `function`."""
        for name, node in self.classes.get(module, {}).items():
            if any(item is function for item in node.body):
                return name
        return None

    @staticmethod
    def _bindings(scope, name=None, attribute=None) -> list:
        """Every binding in `scope` (walked whole, nested functions included)
        of the local `name`, or of `<receiver>.<attr>` for `attribute` =
        (receiver, attr), as (kind, expression):
        - "value": bound to the expression itself (`=`, an annotated `=`,
          `:=`, or an element of a same-length tuple or list display);
        - "from": bound to something derived from the expression (an
          augmented `+=`, a loop or comprehension element, a `with ... as`
          result, an unpacked part, an `except T as` exception of the type
          `T`, a `match` capture of the subject);
        - "param": a parameter of `scope` or of a function or lambda inside it;
        - "import" and "global": an import, or a `global`/`nonlocal`
          declaration;
        - "unknown": any other binding (a nested `def` or `class`, an
          `except` with no type)."""
        import ast

        found = []

        def hit(target):
            if name is not None:
                return isinstance(target, ast.Name) and target.id == name
            receiver, attr = attribute
            return (
                isinstance(target, ast.Attribute) and target.attr == attr
                and isinstance(target.value, ast.Name) and target.value.id == receiver
            )

        def unpack(target, expression, kind):
            if hit(target):
                found.append((kind, expression))
            elif isinstance(target, ast.Starred):
                unpack(target.value, expression, "from")
            elif isinstance(target, (ast.Tuple, ast.List)):
                paired = (
                    kind == "value" and isinstance(expression, (ast.Tuple, ast.List))
                    and len(expression.elts) == len(target.elts)
                    and not any(isinstance(e, ast.Starred) for e in [*target.elts, *expression.elts])
                )
                for index, element in enumerate(target.elts):
                    if paired:
                        unpack(element, expression.elts[index], "value")
                    else:
                        unpack(element, expression, "from")

        for sub in ast.walk(scope):
            if isinstance(sub, ast.Assign):
                for target in sub.targets:
                    unpack(target, sub.value, "value")
            elif isinstance(sub, (ast.AnnAssign, ast.NamedExpr)):
                if sub.value is not None:
                    unpack(sub.target, sub.value, "value")
            elif isinstance(sub, ast.AugAssign):
                unpack(sub.target, sub.value, "from")
            elif isinstance(sub, (ast.For, ast.AsyncFor, ast.comprehension)):
                unpack(sub.target, sub.iter, "from")
            elif isinstance(sub, (ast.With, ast.AsyncWith)):
                for item in sub.items:
                    if item.optional_vars is not None:
                        unpack(item.optional_vars, item.context_expr, "from")
            elif name is None:
                continue
            elif isinstance(sub, ast.ExceptHandler):
                if sub.name == name:
                    found.append(("from", sub.type) if sub.type is not None else ("unknown", None))
            elif isinstance(sub, ast.Match):
                for case in sub.cases:
                    for pattern in ast.walk(case.pattern):
                        if (
                            isinstance(pattern, (ast.MatchAs, ast.MatchStar)) and pattern.name == name
                        ) or (isinstance(pattern, ast.MatchMapping) and pattern.rest == name):
                            found.append(("from", sub.subject))
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if sub is not scope and getattr(sub, "name", None) == name:
                    found.append(("unknown", None))
                arguments = sub.args
                every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                         *(extra for extra in (arguments.vararg, arguments.kwarg) if extra)]
                if any(argument.arg == name for argument in every):
                    found.append(("param", None))
            elif isinstance(sub, ast.ClassDef):
                if sub.name == name:
                    found.append(("unknown", None))
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                for alias in sub.names:
                    bound = alias.asname or (
                        alias.name.split(".")[0] if isinstance(sub, ast.Import) else alias.name
                    )
                    if bound == name:
                        found.append(("import", None))
            elif isinstance(sub, (ast.Global, ast.Nonlocal)):
                if name in sub.names:
                    found.append(("global", None))
        return found

    def instance_method(self, call, module: str, function) -> str:
        """How the census treats a method called on an object it cannot name
        at the call: a LOCAL of `function` (`reader = make(); reader.read()`)
        or an attribute of `self` (`self.reader.read()`). A method
        `instance_target` follows into a class never reaches this rule, and
        this rule follows none.

        "skip" (not this rule's to report): a local that is a parameter only
        (a method on a parameter `parameter_class` cannot type is not
        covered), a name the module rule
        reads (an import, a `global`, a module-level or builtin name with no
        binding here), or a local whose every binding (`_bindings`, every
        form) is a parameter or a value proved to be a standard-library one
        (`_foreign_value`). "report" for everything else: any method on an
        attribute of `self`, a local with any binding that is not proved (a
        project class build included), and a name with no binding the census
        can see."""
        import ast
        import builtins

        func = call.func
        if not isinstance(func, ast.Attribute) or function is None:
            return "skip"
        owner = func.value
        arguments = function.args.posonlyargs + function.args.args
        if (
            isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name)
            and arguments and owner.value.id == arguments[0].arg
            and self._owner_class(module, function) is not None
        ):
            return "report"
        if not isinstance(owner, ast.Name):
            return "skip"
        found = self._bindings(function, name=owner.id)
        kinds = {kind for kind, _expression in found}
        if "global" in kinds or (found and kinds == {"import"}):
            return "skip"
        if not found:
            known = (
                self.project_name(owner.id, module) is not None
                or owner.id in self.aliases[module] or hasattr(builtins, owner.id)
            )
            return "skip" if known else "report"
        if all(
            kind == "param" or (
                kind in ("value", "from") and expression is not None
                and self._foreign_value(expression, module, function, frozenset(), parameters=True)
            )
            for kind, expression in found
        ):
            return "skip"
        return "report"

    def _produced_by_project(self, value, module: str) -> bool:
        """`value` is made by one of the project's own names: a call whose
        callee, followed through attributes, subscripts and inner calls,
        starts at a project name, or such a name itself. Not one: the result
        of a method called directly on a module-level literal table
        (`_is_data`), which is a standard-library value."""
        import ast

        if (
            isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
        ):
            owned = self.project_name(value.func.value.id, module)
            if owned is not None and owned[1] and self._is_data(*owned):
                return False
        head = value
        while isinstance(head, (ast.Attribute, ast.Subscript, ast.Call)):
            head = head.func if isinstance(head, ast.Call) else head.value
        return isinstance(head, ast.Name) and self.project_name(head.id, module) is not None

    _STDLIB_TYPES = frozenset({
        "str", "bytes", "bool", "int", "float", "dict", "list", "tuple", "set", "frozenset",
        "None", "Path", "PurePath", "Optional", "Union", "Dict", "List", "Tuple", "Set",
        "FrozenSet", "Match", "re.Match", "Pattern", "re.Pattern",
    })

    def _stdlib_annotation(self, annotation) -> bool:
        """`annotation` names only builtin and standard-library value types
        (`str`, `Optional[Path]`, `dict`, `re.Match[str]`), as code or as a
        string."""
        import ast

        if annotation is None:
            return False
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            try:
                annotation = ast.parse(annotation.value, mode="eval").body
            except SyntaxError:
                return False
        for node in ast.walk(annotation):
            if isinstance(node, ast.Name) and node.id not in self._STDLIB_TYPES:
                return False
            if isinstance(node, ast.Attribute) and ast.unparse(node) not in self._STDLIB_TYPES:
                return False
        return True

    # PROJECT FUNCTIONS WHOSE RESULT IS THEIR CALLABLE ARGUMENT'S RESULT: the
    # (module, def), the argument's position and its keyword. A call of one
    # is a standard-library value exactly when that argument is a lambda with
    # no parameters whose body `_foreign_value` proves, evaluated in the
    # calling function, or a function passed by name whose returns it proves
    # (`_named_runner_result`). Inside a pass, a later asker of the same
    # question gets the first asker's result, not its own runner's: the typing
    # holds because each question has one runner
    # (`test_each_git_question_has_one_runner`).
    # `test_a_result_passing_function_returns_its_run_result` holds each entry
    # to that contract.
    _RETURNS_ITS_ARGUMENT = {("shared.git_helpers", "answered_once"): (1, "run")}

    def _argument_result(self, call, module: str, function, seen, depth: int, parameters: bool):
        """True when `call` reaches a `_RETURNS_ITS_ARGUMENT` function and
        its callable argument is a parameterless lambda whose body is a
        standard-library value, or a function passed by name whose returns
        are (`_named_runner_result`); False when it reaches one otherwise;
        None when it reaches none."""
        import ast

        target = self.target(call, module)
        if target not in self._RETURNS_ITS_ARGUMENT:
            return None
        rule = self._RETURNS_ITS_ARGUMENT[target]
        position, keyword = rule
        given = [kw.value for kw in call.keywords if kw.arg == keyword]
        argument = given[0] if given else call.args[position] if len(call.args) > position else None
        if isinstance(argument, ast.Name):
            return self._named_runner_result(argument, module, function, seen, depth, parameters)
        if not isinstance(argument, ast.Lambda):
            return False
        lambda_arguments = argument.args
        if (lambda_arguments.posonlyargs or lambda_arguments.args or lambda_arguments.kwonlyargs
                or lambda_arguments.vararg or lambda_arguments.kwarg):
            return False
        return self._foreign_value(argument.body, module, function, seen, depth + 1, parameters)

    def _named_runner_result(self, name, module: str, function, seen, depth: int, parameters: bool) -> bool:
        """A function passed by name as the callable argument of a
        `_RETURNS_ITS_ARGUMENT` function: True when it is a def of the calling
        function (the only binding of the name there, directly in its body,
        with no parameters and no decorator) whose body is a docstring and
        returns, each return value proved in the calling function, the scope
        it reads (`git_helpers.run_git`'s `run`); or a project function with no
        parameters that `returns_foreign`. False for anything else: a def that
        binds a local, takes a parameter, is decorated or has no return value,
        a name with another binding, or a name the census cannot resolve."""
        import ast

        def bare(definition):
            arguments = definition.args
            return not (arguments.posonlyargs or arguments.args or arguments.kwonlyargs
                        or arguments.vararg or arguments.kwarg or definition.decorator_list)

        if function is not None and self._bindings(function, name.id):
            own, pending = [], list(function.body)
            while pending:
                item = pending.pop()
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name.id:
                    own.append(item)
                elif not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    pending.extend(ast.iter_child_nodes(item))
            if len(own) != 1 or self._bindings(function, name.id) != [("unknown", None)]:
                return False
            definition = own[0]
            if not isinstance(definition, ast.FunctionDef) or not bare(definition):
                return False
            statements = [
                statement for statement in definition.body
                if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str))
            ]
            if not statements or not all(
                isinstance(statement, ast.Return) and statement.value is not None for statement in statements
            ):
                return False
            return all(
                self._foreign_value(statement.value, module, function, seen, depth + 1, parameters)
                for statement in statements
            )
        target = self.refers_to(name, module)
        if target is None or target not in self.defs or not bare(self.defs[target]):
            return False
        return self.returns_foreign(target, seen)

    def returns_foreign(self, target, seen=frozenset()) -> bool:
        """PROVED: every value the project function `target` returns is a
        standard-library value, so methods called on its result run no
        project code. Each `return` expression must be `_foreign_value`, a
        function with no return value returns None, and a call into another
        project function must satisfy this in turn (cycles fail)."""
        import ast

        if target in seen or target not in self.defs:
            return False
        node = self.defs[target]
        returns, pending = [], list(node.body)
        while pending:
            item = pending.pop()
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(item, ast.Return):
                returns.append(item.value)
            pending.extend(ast.iter_child_nodes(item))
        return all(
            value is None or self._foreign_value(value, target[0], node, seen | {target})
            for value in returns
        )

    def _foreign_value(
        self, value, module: str, function, seen, depth: int = 0, parameters: bool = False
    ) -> bool:
        """`value`, evaluated in `function`, is a standard-library value:
        a literal; an f-string, comparison or boolean test; a parameter
        annotated with standard-library types (any parameter, with
        `parameters`); an operator, subscript, attribute or conditional over
        such values; a local whose every binding (`_bindings`, in any form)
        is or derives from one (a name declared `global` counts its module
        bindings too); a builtin or a module imported from outside the project; a
        module object that is data (`_is_data`); a call of a non-project
        callable (`json.loads(text)`, `p.strip()`) whose receiver is one and
        whose arguments name no project function, class or non-data object
        (a project callable passed in could run); a method of a data object;
        or a call into a project function that `returns_foreign`, or of a
        `_RETURNS_ITS_ARGUMENT` function whose callable argument is a
        parameterless lambda with a proved body. Anything else, a project
        class build or an unannotated parameter included, is not proved."""
        import ast

        if depth > 12:
            return False

        def again(inner):
            return self._foreign_value(inner, module, function, seen, depth + 1, parameters)

        try:
            ast.literal_eval(value)
            return True
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            pass
        if isinstance(value, (ast.JoinedStr, ast.Compare)):
            return True
        if isinstance(value, ast.BoolOp):
            return all(again(item) for item in value.values)
        if isinstance(value, ast.UnaryOp):
            return again(value.operand)
        if isinstance(value, ast.BinOp):
            return again(value.left) and again(value.right)
        if isinstance(value, ast.IfExp):
            return again(value.body) and again(value.orelse)
        if isinstance(value, (ast.Subscript, ast.Attribute, ast.Starred)):
            return again(value.value)
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            return all(again(item) for item in value.elts)
        if isinstance(value, ast.Dict):
            return all(again(item) for item in [*value.keys, *value.values] if item is not None)
        if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            made = [value.key, value.value] if isinstance(value, ast.DictComp) else [value.elt]
            return all(again(item) for item in made)
        if isinstance(value, ast.Name):
            arguments = function.args
            for argument in arguments.posonlyargs + arguments.args + arguments.kwonlyargs:
                if argument.arg == value.id:
                    return parameters or self._stdlib_annotation(argument.annotation)
            found = self._bindings(function, name=value.id)
            if found:
                # A binding that reads the name itself (`path = path.resolve()`)
                # is proved on the assumption under test; every other binding
                # must still be proved, so a project value cannot enter.
                key = ("name", id(function), value.id)
                if key in seen:
                    return True
                sources = []
                for kind, expression in found:
                    if kind == "global":
                        outside = self.bindings.get(module, {}).get(value.id, [])
                        if not outside or any(item is None for item in outside):
                            return False
                        sources += outside
                    elif kind in ("value", "from") and expression is not None:
                        sources.append(expression)
                    elif kind == "import":
                        if self.project_name(value.id, module) is not None:
                            return False
                    elif not (kind == "param" and parameters):
                        return False
                return all(
                    self._foreign_value(item, module, function, seen | {key}, depth + 1, parameters)
                    for item in sources
                )
            owned = self.project_name(value.id, module)
            if owned is not None and owned[1] and owned[0] == module:
                return self._is_data(module, value.id)
            if owned is None:
                import builtins
                return hasattr(builtins, value.id) or value.id in self.aliases[module]
            return False
        if isinstance(value, ast.Call):
            passed_through = self._argument_result(value, module, function, seen, depth, parameters)
            if passed_through is not None:
                return passed_through
            target = self.target(value, module)
            if target is not None:
                return self.returns_foreign(target, seen)
            func = value.func
            # A callee bound inside the function (a nested def or class, a
            # local holding a callable) is proved like any other local.
            if isinstance(func, ast.Name) and self._bindings(function, name=func.id) and not again(func):
                return False
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                owned = self.project_name(func.value.id, module)
                if owned is not None and owned[1] and self._is_data(*owned):
                    return True
            if self._produced_by_project(value, module) and not (
                isinstance(func, ast.Attribute) and again(func.value)
            ):
                return False
            receiver = func.value if isinstance(func, ast.Attribute) else None
            passed = [*value.args, *(kw.value for kw in value.keywords)]
            # A project function CALLED inside an argument hands over its
            # result, not itself: its callee name is not counted when it
            # `returns_foreign`.
            called = {
                id(inner.func) for argument in passed for inner in ast.walk(argument)
                if isinstance(inner, ast.Call) and self.target(inner, module) is not None
                and self.returns_foreign(self.target(inner, module), seen)
            }
            hands_project_code = any(
                isinstance(inner, ast.Name) and id(inner) not in called
                and self.project_name(inner.id, module) is not None
                and not (self.project_name(inner.id, module)[1] and self._is_data(*self.project_name(inner.id, module)))
                for argument in passed for inner in ast.walk(argument)
            )
            return (receiver is None or again(receiver)) and not hands_project_code
        return False

    def producer_of(self, call, module: str, function):
        """For a method called on a LOCAL of `function`, the project callee
        whose result some binding of the local holds, spelled as written
        (`find_git_marker`, `PACTMemory._declared_dir_identity`), so an
        unread method names what made its object; None otherwise."""
        import ast

        func = call.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)) or function is None:
            return None
        producers = sorted({
            _callee_text(expression.func)
            for _kind, expression in self._bindings(function, name=func.value.id)
            if isinstance(expression, ast.Call) and self._produced_by_project(expression, module)
        })
        return " or ".join(producers) if producers else None

    def unread_call(self, call, module: str, function=None) -> bool:
        """A call the census cannot read: its callee is reached through one
        of the project's own names (`project_name`) and does not resolve to a
        project function. The callee is followed through attributes,
        subscripts and inner calls to the name it starts from. Read, and so
        not reported: a call that resolves to a function, and a method
        called directly on a module-level literal (`_is_data`). A method on a
        local or a `self` attribute follows `instance_method`. Everything
        else through a project name is reported: every build of a project
        class (same module or imported, whatever the class defines), a
        method of a compiled pattern, a logger or a context variable, an
        instance's method, and a function pulled from a table."""
        import ast

        if self.target(call, module) is not None or self.instance_target(call, module, function) is not None:
            return False
        verdict = self.instance_method(call, module, function)
        if verdict != "skip":
            return verdict == "report"
        head, depth = call.func, 0
        while isinstance(head, (ast.Attribute, ast.Subscript, ast.Call)):
            head = head.func if isinstance(head, ast.Call) else head.value
            depth += 1
        if not isinstance(head, ast.Name):
            return False
        owned = self.project_name(head.id, module)
        if owned is None:
            return False
        owner, name = owned
        if depth == 1 and isinstance(call.func, ast.Attribute) and name and self._is_data(owner, name):
            return False
        return True

    def imported_name(self, node, module: str):
        """The dotted name, as written, when `node` loads a module-level name
        of another project module that is not a function: a name bound by
        `from <project module> import NAME`, or `<project module>.NAME` on a
        module imported by name. None for anything else (a function, which
        `references` follows, a submodule, a standard-library name)."""
        import ast

        if not isinstance(getattr(node, "ctx", None), ast.Load):
            return None
        if isinstance(node, ast.Name):
            target = self.aliases[module].get(node.id)
            shown = node.id
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            base = self.aliases[module].get(node.value.id)
            if not base or self._path(base) is None:
                return None
            target = f"{base}.{node.attr}"
            shown = f"{node.value.id}.{node.attr}"
        else:
            return None
        if not target or self._path(target) is not None:
            return None
        owner, _, leaf = target.rpartition(".")
        if not owner or self._path(owner) is None or not self.load(owner):
            return None
        if self.follow(target) is not None or (owner, leaf) in self.defs:
            return None
        return shown

    def class_statement_name(self, expression, module: str) -> bool:
        """Whether a base, keyword value or decorator of a class statement
        (`_class_statement_names`) is reached through one of the project's
        own names (`project_name`, any class the same module defines,
        nested ones included),
        followed through attribute, subscript, call and starred expressions
        to the name it starts from, and does not resolve to a project
        function. Defining the class runs that
        object's code (a base's `__init_subclass__`, a metaclass, a class
        decorator) with no call node, so the name is reported. A project
        function there is followed by `references` like any other use."""
        import ast

        found = self.target(expression, module) if isinstance(expression, ast.Call) else (
            self.refers_to(expression, module)
        )
        if found is not None:
            return False
        head = expression
        while isinstance(head, (ast.Attribute, ast.Subscript, ast.Call, ast.Starred)):
            head = head.func if isinstance(head, ast.Call) else head.value
        return isinstance(head, ast.Name) and self.project_name(head.id, module) is not None

    def own_class_value(self, node, module: str):
        """The qualified names of every class of `module` itself that a
        load of `node`'s name can mean, at any depth (`_class_names`), the
        same-module counterpart of `imported_name`: a class object handed to
        other code (`type(...)`, `types.new_class(...)`, an `isinstance`
        test, a raise, an annotation, a base, keyword or decorator
        expression) can run its hooks with no class statement of its own. A
        name two classes share gives both. None for anything else."""
        import ast

        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and (
                node.id in self.class_names.get(module, {})):
            return sorted(self.class_names[module][node.id])
        return None

    def references(self, node, module: str):
        """Yield (expression, (module, def)) for each call into a project
        function inside `node`, and for each project function used as a
        value there (passed, stored or returned), which is counted as a call
        because the scan cannot see where the value is called."""
        import ast

        callees, bases = set(), set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                callees.add(id(sub.func))
            elif isinstance(sub, ast.Attribute):
                bases.add(id(sub.value))
        function = node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                found = self.callee(sub, module, function)
                if found is not None:
                    yield sub.func, found
            elif (
                isinstance(sub, (ast.Name, ast.Attribute)) and isinstance(sub.ctx, ast.Load)
                and id(sub) not in callees and id(sub) not in bases
            ):
                found = self.refers_to(sub, module)
                if found is not None:
                    yield sub, found


def _own_class_values(graph, module: str, node, callees: set, annotations: set):
    """(qualified class name, ' in an annotation' when it is one, line) for
    each load in `node` of a class of `module` itself
    (`_DetectionGraph.own_class_value`) that is not the callee of a call,
    wherever it stands: a class base, keyword or decorator expression
    included, so a class inside `type(Cls)`, `*(Cls,)` or `@wrap(Cls)` is
    reported as well as the statement's own head."""
    import ast

    for sub in ast.walk(node):
        if id(sub) in callees:
            continue
        for own in graph.own_class_value(sub, module) or ():
            yield f"{own}{' in an annotation' if id(sub) in annotations else ''}", sub.lineno


def _class_names(tree) -> dict:
    """{simple name: {qualified name: ClassDef}} for every class `tree`
    defines, at any depth: a class in a class body is `Outer.Inner`, one in
    a function is `function.Local`."""
    import ast

    found = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{prefix}{child.name}"
                if isinstance(child, ast.ClassDef):
                    found.setdefault(child.name, {})[qualified] = child
                walk(child, qualified + ".")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _class_statement_names(node):
    """(role, expression) for each base, keyword value (`metaclass=`
    included) and decorator of the class statement `node`."""
    return (
        [("base", base) for base in node.bases]
        + [(f"keyword {keyword.arg}", keyword.value) for keyword in node.keywords]
        + [("decorator", decorator) for decorator in node.decorator_list]
    )


def _import_time_code(tree):
    """The code `tree` runs when it is imported, as one function the census
    reads like any other: every module-level statement, class bodies
    included, with each function's body removed (its decorators and
    defaults run at import and are kept). A lambda's body is kept,
    wherever the lambda stands, since a lambda called at import runs it.
    Left out: a
    module-level `if` whose test is exactly `__name__ == "__main__"` and
    which has no `else` branch, since on import `__name__` is the module's
    name. Any other test that mentions `__name__` (reversed operands, an
    `and` or `or`, a variable) and any `else` branch is read."""
    import ast
    import copy

    def main_guard(statement):
        test = getattr(statement, "test", None)
        return (
            isinstance(statement, ast.If) and not statement.orelse
            and isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
        )

    body = [statement for statement in copy.deepcopy(tree).body if not main_guard(statement)]
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.body = [ast.Pass()]
    code = ast.FunctionDef(
        name="<import>",
        args=ast.arguments(posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
                           kw_defaults=[], kwarg=None, defaults=[]),
        body=body or [ast.Pass()], decorator_list=[], returns=None,
    )
    return ast.fix_missing_locations(code)


def _module_bindings(tree) -> dict:
    """{name: [value, ...]}: every binding of a module-level name, in the
    module's own statements (descending into `if`, `try`, `with`, `match`
    and loop bodies, not into a function or class) and in any function that
    declares the name `global`. An assignment to a plain name in module code
    contributes its value; every other binding contributes None: a tuple,
    starred, subscript or attribute target (a store into the object), an
    augmented assignment, a loop or `with` target, a walrus, an `except ...
    as` name, a name a `match` pattern captures, an import, a `def` or
    `class`, and any assignment under `global`."""
    import ast

    found: dict = {}

    def statement(node, only=None):
        def take(target, value=None):
            names = [target] if isinstance(target, ast.Name) else [
                inner for inner in ast.walk(target) if isinstance(inner, ast.Name)
            ]
            for name in names:
                if only is None or name.id in only:
                    plain = only is None and name is target
                    found.setdefault(name.id, []).append(value if plain else None)

        if isinstance(node, ast.Assign):
            for target in node.targets:
                take(target, node.value)
        elif isinstance(node, ast.AnnAssign) and (node.value is not None or only is not None):
            take(node.target, node.value)
        elif isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor)):
            take(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    take(item.optional_vars)
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and only is None:
            for alias in node.names:
                found.setdefault((alias.asname or alias.name).split(".")[0], []).append(None)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and only is None:
            found.setdefault(node.name, []).append(None)
        elif isinstance(node, ast.ExceptHandler) and node.name and (only is None or node.name in only):
            found.setdefault(node.name, []).append(None)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                for pattern in ast.walk(case.pattern):
                    if isinstance(pattern, (ast.MatchAs, ast.MatchStar)):
                        captured = pattern.name
                    elif isinstance(pattern, ast.MatchMapping):
                        captured = pattern.rest
                    else:
                        captured = None
                    if captured and (only is None or captured in only):
                        found.setdefault(captured, []).append(None)
        for walrus in (sub for sub in ast.walk(node) if isinstance(sub, ast.NamedExpr)):
            take(walrus.target)

    pending = list(tree.body)
    while pending:
        node = pending.pop()
        statement(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody", "handlers"):
            pending.extend(getattr(node, field, None) or [])
        for case in getattr(node, "cases", None) or []:
            pending.extend(case.body)
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        declared = {name for sub in ast.walk(function) if isinstance(sub, ast.Global) for name in sub.names}
        for sub in ast.walk(function) if declared else ():
            if isinstance(sub, (ast.stmt, ast.ExceptHandler)) and not isinstance(
                sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                statement(sub, declared)
    return found


def _callee_text(func) -> str:
    """The callee as written, with the arguments of any call inside it
    shown as `...`, so a condition names the callee and not its arguments."""
    import ast
    import copy

    shown = copy.deepcopy(func)
    for node in ast.walk(shown):
        if isinstance(node, ast.Call) and (node.args or node.keywords):
            node.args, node.keywords = [ast.Name(id="...", ctx=ast.Load())], []
    return ast.unparse(shown)


def _evaluates_to_env(value, aliases: dict, bound) -> bool:
    """`value` evaluates to an environment mapping or function itself: an
    anchor name (`_ENV_MAPPINGS`, `_ENV_FUNCTIONS`, through any import form),
    a name in `bound`, or a branch of a conditional or `or`/`and` that does."""
    import ast

    from fixtures.identity_git import qualified_name

    if isinstance(value, ast.Name) and value.id in bound:
        return True
    if isinstance(value, (ast.Name, ast.Attribute)):
        return (qualified_name(value, aliases) or "") in (_ENV_MAPPINGS | _ENV_FUNCTIONS)
    if isinstance(value, ast.IfExp):
        return _evaluates_to_env(value.body, aliases, bound) or _evaluates_to_env(value.orelse, aliases, bound)
    if isinstance(value, ast.BoolOp):
        return any(_evaluates_to_env(item, aliases, bound) for item in value.values)
    if isinstance(value, ast.NamedExpr):
        return _evaluates_to_env(value.value, aliases, bound)
    return False


def _env_reads(node, module: str, function: str, graph: "_DetectionGraph"):
    """Yield (key node or None, description) for every occurrence of the
    process environment in one function.

    THE ANCHOR is every reference to an environment mapping or function
    (`_ENV_MAPPINGS`, `_ENV_FUNCTIONS`, in any import form) and every name
    bound to one, in the module (`_ENV = os.environ`) or the function (an
    assignment, or a parameter default such as `env=os.environ`), followed
    to a fixed point. Each occurrence is READ, ACCEPTED or REPORTED:
    - READ: a mapping's subscript, the first argument of a lookup method
      (`_ENV_KEY_METHODS`), the left side of an `in` test, the first
      argument of a function anchor, or each `$NAME`/`${NAME}` of a constant
      `expandvars` template; the key node is yielded (bytes keys decoded);
    - ACCEPTED: the value of a binding that makes a name an anchor, whose
      own uses are checked in turn, and an identity test (`env is None`),
      which reads no variable; nothing is yielded;
    - REPORTED: any other occurrence (the mapping copied, iterated, passed
      or spliced; a key or template that is not a constant); None is
      yielded, so the census names it as an unreadable condition."""
    import ast
    import re as regex

    from fixtures.identity_git import qualified_name

    aliases = graph.aliases[module]
    parents = {}
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    bound = set(graph.env_aliases.get(module, ()))
    bindings = []
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Assign, ast.AnnAssign)) and sub.value is not None:
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            bindings.append(([t.id for t in targets if isinstance(t, ast.Name)], sub.value))
        elif isinstance(sub, ast.NamedExpr):
            bindings.append(([sub.target.id], sub.value))
    arguments = node.args
    positional = arguments.posonlyargs + arguments.args
    for argument, default in zip(positional[len(positional) - len(arguments.defaults):], arguments.defaults):
        bindings.append(([argument.arg], default))
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        if default is not None:
            bindings.append(([argument.arg], default))
    changed = True
    while changed:
        changed = False
        for names, value in bindings:
            if names and _evaluates_to_env(value, aliases, bound) and not set(names) <= bound:
                bound.update(names)
                changed = True
    accepted = set()

    def accept(value):
        if isinstance(value, ast.IfExp):
            accept(value.body)
            accept(value.orelse)
        elif isinstance(value, ast.BoolOp):
            for item in value.values:
                accept(item)
        elif isinstance(value, ast.NamedExpr):
            accept(value.value)
        else:
            accepted.add(id(value))

    for names, value in bindings:
        if names and _evaluates_to_env(value, aliases, bound):
            accept(value)

    def kind(sub):
        if not isinstance(sub, (ast.Name, ast.Attribute)) or not isinstance(sub.ctx, ast.Load):
            return None
        if isinstance(sub, ast.Name) and sub.id in bound:
            return "mapping"
        qualified = qualified_name(sub, aliases) or ""
        if qualified in _ENV_MAPPINGS:
            return "mapping"
        if qualified in _ENV_FUNCTIONS:
            return qualified
        return None

    def as_text(key):
        if isinstance(key, ast.Constant) and isinstance(key.value, bytes):
            text = ast.Constant(value=key.value.decode("utf-8", "replace"))
            return ast.copy_location(text, key)
        return key

    for sub in ast.walk(node):
        anchor = kind(sub)
        if anchor is None or id(sub) in accepted:
            continue
        parent = parents.get(id(sub))
        shown = ast.unparse(parent if parent is not None else sub)
        if isinstance(parent, ast.Compare) and all(isinstance(op, (ast.Is, ast.IsNot)) for op in parent.ops):
            continue
        if anchor == "mapping":
            key = None
            if isinstance(parent, ast.Attribute) and parent.attr in _ENV_KEY_METHODS:
                call = parents.get(id(parent))
                if isinstance(call, ast.Call) and call.func is parent and call.args:
                    key = call.args[0]
            elif isinstance(parent, ast.Subscript) and parent.value is sub:
                key = parent.slice
            elif isinstance(parent, ast.Compare) and sub in parent.comparators and all(
                isinstance(op, (ast.In, ast.NotIn)) for op in parent.ops
            ):
                key = parent.left
            yield as_text(key), shown
            continue
        call = parent if isinstance(parent, ast.Call) and parent.func is sub else None
        first = call.args[0] if call is not None and call.args else None
        if anchor != "os.path.expandvars":
            yield as_text(first), shown
            continue
        template = as_text(first)
        if not (isinstance(template, ast.Constant) and isinstance(template.value, str)):
            yield None, shown
            continue
        for match in regex.finditer(r"\$(?:(\w+)|\{([^}]*)\})", template.value):
            name = ast.Constant(value=match.group(1) or match.group(2))
            yield ast.copy_location(name, template), shown


def _pass_with_statements(tree, setups) -> list:
    """[(owner name, owner def, [with statements])] for every function or
    method of the parsed module `tree` (a top-level def, or a method of a
    top-level class) holding a `with` or `async with`, at any depth, one of
    whose items calls a name in `setups` by name or attribute. This is the
    set of pass openings `_pass_openings` reads and the pass-entry ban
    (`_pass_entry_problems`) admits."""
    import ast

    owners = [(node.name, node) for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    owners += [
        (f"{node.name}.{item.name}", item) for node in tree.body if isinstance(node, ast.ClassDef)
        for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    found = []
    for name, function in owners:
        statements = [
            node for node in ast.walk(function)
            if isinstance(node, (ast.With, ast.AsyncWith)) and any(
                isinstance(item.context_expr, ast.Call)
                and (getattr(item.context_expr.func, "id", None)
                     or getattr(item.context_expr.func, "attr", None)) in setups
                for item in node.items
            )
        ]
        if statements:
            found.append((name, function, statements))
    return found


def _table_callees(graph, module: str, function, call):
    """The project functions a call of a LOCAL of `function` can reach, when
    every binding of that local (`_DetectionGraph._bindings`) is a project
    function (`run = _reader`) or looks one up in a module-level dict
    display of project functions (`handler = _COMMANDS.get(args.command)`,
    or `_COMMANDS[...]`), each value of which resolves (`refers_to`), and
    every module-level binding of the table is that display. None when the
    call is not of a local; [] when it is of a local the census cannot
    resolve this way, which `route_inputs` reports."""
    import ast
    import builtins

    func = call.func
    if not isinstance(func, ast.Name) or graph.project_name(func.id, module) is not None or (
            func.id in graph.aliases[module] or hasattr(builtins, func.id)):
        return None
    found = graph._bindings(function, name=func.id)
    if not found:
        return None
    targets = []
    for kind, expression in found:
        if kind == "value" and isinstance(expression, (ast.Name, ast.Attribute)) and (
                graph.refers_to(expression, module) is not None):
            targets.append(expression)  # `run = _reader`: the function itself
            continue
        lookup = expression.func.value if (
            kind == "value" and isinstance(expression, ast.Call) and isinstance(expression.func, ast.Attribute)
            and expression.func.attr == "get"
        ) else expression.value if kind == "value" and isinstance(expression, ast.Subscript) else None
        values = graph.bindings[module].get(lookup.id, []) if isinstance(lookup, ast.Name) else []
        if not values or not all(isinstance(value, ast.Dict) for value in values) or any(
                value is not values[0] for value in values):
            return []
        for value in values[0].values:
            if value is None or graph.refers_to(value, module) is None:
                return []
            targets.append(value)
    return targets


def _unresolved_opening_locals(graph) -> list:
    """[(condition, site)] for each call in a pass opening of a local that
    `_table_callees` cannot resolve, which `route_inputs` reports."""
    return [
        (("call", f"<call of a local no table resolves {module}: {_callee_text(call.func)}>"),
         f"{module}.py:{call.lineno}")
        for (module, _name), calls in graph.opening_locals.items() for call, targets in calls if not targets
    ]


def _pass_openings(graph) -> dict:
    """Each place a memory script or a hook opens a pass, as a read root: for
    every function or method of a module in `SCRIPTS` or `hooks/` with a
    `with` whose item calls one of `PASS_SETUPS` (by name or attribute), a
    def named `<function> <pass opening>` in `graph.defs` holding every item
    of those `with` statements and the block each one runs, whose own reads
    the census reports. A call in the block of a local bound before the
    `with` is read as a call of each function `_table_callees` resolves it
    to (`cli.main`'s `handler`, every command in `_COMMANDS`); one it
    cannot resolve is kept in `graph.opening_locals` and reported. The rest
    of the function, such as `cli.main`'s argument parsing, is not part of
    the opening. Returns {key: the part of the opening whose calls are followed}:
    the items and block in a memory script; the items alone in a hook, whose
    block calls the hook command's own work (`backlog.main`'s `_main`). Each
    project class an item builds goes in `graph.opening_classes` with every
    method it defines, since `with` runs its code with no call node.
    `_pass_entry_problems` holds production code to entering a pass only
    this way."""
    import ast

    import copy

    setups = {name for _module, name in PASS_SETUPS}
    openings = {}
    graph.opening_classes, graph.opening_locals = {}, {}
    hooks = PLUGIN_ROOT / "hooks"
    sources = [(path, path.stem, True) for path in sorted(SCRIPTS.glob("*.py"))]
    sources += [(path, ".".join(path.relative_to(hooks).with_suffix("").parts), False)
                for path in sorted(hooks.rglob("*.py")) if path.name != "__init__.py"]
    for path, module, memory_script in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, function, statements in _pass_with_statements(tree, setups):
            items = [ast.Expr(value=item.context_expr) for node in statements for item in node.items]
            body = [statement for node in statements for statement in node.body]
            if not graph.load(module):
                continue

            def opening_def(statements):
                made = ast.FunctionDef(
                    name=f"{name} <pass opening>", args=function.args, decorator_list=[], returns=None,
                    type_params=[], body=statements,
                )
                return ast.copy_location(made, function)

            key = (module, f"{name} <pass opening>")
            # A call of a local bound before the `with` (`cli.main`'s `handler`) is
            # read as a call of each function its table holds; one the census cannot
            # resolve is reported (`graph.opening_locals`).
            resolved = []
            for statement in body:
                for call in [node for node in ast.walk(statement) if isinstance(node, ast.Call)]:
                    targets = _table_callees(graph, module, function, call)
                    if targets is None:
                        continue
                    graph.opening_locals.setdefault(key, []).append((call, targets))
                    resolved += [ast.copy_location(ast.Expr(value=ast.copy_location(
                        ast.Call(func=copy.deepcopy(target), args=call.args, keywords=call.keywords), call)), call)
                        for target in targets]
            graph.defs[key] = opening_def(items + body + resolved)
            openings[key] = opening_def(items + body + resolved if memory_script else items)
            for item in items:
                built = item.value.func if isinstance(item.value, ast.Call) else None
                owned = graph.project_name(built.id, module) if isinstance(built, ast.Name) else None
                if owned is None or not owned[1] or not graph.load(owned[0]):
                    continue
                node = graph.classes.get(owned[0], {}).get(owned[1])
                if node is not None:
                    graph.opening_classes[owned] = [
                        (owned[0], f"{owned[1]}.{method.name}") for method in node.body
                        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ]
    return openings


def test_the_census_root_holds_every_pass_opening():
    """Pin for the census root (`route_inputs`): `_pass_openings` finds the
    places the memory scripts and the hooks open the detection pass,
    `cli.main`'s, a store's construction and `backlog.main`'s among them, and
    each of them and both `PASS_SETUPS` is a def the census reads, the block
    each opening runs included, and every method of the warning holder
    `backlog.main` builds there is a root. Each of `cli.main`'s two handler
    calls resolves, through the `_COMMANDS` table bound before the `with`
    (`_table_callees`), to every command function, and each of those is a
    def the opening's followed part calls, so the census reads the command
    work. If discovery finds nothing, drops a block or the holder's
    methods, or leaves a handler call unresolved, an environment read added
    there would pass unread, so this fails."""
    import ast

    graph = _DetectionGraph()
    for module in (*DETECTION_MODULES, *(module for module, _name in PASS_SETUPS)):
        graph.load(module)
    openings = _pass_openings(graph)
    assert {("cli", "main <pass opening>"), ("memory_api", "PACTMemory.__init__ <pass opening>"),
            ("shared.backlog", "main <pass opening>")} <= set(openings)
    assert [key for key in [*PASS_SETUPS, *openings] if key not in graph.defs] == []
    holder = graph.opening_classes.get(("shared.backlog", "_HeldWarnings"), [])
    assert {name for _module, name in holder} >= {"_HeldWarnings.__init__", "_HeldWarnings.__enter__",
                                                  "_HeldWarnings.__exit__", "_HeldWarnings.filter"}
    assert [key for key in holder if key not in graph.defs] == []
    key = ("cli", "main <pass opening>")
    handler_calls = [node for node in ast.walk(graph.defs[key])
                     if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "handler"]
    assert len(handler_calls) == 2  # the handler call in each of cli.main's two pass blocks
    resolved = {id(call): targets for call, targets in graph.opening_locals.get(key, [])}
    commands = graph.values["cli"]["_COMMANDS"]
    expected = sorted(graph.refers_to(value, "cli") for value in commands.values)
    assert len(expected) == len(set(expected)) >= 11 and None not in expected, expected  # control: the table
    for call in handler_calls:
        assert sorted(graph.refers_to(target, "cli") for target in resolved.get(id(call), [])) == expected
    followed = {graph.target(node, "cli") for node in ast.walk(openings[key]) if isinstance(node, ast.Call)}
    assert set(expected) <= followed and all(command in graph.defs for command in expected)


@pytest.mark.parametrize("prelude, binding, resolved", [
    ("TABLE = {'a': first, 'b': second}\n", "handler = TABLE.get(name)", ["first", "second"]),
    ("TABLE = {'a': first}\n", "handler = TABLE[name]", ["first"]),
    ("", "handler = first", ["first"]),
    ("TABLE = {'a': first}\nTABLE['b'] = second\n", "handler = TABLE.get(name)", []),
    ("TABLE = {'a': first, 'b': len}\n", "handler = TABLE.get(name)", []),
    ("TABLE = {'a': first}\n", "handler = make()", []),
    ("TABLE = {'a': first}\n", "handler = TABLE.get(name) or first", []),
], ids=[
    "table get", "table subscript", "a project function", "a table changed after its display",
    "a table holding a non-project function", "a local bound to a call", "a local bound to an expression",
])
def test_a_local_called_in_a_pass_block_resolves_through_its_table(tmp_path, monkeypatch, prelude, binding, resolved):
    """Rows for `_table_callees`: a call of a local bound before the `with`
    resolves to each project function a module-level dict display of them
    holds when the local looks the table up, or to the function itself;
    any other binding, and a table changed after its display, resolve to
    nothing, which `route_inputs` reports as a call of a local no table
    resolves."""
    import ast
    import sys

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "m.py").write_text(
        "def first(args):\n    return args\n\ndef second(args):\n    return args\n\n"
        "def make():\n    return first\n\n" + prelude
        + f"\ndef main(name, args):\n    {binding}\n    return handler(args)\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path, PLUGIN_ROOT / "hooks"))
    graph = _DetectionGraph()
    assert graph.load("pkg.m")
    main = graph.defs[("pkg.m", "main")]
    call = next(node for node in ast.walk(main) if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "handler")
    targets = _table_callees(graph, "pkg.m", main, call)
    assert [graph.refers_to(target, "pkg.m")[1] for target in targets] == resolved
    graph.opening_locals = {("pkg.m", "main <pass opening>"): [(call, targets)]}
    reported = [condition for condition, _site in _unresolved_opening_locals(graph)]
    assert reported == ([] if resolved else [("call", "<call of a local no table resolves pkg.m: handler>")])


def test_a_scripts_package_import_names_the_script():
    """Row for `_script_module`: the memory scripts import one another through
    the `scripts` package (`from scripts.memory_api import PACTMemory` in
    `cli`), and the census names each by its file stem, so the package
    prefix is taken off a name that names a script, and kept on any other."""
    assert _script_module("scripts.memory_api.PACTMemory") == "memory_api.PACTMemory"
    assert _script_module("scripts.config") == "config"
    assert _script_module("scripts.no_such_script.f") == "scripts.no_such_script.f"
    assert _script_module("shared.backlog") == "shared.backlog"
    graph = _DetectionGraph()
    assert graph.load("cli")
    assert graph.project_name("PACTMemory", "cli") == ("memory_api", "PACTMemory")  # cli's own import


@pytest.mark.parametrize("source, init", [
    ("class Store:\n    def __init__(self):\n        pass\n", "Store.__init__"),
    ("class Store(Exception):\n    def __init__(self):\n        pass\n", "Store.__init__"),
    ("class Store:\n    pass\n", None),
    ("class Base:\n    pass\n\nclass Store(Base):\n    def __init__(self):\n        pass\n", "Store.__init__"),
    ("class Base:\n    def __init__(self):\n        pass\n\nclass Store(Base):\n    pass\n", "Base.__init__"),
    ("class Meta(type):\n    pass\n\nclass Base(metaclass=Meta):\n    pass\n\n"
     "class Store(Base):\n    def __init__(self):\n        pass\n", None),
    ("class Meta(type):\n    pass\n\nclass Store(metaclass=Meta):\n    def __init__(self):\n        pass\n", None),
    ("import functools\n\n@functools.total_ordering\nclass Store:\n    def __init__(self):\n        pass\n", None),
    ("class Store:\n    def __new__(cls):\n        return object.__new__(cls)\n"
     "    def __init__(self):\n        pass\n", None),
], ids=[
    "a plain class", "a plain class on an outside base", "no __init__ to follow", "a plain project base",
    "an __init__ on a plain project base", "a project base with a metaclass",
    "a metaclass", "a decorator", "a __new__",
])
def test_a_plain_class_build_is_followed_into_its_init(tmp_path, monkeypatch, source, init):
    """Rows for `_DetectionGraph.callee`: a build of a `plain_class` is
    followed into the `__init__` its chain defines (`init_of`), its own or
    a plain project base's; a class with a class keyword, a decorator or a
    `__new__`, or a project base that has one, or one whose chain defines
    no `__init__`, is not followed. The build is reported either way
    (`_DetectionGraph.unread_call`), since a method called on an instance
    the census cannot type is not followed into the class."""
    import ast
    import sys

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "m.py").write_text(source + "\n\ndef build():\n    return Store()\n")
    monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path, PLUGIN_ROOT / "hooks"))
    graph = _DetectionGraph()
    assert graph.load("pkg.m")
    build = graph.defs[("pkg.m", "build")]
    call = next(node for node in ast.walk(build) if isinstance(node, ast.Call))
    assert graph.callee(call, "pkg.m") == (None if init is None else ("pkg.m", init))
    assert graph.target(call, "pkg.m") is None and graph.unread_call(call, "pkg.m", build)  # still reported


_STORE = "class Store:\n    def read(self):\n        return 1\n\n"


@pytest.mark.parametrize("source, followed", [
    ("def helper(store):\n    return store.read()\n\ndef entry():\n    memory = Store()\n    helper(memory)\n", True),
    ("def helper(store):\n    return store.read()\n\ndef entry():\n    helper(store=Store())\n", True),
    ("def helper(store):\n    return store.read()\n\ndef middle(store):\n    helper(store)\n\n"
     "def entry():\n    middle(Store())\n", True),
    ("def helper(store):\n    return store.read()\n\ndef entry():\n    helper(Store())\n    helper(object())\n", False),
    ("def helper(store):\n    return store.read()\n\ndef make():\n    return Store()\n\n"
     "def entry():\n    helper(make())\n", False),
    ("def helper(store):\n    return store.read()\n\ndef entry():\n    helper(*[Store()])\n", False),
    ("def helper(first, store, last=None):\n    return store.read()\n\n"
     "def entry():\n    helper(*[1, object()], Store())\n", False),
    ("class Other:\n    def read(self):\n        return 2\n\ndef helper(store):\n    return store.read()\n\n"
     "def entry(flag):\n    memory = Store()\n    if flag:\n        memory = Other()\n    helper(memory)\n", False),
    ("def make():\n    return Store()\n\ndef helper(store):\n    return store.read()\n\n"
     "def entry(flag):\n    memory = Store()\n    if flag:\n        memory = make()\n    helper(memory)\n", False),
    ("def helper(store):\n    return store.read()\n\ndef entry():\n    helper(**{'store': Store()})\n", False),
    ("def helper(store):\n    return store.read()\n", False),
    ("def helper(store, *rest):\n    return store.read()\n\ndef entry():\n    helper(Store())\n", False),
    ("def helper(store):\n    return store.read()\n\ndef entry():\n    helper()\n", False),
    ("def helper(store):\n    return store.read()\n\ndef entry(store):\n    helper(store)\n", False),
], ids=[
    "a local built from the class", "a keyword build", "through a caller's typed parameter",
    "a caller passes another value", "a project function's result", "a starred argument",
    "a starred argument before it", "a local bound to two classes", "a local also bound to a result",
    "unpacked keywords", "no caller", "a variadic helper", "a caller passes nothing",
    "an untyped caller parameter",
])
def test_a_parameter_every_caller_passes_one_plain_class_is_followed(tmp_path, monkeypatch, source, followed):
    """Rows for `_DetectionGraph.parameter_class`: a method on a parameter of
    a top-level function is followed into the class when every call of the
    function passes a build of one `plain_class`, by position or keyword,
    directly, through a local or through a caller's parameter typed the
    same way. Any other caller, a starred or unpacked argument, a variadic
    helper, a missing argument or no caller at all leaves it untyped."""
    import ast
    import sys

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "m.py").write_text(_STORE + source)
    monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path, PLUGIN_ROOT / "hooks"))
    graph = _DetectionGraph()
    assert graph.load("pkg.m")
    helper = graph.defs[("pkg.m", "helper")]
    call = next(node for node in ast.walk(helper) if isinstance(node, ast.Call))
    assert (graph.instance_target(call, "pkg.m", helper) == ("pkg.m", "Store.read")) is followed
    assert ({target for _node, target in graph.references(helper, "pkg.m")} == {("pkg.m", "Store.read")}) is followed



_STORE_READ = "def read_row():\n    return 'row'\n\n"
_ASK = "def ask():\n    return run(['git', '-C', "


def _traced_rows(tmp_path, monkeypatch, source):
    """(source names the trace reaches, unresolved) for the `run` call in
    `ask` of a scratch module holding `source` (`_traced_sources`)."""
    import ast
    import sys

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "m.py").write_text("import operator\nfrom subprocess import run\n\n" + _STORE_READ + source)
    monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path, PLUGIN_ROOT / "hooks"))
    graph = _DetectionGraph()
    assert graph.load("pkg.m")
    key = next(key for key in graph.defs if key[1].rpartition(".")[2] == "ask")
    asking = graph.defs[key]
    question = next(node for node in ast.walk(asking) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "run")
    unresolved = []
    found = _traced_sources(graph, [("pkg.m", asking, argument) for argument in question.args], unresolved)
    return {ast.unparse(call.func) for _owner, call in found}, [why for _where, why, _origin in unresolved]


@pytest.mark.parametrize("source, reaches", [
    ("def ask(directory):\n    return run(['git', '-C', directory])\n\n"
     "def entry():\n    value = read_row()\n    return ask(value)\n", True),
    ("def ask(directory):\n    command = ['git', '-C', directory]\n    return run(command)\n\n"
     "def entry():\n    return ask(read_row())\n", True),
    ("class Store:\n    def load(self):\n        self.seen = read_row()\n\n"
     "    def ask(self):\n        return run(['git', '-C', self.seen])\n", True),
    ("def ask(directory):\n    return run(['git', '-C', directory])\n\n"
     "def entry():\n    value = read_row()\n    return ask(directory=value)\n", True),
    ("def ask(directory):\n    return run(['git', '-C', directory])\n\n"
     "def entry(root):\n    read_row()\n    return ask(root)\n", False),
    ("class Store:\n    def load(self):\n        self.seen = 'constant'\n        read_row()\n\n"
     "    def ask(self):\n        return run(['git', '-C', self.seen])\n", False),
    ("def helper():\n    return read_row()\n\n" + _ASK + "helper()])\n", True),
    ("def helper():\n    read_row()\n    return 'here'\n\n" + _ASK + "helper()])\n", False),
    ("def helper(value):\n    return value\n\n" + _ASK + "helper('here')])\n\n"
     "def other():\n    return helper(read_row())\n", False),
    ("def ask():\n    argv = ['git']\n    argv.append(read_row())\n    return run(argv)\n", True),
    ("def ask():\n    argv = ['git', '-C', '.']\n    argv[2] = read_row()\n    return run(argv)\n", True),
    ("def fill(items):\n    items.extend([read_row()])\n\n"
     "def ask():\n    argv = ['git']\n    fill(argv)\n    return run(argv)\n", True),
    ("def ask():\n    argv = ['git']\n    alias = argv\n    alias.append(read_row())\n    return run(argv)\n", True),
    ("def ask():\n    argv = ['git']\n    argv.append('status')\n    read_row()\n    return run(argv)\n", False),
    ("def ask():\n    table = {}\n    table['k'] = read_row()\n    return run(['git', '-C', table.get('k')])\n", True),
    ("def ask():\n    items = []\n    items.append(read_row())\n    return run(['git', '-C', items.pop()])\n", True),
    ("_SEEN = {}\n\ndef load():\n    _SEEN['root'] = read_row()\n\n" + _ASK + "_SEEN['root']])\n", True),
    ("def ask():\n    value0 = read_row()\n" + "".join(f"    value{n} = value{n - 1}\n" for n in range(1, 10))
     + "    return run(['git', '-C', value9])\n", True),
    ("def first(value):\n    return second(value)\n\n"
     "def second(value):\n    return first(value) if value else read_row()\n\n"
     + _ASK + "first('')])\n", True),
    ("def ask(*parts):\n    return run(['git', *parts])\n\ndef entry():\n    return ask(*[read_row()])\n", True),
    ("def ask(flag, directory):\n    return run(['git', '-C', directory])\n\n"
     "def entry():\n    return ask(*['-q', read_row()])\n", True),
    ("def ask(**options):\n    return run(['git', '-C', options['root']])\n\n"
     "def entry():\n    return ask(root=read_row())\n", True),
    ("def helper():\n    return read_row()\n\n"
     "def ask():\n    getter = helper\n    return run(['git', '-C', getter()])\n", True),
    ("def ask():\n    def nested():\n        return read_row()\n    return run(['git', '-C', nested()])\n", True),
    ("def ask():\n    key = 'here'\n    [key for key in [read_row()]]\n    return run(['git', '-C', key])\n", False),
    ("class Store:\n    def load(self):\n        self.a = read_row()\n        self.b = 'here'\n\n"
     "    def ask(self):\n        return run(['git', '-C', getattr(self, 'b')])\n", False),
    ("class Store:\n    def load(self):\n        self.a = read_row()\n\n"
     "    def ask(self):\n        return run(['git', '-C', getattr(self, 'a')])\n", True),
    ("class Store:\n    def __init__(self):\n        self.root = 'here'\n\n"
     "    def search(self):\n        self.status = read_row()\n        return ask(self)\n\n"
     "def ask(memory):\n    return run(['git', '-C', str(memory.root)])\n", False),
    ("class Store:\n    def __init__(self):\n        self.root = 'here'\n\n"
     "    def search(self):\n        self.root = read_row()\n        return ask(self)\n\n"
     "def ask(memory):\n    return run(['git', '-C', str(memory.root)])\n", True),
    ("class Store:\n    def __init__(self):\n        self.root = 'here'\n\n"
     "    def search(self):\n        self.status = read_row()\n        return ask(self)\n\n"
     "def ask(memory):\n    return run(['git', '-C', str(getattr(memory, 'root'))])\n", False),
    ("class Store:\n    def __init__(self):\n        self.root = 'here'\n\n"
     "    def search(self):\n        self.status = read_row()\n        return ask(self)\n\n"
     "def ask(memory):\n    return run(['git', '-C', memory.status])\n", True),
], ids=[
    "through a caller's local", "through the callee's own local", "through a self attribute",
    "through a keyword argument", "a read beside the question", "an attribute bound to a constant",
    "through a helper's return", "a helper that returns a constant", "a helper called elsewhere with a read",
    "through a container's writer method", "through an item assignment", "through a write a callee makes",
    "through an alias of the container", "a container written only with constants",
    "through a container read back with get", "through a container read back with pop",
    "through a module-level table a function writes", "through a chain of nine steps",
    "through a cycle of two functions", "through a starred argument", "through a starred argument filling a named one", "through unpacked keywords", "through a local holding the function",
    "through a nested function's return", "a comprehension variable of the same name",
    "an attribute getattr names that holds a constant", "an attribute getattr names that holds the read",
    "a helper handed the object reads one field the work does not write",
    "a helper handed the object reads the field the work writes",
    "a helper handed the object reads one field through getattr",
    "a helper handed the object reads the field holding the read",
])
def test_a_git_argument_is_traced_to_every_value_that_reaches_it(tmp_path, monkeypatch, source, reaches):
    """Rows for `_traced_sources`: a value that can reach an argument of a git
    command is found through a local, a caller's argument by position,
    keyword, star or unpacking, a `self` attribute (also named by a literal
    `getattr`), a helper's return, a local or nested function holding the
    callee, a module-level table a function writes, and every write into a
    container (a writer method, an item assignment, an alias, a callee),
    on a chain of any length and through a cycle, which ends. A helper's
    parameter read as `p.attr` carries only that attribute of the object
    handed to it, so a write into another attribute of that object is not
    followed. A read beside the question, a constant, a
    helper's own read it does not return, an argument another call passes
    (the trace enters a function through one call and reads that call's
    arguments), and a comprehension's own variable are not."""
    sources, unresolved = _traced_rows(tmp_path, monkeypatch, source)
    assert ("read_row" in sources) is reaches, sources
    assert unresolved == [], unresolved


@pytest.mark.parametrize("source, why", [
    ("def ask(table):\n    return run(['git', '-C', table['root']()])\n\n"
     "def entry():\n    return ask({'root': read_row})\n", "a call of a computed callee"),
    ("def make():\n    return object()\n\n"
     "def ask(getter):\n    return run(['git', '-C', getter()])\n\n"
     "def entry():\n    return ask(make())\n", "a callable that is neither a lambda nor a project function"),
    ("def ask(path):\n    return run(['git', '-C', path])\n\n"
     "def entry():\n    return sorted(['a'], key=ask)\n",
     "a parameter of a function used as a value whose calls cannot be seen"),
], ids=["a computed callee", "a callable from a call no trace can name", "a function handed to the standard library"])
def test_a_git_argument_the_trace_cannot_follow_fails_closed(tmp_path, monkeypatch, source, why):
    """Rows for `_traced_sources`' fail-closed forms: a value the trace cannot
    follow is reported in `unresolved`, which fails the pin, rather than
    read as harmless."""
    _sources, unresolved = _traced_rows(tmp_path, monkeypatch, source)
    assert any(item.startswith(why) for item in unresolved), unresolved


def _paragraph(doc: str, heading: str) -> str:
    """The paragraph of the whitespace-joined `doc` that opens with
    `heading`, up to the next list heading (`NOT COVERED`,
    `ADVERSARIAL-ONLY`, `LIMITS`) or the end."""
    import re

    start = doc.index(heading)
    rest = doc[start + len(heading):]
    following = re.search(r"\b(NOT COVERED|ADVERSARIAL-ONLY|LIMITS)\b", rest)
    return doc[start:start + len(heading) + (following.start() if following else len(rest))]


# Each form `_traced_sources`' first ADVERSARIAL-ONLY list names (the anchor,
# as its docstring writes it), with a row the trace does not follow.
_NOT_FOLLOWED_ROWS = {
    "`setattr`": "class Box:\n    pass\n\ndef ask():\n    box = Box()\n    setattr(box, 'root', read_row())\n"
                 "    return run(['git', '-C', box.root])\n",
    "`vars()`": "class Box:\n    pass\n\ndef ask():\n    box = Box()\n    vars(box)['root'] = read_row()\n"
                "    return run(['git', '-C', box.root])\n",
    "`__dict__`": "class Box:\n    pass\n\ndef ask():\n    box = Box()\n    box.__dict__['root'] = read_row()\n"
                  "    return run(['git', '-C', box.root])\n",
    "`globals()`": "ROOT = 'here'\n\ndef load():\n    globals()['ROOT'] = read_row()\n\n" + _ASK + "ROOT])\n",
    "`locals()`": "def ask():\n    root = 'here'\n    locals()['root'] = read_row()\n    return run(['git', '-C', root])\n",
    "`list.append(argv, value)`": "def ask():\n    argv = ['git']\n    list.append(argv, read_row())\n    return run(argv)\n",
    "`operator.setitem`": "def ask():\n    argv = ['git', '-C', '.']\n    operator.setitem(argv, 2, read_row())\n"
                          "    return run(argv)\n",
    "exception": "def ask():\n    try:\n        raise ValueError(read_row())\n    except ValueError as error:\n"
                 "        return run(['git', '-C', error.args[0]])\n",
}
# Each form `_traced_sources`' second ADVERSARIAL-ONLY list names, with a row
# read as the built-in only.
_READ_AS_BUILTIN_ROWS = {
    "`:=`": "def ask(item, holder):\n    if isinstance(item, dict):\n"
            "        [item := other for other in holder]\n        return run(['git', '-C', item.get('root')])\n",
    "nested function": "def ask(item, holder):\n    if isinstance(item, dict):\n        def swap():\n"
                       "            nonlocal item\n            item = holder[0]\n        swap()\n"
                       "        return run(['git', '-C', item.get('root')])\n",
    "table": "class Found:\n    reason: dict = None\n\nKINDS = {'found': Found}\n\ndef make(holder):\n"
             "    return KINDS['found'](holder)\n\ndef ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n",
    "`getattr`": "import sys\n\nclass Found:\n    reason: dict = None\n\ndef make(holder):\n"
                 "    return getattr(sys.modules[__name__], 'Found')(holder)\n\n"
                 "def ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n",
}
# The honest forms beside them, each followed: a write by a method of the
# object or an attribute assignment; a narrowed local rebound in the
# function's own statements (in a branch, in a loop that reads it before the
# rebinding on its next pass); and a positional build by the class's name, a
# local bound to it or to a choice of classes, a classmethod's `cls`,
# `type(self)` and `self.__class__`.
_FOLLOWED_ROWS = {
    "an attribute assignment": "class Box:\n    pass\n\ndef ask():\n    box = Box()\n    box.root = read_row()\n"
                               "    return run(['git', '-C', box.root])\n",
    "a writer method": "def ask():\n    argv = ['git']\n    argv.append(read_row())\n    return run(argv)\n",
    "a narrowed local rebound in its branch": "def ask(item, holder):\n    if isinstance(item, dict):\n"
                                              "        item = holder[0]\n        return run(['git', '-C', item.get('root')])\n",
    "a narrowed local rebound after an early return": "def ask(item, holder):\n    if not isinstance(item, dict):\n"
                                                       "        return None\n    item = holder[0]\n"
                                                       "    return run(['git', '-C', item.get('root')])\n",
    "a narrowed local rebound by a loop": "def ask(item, holder):\n    if isinstance(item, dict):\n"
                                          "        for item in holder:\n"
                                          "            return run(['git', '-C', item.get('root')])\n",
    "a narrowed local rebound later in a loop": "def ask(item, holder):\n    if isinstance(item, dict):\n"
                                                "        for other in holder:\n"
                                                "            run(['git', '-C', item.get('root')])\n            item = other\n",
    "a narrowed local rebound with a with item": "def ask(item, holder):\n    if isinstance(item, dict):\n"
                                                 "        with holder as item:\n"
                                                 "            return run(['git', '-C', item.get('root')])\n",
    "a build by the class's name": "class Found:\n    reason: dict = None\n\ndef make(holder):\n    return Found(holder)\n\n"
                                   "def ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n",
    "a build through a local bound to the class": "class Found:\n    reason: dict = None\n\ndef make(holder):\n"
                                                  "    build = Found\n    return build(holder)\n\n"
                                                  "def ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n",
    "a build through a local bound to a choice of classes": "class Found:\n    reason: dict = None\n\n"
                                                            "class Other:\n    pass\n\ndef make(holder, flag):\n"
                                                            "    build = Found if flag else Other\n    return build(holder)\n\n"
                                                            "def ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n",
    "a classmethod's cls": "class Found:\n    reason: dict = None\n\n    @classmethod\n    def make(cls, holder):\n"
                           "        return cls(holder)\n\ndef ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n",
    "type(self)": "class Found:\n    reason: dict = None\n\n    def again(self, holder):\n        return type(self)(holder)\n\n"
                  "def ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n",
    "self.__class__": "class Found:\n    reason: dict = None\n\n    def again(self, holder):\n"
                      "        return self.__class__(holder)\n\n"
                      "def ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n",
}
# Controls that stay built-in: the narrowed local read before its rebinding,
# and a classmethod building another class by position.
_STILL_BUILTIN_ROWS = {
    "a narrowed local read before its rebinding": "def ask(item, holder):\n    if isinstance(item, dict):\n"
                                                  "        found = run(['git', '-C', item.get('root')])\n"
                                                  "        item = holder[0]\n        return found\n",
    "a classmethod building another class": "class Found:\n    reason: dict = None\n\nclass Other:\n"
                                            "    @classmethod\n    def make(cls, holder):\n        return Found()\n\n"
                                            "    @classmethod\n    def other(cls, holder):\n        return cls(holder)\n\n"
                                            "def ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n",
}


def test_the_trace_lists_its_adversarial_only_forms_and_does_not_follow_them(tmp_path, monkeypatch):
    """Pin for `_traced_sources`' two ADVERSARIAL-ONLY lists, by behaviour:
    each form the first list names (`_NOT_FOLLOWED_ROWS`) is not followed,
    and each form the second names (`_READ_AS_BUILTIN_ROWS`) is read as the
    built-in only, so `Memory.get`'s read is not reached; each list names
    exactly its rows' forms (their anchors appear in its paragraph); and the
    honest forms beside them (`_FOLLOWED_ROWS`) are followed, while the
    controls (`_STILL_BUILTIN_ROWS`) stay built-in. If the trace comes to
    follow a listed form, stops following an honest one, or a list drops a
    form it has a row for, this fails."""
    doc = " ".join(_traced_sources.__doc__.split())
    first = _paragraph(doc, "ADVERSARIAL-ONLY, and so not followed")
    second = _paragraph(doc, "ADVERSARIAL-ONLY, and so read as a built-in object")
    assert [anchor for anchor in _NOT_FOLLOWED_ROWS if anchor not in first] == [], first
    assert [anchor for anchor in _READ_AS_BUILTIN_ROWS if anchor not in second] == [], second
    rows = [(source, False) for source in _NOT_FOLLOWED_ROWS.values()]
    rows += [(_MEMORY + source, False) for source in (*_READ_AS_BUILTIN_ROWS.values(), *_STILL_BUILTIN_ROWS.values())]
    rows += [(_MEMORY + source, True) for source in _FOLLOWED_ROWS.values()]
    for index, (source, followed) in enumerate(rows):
        (tmp_path / str(index)).mkdir()
        sources, _unresolved = _traced_rows(tmp_path / str(index), monkeypatch, source)
        assert ("read_row" in sources) is followed, (source, sources)


_MEMORY = "class Memory:\n    def get(self, key):\n        return read_row()\n\n"


@pytest.mark.parametrize("source, reaches", [
    ("def ask(memory):\n    return run(['git', '-C', memory.get('root')])\n", True),
    ("def ask(memory):\n    return run(['git', '-C', memory.update('root')])\n", False),
    ("def ask():\n    table = {'root': 'here'}\n    return run(['git', '-C', table.get('root')])\n", False),
    ("def ask(row):\n    return run(['git', '-C', dict(row).get('root')])\n", False),
    ("from collections import OrderedDict\n\n"
     "def ask():\n    return run(['git', '-C', OrderedDict().get('root')])\n", False),
    ("def ask():\n    table = {'k': {'root': 'here'}}\n    return run(['git', '-C', table.get('k').get('root')])\n",
     False),
    ("def ask(memory):\n    table = {'k': memory}\n    return run(['git', '-C', table.get('k').get('root')])\n", True),
    ("def ask():\n    table = {'k': {'root': 'here'}}\n    return run(['git', '-C', table['k'].get('root')])\n", False),
    ("def ask(memory):\n    table = {'k': memory}\n    return run(['git', '-C', table['k'].get('root')])\n", True),
    ("def ask(memory):\n    rows = [{'root': 'here'}, memory]\n    return run(['git', '-C', rows[0].get('root')])\n",
     True),
    ("def ask(items):\n    found = [item for item in items if isinstance(item, dict)]\n"
     "    return run(['git', '-C', found[0].get('root')])\n", False),
    ("def ask(items):\n    found = [item for item in items if isinstance(item, Memory)]\n"
     "    return run(['git', '-C', found[0].get('root')])\n", True),
    ("def ask(items):\n    found = {item: item for item in items}\n"
     "    return run(['git', '-C', found['k'].get('root')])\n", True),
    ("def ask():\n    found = {key: {'root': key} for key in 'ab'}\n"
     "    return run(['git', '-C', found['a'].get('root')])\n", False),
    ("def ask(item):\n    if isinstance(item, dict):\n        return run(['git', '-C', item.get('root')])\n", False),
    ("def ask(item):\n    if isinstance(item, Memory):\n        return run(['git', '-C', item.get('root')])\n", True),
    ("def ask(item):\n    return not isinstance(item, dict) or run(['git', '-C', item.get('root')])\n", False),
    ("def ask(item):\n    return isinstance(item, dict) and run(['git', '-C', item.get('root')])\n", False),
    ("def ask(item):\n    if not isinstance(item, dict):\n        return None\n"
     "    return run(['git', '-C', item.get('root')])\n", False),
    ("def ask(item):\n    if not isinstance(item, dict):\n        print(item)\n"
     "    return run(['git', '-C', item.get('root')])\n", True),
    ("def ask():\n    for item in [{'root': 'here'}]:\n        return run(['git', '-C', item.get('root')])\n", False),
    ("def ask(memory):\n    for item in [memory]:\n        return run(['git', '-C', item.get('root')])\n", True),
    ("def ask(memory):\n    item = memory\n    [item for item in [{'root': 'here'}]]\n"
     "    return run(['git', '-C', item.get('root')])\n", True),
    ("def ask(memory):\n    return [run(['git', '-C', item.get('root')]) for item in [{'root': 'here'}]]\n", False),
    ("def ask(**options):\n    return run(['git', '-C', options.get('root')])\n", False),
    ("def ask(*parts):\n    return run(['git', '-C', parts[0].get('root')])\n\n"
     "def entry(memory):\n    return ask(memory)\n", True),
    ("_TABLE = {}\n\ndef ask():\n    return run(['git', '-C', _TABLE.get('root')])\n", False),
    ("_TABLE = Memory()\n\ndef ask():\n    return run(['git', '-C', _TABLE.get('root')])\n", True),
    ("_TABLE = {}\n\ndef load(memory):\n    _TABLE['k'] = memory\n\n"
     "def ask():\n    return run(['git', '-C', _TABLE['k'].get('root')])\n", True),
    ("from pkg import m as me\n\n_TABLE = {}\n\ndef ask():\n    return run(['git', '-C', me._TABLE.get('root')])\n",
     False),
    ("from pkg import m as me\n\n_TABLE = Memory()\n\n"
     "def ask():\n    return run(['git', '-C', me._TABLE.get('root')])\n", True),
    ("class Found:\n    reason: dict = None\n\ndef make():\n    return Found(reason={'root': 'here'})\n\n"
     "def ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n", False),
    ("class Found:\n    reason: dict = None\n\ndef make(memory):\n    return Found(reason=memory)\n\n"
     "def ask(found):\n    return run(['git', '-C', found.reason.get('root')])\n", True),
    ("def table():\n    return {'root': 'here'}\n\ndef ask():\n    return run(['git', '-C', table().get('root')])\n",
     False),
    ("def table(memory):\n    return memory\n\n"
     "def ask(memory):\n    return run(['git', '-C', table(memory).get('root')])\n", True),
    ("def ask():\n    table = {}\n    table['k'] = {'root': 'here'}\n"
     "    return run(['git', '-C', table['k'].get('root')])\n", False),
    ("def ask(memory, flag):\n    table = {'root': 'here'} if flag else memory\n"
     "    return run(['git', '-C', table.get('root')])\n", True),
    ("def ask(flag):\n    table = {'root': 'here'} if flag else {}\n    return run(['git', '-C', table.get('root')])\n",
     False),
    ("def ask(memory):\n    table = memory or {}\n    return run(['git', '-C', table.get('root')])\n", True),
    ("def ask(row):\n    table = row or {}\n    return run(['git', '-C', table.copy().get('root')])\n", True),
    ("def ask():\n    table = {}\n    return run(['git', '-C', table.copy().get('root')])\n", False),
    ("def ask(name):\n    rows = [{'root': name}]\n    return run(['git', '-C', rows[0].get('root')])\n", False),
    ("def ask(name, memory):\n    rows = [{'root': name}]\n    rows[0] = memory\n"
     "    return run(['git', '-C', rows[0].get('root')])\n", True),
    ("_UNSET = object()\n_SLOT = _UNSET\n\ndef ask():\n    return run(['git', '-C', _SLOT.get('root')])\n", False),
    ("def table(name):\n    return {'root': name}\n\n"
     "def ask(name):\n    return run(['git', '-C', table(name).get('root')])\n", False),
    ("def ask(memory):\n    rows = [{'root': 'here'}]\n    item = memory\n"
     "    return [run(['git', '-C', item.get('root')]) for item in rows]\n", False),
], ids=[
    "an untyped receiver", "a method name the class does not define", "a dict display", "a built-in type's call",
    "a standard-library class", "a content method on a display of displays",
    "a content method on a display holding an untyped value", "an item of a display of displays",
    "an item of a display holding an untyped value", "an item of a display holding one untyped value",
    "an isinstance comprehension", "a comprehension tested against a project class",
    "a dict comprehension of untyped values", "a dict comprehension of displays",
    "inside an if isinstance", "inside an if isinstance of a project class", "after or not isinstance",
    "after and isinstance", "after an early return", "after a test that does not leave",
    "a loop over a display of displays", "a loop over an untyped value",
    "a local beside a comprehension of the same name", "a comprehension's own variable",
    "a **kwargs parameter", "an element of *args", "a module dict", "a module object of the class",
    "a module dict written with an untyped value", "a module dict read through the module",
    "a module object read through the module", "a field filled with a display",
    "a field filled with an untyped value", "a project function returning a display",
    "a project function returning an untyped value",
    "a local dict written with a display", "a choice holding an untyped value", "a choice of displays",
    "a boolean choice holding an untyped value", "a copy of an untyped value", "a copy of a display",
    "an item of a list of displays", "an item of a list of displays written with an untyped value",
    "a module object holding a built-in build", "a project function returning a display of a parameter",
    "a comprehension's own variable beside a local of the same name",
])
def test_a_method_name_a_builtin_shares_is_read_both_ways(tmp_path, monkeypatch, source, reaches):
    """Rows for `_untyped_methods` and `_builtin_receiver`: a call of a
    method name a built-in also defines (`get`) on a value the census cannot
    type is read both ways, as the built-in and as every project method of
    that name, so `Memory.get`'s read is reached; on a value proved a
    built-in or standard-library object (`_holds`), only as the built-in.
    Each row is one form of that proof, beside the same form holding a
    value it cannot type."""
    sources, unresolved = _traced_rows(tmp_path, monkeypatch, _MEMORY + source)
    assert ("read_row" in sources) is reaches, sources
    assert unresolved == [], unresolved


_LIMIT_ROWS = {
    "a container item written with a project object":
        "def ask(memory):\n    table = {}\n    table['k'] = memory\n    return run(['git', '-C', table['k'].get('root')])\n",
    "super()": "class Child(Memory):\n    def ask(self):\n        return run(['git', '-C', super().get('root')])\n",
    "next(iter(x))": "def ask(memory):\n    return run(['git', '-C', next(iter([memory])).get('root')])\n",
    "getattr(o, n)": "def ask(holder, name):\n    return run(['git', '-C', getattr(holder, name).get('root')])\n",
    "max(...)": "def ask(memory):\n    return run(['git', '-C', max([memory]).get('root')])\n",
    "copy.copy(x)": "import copy\n\ndef ask(memory):\n    return run(['git', '-C', copy.copy(memory).get('root')])\n",
}


def test_the_trace_names_its_limits(tmp_path, monkeypatch):
    """Pin for `_traced_sources`' LIMITS, by behaviour: the paragraph names
    each form in `_LIMIT_ROWS` (its anchor appears there), and each still
    reads as the built-in only, so `Memory.get`'s read is not reached. If the
    census comes to read one of them, or the paragraph drops one, this
    fails: the list is a ruled boundary of the static census, and changing
    it is a decision."""
    named = _paragraph(" ".join(_traced_sources.__doc__.split()), "LIMITS, by ruling")
    anchors = {form: ("table['k']" if form.startswith("a container item") else form) for form in _LIMIT_ROWS}
    assert [anchor for anchor in anchors.values() if anchor not in named] == [], named
    for index, (form, source) in enumerate(_LIMIT_ROWS.items()):
        (tmp_path / str(index)).mkdir()
        sources, _unresolved = _traced_rows(tmp_path / str(index), monkeypatch, _MEMORY + source)
        assert "read_row" not in sources, (form, sources)


def _computed_callee_calls(graph) -> list:
    """`module.py:line` and source for each call with a positional argument
    whose callee is computed where it is called: a subscript (`KINDS[k](x)`)
    or the result of another call (`getattr(m, "Found")(x)`), in any def or
    module-level code of the loaded graph. `_field_values` cannot see which
    class such a call builds, so a field it fills by position is not read."""
    import ast

    found = []
    scopes = [(owner, node) for (owner, _name), node in graph.defs.items()]
    scopes += list(graph.import_time.items())
    for owner, node in scopes:
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and call.args and isinstance(call.func, (ast.Subscript, ast.Call)):
                found.append(f"{owner}.py:{call.lineno} {ast.unparse(call)[:80]}")
    return sorted(set(found))


def test_no_production_call_builds_through_a_computed_callee():
    """The store-work value pin lists a field filled by position through a
    class reached by a table or `getattr` as adversarial-only. No production
    module the pin reads calls a subscript or a call result with positional
    arguments, so the listing hides no real site; a new one fails here.
    Calls of a parameter or a local that holds a function are not counted:
    the trace follows them through their callers (`_callables`,
    `_arguments_for`)."""
    _questions, _problems, _allowed, graph = _store_work_taint_for(_source_fingerprint())
    assert len(graph.defs) > 500, len(graph.defs)  # control: the pin's whole graph is loaded
    sites = _computed_callee_calls(graph)
    assert sites == [], (
        "a production call passes positional arguments to a callee computed where it is called (a subscript "
        "or a call result). The store-work value pin cannot see which class it builds, so classify each site: "
        "call the class by name, or extend _built_classes to read the form, or list it in _traced_sources' "
        f"ADVERSARIAL-ONLY paragraph with a row: {sites}"
    )


def test_a_computed_callee_call_is_found(tmp_path, monkeypatch):
    """Control for `_computed_callee_calls` on scratch code: a table lookup
    and a getattr result called with a positional argument are found, and a
    call of a name, a parameter or a method is not."""
    import sys

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "m.py").write_text(
        "import sys\n\nclass Found:\n    pass\n\nKINDS = {'found': Found}\n\n"
        "def by_table(holder):\n    return KINDS['found'](holder)\n\n"
        "def by_getattr(holder):\n    return getattr(sys.modules[__name__], 'Found')(holder)\n\n"
        "def plain(holder, make):\n    Found()\n    make(holder)\n    return holder.copy(holder)\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path,))
    graph = _DetectionGraph()
    assert graph.load("pkg.m")
    found = _computed_callee_calls(graph)
    assert {site.split(" ", 1)[0] for site in found} == {"pkg.m.py:9", "pkg.m.py:12"}, found


def _pass_entry_problems(trees: dict) -> tuple:
    """(entries, problems) for the pass-entry ban over `trees` ({file path:
    parsed module}). "The pass" is `detection_pass` or `git_results_pass`
    (`PASS_SETUPS`): a name spelled as either, or as a local alias an
    `import ... as` gives either, and any attribute named either, whatever
    object it is read from. Each such reference must be the callee of a
    `with` item that `_pass_with_statements` records, which is exactly where
    `_pass_openings` reads a pass opening; that use is an entry. Every other
    reference is a problem: an `ExitStack().enter_context(...)` or other call
    of the pass's result, a manual `__enter__`, an assignment or rebinding of
    the pass object or its result, a return of either, passing either as an
    argument, an aliased name in a `with`, and a `with` outside a top-level
    function or a top-level class's method. The defining `def` and an import
    statement are not references, so they pass.

    ADVERSARIAL-ONLY, and so not reported: a pass reached through its name
    spelled as data (`getattr(module, "detection_pass")`, `globals()[...]`,
    `importlib`). It needs the name written as a string, which no honest
    caller does: every production entry today spells the call."""
    import ast

    setups = {name for _module, name in PASS_SETUPS}
    entries, problems = [], []
    for path, tree in trees.items():
        admitted = {
            id(item.context_expr)
            for _name, _function, statements in _pass_with_statements(tree, setups)
            for statement in statements for item in statement.items
        }
        names = setups | {
            alias.asname for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names if alias.name in setups and alias.asname
        }
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Name) and node.id in names
                    or isinstance(node, ast.Attribute) and node.attr in setups):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Call) and parent.func is node and id(parent) in admitted:
                entries.append(f"{path}:{node.lineno}")
            else:
                problems.append(f"{path}:{node.lineno}: {ast.unparse(parent if parent is not None else node)}")
    return entries, problems


def test_the_passes_are_entered_only_as_with_items_the_census_reads():
    """Pin for the pass-entry ban over production code: every module in the
    memory scripts (`SCRIPTS`) and in `hooks/`, the population
    `_pass_openings` scans. Production code enters `detection_pass` and
    `git_results_pass` only as a `with` item the census reads as a pass
    opening, so an opening entered any other way cannot escape the census
    root. Control: cli.main's, the store construction's, backlog.main's and
    detection_pass's own entries are found."""
    files = [*sorted(SCRIPTS.glob("*.py")), *sorted((PLUGIN_ROOT / "hooks").rglob("*.py"))]
    entries, problems = _pass_entry_problems(_parse_files(files, PLUGIN_ROOT))
    assert problems == []
    assert {entry.rpartition(":")[0] for entry in entries} >= {
        "skills/pact-memory/scripts/cli.py", "skills/pact-memory/scripts/memory_api.py", "hooks/shared/backlog.py",
    }, entries


@pytest.mark.parametrize("source, honest", [
    ("from m import detection_pass\n\ndef f():\n    with detection_pass():\n        pass\n", True),
    ("def f():\n    with other(), mod.detection_pass() as memos:\n        pass\n", True),
    ("class C:\n    def m(self):\n        if x:\n            with git_results_pass():\n                pass\n", True),
    ("from m import detection_pass\n\ndef f():\n    with ExitStack() as stack:\n"
     "        stack.enter_context(detection_pass())\n", False),
    ("def f():\n    detection_pass().__enter__()\n", False),
    ("def f():\n    entered = detection_pass()\n", False),
    ("def f():\n    opener = detection_pass\n    with opener():\n        pass\n", False),
    ("def f():\n    return git_results_pass()\n", False),
    ("def f():\n    run(detection_pass)\n", False),
    ("def f():\n    run(mod.detection_pass())\n", False),
    ("from m import detection_pass as dp\n\ndef f():\n    with dp():\n        pass\n", False),
    ("with detection_pass():\n    pass\n", False),
    ("class Outer:\n    class Inner:\n        def m(self):\n            with detection_pass():\n                pass\n", False),
], ids=[
    "with by name", "with by attribute beside another item", "with in a method, nested in a branch",
    "ExitStack.enter_context", "manual __enter__", "assigned result", "rebound and entered", "returned",
    "passed as an argument", "result passed as an argument", "aliased name in a with", "with at module level",
    "with in a nested class's method",
])
def test_a_pass_is_entered_only_as_a_with_item(source, honest):
    """Rows for `_pass_entry_problems`: a `with` item calling the pass by
    name or attribute, in a top-level function or a top-level class's method
    at any depth, is an entry. Every other way of entering or handing on the
    pass is a problem, and so is a `with` the census does not read as an
    opening (an aliased name, module level, a nested class)."""
    import ast

    entries, problems = _pass_entry_problems({"m.py": ast.parse(source)})
    assert entries or problems  # control: the pass was seen
    assert (problems == []) is honest, problems


def route_inputs() -> dict:
    """{condition: sorted sites} for every input the detection path reads, from
    `_detect_project_identity_with_legacy`, the pass it runs inside
    (`PASS_SETUPS`: `detection_pass`, its join branch included, and
    `git_results_pass`) and every place a memory script or a hook opens that
    pass (`_pass_openings`: the `with` statements of `cli.main`,
    `PACTMemory.__init__` and `backlog.main`, with the blocks they run, the
    memory CLI's command handler read as every command `_COMMANDS` holds,
    and every method of a class one of them builds), through every function
    they call in the project's own modules (the memory scripts and
    `hooks/`, a `scripts.<module>` import included), helpers and re-exports
    included, and the `__init__` of every `plain_class` a reached function
    builds, and the code that runs when the modules it reaches
    are imported (`_import_time_code`: module statements and class bodies,
    with decorators, defaults and lambda bodies). The modules read at import are every
    module holding a reached function, every project module one of them
    imports (at module level, or inside a reached function), transitively,
    and the package of each dotted prefix of every such module's name; a
    project call in import-time code is followed like any other, to a fixed
    point.

    A condition is `("env", KEY)` for an environment read whose variable is a
    constant, a module constant, or a parameter resolved from the constant
    arguments at its call sites; `("cwd",)` and `("home",)` for the process
    and home directories; and `("call", NAME)` for every call from the two
    detection modules into another project module, NAME as spelled at the
    call. Every occurrence of the environment is read or reported
    (`_env_reads`): one the census cannot read is `("env", "<...>")` naming
    the function and the expression. Every call through one of the project's
    own names that does not resolve to a function (`_DetectionGraph.unread_call`)
    is `("call", "<call to no function ...>")`, and every use of a
    module-level name of another project module that is not a function (a
    constant, a class, an object) is `("call", "<name imported from a
    project module ...>")`, marked when the use is an annotation. Every
    class statement, in a function or at import, whose base, keyword value
    (`metaclass=` included) or decorator is reached through a project name
    (any class the same module defines, nested ones included), through a
    starred, subscripted, attribute or call expression too, and does not
    resolve to a project
    function (`_DetectionGraph.class_statement_name`) is `("call", "<class
    statement on a project name ...>")`, since defining the class runs that
    object's code (a base's `__init_subclass__` or `__class_getitem__`, a
    metaclass, a class decorator) with no call node. Every use of a class
    the same module defines, at any depth (`_class_names`, keyed by
    qualified name such as `Outer.Inner`), other than as the callee of a
    call (`_own_class_values`: handed to `type` or `types.new_class`,
    tested with `isinstance`, an attribute read from it, or anywhere inside
    a class base, keyword or decorator expression) is `("call", "<class of
    its own module used as a value ...>")`, marked when the use is an
    annotation, as another module's classes are; a name two classes share
    is reported for both.
    Nothing classifies any of these unless NOT_A_ROUTE names it, so the
    census fails closed. A project
    function used as a value (passed, stored, returned) counts as a call
    where it is named, so the function it reaches is read even when the call
    is elsewhere.

    READ, and so not reported:
    - an environment read with a constant key, and an environment alias
      binding, whose every use is itself an anchor; an `is` test on the
      environment, which reads no variable (the other operand is followed);
    - a direct call to a project function or method that resolves to its
      `def`, which is then read in turn;
    - a method called directly on a module-level literal table
      (`_DetectionGraph._is_data`: every binding of the name is accepted by
      `ast.literal_eval`);
    - a method on a local whose every binding, in every form
      (`_DetectionGraph._bindings`), is a parameter or a value proved to be
      a standard-library one (`_DetectionGraph._foreign_value`, which counts
      a project function's result only when it `returns_foreign`, or, for a
      function in `_DetectionGraph._RETURNS_ITS_ARGUMENT`, when its callable
      argument is a parameterless lambda whose body is proved or a function
      passed by name whose returns are (`_named_runner_result`)).
    REPORTED, as a condition NOT_A_ROUTE must name with its reason: every
    other call through a project name. That is every build of a project
    class (a nested one included), in a function or at import, whatever
    the class defines, a `plain_class` build included although its
    `__init__` is read; a call in a pass opening of a local no table
    resolves (`_table_callees`); a method
    of any other project object (a compiled pattern, a logger, a context
    variable, an instance); a method on any `self` attribute; and a method
    on a local holding anything else; every class statement on a project
    name; and every use of a class the same module defines, at any depth,
    as a value. A method called on a build of a `plain_class`, on a local
    every binding of which is such a build (`memory = PACTMemory();
    memory.save(...)`), or on a parameter of a top-level function every
    call of which passes such a value (`_DetectionGraph.parameter_class`:
    `cli._project_candidates(memory, db_path)`), is followed into the class
    (`_DetectionGraph.instance_target`); no other method is.

    The process and home directories are each ONE condition, `("cwd",)` and
    `("home",)`, whichever function reads them, so a new reader of either is
    absorbed by the condition already there: a new rung keyed on the process
    directory is the rung-parity census's to catch, and a new setting read
    from the home directory is not caught here.

    NOT COVERED: a call through `getattr`, `exec` or `importlib`; modules
    outside the plugin (the standard library, third-party packages); an
    environment read reached only through such a call; a read through a
    C extension (`ctypes`), which no source anchor can see; a method called
    on a parameter `parameter_class` cannot type (a method's parameter, or
    one a caller passes anything else to), or on a local proved a standard-library
    value only by taking a parameter as one, whose type the census cannot
    see; a class statement whose base, keyword value or decorator is a
    local or a parameter; a plain attribute read, `with`, `for` or
    formatting of an object a project function returned, which runs no code
    the census sees; and the functions a hook's pass block calls
    (`backlog.main`'s `_main`, the backlog command's own work), whose block
    is read but not followed.
    Each such object is built somewhere, and every project class build is
    reported, so its origin is a named condition. A condition names a
    callee, not a site: a further call of an already named callee is
    absorbed by that name.

    ADVERSARIAL-ONLY, and so not reported: a context variable read through
    `contextvars.copy_context()` (`.get(...)` or a subscript) in the module
    that defines it. Honest code reads one with its own `.get()`, which is
    reported (`test_a_context_read_through_copy_context_is_listed_not_reported`).
    """
    import ast

    from fixtures.identity_git import qualified_name

    graph = _DetectionGraph()
    for module in (*DETECTION_MODULES, *(module for module, _name in PASS_SETUPS)):
        graph.load(module)
    openings = _pass_openings(graph)
    reached, pending, outward = set(), [DETECTION_ENTRY, *PASS_SETUPS, *openings], {}
    # A class a pass opening builds runs its code with no call node the walk sees
    # (`__init__`, `__enter__`, `__exit__`, a logging callback): read every method.
    pending += [method for methods in graph.opening_classes.values() for method in methods]
    imported, importing = set(), []

    def read_module(module):
        """Queue `module`, and the package of each dotted prefix of its name,
        for its import-time code: importing `a.b.c` runs `a`, `a.b` and
        `a.b.c`."""
        parts = module.split(".")
        for end in range(1, len(parts) + 1):
            name = ".".join(parts[:end])
            if name not in imported and graph.load(name):
                imported.add(name)
                importing.append(name)

    def imports_in(module, scope):
        """The project modules an import statement in `scope` names: the
        module itself, and a submodule a `from` import names."""
        path = graph._files[module]
        package = module.split(".")[:-1] if path.name != "__init__.py" else module.split(".")
        named = set()
        for node in ast.walk(scope):
            if isinstance(node, ast.Import):
                named.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.level - 1 > len(package):
                        continue
                    base = package[: len(package) - (node.level - 1)]
                    owner = ".".join([*base, *([node.module] if node.module else [])])
                else:
                    owner = _script_module(node.module or "")
                named.add(owner)
                named.update(f"{owner}.{alias.name}" if owner else alias.name for alias in node.names)
        return {name for name in named if name and graph._path(name) is not None}

    def follow(module, scope):
        for expression, target in graph.references(scope, module):
            if module in DETECTION_MODULES and target[0] not in DETECTION_MODULES:
                spelled = (qualified_name(expression, graph.aliases[module]) or "").rpartition(".")[2]
                outward.setdefault(("call", spelled), set()).add(f"{module}.py:{expression.lineno}")
            pending.append(target)
        for name in imports_in(module, scope):
            read_module(name)

    while pending or importing:
        if importing:
            module = importing.pop()
            follow(module, graph.import_time[module])
            continue
        key = pending.pop()
        if key in reached:
            continue
        reached.add(key)
        if key not in openings:  # an opening is part of its function, not its module's import
            read_module(key[0])
        follow(key[0], openings.get(key, graph.defs[key]))  # a hook's pass block is read, not followed

    def params(node):
        return [arg.arg for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]

    found, parameter_keys = {}, {}

    def add(condition, site, key):
        found.setdefault(condition, set()).add(site)

    scanned = [(key, graph.defs[key]) for key in sorted(reached)] + [
        ((module, "<import>"), graph.import_time[module]) for module in sorted(imported)
    ]
    for key, node in scanned:
        module = key[0]
        names = graph.aliases[module]
        site = f"{module}.py"
        callees = {id(sub.func) for sub in ast.walk(node) if isinstance(sub, ast.Call)}
        annotations = {
            id(inner) for sub in ast.walk(node) for field in ("annotation", "returns")
            if isinstance(getattr(sub, field, None), ast.AST)
            for inner in ast.walk(getattr(sub, field))
        }
        for shown, line in _own_class_values(graph, module, node, callees, annotations):
            add(("call", f"<class of its own module used as a value {module}: {shown}>"), f"{site}:{line}", key)
        for sub in ast.walk(node):
            imported_name = graph.imported_name(sub, module) if id(sub) not in callees else None
            if imported_name is not None:
                place = " in an annotation" if id(sub) in annotations else ""
                add(("call", f"<name imported from a project module {module}: {imported_name}{place}>"),
                    f"{site}:{sub.lineno}", key)
            if isinstance(sub, ast.Name) and sub.id in graph.unresolved.get(module, ()):
                add(("call", f"<unresolved relative import {module}: {sub.id}>"),
                    f"{site}:{sub.lineno}", key)
            elif isinstance(sub, ast.Call) and graph.unread_call(sub, module, node):
                producer = graph.producer_of(sub, module, node)
                shown = f"{module}: {_callee_text(sub.func)}" if producer is None else (
                    f"{module}: a method of what {producer} returns"
                )
                add(("call", f"<call to no function {shown}>"), f"{site}:{sub.lineno}", key)
            if isinstance(sub, ast.ClassDef):
                for role, expression in _class_statement_names(sub):
                    if graph.class_statement_name(expression, module):
                        add(("call", f"<class statement on a project name {module}: "
                                     f"{sub.name} {role} {ast.unparse(expression)}>"),
                            f"{site}:{sub.lineno}", key)
        for read, shown in _env_reads(node, module, key[1], graph):
            if isinstance(read, ast.Constant) and isinstance(read.value, str):
                add(("env", read.value), f"{site}:{read.lineno}", key)
            elif isinstance(read, ast.Name) and read.id in graph.constants[module] and (
                key[1] == "<import>" or not graph._bindings(node, name=read.id)
            ):
                add(("env", graph.constants[module][read.id]), f"{site}:{read.lineno}", key)
            elif isinstance(read, ast.Name) and read.id in params(node):
                parameter_keys[key] = params(node).index(read.id)
            else:
                add(("env", f"<{module}.{key[1]}: {shown}>"), site, key)
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            called = qualified_name(sub.func, names) or ""
            if called in ("pathlib.Path.cwd", "Path.cwd", "os.getcwd"):
                add(("cwd",), f"{site}:{sub.lineno}", key)
            elif called in ("pathlib.Path.home", "Path.home", "os.path.expanduser"):
                add(("home",), f"{site}:{sub.lineno}", key)
    for key, scope in scanned:
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            target = graph.target(node, key[0])
            if target not in parameter_keys:
                continue
            position = parameter_keys[target]
            argument = node.args[position] if len(node.args) > position else None
            constants = graph.constants[key[0]]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                add(("env", argument.value), f"{key[0]}.py:{node.lineno}", target)
            elif isinstance(argument, ast.Name) and argument.id in constants and (
                key[1] == "<import>" or not graph._bindings(scope, name=argument.id)
            ):
                add(("env", constants[argument.id]), f"{key[0]}.py:{node.lineno}", target)
            else:
                add(("env", f"<{target[0]}.{target[1]}: argument at {key[0]}.py:{node.lineno}>"),
                    f"{key[0]}.py:{node.lineno}", target)
    for condition, site in _unresolved_opening_locals(graph):
        add(condition, site, None)
    for condition, sites in outward.items():
        found.setdefault(condition, set()).update(sites)
    return {condition: sorted(sites) for condition, sites in found.items()}


def _source_fingerprint() -> tuple:
    """The roots `route_inputs` reads, with every `.py` file under them and
    its content digest, so a changed, added or removed file, or another
    root, gives another fingerprint."""
    import hashlib

    return tuple(_PROJECT_ROOTS) + tuple(
        (str(path), hashlib.sha256(path.read_bytes()).hexdigest())
        for root in _PROJECT_ROOTS for path in sorted(Path(root).rglob("*.py"))
    )


@functools.lru_cache(maxsize=4)
def _route_inputs_for(fingerprint: tuple) -> dict:
    """`route_inputs` for the tree `fingerprint` names."""
    return route_inputs()


def route_entry_conditions() -> dict:
    """{condition: sorted sites}: `route_inputs`, computed once per source
    tree (the import closure it reads makes it slow); a changed tree
    computes afresh."""
    return _route_inputs_for(_source_fingerprint())


class _RecordingMonkeyPatch(pytest.MonkeyPatch):
    """A MonkeyPatch that also records which inputs a route sets."""

    def __init__(self):
        super().__init__()
        self.driven = set()

    def setenv(self, name, value, prepend=None):
        self.driven.add(("env", name))
        super().setenv(name, value, prepend)

    def setattr(self, target, *args, **kwargs):
        # `setattr("module.name", value)` or `setattr(obj, "name", value)`.
        if isinstance(target, str) and len(args) == 1:
            name = target.rpartition(".")[2]
        else:
            name = args[0]
        self.driven.add(("call", name))
        super().setattr(target, *args, **kwargs)

    def chdir(self, path):
        self.driven.add(("cwd",))
        super().chdir(path)


@pytest.fixture(scope="module")
def route_driven_conditions(tmp_path_factory):
    """{condition: routes that set it}, measured by running every route."""
    from fixtures import identity_routes as routes

    driven = {}
    for route, (run, _sources) in routes.ROUTES.items():
        base = tmp_path_factory.mktemp("route_inputs")
        target = routes.SHAPES["control_own_checkout"](base)
        elsewhere = base / "elsewhere"
        elsewhere.mkdir()
        mp = _RecordingMonkeyPatch()
        try:
            routes._clear(mp)
            mp.driven.clear()
            run(mp, target, elsewhere)
        finally:
            mp.undo()
        for condition in mp.driven:
            driven.setdefault(condition, set()).add(route)
    return driven


def test_a_result_passing_function_returns_its_run_result():
    """Pin for `_DetectionGraph._RETURNS_ITS_ARGUMENT`: the census types a
    call of each listed function as its callable argument's result, so each
    must return exactly what that argument returns, or raise what it raises,
    on every path. For `answered_once`: outside a pass and on the first ask
    inside one, it returns the very object `run` returned. A later ask of the
    same question in the pass gets the first asker's object, and its own
    runner never runs; a raise is the same exception again. That is sound
    because each question has one runner (`test_each_git_question_has_one_runner`).
    The argument sits where the table says, by position and by keyword. A
    new entry fails here until it has a pin of its own."""
    import inspect

    from shared import git_helpers

    assert _DetectionGraph._RETURNS_ITS_ARGUMENT == {("shared.git_helpers", "answered_once"): (1, "run")}
    position, keyword = _DetectionGraph._RETURNS_ITS_ARGUMENT[("shared.git_helpers", "answered_once")]
    assert list(inspect.signature(git_helpers.answered_once).parameters)[position] == keyword

    made = []

    def run():
        made.append(object())
        return made[-1]

    def raising():
        made.append(OSError("could not run"))
        raise made[-1]

    def raised(call):
        try:
            call()
        except OSError as error:
            return error
        return None

    argv, env = ["git", "rev-parse", "--git-dir"], {"PATH": "/usr/bin"}
    outside = git_helpers.answered_once(argv, run, text=False, env=env)
    assert outside is made[-1]
    with git_helpers.git_results_pass():
        first = git_helpers.answered_once(argv, run, text=False, env=env)
        assert first is made[-1]
        again = git_helpers.answered_once(argv, run=lambda: made.append(object()) or made[-1], text=False, env=env)
        failing = ["git", "rev-parse", "--absolute-git-dir"]
        errors = [raised(lambda: git_helpers.answered_once(failing, raising, text=False, env=env))
                  for _ in range(2)]
    assert (again is first, len(made), errors[0] is made[-1], errors[1] is made[-1]) == (True, 3, True, True)


def _parse_files(paths, root) -> dict:
    """{path relative to `root`: parsed module} for each file at `paths`. A
    file's path is its identity: `hooks/__init__.py`, `hooks/shared/__init__.py`
    and the memory scripts' `__init__.py` share a basename, so a map keyed by
    the basename would keep one and drop the others unread."""
    import ast

    return {path.relative_to(root).as_posix(): ast.parse(path.read_text(encoding="utf-8")) for path in paths}


def test_files_that_share_a_basename_are_each_read(tmp_path):
    """Row for `_parse_files`: two files named `m.py` in different
    directories, each holding a runner that is not its question's own
    command, are both parsed and both reported, so neither hides the other."""
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "m.py").write_text(
            "import subprocess\nfrom shared.git_helpers import answered_once\n\n"
            "def reader(argv):\n"
            "    return answered_once(argv, lambda: subprocess.run(list(argv), capture_output=True,"
            " text=False, timeout=5, env=None), text=False, env=None)\n",
            encoding="utf-8",
        )
    trees = _parse_files(sorted(tmp_path.rglob("m.py")), tmp_path)
    sites, problems, _timeouts, _forms = _one_runner_problems(trees)
    assert sorted(trees) == ["a/m.py", "b/m.py"] and len(sites) == 2
    assert [problem.partition(":")[0] for problem in problems] == ["a/m.py", "b/m.py"], problems


_GIT_OPTIONS_WITH_A_VALUE = frozenset({"-C", "--git-dir", "--work-tree", "-c"})


def _question_signature(argv, function):
    """The literal git words of `argv` after its leading option pairs (`-C
    <dir>`, `--git-dir <dir>`, `-c <name=value>`), up to the first word that
    is not a string constant: `("rev-parse", "--git-dir")` for `["git", "-C",
    str(root), "rev-parse", "--git-dir"]`. Two calls whose signatures differ
    ask different questions. None when the census cannot tell: an argv
    that is not a list display, or a local of `function` bound other than
    once to one."""
    import ast

    if isinstance(argv, ast.Name) and function is not None:
        found = _DetectionGraph._bindings(function, name=argv.id)
        argv = found[0][1] if len(found) == 1 and found[0][0] == "value" else None
    if not isinstance(argv, ast.List):
        return None
    words = list(argv.elts)
    if words and isinstance(words[0], ast.Constant) and words[0].value == "git":
        words = words[1:]
    while len(words) >= 2 and isinstance(words[0], ast.Constant) and words[0].value in _GIT_OPTIONS_WITH_A_VALUE:
        words = words[2:]
    while words and isinstance(words[0], ast.Constant) and isinstance(words[0].value, str) and (
            words[0].value.startswith("--git-dir=")):
        words = words[1:]
    signature = []
    for word in words:
        if not (isinstance(word, ast.Constant) and isinstance(word.value, str)):
            break
        signature.append(word.value)
    return tuple(signature) or None


def _module_number(tree, name):
    """The int or float every module-level binding of `name` in `tree` assigns,
    or None when it is bound any other way or to different values."""
    import ast

    values = _module_bindings(tree).get(name, [])
    numbers = {value.value for value in values if isinstance(value, ast.Constant)
               and isinstance(value.value, (int, float)) and not isinstance(value.value, bool)}
    return numbers.pop() if values and len(numbers) == 1 and all(
        isinstance(value, ast.Constant) for value in values) else None


def _one_runner_problems(trees: dict) -> tuple:
    """(sites, problems, timeouts, forms) for every call of `answered_once`
    in `trees` ({file path: parsed module}). A call's runner must be
    `subprocess.run(<the argv it is keyed by>, capture_output=True,
    text=<its text>, timeout=<a timeout>, env=<its env>)`, read from a
    parameterless lambda or a nested def that returns it, with any further
    keyword arguments (`stdin=subprocess.DEVNULL`, `check=False`); a runner
    that passes `cwd=` is valid only when that same call passes
    `answered_once` the same `cwd=`, and a runner with no `cwd=` must not
    pass one. Each argument is compared as written, so an argv, text, env
    or cwd the runner reaches by another expression fails, even when it
    holds the same value. A timeout is a number, a module-level name bound
    only to one number, or a parameter of the enclosing def resolved at each
    caller that asks once per pass (`run_git`'s).

    The sites of one question must then run it the same way: the same
    further keyword arguments, as written, and the same timeout. Two sites
    ask different questions when their `_question_signature`s differ; a
    site whose signature the census cannot tell is compared with every
    other site, so it fails closed."""
    import ast

    def called(func):
        return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None

    def is_def(node):
        return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))

    sites, problems, timeouts, forms, asked = [], [], set(), set(), []
    for path, tree in trees.items():
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and called(node.func) == "answered_once"]
        heads = {call.func for call in calls}
        for node in ast.walk(tree):
            named = (isinstance(node, ast.Name) and node.id == "answered_once"
                     or isinstance(node, ast.Attribute) and node.attr == "answered_once")
            if named and node not in heads or isinstance(node, ast.alias) and node.name == "answered_once" and node.asname:
                problems.append(f"{path}:{getattr(node, 'lineno', '?')}: answered_once used but not called")
        for call in calls:
            sites.append(f"{path}:{call.lineno}")
            function = parents.get(call)
            while function is not None and not is_def(function):
                function = parents.get(function)
            given = {keyword.arg: keyword.value for keyword in call.keywords}
            argv = call.args[0] if call.args else given.get("argv")
            run = call.args[1] if len(call.args) > 1 else given.get("run")
            runner = None
            if isinstance(run, ast.Lambda):
                arguments = run.args
                if not (arguments.posonlyargs or arguments.args or arguments.kwonlyargs
                        or arguments.vararg or arguments.kwarg):
                    runner, form = run.body, "lambda"
            elif isinstance(run, ast.Name) and function is not None:
                found = [node for node in ast.walk(function) if is_def(node) and node.name == run.id]
                body = [statement for statement in found[0].body if not (
                    isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str))] if len(found) == 1 else []
                arguments = found[0].args if found else None
                if (len(body) == 1 and isinstance(body[0], ast.Return) and isinstance(found[0], ast.FunctionDef)
                        and not found[0].decorator_list
                        and not (arguments.posonlyargs or arguments.args or arguments.kwonlyargs
                                 or arguments.vararg or arguments.kwarg)):
                    runner, form = body[0].value, "nested def"
            keywords = {keyword.arg: keyword.value for keyword in getattr(runner, "keywords", [])}
            if not (isinstance(runner, ast.Call) and ast.unparse(runner.func) == "subprocess.run"
                    and len(runner.args) == 1 and not isinstance(runner.args[0], ast.Starred)
                    and len(keywords) == len(runner.keywords)
                    and {"capture_output", "text", "timeout", "env"} <= set(keywords)
                    and isinstance(keywords["capture_output"], ast.Constant)
                    and keywords["capture_output"].value is True
                    and argv is not None and "text" in given and "env" in given
                    and ast.unparse(runner.args[0]) == ast.unparse(argv)
                    and ast.unparse(keywords["text"]) == ast.unparse(given["text"])
                    and ast.unparse(keywords["env"]) == ast.unparse(given["env"])
                    and ("cwd" in keywords) == ("cwd" in given)
                    and ("cwd" not in keywords or ast.unparse(keywords["cwd"]) == ast.unparse(given["cwd"]))):
                problems.append(f"{path}:{call.lineno}: the runner is not the question's own git command")
                continue
            forms.add(form)
            timeout = keywords["timeout"]
            extras = tuple(sorted(
                (name, ast.unparse(value)) for name, value in keywords.items()
                if name not in ("capture_output", "text", "timeout", "env", "cwd")
            ))
            signature = _question_signature(argv, function)
            parameters = [*function.args.posonlyargs, *function.args.args] if function is not None else []
            number = _module_number(tree, timeout.id) if isinstance(timeout, ast.Name) and timeout.id not in [
                parameter.arg for parameter in parameters] else None
            if isinstance(timeout, ast.Constant) or number is not None:
                value = timeout.value if isinstance(timeout, ast.Constant) else number
                timeouts.add(value)
                asked.append((f"{path}:{call.lineno}", signature, extras, value))
            elif isinstance(timeout, ast.Name) and timeout.id in [parameter.arg for parameter in parameters]:
                # A parameter: the timeout each caller that asks once per pass gives.
                index = [parameter.arg for parameter in parameters].index(timeout.id)
                defaults = function.args.defaults
                default = defaults[index - (len(parameters) - len(defaults))] if index >= len(parameters) - len(defaults) else None
                for other in trees.values():
                    for caller in ast.walk(other):
                        if not (isinstance(caller, ast.Call) and called(caller.func) == function.name):
                            continue
                        flags = {keyword.arg: keyword.value for keyword in caller.keywords}
                        if "once_per_pass" not in flags:
                            continue
                        if not isinstance(flags["once_per_pass"], ast.Constant):
                            problems.append(f"{function.name} call at line {caller.lineno}: once_per_pass is not a constant")
                        elif flags["once_per_pass"].value is True:
                            value = flags.get(timeout.id, caller.args[index] if len(caller.args) > index else default)
                            if isinstance(value, ast.Constant):
                                timeouts.add(value.value)
                                words = caller.args[0] if caller.args else flags.get("args")
                                asked.append((
                                    f"{function.name} call at line {caller.lineno}",
                                    _question_signature(
                                        ast.List(elts=[ast.Constant("git"), *words.elts]) if isinstance(words, ast.List)
                                        else None, None),
                                    extras, value.value,
                                ))
                            else:
                                problems.append(f"{function.name} call at line {caller.lineno}: timeout is not a constant")
            else:
                problems.append(f"{path}:{call.lineno}: the timeout is neither a constant nor a parameter")
    known = sorted({signature for _site, signature, _extras, _timeout in asked if signature is not None})
    unknown = [entry for entry in asked if entry[1] is None]
    for group in [*([entry for entry in asked if entry[1] == signature] + unknown for signature in known), unknown]:
        ways = {(extras, timeout) for _site, _signature, extras, timeout in group}
        if len(ways) > 1:
            problems.append(
                f"{', '.join(site for site, *_rest in group)}: sites of one question run it different ways: "
                f"{sorted(ways)}"
            )
    return sites, sorted(set(problems), key=problems.index), timeouts, forms


def test_each_git_question_has_one_runner():
    """`answered_once` keys a question on its argv, `text`, `env` and `cwd`
    (`git_helpers._question`). Inside a pass, a later asker of the same
    question gets the first asker's result, and its own runner never runs.
    The census still types each call as its own runner's result
    (`_DetectionGraph._RETURNS_ITS_ARGUMENT`), and that is sound because
    each question has one runner: every call site's runner is the question's
    own git command (`_one_runner_problems`), and the sites of one question
    carry the same further keyword arguments and the same timeout, so two
    sites that ask one question run one command the same way.
    The argv is matched as an expression, not resolved to its constant parts:
    the runner must pass the very expression the call is keyed on, a rule
    that is decidable and fails closed on any other spelling of the same
    value. A runner of any other shape, two sites of one question run
    differently (a `run_git` caller's timeout included), an alias of
    `answered_once` or any use of it but a call fails here."""
    files = sorted({*SCRIPTS.glob("*.py"), *(PLUGIN_ROOT / "hooks").rglob("*.py")})
    sites, problems, timeouts, forms = _one_runner_problems(_parse_files(files, PLUGIN_ROOT))
    assert problems == [], problems
    assert sites and timeouts and forms == {"lambda", "nested def"}  # control: both runner forms were read


_ASK = "answered_once(argv, {run}, text=False, env=None{cwd})"
_GIT = "subprocess.run({argv}, capture_output=True, text=False, timeout=5, env=None{cwd})"


@pytest.mark.parametrize("body, honest", [
    (f"    argv = ['git', 'rev-parse']\n    return {_ASK.format(run='lambda: ' + _GIT.format(argv='argv', cwd=''), cwd='')}\n",
     True),
    (f"    argv = ['git', 'rev-parse']\n    def run():\n        return {_GIT.format(argv='argv', cwd='')}\n"
     f"    return {_ASK.format(run='run', cwd='')}\n", True),
    (f"    argv = ['git', 'rev-parse']\n"
     f"    return {_ASK.format(run='lambda: ' + _GIT.format(argv='argv', cwd=', cwd=where'), cwd=', cwd=where')}\n",
     True),
    (f"    argv = ['git', 'rev-parse']\n"
     f"    return {_ASK.format(run='lambda: ' + _GIT.format(argv='argv', cwd=', cwd=where'), cwd='')}\n", False),
    (f"    argv = ['git', 'rev-parse']\n"
     f"    return {_ASK.format(run='lambda: ' + _GIT.format(argv='argv', cwd=''), cwd=', cwd=where')}\n", False),
    (f"    argv = ['git', 'rev-parse']\n"
     f"    return {_ASK.format(run='lambda: ' + _GIT.format(argv='argv', cwd=', cwd=where'), cwd=', cwd=other')}\n",
     False),
    (f"    argv = ['git', 'rev-parse']\n    copied = argv\n"
     f"    return {_ASK.format(run='lambda: ' + _GIT.format(argv='copied', cwd=''), cwd='')}\n", False),
    (f"    argv = ['git', 'rev-parse']\n"
     f"    return {_ASK.format(run='lambda: ' + _GIT.format(argv='list(argv)', cwd=''), cwd='')}\n", False),
], ids=[
    "argv in a variable", "nested def", "same cwd on both", "cwd on the runner only", "cwd on the call only",
    "two different cwds", "argv the runner reads through a copy", "argv the runner rebuilds",
])
def test_a_runner_is_its_questions_own_command(body, honest):
    """Rows for `_one_runner_problems`: an argv built in a variable and passed
    to both is the question's own command, and so is a runner that passes
    the same `cwd=` the call passes. A runner whose `cwd=` the call does not
    pass, a call whose `cwd=` the runner does not pass, two different cwds,
    and an argv the runner reaches by another expression each fail; an argv
    the census cannot show to be the one keyed fails closed."""
    import ast

    source = ("import subprocess\nfrom shared.git_helpers import answered_once\n\n"
              "def reader(where, other):\n" + body)
    sites, problems, _timeouts, _forms = _one_runner_problems({"m.py": ast.parse(source)})
    assert len(sites) == 1  # control: the call was read
    assert (problems == []) is honest, problems


def _two_askers(first, second, prelude=""):
    """A module with two readers, each asking `answered_once` its own argv
    (`first` and `second` are (argv display, extra runner keywords,
    timeout))."""
    def reader(name, argv, extra, timeout):
        return (f"def {name}():\n    argv = {argv}\n"
                f"    return answered_once(argv, lambda: subprocess.run(argv, capture_output=True, text=False, "
                f"timeout={timeout}, env=None{extra}), text=False, env=None)\n\n")
    return ("import subprocess\nfrom shared.git_helpers import answered_once\n\n" + prelude
            + reader("first", *first) + reader("second", *second))


_GIT_DIR = "['git', 'rev-parse', '--git-dir']"
_WORKTREE = "['git', 'config', '--get', 'core.worktree']"


@pytest.mark.parametrize("source, honest", [
    (_two_askers((_GIT_DIR, ", stdin=subprocess.DEVNULL", 5), (_GIT_DIR, ", stdin=subprocess.DEVNULL", 5)), True),
    (_two_askers((_GIT_DIR, ", check=False", 5), (_GIT_DIR, ", check=False", 5)), True),
    (_two_askers((_GIT_DIR, "", "_TIMEOUT"), (_GIT_DIR, "", 5), "_TIMEOUT = 5\n\n"), True),
    (_two_askers((_GIT_DIR, "", 5), (_WORKTREE, "", 10)), True),
    (_two_askers(("['git', '-C', str(1), 'rev-parse', '--git-dir']", "", 5),
                 ("['git', '-C', str(1), 'config', '--get', 'core.worktree']", "", 10)), True),
    (_two_askers((_GIT_DIR, ", stdin=subprocess.DEVNULL", 5), (_GIT_DIR, "", 5)), False),
    (_two_askers((_GIT_DIR, "", 5), (_GIT_DIR, "", 10)), False),
    (_two_askers((_GIT_DIR, "", 5), ("list(_GIT_DIR_ARGV)", "", 10), "_GIT_DIR_ARGV = ['git']\n\n"), False),
    (_two_askers((_GIT_DIR, "", "_TIMEOUT"), (_GIT_DIR, "", 5), "_TIMEOUT = 5\nif True:\n    _TIMEOUT = 10\n\n"),
     False),
], ids=[
    "stdin on every site of one question", "check=False on every site of one question",
    "a module-constant timeout", "two questions with two timeouts", "two questions behind one -C",
    "stdin on one site of a question", "two timeouts for one question",
    "a site whose question cannot be told, with another timeout", "a name bound to two timeouts",
])
def test_runners_are_compared_per_question(source, honest):
    """Rows for `_one_runner_problems`' per-question comparison: further
    keyword arguments every site of one question carries, a module-level
    timeout constant, and two different questions with two timeouts are
    honest. A keyword one site of a question carries and another does not,
    two timeouts for one question, a site whose question the census cannot
    tell (compared with every other site) with another timeout, and a
    timeout name bound to two values each fail."""
    import ast

    sites, problems, _timeouts, _forms = _one_runner_problems({"m.py": ast.parse(source)})
    assert len(sites) == 2  # control: both calls were read
    assert (problems == []) is honest, problems


_RUN = "subprocess.run(['git', 'rev-parse'], capture_output=True)"


@pytest.mark.parametrize("body, typed", [
    (f"    return answered_once(['git'], lambda: {_RUN}, text=True, env=None)\n", True),
    (f"    def run():\n        return {_RUN}\n    return answered_once(['git'], run, text=True, env=None)\n", True),
    (f"    def run():\n        \"\"\"The command.\"\"\"\n        return {_RUN}\n"
     "    return answered_once(['git'], run=run, text=True, env=None)\n", True),
    (f"    return answered_once(['git'], _module_run, text=True, env=None)\n", True),
    (f"    def run(flag):\n        return {_RUN}\n    return answered_once(['git'], run, text=True, env=None)\n", False),
    (f"    @functools.cache\n    def run():\n        return {_RUN}\n"
     "    return answered_once(['git'], run, text=True, env=None)\n", False),
    (f"    def run():\n        done = {_RUN}\n        return done\n"
     "    return answered_once(['git'], run, text=True, env=None)\n", False),
    ("    def run():\n        return Thing()\n    return answered_once(['git'], run, text=True, env=None)\n", False),
    (f"    def run():\n        return {_RUN}\n    run = _module_run\n"
     "    return answered_once(['git'], run, text=True, env=None)\n", False),
    ("    return answered_once(['git'], _module_run_with_flag, text=True, env=None)\n", False),
    ("    return answered_once(['git'], _module_build, text=True, env=None)\n", False),
    ("    return answered_once(['git'], missing, text=True, env=None)\n", False),
    (f"    return answered_once(['git'], lambda c=1: {_RUN}, text=True, env=None)\n", False),
    ("    return answered_once(['git'], functools.partial(subprocess.run, ['git']), text=True, env=None)\n", False),
], ids=[
    "lambda", "nested def", "nested def with a docstring, by keyword", "module function",
    "nested def with a parameter", "decorated nested def", "nested def binding a local",
    "nested def returning a project build", "nested def rebound", "module function with a parameter",
    "module function returning a project build",
    "unbound name", "lambda with a default", "functools.partial",
])
def test_a_named_runner_is_read_through_its_returns(tmp_path, monkeypatch, body, typed):
    """`_DetectionGraph._argument_result` types a call of `answered_once` as
    its runner's result when the runner is a parameterless lambda or a
    function passed by name whose returns are proved (`git_helpers.run_git`
    passes its nested `run`); a runner the census cannot prove (a parameter,
    a decorator, a local binding, a project build, another binding of the
    name, an unbound name) is not typed, so the census reports the value."""
    import ast
    import sys

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "m.py").write_text(
        "import functools\nimport subprocess\nfrom shared.git_helpers import answered_once\n\n"
        "class Thing:\n    pass\n\n"
        f"def _module_run():\n    return {_RUN}\n\n"
        f"def _module_run_with_flag(flag):\n    return {_RUN}\n\n"
        "def _module_build():\n    return Thing()\n\n"
        "def reader():\n" + body
    )
    monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path, PLUGIN_ROOT / "hooks"))
    graph = _DetectionGraph()
    assert graph.load("pkg.m")
    reader = graph.defs[("pkg.m", "reader")]
    call = next(
        node for node in ast.walk(reader)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "answered_once"
    )
    assert graph.target(call, "pkg.m") == ("shared.git_helpers", "answered_once")  # control: the rule's target
    assert graph._foreign_value(call, "pkg.m", reader, frozenset()) is typed


def test_a_context_read_through_copy_context_is_listed_not_reported(tmp_path, monkeypatch):
    """Pin for `route_inputs`' ADVERSARIAL-ONLY list: a context variable read
    through `contextvars.copy_context()`, by `.get(...)` or a subscript, in
    the module that defines it, is not reported, and the list names it. The
    honest read, the variable's own `.get()`, is reported (control). If the
    census comes to report the listed forms, or the list drops them, this
    fails."""
    import ast
    import sys

    listed = _paragraph(" ".join(route_inputs.__doc__.split()), "ADVERSARIAL-ONLY, and so not reported")
    assert "`contextvars.copy_context()`" in listed, listed
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "m.py").write_text(
        "import contextvars\n\nVAR = contextvars.ContextVar('v', default=None)\n\n"
        "def honest():\n    return VAR.get()\n\n"
        "def by_get():\n    return contextvars.copy_context().get(VAR)\n\n"
        "def by_subscript():\n    return contextvars.copy_context()[VAR]\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path,))
    graph = _DetectionGraph()
    assert graph.load("pkg.m")

    def reported(name):
        function = graph.defs[("pkg.m", name)]
        return [ast.unparse(call) for call in ast.walk(function)
                if isinstance(call, ast.Call) and graph.unread_call(call, "pkg.m", function)]

    assert (reported("honest"), reported("by_get"), reported("by_subscript")) == (["VAR.get()"], [], [])


_GIT_RUNNERS = frozenset({"answered_once", "run_git"})
_SUBPROCESS_RUNNERS = frozenset({"subprocess.run", "subprocess.check_output", "subprocess.Popen", "subprocess.call"})


def _store_work_git_questions(graph) -> tuple:
    """(questions, reached) for the memory CLI's command work: `reached` is
    every def the census graph reaches from each PACTMemory method other than
    `__init__` and each `cli` command handler (`cmd_*`), loading modules as it
    goes; `questions` is each call there that runs git, as (module, def key,
    call): a call of `answered_once`, `run_git` or a subprocess runner."""
    import ast

    from fixtures.identity_git import qualified_name

    def calls(key):
        node = graph.defs[key]
        by_func = {id(call.func): call for call in ast.walk(node) if isinstance(call, ast.Call)}
        for func, target in graph.references(node, key[0]):
            if id(func) in by_func:
                yield by_func[id(func)], target

    def runs_git(call, module):
        name = qualified_name(call.func, graph.aliases.get(module, {})) or ast.unparse(call.func)
        return name.rpartition(".")[2] in _GIT_RUNNERS or name in _SUBPROCESS_RUNNERS

    roots = [key for key in list(graph.defs) if key[0] == "memory_api"
             and key[1].startswith("PACTMemory.") and key[1] != "PACTMemory.__init__"]
    roots += [key for key in list(graph.defs) if key[0] == "cli" and key[1].startswith("cmd_")]
    reached, pending = set(), list(roots)
    while pending:
        key = pending.pop()
        if key in reached or not graph.load(key[0]) or key not in graph.defs:
            continue
        reached.add(key)
        pending += [target for _call, target in calls(key)]
    questions = []
    for key in sorted(reached):
        for call in ast.walk(graph.defs[key]):
            if isinstance(call, ast.Call) and runs_git(call, key[0]):
                questions.append((key[0], key, call))
    return questions, reached


# How many calls a trace remembers having entered a function through: a
# parameter read inside an entered function holds that call's arguments, and
# beyond this depth it holds every argument any call passes there.
_CONTEXT_DEPTH = 4
# Methods that write into the object they are called on (a context variable's
# `set` included).
_CONTAINER_WRITERS = frozenset({
    "append", "extend", "insert", "add", "update", "setdefault", "appendleft", "extendleft", "__setitem__",
    "set",
})


def _value_sources(graph, module: str, function, expression) -> set:
    """{(module, call)} for every call whose result can reach `expression` in
    `function` (`_traced_sources`, from that one start)."""
    return set(_traced_sources(graph, [(module, function, expression)]))


def _traced_sources(graph, starts, unresolved=None, parents=None, barriers=frozenset(), crossings=None) -> dict:
    """{(module, call): (index of the start it was reached from, the
    trace state it was found in)} for every call whose result can reach one
    of `starts`, each a (module, def or None for module scope, expression);
    `parents` records, for each trace state, (the state it was reached
    from, its site), so a hit can be explained. A parameter of a def in
    `barriers` is not followed to its arguments: each (module, def, argument)
    and the barrier def's key is appended to `crossings` instead, as
    (module, def, argument, barrier), for the caller to trace on its own
    terms. Every (expression, context)
    is visited once, so the trace ends on any chain length and on cycles:
    there is no depth bound. From each expression it follows:
    - each call inside it, and the values that call returns: every `return`
      and `yield` of the project function it runs (`_DetectionGraph.callee`,
      a local looked up in a table, `_table_callees`, or a callable a local
      or parameter holds, `_callables`), entered through that call; for a
      `_RETURNS_ITS_ARGUMENT` function, its callable argument's body or
      returns; for a method on a value not proved a built-in or
      standard-library object (`_builtin_receiver`) that no call resolves,
      the returns of every loaded project method of that name
      (`_untyped_methods`), a name a built-in also defines (`get`,
      `update`) included. A call's receiver and arguments are part of the
      expression, so a standard-library call is followed through them;
    - each local it names: every binding visible there (`_visible_bindings`,
      a comprehension's variable only inside it) and every write into the
      object the local holds (`_writes_into`; only into the attribute read,
      for `x.attr` or a literal `getattr(x, "attr")`);
    - each parameter: inside a function entered through a call, that call's
      arguments (up to `_CONTEXT_DEPTH` calls deep); otherwise its default
      and every argument any call passes there, a starred or unpacked one
      included (`_arguments_for`); where the parameter is read as
      `p.attr` (or `getattr(p, "attr")`), only that attribute of each
      argument, read as `argument.attr`;
    - each attribute it reads on a local not proved a built-in or
      standard-library object (`_builtin_receiver`): every assignment of and write into an attribute of that name,
      on any receiver, in the loaded modules (`_attribute_values`), and the
      returns of every project method of that name (a property);
    - each module-level name it reads that is not a function or class:
      every module-level binding, and every write into it in the loaded
      modules (`_module_values`).
    Each place the trace cannot follow is appended to `unresolved` as
    (site, why, start index), and the pin fails on it: a call of a project
    object that is not a function, a call of a computed callee, a callable a
    local or parameter holds that is neither a lambda nor a project function
    (`_callables`), and a parameter of a function the loaded code uses as a
    value whose calls it cannot see (`_calls_of`).

    NOT COVERED: a parameter of a function no loaded code calls or uses as
    a value holds only what code outside the loaded modules passes.

    ADVERSARIAL-ONLY, and so not followed: a write into an object through
    `setattr`, `vars()`, `__dict__`, `globals()` or `locals()`; a
    standard-library function that writes into an object passed to it
    (`list.append(argv, value)`, `operator.setitem`); and a value carried
    out of a function by an exception it raises. Honest code writes with a
    method of the object or an item or attribute assignment, which is
    followed (`test_the_trace_lists_its_adversarial_only_writes_and_does_not_follow_them`).

    ADVERSARIAL-ONLY, and so read as a built-in object: a local an
    `isinstance` test has proved a built-in, rebound before the read only
    by a `:=` inside a comprehension or from a nested function
    (`_rebindings`); and a field filled by position in a call that reaches
    its class through a table or `getattr` (`_built_classes`). A rebinding
    in the function's own statements, and a build by the class's name, a
    local bound to it, or a method's `cls`, `type(self)` or
    `self.__class__`, are followed.

    LIMITS, by ruling: a method called on a value `_foreign` proves a
    standard-library one is read as the built-in only, so a project method
    of the same name that the value could still run is not read. Two such
    forms are named: a container an item of which holds a project object
    written into it (`table['k'] = memory`, then `table['k'].get(...)`),
    and the result of a non-project callable that hands back what it is
    given (`super().get(...)`, `next(iter(x))`, `getattr(o, n)`,
    `max(...)`, `copy.copy(x)`). The values themselves are still traced
    through the container and the call; only that method's own returns are
    not (`test_the_trace_names_its_limits`)."""
    import ast

    _memoize(graph)
    unresolved = [] if unresolved is None else unresolved
    parents = {} if parents is None else parents
    crossings = [] if crossings is None else crossings
    found, seen, synthetic = {}, set(), {}

    def narrowed(value, attr):
        # A parameter read only as `p.attr` (or `getattr(p, "attr")`) carries
        # that one attribute of its argument, not the whole object: the
        # argument is traced as `argument.attr`. One node per (argument,
        # attribute), kept for the whole trace, so a revisit is seen.
        if attr is None:
            return value
        key = (id(value), attr)
        if key not in synthetic:
            synthetic[key] = ast.copy_location(ast.Attribute(value=value, attr=attr, ctx=ast.Load()), value)
        return synthetic[key]

    pending = [(module, function, expression, (), index, None)
               for index, (module, function, expression) in enumerate(starts)]

    while pending:
        module, function, expression, context, origin, parent = pending.pop()
        state = (id(expression), context)
        if state in seen:
            continue
        seen.add(state)
        parents[state] = (parent, f"{module}.py:{getattr(expression, 'lineno', '?')}")
        problems, more = [], []

        def returns_of(target, call=None):
            # Entering a function through one call: its parameters, read in
            # there, hold that call's arguments (`_CONTEXT_DEPTH` frames kept).
            node = graph.defs[target]
            entered = (((id(node), call, module, function),) + context)[:_CONTEXT_DEPTH]
            return [(target[0], node, value, entered) for value in _returned_values(node)]

        def add(items):
            # A value read in this same function keeps this context; one read
            # in another function holds whatever any call passes there.
            for item in items:
                if len(item) == 4:
                    more.append(item)
                else:
                    owner, scope, value = item
                    more.append((owner, scope, value, context if scope is function else ()))

        # `obj.name` read as a value reads that one attribute; `obj.method(...)` reads
        # the object itself (`d.get(k)` returns what was written into `d`), so it is
        # not narrowed.
        called = {id(sub.func) for sub in ast.walk(expression) if isinstance(sub, ast.Call)}
        read_from = {id(sub.value): sub.attr for sub in ast.walk(expression)
                     if isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Load)
                     and isinstance(sub.value, ast.Name) and id(sub) not in called}
        # `getattr(obj, "name", ...)` reads that one attribute, like `obj.name`.
        read_from.update({id(sub.args[0]): sub.args[1].value for sub in ast.walk(expression)
                          if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "getattr"
                          and len(sub.args) >= 2 and isinstance(sub.args[0], ast.Name)
                          and isinstance(sub.args[1], ast.Constant) and isinstance(sub.args[1].value, str)})
        for sub in ast.walk(expression):
            if isinstance(sub, ast.Call):
                found.setdefault((module, sub), (origin, state))
                add(_call_results(graph, module, function, sub, problems, returns_of))
                if id(sub.args[0] if sub.args else None) in read_from and isinstance(sub.func, ast.Name) and (
                        sub.func.id == "getattr"):
                    add(_attribute_values(graph, read_from[id(sub.args[0])]))
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                bindings = _visible_bindings(graph, function, sub)
                if not bindings:
                    add(_module_values(graph, module, sub.id))
                    continue
                barred = _def_key(graph, function) if barriers and _def_key(graph, function) in barriers else None
                for kind, bound in bindings:
                    if kind in ("value", "from") and bound is not None:
                        more.append((module, function, bound, context))
                    elif kind == "param" and barred:
                        crossings += [(owner, caller, value, barred) for owner, caller, value
                                      in _arguments_for(graph, module, function, sub.id, problems)
                                      if caller is not None]
                    elif kind == "param" and context and context[0][0] == id(function) and context[0][1] is not None:
                        _frame, call, caller_module, caller = context[0]
                        more += [(caller_module, caller, narrowed(value, read_from.get(id(sub))), context[1:])
                                 for value in _passed(graph, function, sub.id, call)]
                        add(_default_of(graph, function, sub.id))
                    elif kind == "param":
                        add([(owner, scope, narrowed(value, read_from.get(id(sub))))
                             for owner, scope, value in _arguments_for(graph, module, function, sub.id, problems)])
                    elif kind in ("global", "import"):
                        add(_module_values(graph, module, sub.id))
                add(_writes_into(graph, module, function, sub.id, read_from.get(id(sub))))
            elif (isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Load)
                  and isinstance(sub.value, ast.Name) and _bound(graph, function, sub.value.id)
                  and not _builtin_receiver(graph, module, function, sub.value)):
                add(_attribute_values(graph, sub.attr))
                for target in _untyped_methods(graph, sub.attr):
                    add(returns_of(target))
        unresolved += [(where, why, origin) for where, why in problems]
        pending += [(owner, scope, value, entered, origin, state) for owner, scope, value, entered in more]
    return found


def _memoize(graph) -> None:
    """Cache, on this graph instance, the lookups a trace repeats: the
    bindings of a name in a scope and the def a call runs. The graph's
    loaded modules do not change while a trace runs; a load that adds defs
    clears the call cache."""
    if getattr(graph, "_memoized", False):
        return
    bindings, callees = {}, {}
    plain_bindings, plain_callee = graph._bindings, graph.callee

    def cached_bindings(scope, name=None, attribute=None):
        key = (id(scope), name, attribute)
        if key not in bindings:
            bindings[key] = plain_bindings(scope, name=name, attribute=attribute)
        return bindings[key]

    def cached_callee(call, module, function=None):
        key = (id(call), module, id(function), len(graph.defs))
        if key not in callees:
            callees[key] = plain_callee(call, module, function)
        return callees[key]

    graph._bindings, graph.callee, graph._memoized = cached_bindings, cached_callee, True


def _def_key(graph, function):
    """The (module, def) key of the def node `function`; None for a nested
    def, a lambda or module scope."""
    if function is None:
        return None
    graph.parameter_class(function, "")  # builds the def-key index
    return graph._keys.get(id(function))


def _visible_bindings(graph, function, name) -> list:
    """`_bound` of the Name node `name`, without the bindings a
    comprehension makes of a variable of the same name elsewhere in
    `function`: a comprehension's loop variable is visible only inside it."""
    import ast

    bindings = _bound(graph, function, name.id)
    if function is None or not bindings or not any(kind == "from" for kind, _value in bindings):
        return bindings
    cache = graph.__dict__.setdefault("_comprehension_cache", {})
    if id(function) not in cache:
        cache[id(function)] = [
            ({id(inner) for inner in ast.walk(node)},
             [(id(generator.iter), {target.id for target in ast.walk(generator.target) if isinstance(target, ast.Name)})
              for generator in node.generators])
            for node in ast.walk(function) if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp))]
    hidden = {iter_id for inside, generators in cache[id(function)] if id(name) not in inside
              for iter_id, targets in generators if name.id in targets}
    return [(kind, value) for kind, value in bindings if not (kind == "from" and id(value) in hidden)]


def _bound(graph, function, name: str) -> list:
    """`_DetectionGraph._bindings` of `name` in `function`, cached; [] at
    module scope."""
    if function is None:
        return []
    cache = graph.__dict__.setdefault("_bound_cache", {})
    key = (id(function), name)
    if key not in cache:
        cache[key] = graph._bindings(function, name=name)
    return cache[key]


def _call_results(graph, module: str, function, call, unresolved: list, returns_of) -> list:
    """(module, def, value) for each value `call`'s result can come from
    (`_value_sources`)."""
    import ast

    where = f"{module}.py:{call.lineno}"
    target = graph.target(call, module)
    if target in graph._RETURNS_ITS_ARGUMENT:
        position, keyword = graph._RETURNS_ITS_ARGUMENT[target]
        runner = next((item.value for item in call.keywords if item.arg == keyword), None)
        if runner is None and position < len(call.args):
            runner = call.args[position]
        return _callable_results(graph, module, function, runner, where, unresolved, returns_of)
    callee = graph.callee(call, module, function)
    if callee is not None:
        return returns_of(callee, call)
    func = call.func
    if isinstance(func, ast.Name):
        if _bound(graph, function, func.id):
            return _callable_results(graph, module, function, func, where, unresolved, returns_of)
        owned = graph.project_name(func.id, module)
        if owned is None or owned[1] in graph.class_names.get(owned[0], {}) or (
                not owned[1] and graph._path(owned[0]) is not None):
            return []  # a builtin, a standard-library name or a project class build
        unresolved.append((where, f"a call of a project object that is not a function: {func.id}"))
        return []
    if isinstance(func, ast.Attribute):
        if _builtin_receiver(graph, module, function, func.value):
            return []
        found = []
        for method in _untyped_methods(graph, func.attr):
            found += returns_of(method)
        return found
    unresolved.append((where, f"a call of a computed callee: {_callee_text(func)}"))
    return []


def _callable_results(graph, module: str, function, runner, where: str, unresolved: list, returns_of) -> list:
    """The values a call of `runner` returns, for each callable it can hold
    (`_callables`); appends to `unresolved` when it holds anything else."""
    runners = _callables(graph, module, function, runner, set())
    if runners is None:
        unresolved.append((where, "a callable that is neither a lambda nor a project function"))
        return []
    found = []
    for owner, scope, value in runners:
        if isinstance(value, tuple):
            found += returns_of(value)
        else:
            found.append((owner, scope, value))
    return found


def _callables(graph, module: str, function, runner, seen: set):
    """[(module, def, body expression) or (module, def, def key)] for the
    callable `runner` holds: a lambda (its body, read in the def it sits
    in); either side of a conditional or boolean expression, and each
    element of a display; a project function (its key), named directly, through an import
    inside `function` or as a table lookup (`_table_callees`); a method
    reached through an attribute chain (`x().Store.method`: every loaded
    project method of that name); a function nested in `function` (each
    value it returns, read in `function`, whose locals it closes over); or a
    local or parameter every binding, loop element or argument of which is
    one of these. A class nested in `function`, a standard-library callable
    and a literal hold no project code, so they give []. None for anything
    else."""
    import ast

    if runner is None:
        return None
    if id(runner) in seen:
        return []
    seen.add(id(runner))
    if isinstance(runner, ast.Lambda):
        return [(module, function, runner.body)]
    if isinstance(runner, ast.Constant):
        return []
    if isinstance(runner, (ast.Tuple, ast.List, ast.Set, ast.IfExp, ast.BoolOp)):
        found = []
        items = runner.elts if isinstance(runner, (ast.Tuple, ast.List, ast.Set)) else (
            [runner.body, runner.orelse] if isinstance(runner, ast.IfExp) else runner.values)
        for item in items:
            inner = _callables(graph, module, function, item, seen)
            if inner is None:
                return None
            found += inner
        return found
    named = graph.refers_to(runner, module)
    if named is not None:
        return [(module, function, named)]
    if isinstance(runner, ast.Call):
        maker = graph.callee(runner, module, function)
        if maker is None:
            return None
        found = []
        for value in _returned_values(graph.defs[maker]):
            inner = _callables(graph, maker[0], graph.defs[maker], value, seen)
            if inner is None:
                return None
            found += inner
        return found
    if isinstance(runner, ast.Subscript):
        return _callables(graph, module, function, runner.value, seen)
    if isinstance(runner, ast.Attribute):
        head = runner
        while isinstance(head, ast.Attribute):
            head = head.value
        if _builtin_receiver(graph, module, function, runner.value):
            return []  # a standard-library callable (`os.path.join`, `text.strip`)
        return [(module, function, key) for key in _untyped_methods(graph, runner.attr)] or (
            [] if not isinstance(head, ast.Name) or graph.project_name(head.id, module) is None else None)
    if not isinstance(runner, ast.Name):
        return None
    if function is None or not _bound(graph, function, runner.id):
        owned = graph.project_name(runner.id, module)
        if owned is None or owned[1] in graph.class_names.get(owned[0], {}):
            return []  # a builtin, a standard-library name or a project class build
        return None
    found = []
    for kind, bound in _bound(graph, function, runner.id):
        if kind == "value":
            inner = _callables(graph, module, function, bound, seen)
            if inner is None and isinstance(bound, (ast.Call, ast.Subscript)):
                call = ast.Call(func=runner, args=[], keywords=[])
                tabled = _table_callees(graph, module, function, call)
                inner = [(module, function, graph.refers_to(value, module)) for value in tabled] if tabled else None
        elif kind == "from":
            inner = _callables(graph, module, function, bound, seen)
        elif kind == "param":
            inner = []
            for owner, caller, argument in _arguments_for(graph, module, function, runner.id, []):
                more = _callables(graph, owner, caller, argument, seen)
                if more is None:
                    return None
                inner += more
        elif kind == "import":
            inner = _imported_callable(graph, module, function, runner.id)
        elif kind == "unknown":
            nested = [node for node in ast.walk(function) if node is not function and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == runner.id]
            inner = [] if nested and all(isinstance(node, ast.ClassDef) for node in nested) else [
                (module, function, value) for node in nested if not isinstance(node, ast.ClassDef)
                for value in _returned_values(node)] if nested else None
        else:
            inner = None
        if inner is None:
            return None
        found += inner
    return found


def _imported_callable(graph, module: str, function, name: str):
    """[(module, def, def key)] for a name an import inside `function` binds
    to a project function; [] for one bound to a standard-library or
    third-party name; None when it names a project object that is not a
    function."""
    import ast

    package = module.rpartition(".")[0]
    for node in ast.walk(function):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for alias in node.names:
            bound = alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
            if bound != name:
                continue
            if isinstance(node, ast.Import):
                return [] if graph._path(alias.name) is None else None
            base = node.module or ""
            if node.level:
                parts = module.split(".")
                stem = ".".join(parts[:len(parts) - node.level]) if len(parts) > node.level else package
                base = f"{stem}.{base}".strip(".") if base else stem
            source = _script_module(base)
            if graph._path(source) is None:
                return []
            target = graph.follow(f"{source}.{alias.name}")
            return [(module, function, target)] if target is not None else None
    return None


def _returned_values(node) -> list:
    """Every `return` value and every `yield` value of the def `node`, not
    counting functions, lambdas and classes nested in it."""
    import ast

    values, pending = [], list(node.body)
    while pending:
        item = pending.pop()
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(item, (ast.Return, ast.Yield, ast.YieldFrom)) and item.value is not None:
            values.append(item.value)
        pending.extend(ast.iter_child_nodes(item))
    return values


def _untyped_methods(graph, name: str) -> list:
    """The project methods a call of `<value>.name(...)` can run when the
    census cannot type `<value>`: every loaded project method of that name.
    A name a built-in container or string also defines (`dict.get`,
    `PACTMemory.get`) is read both ways: as the built-in, through the
    receiver and arguments the expression already holds, and as every
    project method of that name."""
    return _methods_named(graph, name)


def _methods_named(graph, name: str) -> list:
    """The key of every loaded project method called `name`."""
    cache = getattr(graph, "_methods_cache", None)
    if cache is None or cache[0] != len(graph.defs):
        index = {}
        for key in list(graph.defs):
            if "." in key[1]:
                index.setdefault(key[1].rpartition(".")[2], []).append(key)
        cache = graph._methods_cache = (len(graph.defs), index)
    return cache[1].get(name, [])


def _module_values(graph, module: str, name: str) -> list:
    """(module, def or None, value) for a name `module` reads at module
    level that is one of a project module's own objects other than a
    function or class (`project_name`): each module-level binding (read at
    module scope, so with no def), each write into it in a function of the
    owning module (`_writes_into`) or through a `global` declaration, and
    each write into an attribute of that name elsewhere (`_attribute_values`).
    [] for a function, a class, a module, a builtin and a standard-library
    name."""
    cache = graph.__dict__.setdefault("_module_values_cache", {})
    if (module, name) in cache:
        return cache[(module, name)]
    found = []
    owned = graph.project_name(name, module)
    if owned is not None and owned[1] and graph.load(owner := owned[0]) and (owner, owned[1]) not in graph.defs and (
            owned[1] not in graph.class_names.get(owner, {})):
        leaf = owned[1]
        found = [(owner, None, value) for value in graph.bindings.get(owner, {}).get(leaf, []) if value is not None]
        for node in _defs_naming(graph, owner, leaf):
            bindings = _bound(graph, node, leaf)
            if not bindings:
                found += _writes_into(graph, owner, node, leaf)
            elif any(kind == "global" for kind, _value in bindings):
                found += [(owner, node, value) for kind, value in bindings
                          if kind in ("value", "from") and value is not None]
                found += _writes_into(graph, owner, node, leaf)
        found += _attribute_values(graph, leaf)
    cache[(module, name)] = found
    return found


def _defs_naming(graph, module: str, name: str) -> list:
    """Every def of `module` that reads or binds the name `name`."""
    import ast

    cache = getattr(graph, "_names_cache", None)
    if cache is None or cache[0] != len(graph.defs):
        index = {}
        for (owner, _qualified), node in list(graph.defs.items()):
            for used in {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)} | {
                    name for sub in ast.walk(node) if isinstance(sub, (ast.Global, ast.Nonlocal))
                    for name in sub.names}:
                index.setdefault((owner, used), []).append(node)
        cache = graph._names_cache = (len(graph.defs), index)
    return cache[1].get((module, name), [])


def _base(node):
    """The name or attribute an item, attribute or method chain starts from
    (`groups.setdefault(k, [])[0]` -> `groups`)."""
    import ast

    while True:
        if isinstance(node, ast.Subscript):
            node = node.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            node = node.func.value
        elif isinstance(node, ast.Attribute) and not isinstance(node.value, ast.Name):
            node = node.value
        else:
            return node


def _attribute_values(graph, attr: str) -> list:
    """(module, def, value) for every assignment of an attribute named
    `attr`, on any receiver, in a function of the loaded modules, and every
    write into such an attribute (an item or attribute assignment, or a
    writer method, `_CONTAINER_WRITERS`, on a chain that starts from it)."""
    import ast

    cache = getattr(graph, "_attribute_cache", None)
    if cache is None or cache[0] != len(graph.defs):
        index = {}
        for (owner, _qualified), node in list(graph.defs.items()):
            for sub in ast.walk(node):
                for target, value in _stores(sub):
                    if isinstance(target, ast.Attribute):
                        index.setdefault(target.attr, []).append((owner, node, value))
                    base = _base(target.value) if isinstance(target, (ast.Subscript, ast.Attribute)) else None
                    if isinstance(base, ast.Attribute):
                        index.setdefault(base.attr, []).append((owner, node, value))
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and (
                        sub.func.attr in _CONTAINER_WRITERS):
                    base = _base(sub.func.value)
                    if isinstance(base, ast.Attribute):
                        for value in [*sub.args, *(item.value for item in sub.keywords)]:
                            index.setdefault(base.attr, []).append((owner, node, value))
        cache = graph._attribute_cache = (len(graph.defs), index)
    return cache[1].get(attr, [])


def _stores(node) -> list:
    """(target, value) for each plain, annotated or augmented assignment
    `node` makes."""
    import ast

    if isinstance(node, ast.Assign):
        return [(target, node.value) for target in node.targets]
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)) and node.value is not None:
        return [(node.target, node.value)]
    return []


def _writes_into(graph, module: str, function, name: str, attr=None) -> list:
    """`_writes_into_uncached`, cached."""
    cache = graph.__dict__.setdefault("_writes_cache", {})
    key = (id(function), name, attr)
    if key not in cache:
        cache[key] = _writes_into_uncached(graph, module, function, name, attr, set())
    return cache[key]


def _writes_into_uncached(graph, module: str, function, name: str, attr, seen) -> list:
    """(module, def, value) for each value `function` writes into the
    object its local `name` holds, or into a local bound to that same
    object (`alias = name`); with `attr`, only into that attribute of it.
    A write is an item or attribute assignment, or a writer method
    (`_CONTAINER_WRITERS`), on a chain that starts from the object
    (`_base`); and, when the object is passed to a project function, each
    write that function makes into its parameter, or, passed to a method no
    call resolves on a receiver not proved a standard-library value
    (`_foreign`), each write every loaded project method of that name makes
    into the parameter at that place."""
    import ast

    if function is None or (id(function), name, attr) in seen:
        return []
    seen.add((id(function), name, attr))
    names, pending = set(), [name]
    while pending:
        current = pending.pop()
        if current in names:
            continue
        names.add(current)
        for sub in ast.walk(function):
            for target, value in _stores(sub):
                if isinstance(target, ast.Name) and isinstance(value, ast.Name) and value.id == current:
                    pending.append(target.id)
    found = []

    def holds(node):
        base = _base(node)
        if attr is None:
            return isinstance(base, ast.Name) and base.id in names
        return (isinstance(base, ast.Attribute) and base.attr == attr and isinstance(base.value, ast.Name)
                and base.value.id in names)

    def held(node):
        base = _base(node)
        return isinstance(base, ast.Name) and base.id in names

    for sub in ast.walk(function):
        for target, value in _stores(sub):
            if isinstance(target, ast.Attribute) and attr is not None and target.attr == attr and held(target.value):
                found.append((module, function, value))
            elif isinstance(target, (ast.Subscript, ast.Attribute)) and holds(target.value):
                found.append((module, function, value))
        if not isinstance(sub, ast.Call):
            continue
        if isinstance(sub.func, ast.Attribute) and sub.func.attr in _CONTAINER_WRITERS and holds(sub.func.value):
            found += [(module, function, value) for value in [*sub.args, *(item.value for item in sub.keywords)]]
            continue
        handed = [(index, None) for index, value in enumerate(sub.args) if held(value)] + [
            (None, item.arg) for item in sub.keywords if item.arg is not None and held(item.value)]
        if not handed:
            continue
        callee = graph.callee(sub, module, function)
        callees = [callee] if callee is not None else (
            _untyped_methods(graph, sub.func.attr) if isinstance(sub.func, ast.Attribute)
            and not _builtin_receiver(graph, module, function, sub.func.value) else [])
        for key in callees:
            node = graph.defs[key]
            positional = [argument.arg for argument in node.args.posonlyargs + node.args.args]
            offset = 1 if "." in key[1] and isinstance(sub.func, ast.Attribute) else 0
            for index, keyword in handed:
                parameter = keyword if keyword is not None else (
                    positional[index + offset] if index + offset < len(positional) else None)
                if parameter is not None:
                    found += _writes_into_uncached(graph, key[0], node, parameter, attr, seen)
    return found


def _foreign(graph, module: str, function, value, seen=None, deep=True) -> bool:
    """`value` is a standard-library value: `_DetectionGraph._foreign_value`
    proves it one, or it is an attribute, item or method chain on one, or a
    local every binding of which is one, or a parameter that holds some
    value and every value of which (its default, and its argument at every
    call `_calls_of` finds) is one, or it
    starts from a builtin or a module imported from outside the project
    (`os.environ`, `sys.stderr`). A method called on it, or an attribute
    read from it, runs no project code. Without `deep`, locals and
    parameters are judged by `_foreign_value` alone (used while the call
    index `_calls_of` reads is being built)."""
    import ast

    seen = set() if seen is None else seen
    cache = graph.__dict__.setdefault("_foreign_cache", {})
    if deep and id(value) in cache:
        return cache[id(value)]
    if id(value) in seen:
        return True  # a cycle adds no value of its own
    seen.add(id(value))
    parts = (
        [value.body, value.orelse] if isinstance(value, ast.IfExp) else value.values if isinstance(value, ast.BoolOp)
        else [value.left, value.right] if isinstance(value, ast.BinOp)
        else value.elts if isinstance(value, (ast.Tuple, ast.List, ast.Set)) else None)
    if parts is not None:
        answer = all(_foreign(graph, module, function, part, seen, deep) for part in parts)
        if deep and len(seen) == 1:
            cache[id(value)] = answer
        return answer
    head = value
    while isinstance(head, (ast.Attribute, ast.Subscript)) or (
            isinstance(head, ast.Call) and isinstance(head.func, ast.Attribute)
            and graph.callee(head, module, function) is None):
        head = head.func.value if isinstance(head, ast.Call) else head.value
    if isinstance(head, (ast.Constant, ast.JoinedStr, ast.Compare)):
        answer = True
    elif isinstance(head, ast.Name) and not _bound(graph, function, head.id):
        answer = graph.project_name(head.id, module) is None
    elif function is None:
        answer = False
    else:
        try:
            answer = bool(graph._foreign_value(head, module, function, frozenset()))
        except RecursionError:
            answer = False
        if not answer and deep and isinstance(head, ast.Name):
            answer = True
            for kind, bound in _bound(graph, function, head.id):
                if kind in ("value", "from") and bound is not None:
                    answer = _foreign(graph, module, function, bound, seen)
                elif kind == "param":
                    held = _arguments_for(graph, module, function, head.id, [])
                    answer = bool(held) and all(_foreign(graph, owner, scope, argument, seen)
                                                for owner, scope, argument in held)
                elif kind == "import":
                    answer = graph.project_name(head.id, module) is None
                else:
                    answer = False
                if not answer:
                    break
    if deep and len(seen) == 1:
        cache[id(value)] = answer
    return answer


def _enter_proof(graph) -> bool:
    """Count one more nested step of a `_holds` proof, or refuse it past
    `_PROOF_DEPTH` nested steps: a proof deeper than that is not a proof,
    and its False only widens what the census reads."""
    active = graph.__dict__.get("_active_proofs", 0)
    if active >= _PROOF_DEPTH:
        return False
    graph._active_proofs = active + 1
    return True


# Nested proof steps allowed at once, well inside the interpreter's stack.
_PROOF_DEPTH = 120


_FRESH_BUILTIN_FUNCTIONS = frozenset({
    "sorted", "len", "repr", "ascii", "format", "isinstance", "issubclass", "hasattr", "callable", "open",
    "id", "hash", "abs", "round", "divmod", "ord", "chr", "hex", "oct", "bin", "any", "all", "input"})


def _makes_builtin(graph, module: str, function, call) -> bool:
    """`call` makes a fresh built-in or standard-library object: it calls a
    built-in type other than `type` and `super` (which hand back a project
    class or method), a built-in of `_FRESH_BUILTIN_FUNCTIONS`, or a class
    imported from the standard library (`ContextVar`, `Path`)."""
    import ast
    import builtins
    import importlib
    import sys

    if not isinstance(call.func, ast.Name) or _bound(graph, function, call.func.id):
        return False
    name = call.func.id
    imported = graph.aliases.get(module, {}).get(name)
    if imported is None and graph.project_name(name, module) is None:
        made = getattr(builtins, name, None)
        return name in _FRESH_BUILTIN_FUNCTIONS or (
            isinstance(made, type) and name not in ("type", "super"))
    if imported is None or imported.partition(".")[0] not in sys.stdlib_module_names:
        return False
    owner, _, leaf = imported.rpartition(".")
    try:
        made = getattr(importlib.import_module(owner), leaf) if owner else None
    except (ImportError, AttributeError):
        return False
    return isinstance(made, type) and made is not type


def _builtin_receiver(graph, module: str, function, value, deep=True, seen=None) -> bool:
    """`value` is itself a built-in or standard-library object, whatever it
    holds, so a method called on it is that object's own method and no
    project method of the name (`_holds` at level 0). A proof begun outside
    any other is cached; one begun inside another can be cut short by
    `_PROOF_DEPTH`, so it is not."""
    if seen is not None or graph.__dict__.get("_active_proofs", 0):
        return _holds(graph, module, function, value, 0, deep, set() if seen is None else seen)
    cache = graph.__dict__.setdefault("_receiver_cache", {})
    key = (id(value), id(function), deep)
    if key not in cache:
        cache[key] = _holds(graph, module, function, value, 0, deep, set())
    return cache[key]


# Methods of a built-in container that hand back what it holds (or a
# default the call passes).
_CONTENT_METHODS = frozenset({"get", "pop", "setdefault", "popitem", "popleft"})


def _holds(graph, module: str, function, value, level: int, deep, seen) -> bool:
    """At level 0, `value` is a built-in or standard-library object: it is
    `_foreign`; or a literal, display or comprehension; or a call that
    `_makes_builtin`; or a method of one that is not a content method
    (`_CONTENT_METHODS`: `d.get(k)` is what `d` holds, or its default); or
    an item of a container (`x[k]`), or the result of a content method, that
    holds such values only; or a `*args` or `**kwargs` parameter.
    At level n > 0, everything `value` holds n steps down (by iteration,
    item or content method) is one: it is `_foreign`, or a display or
    comprehension of such values, or a built-in call on such arguments
    (`dict(row)`, `sorted(items)`).
    At every level, an operator, conditional or boolean choice over such
    values; a local every binding of which is one, a loop, comprehension or
    unpacking target counting as one step into its source and a `with`
    target as its context, and everything written into which
    (`_writes_into`) is one step up; a project module-level object every
    assigned and written value of which is; and, with `deep`, a parameter
    every argument and the default of which is, and the result of a project
    function every returned value of which is. A cycle adds no value of its
    own; a level past 4 is not proved."""
    if level > 4:
        return False
    key = (id(value), level)
    if key in seen:
        return True
    seen.add(key)
    if not _enter_proof(graph):
        return False
    try:
        return _foreign(graph, module, function, value, deep=deep) or _holds_by_form(
            graph, module, function, value, level, deep, seen)
    finally:
        graph._active_proofs -= 1


def _holds_by_form(graph, module: str, function, value, level: int, deep, seen) -> bool:
    """`_holds` by the form of `value`, once `_foreign` has not proved it."""
    import ast

    def again(where, scope, item, depth=level):
        return _holds(graph, where, scope, item, depth, deep, seen)

    if isinstance(value, (ast.Constant, ast.JoinedStr, ast.Compare)):
        return True
    if isinstance(value, ast.BinOp):
        return again(module, function, value.left) and again(module, function, value.right)
    if isinstance(value, ast.UnaryOp):
        return again(module, function, value.operand)
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return level == 0 or all(
            again(module, function, part.value if isinstance(part, ast.Starred) else part,
                  level if isinstance(part, ast.Starred) else level - 1) for part in value.elts)
    if isinstance(value, ast.Dict):
        return level == 0 or all(
            again(module, function, part, level - 1) for part in value.keys if part is not None) and all(
            again(module, function, part, level if key is None else level - 1)
            for key, part in zip(value.keys, value.values))
    if isinstance(value, ast.DictComp):
        return level == 0 or (again(module, function, value.key, level - 1)
                              and again(module, function, value.value, level - 1))
    if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        if level == 0:
            return True
        element = value.elt
        return again(module, function, element, level - 1)
    if isinstance(value, ast.IfExp):
        return again(module, function, value.body) and again(module, function, value.orelse)
    if isinstance(value, ast.BoolOp):
        return all(again(module, function, part) for part in value.values)
    if isinstance(value, ast.Subscript):
        return again(module, function, value.value, level + 1)
    if isinstance(value, ast.Attribute) and isinstance(value.ctx, ast.Load):
        package = graph.project_name(value.value.id, module) if isinstance(value.value, ast.Name) and not _bound(
            graph, function, value.value.id) else None
        if package is not None and not package[1]:
            # `project_module.name`
            return bool(graph.load(package[0])) and again(package[0], None, ast.Name(id=value.attr, ctx=ast.Load()))
        held = _field_values(graph, value.attr)
        return held is not None and all(again(where, scope, item) for where, scope, item in held)
    if isinstance(value, ast.Call):
        func = value.func
        passed = [*value.args, *(item.value for item in value.keywords)]
        if _makes_builtin(graph, module, function, value):
            return level == 0 or all(
                again(module, function, item.value if isinstance(item, ast.Starred) else item, level)
                for item in value.args) and all(
                again(module, function, item.value, level if item.arg is None else level - 1)
                for item in value.keywords)
        if isinstance(func, ast.Attribute) and again(module, function, func.value, 0):
            if func.attr in _CONTENT_METHODS:
                return again(module, function, func.value, level + 1) and all(
                    again(module, function, item) for item in passed)
            return level == 0 or (again(module, function, func.value) and all(
                again(module, function, item) for item in passed))
        made = graph.callee(value, module, function)
        if deep and made is not None and not made[1].endswith(".__init__"):
            returned = _returned_values(graph.defs[made])
            return bool(returned) and all(again(made[0], graph.defs[made], item) for item in returned)
        return False
    if not isinstance(value, ast.Name):
        return False
    if level == 0 and _narrowed(graph, module, function, value):
        return True
    bindings = _scoped_bindings(graph, function, value)
    if not bindings or any(kind in ("global", "import") for kind, _bound_value in bindings):
        owned = graph.project_name(value.id, module)
        if owned is None or not owned[1] or not graph.load(owned[0]) or owned in graph.defs or (
                owned[1] in graph.class_names.get(owned[0], {})):
            return False
        owner, leaf = owned
        held = [(owner, None, item, level) for item in graph.bindings.get(owner, {}).get(leaf, []) if item is not None]
        for node in _defs_naming(graph, owner, leaf):
            rebinds = _bound(graph, node, leaf)
            if not rebinds or any(kind == "global" for kind, _bound_value in rebinds):
                held += [(owner, node, item, level) for kind, item in rebinds if kind not in ("global", "import")]
                if level:
                    held += [(where, scope, item, level - 1) for where, scope, item in _writes_into(graph, owner, node, leaf)]
        held += [(where, scope, item, level) for where, scope, item in _attribute_values(graph, leaf)]
        return bool(held) and all(item is not None and again(where, scope, item, depth)
                                  for where, scope, item, depth in held)
    arguments = function.args if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) else None
    packed = {item.arg for item in (arguments.vararg, arguments.kwarg) if item is not None} if arguments else set()
    for kind, bound in bindings:
        if kind == "value" and bound is not None:
            if not again(module, function, bound):
                return False
        elif kind == "param" and deep:
            held = _arguments_for(graph, module, function, value.id, [])
            if value.id in packed:
                if level and not all(again(where, scope, item, level - 1) for where, scope, item in held):
                    return False
            elif not held or not all(again(where, scope, item) for where, scope, item in held):
                return False
        elif kind == "from" and bound is not None:
            entered = any(item.context_expr is bound for node in ast.walk(function)
                          if isinstance(node, (ast.With, ast.AsyncWith)) for item in node.items)
            if not again(module, function, bound, level if entered else level + 1):
                return False
        else:
            return False
    return not level or all(again(where, scope, item, level - 1)
                            for where, scope, item in _writes_into(graph, module, function, value.id))


def _scoped_bindings(graph, function, name) -> list:
    """`_visible_bindings` of the Name node `name`; inside a comprehension
    that binds the name, only that comprehension's own binding."""
    import ast

    bindings = _visible_bindings(graph, function, name)
    if function is None:
        return bindings
    innermost = None
    for node in ast.walk(function):
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for generator in node.generators:
                if name.id in {target.id for target in ast.walk(generator.target) if isinstance(target, ast.Name)} and (
                        any(sub is name for sub in ast.walk(node)) and not any(sub is name for sub in ast.walk(generator.iter))):
                    if innermost is None or any(sub is node for sub in ast.walk(innermost[0])):
                        innermost = (node, generator)
    if innermost is None:
        return bindings
    return [(kind, value) for kind, value in bindings if kind == "from" and value is innermost[1].iter]


def _builtin_test(graph, module: str, function, test, name: str):
    """True when `test` is `isinstance(name, T)` and every T is a built-in
    type other than `type` and `super`; "not" when it is `not` such a test;
    None otherwise."""
    import ast
    import builtins

    negated = isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
    call = test.operand if negated else test
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "isinstance"
            and len(call.args) == 2 and isinstance(call.args[0], ast.Name) and call.args[0].id == name):
        return None
    kinds = call.args[1].elts if isinstance(call.args[1], ast.Tuple) else [call.args[1]]
    if all(isinstance(kind, ast.Name) and not _bound(graph, function, kind.id)
           and graph.project_name(kind.id, module) is None and kind.id not in ("type", "super")
           and isinstance(getattr(builtins, kind.id, None), type) for kind in kinds):
        return "not" if negated else True
    return None


def _narrowed(graph, module: str, function, name) -> bool:
    """The Name node `name` is read where an `isinstance` test against
    built-in types (`_builtin_test`) has just held for it: after `and` the
    test, after `or not` the test, in the body of an `if` or `while` whose
    test (or one `and` part of it) is the test, in the element and later
    conditions of a comprehension with the test as a condition, or in a
    statement after `if not <test>:` whose body ends in `return`, `raise`,
    `continue` or `break`; and not rebound there before the read
    (`_rebindings`): a read after the name is assigned again inside that
    reach, or inside a loop there that also assigns it, holds the new value
    and is not narrowed."""
    import ast

    scope = function if function is not None else None
    if scope is None:
        return False
    cache = graph.__dict__.setdefault("_narrowed_cache", {})
    if id(scope) not in cache:
        covered = {}

        def cover(names, nodes):
            parents = {id(child): sub for node in nodes for sub in ast.walk(node)
                       for child in ast.iter_child_nodes(sub)}
            stores = _rebindings(names, nodes, parents)
            for node in nodes:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                        loops = _enclosing_loops(sub, parents)
                        covered.setdefault(id(sub), set()).update(
                            name for name in names if not any(
                                position < (sub.lineno, sub.col_offset) or loops & inside
                                for position, inside in stores.get(name, ())))

        def held(test, wanted):
            parts = test.values if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And) else [test]
            return {part_name for part in parts for part_name in {
                sub.id for sub in ast.walk(part) if isinstance(sub, ast.Name)}
                if _builtin_test(graph, module, function, part, part_name) is wanted}

        for node in ast.walk(scope):
            if isinstance(node, ast.BoolOp):
                for index, part in enumerate(node.values):
                    names = {sub.id for sub in ast.walk(part) if isinstance(sub, ast.Name)}
                    wanted = True if isinstance(node.op, ast.And) else "not"
                    hit = {candidate for candidate in names
                           if _builtin_test(graph, module, function, part, candidate) == wanted}
                    cover(hit, node.values[index + 1:])
            elif isinstance(node, (ast.If, ast.While, ast.IfExp)):
                cover(held(node.test, True), node.body if isinstance(node.body, list) else [node.body])
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                for generator in node.generators:
                    for index, test in enumerate(generator.ifs):
                        hit = held(test, True)
                        rest = [*generator.ifs[index + 1:],
                                *([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt])]
                        cover(hit, rest)
            for field in ("body", "orelse", "finalbody"):
                statements = getattr(node, field, None)
                if not isinstance(statements, list):
                    continue
                for index, statement in enumerate(statements):
                    if (isinstance(statement, ast.If) and not statement.orelse and statement.body
                            and isinstance(statement.body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))):
                        test = statement.test
                        parts = test.values if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or) else [test]
                        hit = {candidate for part in parts for candidate in {
                            sub.id for sub in ast.walk(part) if isinstance(sub, ast.Name)}
                            if _builtin_test(graph, module, function, part, candidate) == "not"}
                        cover(hit, statements[index + 1:])
        cache[id(scope)] = covered
    return name.id in cache[id(scope)].get(id(name), set())


def _enclosing_loops(node, parents) -> set:
    """The ids of the `for` and `while` loops around `node` that `parents`
    (child id to parent, over one reach of statements) holds."""
    import ast

    loops = set()
    while id(node) in parents:
        node = parents[id(node)]
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            loops.add(id(node))
    return loops


def _rebindings(names, nodes, parents) -> dict:
    """{name: [(position, loops)]} for each assignment of one of `names`
    in `nodes`, in their own scope (not in a nested def, lambda, class or
    comprehension): `position` is where the new value takes hold (the end
    of the assignment, of a `for` loop's iterable, of a `with` item's
    expression, of a `:=`), and `loops` the loops around it
    (`_enclosing_loops`), whose next pass the new value also reaches."""
    import ast

    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
              ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
    found = {}
    for node in nodes:
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store) and sub.id in names):
                continue
            chain, node_above = [], parents.get(id(sub))
            while node_above is not None:
                chain.append(node_above)
                node_above = parents.get(id(node_above))
            if any(isinstance(above, nested) for above in chain):
                continue
            child, holder = sub, None
            for above in chain:
                if isinstance(above, (ast.stmt, ast.NamedExpr, ast.withitem)):
                    holder = above
                    break
                child = above
            if holder is None:
                continue
            end = (holder.iter if isinstance(holder, (ast.For, ast.AsyncFor)) and child is holder.target
                   else holder.context_expr if isinstance(holder, ast.withitem) else holder)
            found.setdefault(sub.id, []).append(((end.end_lineno, end.end_col_offset), _enclosing_loops(sub, parents)))
    return found


def _field_values(graph, attr: str):
    """(module, def, value) for every value an attribute named `attr` of any
    object can hold, or None when one cannot be listed: each assignment of
    or write into such an attribute (`_attribute_values`); each class-level
    default of a field of that name; each keyword argument of that name in
    any call (a dataclass or record build, `replace(obj, attr=...)`); each
    positional argument of a call that builds a class declaring the field
    (`_built_classes`: by its name, a local bound to it, or its own
    method's `cls`, `type(self)` or `self.__class__`; only the field's own slot for a class without its own
    `__init__` or a base); and each value a project method of that name
    returns (a property). A `*` or `**` argument in a call of such a class
    is None."""
    import ast

    cache = graph.__dict__.setdefault("_field_cache", {})
    if attr in cache:
        return cache[attr]
    declaring, found = {}, list(_attribute_values(graph, attr))
    for owner in list(graph.class_names):
        for classes in graph.class_names[owner].values():
            for _qualified, node in classes.items():
                fields = [statement.target.id for statement in node.body
                          if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)]
                for statement in node.body:
                    target = statement.target if isinstance(statement, ast.AnnAssign) else (
                        statement.targets[0] if isinstance(statement, ast.Assign) and len(statement.targets) == 1
                        else None)
                    if isinstance(target, ast.Name) and target.id == attr:
                        # The positional slot a generated `__init__` gives the field; None (every slot)
                        # for a class with its own `__init__` or a base, whose order is not read here.
                        own = any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                                  and item.name == "__init__" for item in node.body)
                        slot = fields.index(attr) if attr in fields and not own and not node.bases else None
                        declaring.setdefault(node.name, set()).add(slot)
                        if statement.value is not None:
                            found.append((owner, None, statement.value))
    for (owner, qualified), node in list(graph.defs.items()):
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            found += [(owner, node, item.value) for item in call.keywords if item.arg == attr]
            built = _built_classes(graph, qualified, node, call.func) & set(declaring)
            if built:
                if any(isinstance(item, ast.Starred) for item in call.args) or any(
                        item.arg is None for item in call.keywords):
                    cache[attr] = None
                    return None
                slots = set().union(*(declaring[name] for name in built))
                found += [(owner, node, item) for index, item in enumerate(call.args)
                          if None in slots or index in slots]
    for key in _methods_named(graph, attr):
        found += [(key[0], graph.defs[key], item) for item in _returned_values(graph.defs[key])]
    cache[attr] = found
    return found


def _built_classes(graph, qualified: str, function, func) -> set:
    """The names of the classes a call of `func` in the def `function`
    (named `qualified`) can build, as far as a field's slot is read: a class
    named plainly (`Found(...)`, `module.Found(...)`); a local bound to a
    class name, or to a choice of class names (`build = Found`, `build =
    Found if flag else Other`, then `build(...)`); and, in a method of a
    class, the method's own class through its first parameter (`cls(...)`
    in a classmethod, `type(self)(...)`, `self.__class__(...)`). Empty for
    any other callee."""
    import ast

    def names(value):
        if isinstance(value, ast.Name):
            return {value.id}
        if isinstance(value, ast.IfExp):
            return names(value.body) | names(value.orelse)
        if isinstance(value, ast.BoolOp):
            return set().union(*(names(item) for item in value.values))
        return set()

    if isinstance(func, ast.Attribute) and func.attr != "__class__":
        return {func.attr}
    positional = [argument.arg for argument in function.args.posonlyargs + function.args.args]
    first = positional[0] if positional and "." in qualified else None
    owner = {qualified.rpartition(".")[0].rpartition(".")[2]} if first else set()
    if isinstance(func, ast.Attribute):
        return owner if isinstance(func.value, ast.Name) and func.value.id == first else set()
    if isinstance(func, ast.Call):
        return owner if (isinstance(func.func, ast.Name) and func.func.id == "type" and len(func.args) == 1
                         and isinstance(func.args[0], ast.Name) and func.args[0].id == first) else set()
    if not isinstance(func, ast.Name):
        return set()
    if func.id == first and any(isinstance(item, ast.Name) and item.id == "classmethod"
                                for item in function.decorator_list):
        return owner
    return {func.id}.union(*(names(bound) for kind, bound in _bound(graph, function, func.id) if kind == "value"))


def _arguments_for(graph, module: str, function, name: str, unresolved: list) -> list:
    """(module, def, value) for each value the parameter `name` can hold.
    For a parameter of `function` itself: its default, and each argument a
    call of the function in the loaded modules passes there (`_calls_of`),
    by position or keyword; a starred or unpacked argument that could fill
    the parameter counts, and so does every extra argument for `*args` or
    `**kwargs`. The first parameter of a method (`self`, `cls`) holds the
    object, whose attributes are followed where they are read. A function
    no loaded code calls and that is never used as a value is called only
    from outside the loaded modules, so its parameters hold nothing the
    store work computes. One the loaded code uses as a value in a way whose
    calls cannot be seen (`_calls_of`) is appended to `unresolved`.
    For a parameter of a function or lambda nested in `function`: every
    argument of every call in `function` of the nested function's name, or,
    for a lambda passed to a call, every other argument of that call (a key
    function's element)."""
    graph.parameter_class(function, name)  # builds the def-key index
    key = graph._keys.get(id(function))
    arguments = function.args
    own = [argument.arg for argument in (
        *arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
        *(extra for extra in (arguments.vararg, arguments.kwarg) if extra))]
    if key is None or name not in own:
        return _nested_arguments(module, function, name)
    positional = [argument.arg for argument in arguments.posonlyargs + arguments.args]
    method = "." in key[1]
    if method and positional[:1] == [name]:
        return []
    found = list(_default_of(graph, function, name))
    calls, unseen = _calls_of(graph, key)
    if unseen:
        unresolved.append((f"{key[0]}.py:{function.lineno}",
                           f"a parameter of a function used as a value whose calls cannot be seen: {name}"))
    for owner, caller, call in calls:
        found += [(owner, caller, value) for value in _passed(graph, function, name, call)]
    return found


def _default_of(graph, function, name: str) -> list:
    """[(module, None, default)] for the default of the parameter `name` of
    the def `function`, read at module scope; [] when it has none."""
    graph.parameter_class(function, name)  # builds the def-key index
    key = graph._keys.get(id(function))
    arguments = function.args
    positional = [argument.arg for argument in arguments.posonlyargs + arguments.args]
    defaults = dict(zip(positional[len(positional) - len(arguments.defaults):], arguments.defaults))
    defaults.update({argument.arg: value for argument, value in zip(arguments.kwonlyargs, arguments.kw_defaults)
                     if value is not None})
    return [(key[0], None, defaults[name])] if key is not None and name in defaults else []


def _passed(graph, function, name: str, call) -> list:
    """The argument expressions `call`, a call of the def `function`, can pass
    to its parameter `name`: by position or keyword; a starred or unpacked
    argument that could fill it; every extra argument for `*args` or
    `**kwargs`. A method's position counts `self` when it is called on an
    attribute."""
    import ast

    graph.parameter_class(function, name)  # builds the def-key index
    key = graph._keys.get(id(function))
    arguments = function.args
    positional = [argument.arg for argument in arguments.posonlyargs + arguments.args]
    method = key is not None and "." in key[1]
    star = arguments.vararg.arg if arguments.vararg else None
    double = arguments.kwarg.arg if arguments.kwarg else None
    named = set(positional) | {argument.arg for argument in arguments.kwonlyargs}
    shift = 1 if method and isinstance(call.func, ast.Attribute) else 0
    if name == star:
        passed = [item.value if isinstance(item, ast.Starred) else item
                  for item in call.args[max(len(positional) - shift, 0):]]
        return passed + [item.value for item in call.args if isinstance(item, ast.Starred)]
    if name == double:
        return [item.value for item in call.keywords if item.arg is None or item.arg not in named]
    passed = [item.value for item in call.keywords if item.arg == name or item.arg is None]
    if name in positional:
        index = positional.index(name) - shift
        for position, item in enumerate(call.args):
            if isinstance(item, ast.Starred) and position <= index:
                passed.append(item.value)
            elif position == index and not any(isinstance(earlier, ast.Starred) for earlier in call.args[:position]):
                passed.append(item)
    return passed


def _nested_arguments(module: str, function, name: str) -> list:
    """(module, def, value) for a parameter `name` of a function or lambda
    nested in `function` (`_arguments_for`)."""
    import ast

    found, holders = [], []
    for node in ast.walk(function):
        if node is function or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        every = node.args
        if any(argument.arg == name for argument in (
                *every.posonlyargs, *every.args, *every.kwonlyargs,
                *(extra for extra in (every.vararg, every.kwarg) if extra))):
            holders.append(node)
    for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
        passed = [*call.args, *(item.value for item in call.keywords)]
        for holder in holders:
            if isinstance(holder, ast.Lambda) and any(item is holder for item in passed):
                found += [(module, function, item) for item in passed if item is not holder]
            elif not isinstance(holder, ast.Lambda) and isinstance(call.func, ast.Name) and (
                    call.func.id == holder.name):
                found += [(module, function, item.value if isinstance(item, ast.Starred) else item)
                          for item in passed]
    return found


def _calls_of(graph, key) -> tuple:
    """(calls, unseen) for the def `key`. `calls` is (caller module, caller
    def, call) for every call of it the loaded modules make: a call
    `_DetectionGraph.callee` or `_table_callees` resolves to it; for a
    method, a call of a method of that name on an object no call resolves;
    a call of a local or parameter that holds it (`_callables`, so a
    function passed as an argument is found where the receiving function
    calls its parameter). `unseen` is True when the def is used as a value
    (`references` on a name that is not called) and some such use is an
    argument of a call that resolves to no project function (a
    standard-library higher-order call such as `sorted(key=...)` or
    `functools.partial`), or is stored anywhere but a local, a loop display
    or a module-level table, or returned; or when it is used as a value and
    the loaded code calls some local, parameter or computed callee whose
    callables `_callables` cannot name, since that call could be one of
    it."""
    import ast

    cache = getattr(graph, "_calls_cache", None)
    if cache is None or cache[0] != len(graph.defs):
        resolved, untyped, local_calls, foreign_uses, value_uses = {}, {}, [], set(), set()
        for (owner, _qualified), node in list(graph.defs.items()):
            arguments_of = {}
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                for item in [*call.args, *(keyword.value for keyword in call.keywords)]:
                    arguments_of[id(item.value if isinstance(item, ast.Starred) else item)] = call
                found = graph.callee(call, owner, node)
                if found is not None:
                    resolved.setdefault(found, []).append((owner, node, call))
                    continue
                tabled = _table_callees(graph, owner, node, call)
                if tabled:
                    for value in tabled:
                        resolved.setdefault(graph.refers_to(value, owner), []).append((owner, node, call))
                elif (isinstance(call.func, ast.Name) and _bound(graph, node, call.func.id)) or isinstance(
                        call.func, (ast.Call, ast.Subscript)):
                    local_calls.append((owner, node, call))
                elif isinstance(call.func, ast.Attribute) and not _builtin_receiver(
                        graph, owner, node, call.func.value, deep=False):
                    untyped.setdefault(call.func.attr, []).append((owner, node, call))
            called = {id(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}
            held = {id(value) for sub in ast.walk(node) for target, value in _stores(sub)
                    if isinstance(target, ast.Name)}
            held |= {id(item) for sub in ast.walk(node) if isinstance(sub, (ast.For, ast.comprehension))
                     for item in ast.walk(sub.iter)}
            held |= {id(item) for value in _returned_values(node)
                     for item in (value.elts if isinstance(value, (ast.Tuple, ast.List)) else [value])}
            for expression, target in graph.references(node, owner):
                if id(expression) not in called:
                    value_uses.add(target)
                if id(expression) in called or id(expression) in held or (
                        isinstance(expression, ast.Name) and any(
                            kind != "import" for kind, _value in _bound(graph, node, expression.id))):
                    continue
                receiver = arguments_of.get(id(expression))
                if receiver is None or (graph.callee(receiver, owner, node) is None
                                        and not _table_callees(graph, owner, node, receiver)):
                    foreign_uses.add(target)
        cache = graph._calls_cache = [len(graph.defs), resolved, untyped, local_calls, foreign_uses, None, value_uses]
    if cache[5] is None:
        cache[5], graph._unknown_callable_calls = {}, []
        for owner, node, call in cache[3]:
            held = _callables(graph, owner, node, call.func, set())
            if held is None:
                graph._unknown_callable_calls.append(f"{owner}.py:{call.lineno} {_callee_text(call.func)}")
            for _module, _scope, value in held or ():
                if isinstance(value, tuple):
                    cache[5].setdefault(value, []).append((owner, node, call))
    calls = list(cache[1].get(key, [])) + list(cache[5].get(key, []))
    if "." in key[1]:
        calls += cache[2].get(key[1].rpartition(".")[2], [])
    return calls, key in cache[4] or (key in cache[6] and bool(graph._unknown_callable_calls))


@functools.lru_cache(maxsize=2)
def _store_work_taint_for(fingerprint: tuple) -> tuple:
    """`_store_work_git_taint` over the census's own NOT_A_ROUTE and
    `route_entry_conditions`, for the tree `fingerprint` names: computed
    once per source tree, so the tests that read it share one trace and one
    graph."""
    return _store_work_git_taint(TestEveryRouteEntryConditionIsARoute.NOT_A_ROUTE, route_entry_conditions())


def _flow_allowed(not_a_route: dict, condition, sink) -> bool:
    """A store-work condition's value may reach a git question as an argument
    of the def `sink` only when the condition is a key of
    `_ASKS_ABOUT_RECORDED_DIRECTORY` naming that sink and its NOT_A_ROUTE
    reason ends with `_AFTER_DETECTION_ASKS`."""
    return (_ASKS_ABOUT_RECORDED_DIRECTORY.get(condition) == sink
            and not_a_route.get(condition, "").endswith(_AFTER_DETECTION_ASKS))


def _store_work_git_taint(not_a_route: dict, inputs: dict) -> tuple:
    """(questions, problems, allowed, graph): each git question the memory
    CLI's command work asks (`_store_work_git_questions`); each problem, as
    (question site, source site, conditions, trace path); and each allowed
    flow, as (condition, sink call site). A problem is a call at a site
    (`route_inputs`' `module.py:line`) of a store-work condition (its
    NOT_A_ROUTE reason ends with `_AFTER_DETECTION` or
    `_AFTER_DETECTION_ASKS`) that one trace back from every question's
    arguments reaches (`_traced_sources`), or a place that trace cannot
    follow, whose conditions are `(("unresolved", why),)`. One trace serves
    every question, so a source is reported once, under the first question
    that reached it. `graph` is the loaded census graph the trace read. The
    trace stops at the parameters of each sink of
    `_ASKS_ABOUT_RECORDED_DIRECTORY` and traces each argument a call passes
    there on its own: a condition `_flow_allowed` for that sink is an
    allowed flow there; every other store-work condition is a problem."""
    graph = _DetectionGraph()
    for module in (*DETECTION_MODULES, *(module for module, _name in PASS_SETUPS)):
        graph.load(module)
    _pass_openings(graph)
    questions, _reached = _store_work_git_questions(graph)
    store_work = {condition for condition, reason in not_a_route.items()
                  if reason.endswith(_AFTER_DETECTION) or reason.endswith(_AFTER_DETECTION_ASKS)}
    tainted_sites = {}
    for condition, sites in inputs.items():
        if condition in store_work:
            for site in sites:
                tainted_sites.setdefault(site, set()).add(condition)
    starts, asked = [], []
    for module, key, call in questions:
        for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
            starts.append((module, graph.defs[key], argument))
            asked.append(f"{module}.py:{call.lineno}")
    sinks = frozenset(_ASKS_ABOUT_RECORDED_DIRECTORY.values())
    unresolved, parents, problems, crossings, allowed = [], {}, [], [], set()

    def path(step):
        sites = []
        while step is not None:
            step, site = parents[step]
            sites.append(site)
        return " <- ".join(sites)

    for (owner, source), (origin, step) in _traced_sources(
            graph, starts, unresolved, parents, barriers=sinks, crossings=crossings).items():
        hit = tainted_sites.get(f"{owner}.py:{source.lineno}")
        if hit:
            problems.append((asked[origin], f"{owner}.py:{source.lineno}", tuple(sorted(hit)), path(step)))
    problems += [(asked[origin], where, (("unresolved", why),), "") for where, why, origin in unresolved]
    for owner, caller, argument, sink in crossings:
        crossing_unresolved, crossing_parents = [], {}
        handed = f"{owner}.py:{argument.lineno}"
        for (module, source), (_origin, step) in _traced_sources(
                graph, [(owner, caller, argument)], crossing_unresolved, crossing_parents,
                barriers=sinks).items():
            site = f"{module}.py:{source.lineno}"
            for condition in tainted_sites.get(site, ()):
                if _flow_allowed(not_a_route, condition, sink):
                    allowed.add((condition, handed))
                else:
                    problems.append((handed, site, (condition,), "through a sink of _ASKS_ABOUT_RECORDED_DIRECTORY"))
        problems += [(handed, where, (("unresolved", why),), "") for where, why, _o in crossing_unresolved]
    return questions, sorted(set(problems)), sorted(allowed), graph


# The second half of each NOT_A_ROUTE reason for a memory CLI command's store work.
# Each half is pinned: `test_only_pact_memory_init_sets_the_identity_store_work_reads`
# and `test_no_git_question_takes_a_store_work_value`.
_AFTER_DETECTION = (
    "; it runs after PACTMemory.__init__ has detected the identity, which only __init__ sets, "
    "and no git question takes its value"
)
# The second half of the NOT_A_ROUTE reason of each condition whose value the
# refusal check hands to git, and the def through which alone it may reach a
# git question (`_flow_allowed`): `format_project_dir_disagreement`'s kind check
# asks `recorded_main_root` two questions about the recorded project directory,
# `rev-parse --show-toplevel` and then `--git-common-dir`. Pinned by
# `test_no_git_question_takes_a_store_work_value` and
# `test_the_recorded_directory_allowance_names_its_conditions`.
_AFTER_DETECTION_ASKS = (
    "; it runs after PACTMemory.__init__ has detected the identity, which only __init__ sets, "
    "and a git question takes its value only in the refusal check `format_project_dir_disagreement`, "
    "which asks git two questions about the recorded project directory (`recorded_main_root`): "
    "`rev-parse --show-toplevel`, then `--git-common-dir`, each keyed on that directory"
)
_ASKS_ABOUT_RECORDED_DIRECTORY = {
    ("call", "env_record_project_dir_disagreement"): ("pact_session", "format_project_dir_disagreement"),
    ("call", "format_project_dir_disagreement"): ("pact_session", "format_project_dir_disagreement"),
}


# The exact-text pins this census keeps, each with why a behaviour check
# cannot stand in for it. A pin of source or docstring text that is not here
# is a change detector to replace with a check of the behaviour it states.
_KEPT_TEXT_PINS = {
    "TestEveryRouteEntryConditionIsARoute.NOT_A_ROUTE": (
        "each row is a judgement that an input the resolver reads needs no route, which no code "
        "computes; a new or renamed input must fail until someone writes that judgement. A reason the "
        "code computes (`_derived_reason`) is not a row"
    ),
    "_AFTER_DETECTION": (
        "the store-work pin selects its conditions by this reason suffix, so the text is the selector; "
        "what it claims is pinned by behaviour (the identity attributes only __init__ sets, and no git "
        "question taking a store-work value)"
    ),
    "_AFTER_DETECTION_ASKS": "the same selector, for the one store-work flow allowed through a sink",
    "_traced_sources": (
        "its ADVERSARIAL-ONLY and LIMITS paragraphs are read for each listed form's anchor only, tying "
        "each named form to the row that shows the trace does not follow it"
    ),
    "route_inputs": (
        "its ADVERSARIAL-ONLY paragraph is read for the `contextvars.copy_context()` anchor only, tied "
        "to the row that shows the read is not reported"
    ),
}


def test_each_kept_text_pin_names_a_pin_here():
    """`_KEPT_TEXT_PINS` names only pins this module holds, each with a
    reason, so the table cannot outlive what it explains."""
    import sys

    module = sys.modules[__name__]
    for name, reason in _KEPT_TEXT_PINS.items():
        owner = module
        for part in name.split("."):
            assert hasattr(owner, part), name
            owner = getattr(owner, part)
        assert reason, name


# The reasons `_derived_reason` computes for a build or a method call, by the
# kind of code it finds.
_KIND_REASONS = {
    "opening class": "a class a pass opening builds; the census reads every method it defines",
    "build": "builds a record class (fields only)",
    "pattern": "a method of a compiled literal pattern",
    "method-free build": (
        "builds a method-free class whose chain ends in outside bases, so neither the build nor an "
        "instance runs project code"
    ),
    "followed build": (
        "builds a plain class; the census follows the build into its __init__, and a method the class "
        "defines into the class wherever it types the object as that build (instance_target)"
    ),
}
_INERT_BASE = "a base whose project classes define no __init_subclass__ or __class_getitem__ and name no metaclass"
_SHAPE_CALL = re.compile(r"<call to no function ([\w.]+): (\w+)(?:\.(\w+))?>")
_SHAPE_ANNOTATED = re.compile(r"<name imported from a project module ([\w.]+): (\w+) in an annotation>")
_SHAPE_STATEMENT = re.compile(r"<class statement on a project name ([\w.]+): \w+ (base|keyword \w+|decorator) (\w+)>")
_SHAPE_OWN = re.compile(r"<class of its own module used as a value ([\w.]+): ([\w.]+?)( in an annotation)?>")


def _derived_graph():
    """The census graph `route_inputs` reads, with the pass openings read."""
    graph = _DetectionGraph()
    for module in (*DETECTION_MODULES, *(module for module, _name in PASS_SETUPS)):
        graph.load(module)
    _pass_openings(graph)
    return graph


def _call_kind(graph, condition):
    """The kind of code a `<call to no function m: X[.method]>` condition
    names, as a key of `_KIND_REASONS`, or None for any other code:
    - "opening class": a class a pass opening builds (`_pass_openings`),
      whose methods `route_inputs` reads as roots;
    - "build": a NamedTuple whose body holds only annotated fields with
      literal defaults and a docstring (no method, decorator or keyword);
    - "method-free build": a `plain_class` that defines no method, nor does
      any project base of it;
    - "followed build": a `plain_class` with an `__init__` in its chain
      (`init_of`);
    - "pattern": a method called on a module name (followed through
      imports) whose every binding is `re.compile` of literals or `re`
      flags."""
    import ast

    from fixtures.identity_git import qualified_name

    found = _SHAPE_CALL.fullmatch(condition[1]) if condition[0] == "call" else None
    if not found:
        return None
    module, name, method = found.groups()
    assert graph.load(module), module
    owned = graph.project_name(name, module)
    if owned is None or not owned[1]:
        return None

    def literal(value):
        try:
            ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            return False
        return True

    owner, leaf = owned
    graph.load(owner)
    node = graph.classes.get(owner, {}).get(leaf)
    if method is not None:
        aliases = graph.aliases[owner]
        values = graph.bindings[owner].get(leaf, [])

        def flag(value):
            if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
                return flag(value.left) and flag(value.right)
            return (qualified_name(value, aliases) or "").startswith("re.")

        return "pattern" if values and all(
            isinstance(value, ast.Call) and qualified_name(value.func, aliases) == "re.compile"
            and all(literal(argument) or flag(argument) for argument in [*value.args, *(kw.value for kw in value.keywords)])
            for value in values
        ) else None
    if node is None:
        return None
    plain = graph.plain_class(ast.Name(id=name, ctx=ast.Load()), module) == owned

    def methods(of):
        return [item for item in of.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]

    if owned in graph.opening_classes:
        return "opening class"
    if ([ast.unparse(base) for base in node.bases] in (["NamedTuple"], ["typing.NamedTuple"])
            and not node.decorator_list and not node.keywords and all(
                (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
                 and (item.value is None or literal(item.value)))
                or (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant))
                for item in node.body)):
        return "build"
    if plain and not methods(node) and all(
            not methods(graph.classes[base[0]][base[1]]) for base in graph.project_bases(owned)):
        return "method-free build"
    if plain and graph.init_of(owned) is not None:
        return "followed build"
    return None


def _class_reason(graph, condition):
    """The reason a class condition's code gives, or None for a form this
    does not read: for a name used in an annotation, the class's module, its
    form (a NamedTuple, an Enum, a @dataclass, a plain class or a class
    whose every base is from outside the project, such as Exception) and
    whether its body defines a method; for a class statement on a project
    base, that the base and every project class it derives from define no
    `__init_subclass__` or `__class_getitem__` and name no metaclass; for a
    class of its own module used as a value, its form as for an annotation,
    that same hook-free chain, and whether the use is an annotation."""
    import ast

    text = condition[1] if condition[0] == "call" and len(condition) > 1 else ""

    def class_node(name, module):
        assert graph.load(module), module
        owned = graph.project_name(name, module)
        if owned is None or not owned[1] or not graph.load(owned[0]):
            return None, None
        return owned[0], graph.classes.get(owned[0], {}).get(owned[1])

    def described(owner, node):
        bases = [ast.unparse(base) for base in node.bases]
        decorators = [ast.unparse(decorator) for decorator in node.decorator_list]
        outside = all(
            isinstance(base, ast.Name) and graph.project_name(base.id, owner) is None
            or isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
            and graph.project_name(base.value.id, owner) is None
            for base in node.bases
        )
        form = (
            "NamedTuple " if bases in (["NamedTuple"], ["typing.NamedTuple"]) and not decorators
            else "Enum " if bases in (["Enum"], ["enum.Enum"]) and not decorators
            else "@dataclass " if decorators in (["dataclass"], ["dataclasses.dataclass"]) and not bases
            else "" if not bases and not decorators
            else f"{' and '.join(bases)}-based " if outside and not decorators
            else None
        )
        if form is None or node.keywords:
            return None
        short = owner.rpartition(".")[2]
        has_methods = any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) for item in node.body)
        return (f"{short}{chr(39) if short.endswith('s') else chr(39) + 's'} {form}class"
                f"{' with methods' if has_methods else ''}")

    def no_hooks(owner, node, depth=0):
        if depth > 8 or any(keyword.arg == "metaclass" for keyword in node.keywords) or any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name in ("__init_subclass__", "__class_getitem__")
            for item in node.body
        ):
            return False
        for base in node.bases:
            if isinstance(base, ast.Name):
                parent_owner, parent = class_node(base.id, owner)
                if parent is not None and not no_hooks(parent_owner, parent, depth + 1):
                    return False
                if parent is None and graph.project_name(base.id, owner) is not None:
                    return False
            elif not (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                      and graph.project_name(base.value.id, owner) is None):
                return False
        return True

    found = _SHAPE_ANNOTATED.fullmatch(text)
    if found:
        owner, node = class_node(found.group(2), found.group(1))
        described_text = described(owner, node) if node is not None else None
        return None if described_text is None else f"{described_text}, named in an annotation"
    found = _SHAPE_STATEMENT.fullmatch(text)
    if found:
        module, role, name = found.groups()
        owner, node = class_node(name, module)
        return _INERT_BASE if role == "base" and node is not None and no_hooks(owner, node) else None
    found = _SHAPE_OWN.fullmatch(text)
    if found:
        module, name, in_annotation = found.groups()
        assert graph.load(module), module
        node = next((names[name] for names in graph.class_names.get(module, {}).values() if name in names), None)
        described_text = described(module, node) if node is not None and no_hooks(module, node) else None
        return None if described_text is None else (
            f"{described_text}, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, "
            + ("named in an annotation" if in_annotation else "used as a value")
        )
    return None


def _derived_reason(graph, condition):
    """The reason the census computes from the code a condition names, or
    None when it computes none and NOT_A_ROUTE must name the condition: a
    build or a compiled pattern's method (`_call_kind`, `_KIND_REASONS`),
    or a class used in an annotation, as a value or as a base
    (`_class_reason`)."""
    kind = _call_kind(graph, condition)
    return _KIND_REASONS[kind] if kind is not None else _class_reason(graph, condition)


@functools.lru_cache(maxsize=2)
def _derived_reasons_for(fingerprint: tuple) -> dict:
    """{condition: reason} for each condition `route_entry_conditions`
    finds whose reason `_derived_reason` computes, for the tree
    `fingerprint` names."""
    graph = _derived_graph()
    reasons = {condition: _derived_reason(graph, condition) for condition in route_entry_conditions()}
    return {condition: reason for condition, reason in reasons.items() if reason is not None}


class TestEveryRouteEntryConditionIsARoute:
    """Every input that selects a resolver route is a route the matrices
    measure.

    `route_inputs` reads the inputs from the detection path's code, helper
    modules included, and fails closed on any use of the environment it
    cannot resolve to a variable; `route_driven_conditions` measures what
    each route in `identity_routes.ROUTES` sets. A call into a helper counts
    as driven only when a route replaces it: an input a route drives that is
    read inside a helper does not drive the helper's other inputs.
    Every input must be driven, have a reason the census computes from its
    code (`_derived_reason`), or be listed in NOT_A_ROUTE, and every route
    input must still be read. A new route through any rung, a new reader of
    a record or setting in another project module, or a new environment
    variable in any form therefore reds an arm here until a route drives it
    or an exclusion names it. A new reader of the process or home directory
    joins the one `("cwd",)` or `("home",)` condition already there and
    reds nothing here. A new rung call site is the rung-parity test's census
    (every `_root_guard` and `_identity_at_root` call site must run).
    """

    # Inputs the detection path reads that no route sets and whose reason the
    # census cannot compute (`_derived_reason`), each with the reason the
    # matrices do not need one to.
    NOT_A_ROUTE = {
        ("env", "GIT_DISCOVERY_ACROSS_FILESYSTEM"): (
            "read beside the driven GIT_CEILING_DIRECTORIES by both marker walks "
            "(find_git_marker and project_scope._git_metadata_at_or_above), to stop at a "
            "filesystem boundary; a real filesystem boundary cannot be built in a test"
        ),
        ("env", "CLAUDE_CODE_SESSION_ID"): (
            "names the session whose record the session-record readers open; the "
            "session-record and worktree identity record routes replace those readers"
        ),
        ("env", "PYTEST_CURRENT_TEST"): "the session-record readers' test-process guard",
        ("env", "PACT_TEST_MEMORY_DIR"): (
            "read inside get_user_memory_dir, which the user-setting route replaces"
        ),
        ("env", "CLAUDE_CONFIG_DIR"): (
            "the config root under which _session_record_on_disk opens a session record, "
            "read through the session-record readers, which the session-record and "
            "worktree identity record routes replace"
        ),
        ("env", "<shared.project_scope.git_env_without_location: os.environ.items>"): (
            "the copy of the environment git_env_without_location builds for git "
            "subprocesses"
        ),
        ("home",): "a landing property, measured as the home_root shape",
        ("call", "get_worktree_identity_from_session_record"): (
            "its record is read by recorded_main_root, which the worktree identity record "
            "route replaces"
        ),
        ("call", "git_env_without_location"): "builds the environment of every git call",
        ("call", "git_dir_checkout"): (
            "the checkout a start inside a git dir belongs to, from git's answers about that "
            "start; it replaces the start the git rung and the declared rungs ask from, and "
            "its own body is read like any reached function"
        ),
        ("call", "<call to no function project_identity: git_env_without_location().items>"): (
            "iterates the environment copy git_env_without_location builds for a git "
            "subprocess"
        ),
        ("call", "<call to no function memory_api: PACTMemory._identity_at_root(...)._replace>"): (
            "_replace on the result of _identity_at_root"
        ),
        ("call", "<call to no function memory_api: a method of what PACTMemory._declared_dir_identity or PACTMemory._identity_at_root(...)._replace returns>"): (
            "a method of what _declared_dir_identity or _identity_at_root returned"
        ),
        ("call", "<call to no function memory_api: a method of what _root_guard returns>"): (
            "a method of what _root_guard returned"
        ),
        ("call", "<call to no function memory_api: a method of what main_repo_common_dir returns>"): (
            "a method of main_repo_common_dir's result, which the census cannot type"
        ),
        ("call", "<call to no function shared.project_scope: sessions_root.glob>"): (
            "a method of sessions_root, built from get_claude_config_dir(), which the "
            "census cannot type"
        ),
        ("call", "<call to no function shared.project_scope: a method of what _discover_context_record returns>"): (
            "a method of _discover_context_record's result, which the census cannot type"
        ),
        ("call", "<call to no function config: _STORE_DB_PATH.set>"): (
            "get_user_memory_dir sets the store scope aside; the user-setting route "
            "replaces get_user_memory_dir"
        ),
        ("call", "<call to no function config: _STORE_DB_PATH.get>"): (
            "get_memory_dir reads the store scope, reached through get_user_memory_dir, "
            "which the user-setting route replaces"
        ),
        ("call", "<call to no function config: _STORE_DB_PATH.reset>"): (
            "get_user_memory_dir restores the store scope; the user-setting route "
            "replaces get_user_memory_dir"
        ),
        ("call", "<call to no function memory_api: _DETECTION_ANSWERS.get>"): (
            "the constructor's per-pass answer memo"
        ),
        ("call", "<call to no function memory_api: _DETECTION_LISTINGS.get>"): (
            "the constructor's per-pass worktree-listing memo"
        ),
        ("call", "<call to no function memory_api: _DETECTION_COMMON_DIRS.get>"): (
            "the constructor's per-pass common-dir memo"
        ),
        ("call", "<call to no function memory_api: _DETECTION_COMMON_DIRS.set>"): (
            "main_repo_common_dir sets the per-pass memo aside while it asks git"
        ),
        ("call", "<call to no function memory_api: _DETECTION_COMMON_DIRS.reset>"): (
            "main_repo_common_dir restores the per-pass memo after asking git"
        ),
        ("call", "<call to no function project_identity: DETECTION_GIT_DIRS.get>"): (
            "the constructor's per-pass git-dir memo"
        ),
        ("call", "<name imported from a project module memory_api: project_identity.DETECTION_GIT_DIRS>"): (
            "detection_pass names the identity layer's per-pass git-dir memo, to start it and to hand "
            "it back on a join"
        ),
        ("call", "<call to no function memory_api: project_identity.DETECTION_GIT_DIRS.get>"): (
            "detection_pass's join hands back the outer pass's git-dir memo"
        ),
        ("call", "<call to no function memory_api: project_identity.DETECTION_GIT_DIRS.set>"): (
            "detection_pass starts the per-pass git-dir memo empty in an outermost pass"
        ),
        ("call", "<call to no function memory_api: _DETECTION_ANSWERS.set>"): (
            "detection_pass starts the per-pass answer memo empty in an outermost pass"
        ),
        ("call", "<call to no function memory_api: _DETECTION_LISTINGS.set>"): (
            "detection_pass starts the per-pass worktree-listing memo empty in an outermost pass"
        ),
        ("call", "git_results_pass"): (
            "detection_pass opens the pass's memo of git results; git_results_pass is read as a root"
        ),
        ("call", "<call to no function shared.git_helpers: PASS_GIT_RESULTS.set>"): (
            "git_results_pass starts the pass's memo of git results empty in an outermost pass"
        ),
        ("call", "<call to no function shared.git_helpers: PASS_GIT_RESULTS.reset>"): (
            "git_results_pass ends the pass's memo of git results with the block"
        ),
        ("call", "<call to no function shared.backlog: self.logger.addFilter>"): (
            "the decline-warning holder attaches itself to the memory skill's logger while a backlog "
            "command runs"
        ),
        ("call", "<call to no function shared.backlog: self.logger.removeFilter>"): (
            "the decline-warning holder detaches itself from the memory skill's logger"
        ),
        ("call", "<call to no function shared.backlog: self.records.append>"): (
            "the decline-warning holder keeps a held record in its own list"
        ),
        ("call", "<call to no function shared.backlog: self.logger.handle>"): (
            "the decline-warning holder hands a held record back to the memory skill's logger"
        ),
        ("call", "<call to no function shared.backlog: _memory_api().detection_pass>"): (
            "backlog.main opens the memory skill's detection_pass through the backlog's lazy import of "
            "the skill; detection_pass is read as a root"
        ),
        ("call", "<call to no function shared.backlog: held.replay>"): (
            "backlog.main prints the held decline warnings when a command does not succeed; it hands "
            "logged records to the skill's logger and reads no input"
        ),
        ("call", "<call to no function memory_api: logger.debug>"): (
            "a logger method; every project object built on the path is a named build"
        ),
        ("call", "<call to no function memory_api: logger.warning>"): (
            "a logger method; every project object built on the path is a named build"
        ),
        ("call", "<call to no function database: a method of what CANONICAL_ID_RE.fullmatch returns>"): (
            "a method of that pattern's match"
        ),
        ("call", "<call to no function project_identity: a method of what _SCHEME_PREFIX_RE.match returns>"): (
            "a method of that pattern's match"
        ),
        ("call", "<call to no function project_identity: a method of what _SCP_FORM_RE.match returns>"): (
            "a method of that pattern's match"
        ),
        ("call", "<call to no function project_identity: a method of what _scp_match returns>"): (
            "a method of _scp_match's result"
        ),
        ("call", "<call to no function project_identity: host.isascii>"): (
            "a method on a local the census cannot type; every project object built on "
            "the path is a named build"
        ),
        ("call", "<call to no function project_identity: host.isalpha>"): (
            "a method on a local the census cannot type; every project object built on "
            "the path is a named build"
        ),
        ("call", "<call to no function project_identity: host.lower>"): (
            "a method on a local the census cannot type; every project object built on "
            "the path is a named build"
        ),
        ("call", "<call to no function project_identity: scp_path.startswith>"): (
            "a method on a local the census cannot type; every project object built on "
            "the path is a named build"
        ),
        ("call", "<call to no function project_identity: scp_path.replace>"): (
            "a method on a local the census cannot type; every project object built on "
            "the path is a named build"
        ),
        ("call", "store_scope"): (
            "the _with_store_scope decorator runs each PACTMemory method inside "
            "store_scope(self._db_path), which binds the store file; get_user_memory_dir, "
            "which the user-setting route replaces, sets that binding aside"
        ),
        ("call", "<name imported from a project module memory_api: ProjectScopeDisagreementError>"): (
            "pact_session's error class, the base of a class memory_api defines"
        ),
        ("call", "<name imported from a project module project_identity: CANONICAL_ID_MAX_LENGTH>"): (
            "database's id length constant, which the importing function returns"
        ),
        ("call", "<name imported from a project module shared.project_scope: _UNSAFE_SLUG_CHARS_RE>"): (
            "pact_context's compiled pattern, whose sub replaces unsafe characters in the session id"
        ),
        ("call", "<name imported from a project module graph: _connection_sqlite3>"): (
            "the sqlite3 module database imports, read for its IntegrityError class in a module-level tuple"
        ),
        ("call", "is_canonical_id"): "validates an id value",
        ("call", "validate_identifier_for_write"): "validates an id value",
        ("call", "answered_once"): (
            "runs the reader's own git command once per detection pass and hands later "
            "askers of the same question its result; the command is the reader's input, "
            "and answered_once's own body is read like any reached function"
        ),
        ("call", "read_git_file"): (
            "the one bounded reader of a .git file or a worktree admin gitdir file, which "
            "the layout routes build; its own body is read like any reached function"
        ),
        ("call", "worktree_admin_gitdir"): (
            "trims the text read_git_file returned; its own body is read like any reached "
            "function"
        ),
        ("call", "git_config_bool"): (
            "parses the value of a git boolean environment variable the way git does; "
            "the variable is the input, and its own body is read like any reached "
            "function"
        ),
        ("call", "<call to no function shared.git_helpers: PASS_GIT_RESULTS.get>"): (
            "reads the pass's memo of git results, set only by git_results_pass"
        ),
        ("env", "<shared.git_helpers._question: environment.items>"): (
            "the copy of the environment a git command runs with, scanned by prefix "
            "(git reads arbitrary GIT_* variables) only to key the pass's memo of that "
            "command's result"
        ),
        # The memory CLI's command work, run inside cli.main's detection pass.
        ("call", "<call to no function cli: r.to_dict>"): (
            "renders a search result the store returned for the command's envelope"
        ),
        ("call", "<call to no function cli: result.to_dict>"): (
            "renders the record the store returned for the command's envelope"
        ),
        ("call", "<call to no function cli: state.get>"): (
            "reads the legacy-bucket state the store recorded at construction, for the rekey verdict"
        ),
        ("call", "<call to no function cli: store.exists>"): (
            "a method of the store path rekey resolves (resolve_db_path), checked before the store "
            "is opened"
        ),
        ("call", "<call to no function config: _READ_ONLY_SCOPE.set>"): (
            "read_only_scope declares the block read-only"
        ),
        ("call", "<call to no function config: _READ_ONLY_SCOPE.reset>"): (
            "read_only_scope restores the read-only declaration"
        ),
        ("call", "<call to no function config: _READ_ONLY_SCOPE.get>"): (
            "read_only_scope_active reads the read-only declaration"
        ),
        ("call", "<call to no function database: a method of what get_db_path returns>"): (
            "a method of the store path get_db_path returns, checked when the store is created"
        ),
        ("call", "<call to no function database: logger.debug>"): "database's module logger",
        ("call", "<call to no function database: logger.info>"): "database's module logger",
        ("call", "<call to no function database: logger.warning>"): "database's module logger",
        ("call", "<call to no function pact_session: _context_record_on_disk(...).get>"): (
            "reads the session id from the session's context record"
        ),
        ("call", "<call to no function pact_session: a method of what _context_file_path returns>"): (
            "reads the session's context file, for the session id a saved memory records"
        ),
        ("call", "<call to no function setup_memory: a method of what get_memory_dir returns>"): (
            "a method of the memory directory get_memory_dir returns, for the setup status"
        ),
        ("call", "<call to no function setup_memory: get_memory_dir().mkdir>"): (
            "creates the memory directory during setup"
        ),
        ("call", "<call to no function setup_memory: logger.error>"): "setup_memory's module logger",
        ("call", "<call to no function setup_memory: logger.info>"): "setup_memory's module logger",
        ("call", "<call to no function shared.claude_md_manager: a method of what managed_document_paths returns>"): (
            "a method of a candidate CLAUDE.md path, to find the session's managed document for a sync"
        ),
        ("call", "<call to no function shared.claude_md_manager: a method of what session_memory_root returns>"): (
            "_replace on the session-root record session_memory_root returned"
        ),
        ("call", "<call to no function shared.pact_context: candidate.resolve>"): (
            "a method of a session-directory path built from get_claude_config_dir(), which the "
            "census cannot type"
        ),
        ("call", "<call to no function shared.pact_context: sessions_root.resolve>"): (
            "a method of the sessions directory built from get_claude_config_dir(), which the "
            "census cannot type"
        ),
        ("call", "<call to no function shared.project_scope: _fsdecode(...).split>"): (
            "splits the text _fsdecode decodes from git's output"
        ),
        ("call", "<call to no function shared.project_scope: _fsdecode(...).strip>"): (
            "strips the text _fsdecode decodes from git's output"
        ),
        ("call", "<call to no function shared.project_scope: _fsdecode(...).strip().lower>"): (
            "lowercases the text _fsdecode decodes from git's output"
        ),
        ("call", "<call to no function shared.project_scope: a method of what get_claude_config_dir returns>"): (
            "a method of the config directory get_claude_config_dir returns, which the census "
            "cannot type"
        ),
        ("call", "<call to no function shared.project_scope: forms.append>"): (
            "adds a path form to a local list of one located document's forms"
        ),
        ("call", "<call to no function shared.project_scope: located.absolute>"): (
            "a method of a located document path, which the census cannot type"
        ),
        ("call", "<name imported from a project module cli: AmbiguousPrefixError>"): (
            "database's error class, caught by a command"
        ),
        ("call", "<name imported from a project module cli: PrefixTooShortError>"): (
            "database's error class, caught by a command"
        ),
        ("call", "<name imported from a project module cli: ProjectIdentityUnresolvedError>"): (
            "memory_api's error class, caught by a command"
        ),
        ("call", "<name imported from a project module cli: ProjectScopeDisagreementError>"): (
            "pact_session's error class, caught by a command"
        ),
        ("call", "<name imported from a project module cli: CALLER_FACING_CREATE_FIELDS>"): (
            "the field names a caller may set on a save, shown in an error envelope"
        ),
        ("call", "<name imported from a project module cli: CALLER_FACING_UPDATE_FIELDS>"): (
            "the field names a caller may set on an update, shown in an error envelope"
        ),
        ("call", "<name imported from a project module cli: db_sqlite3>"): (
            "database's sqlite3 module, whose DatabaseError a command catches"
        ),
        ("call", "<name imported from a project module database: DERIVED_STORE_ORIGINS>"): (
            "the store-path origins config derives, a constant set"
        ),
        ("call", "<name imported from a project module shared.backlog: memory_api.logger>"): (
            "the memory skill's logger, which the decline-warning holder attaches to"
        ),
        ("call", "<name imported from a project module shared.claude_md_manager: ROOT_CAUSE_USER_GLOBAL>"): (
            "a root-cause constant project_scope names"
        ),
        ("call", "<name imported from a project module staleness: PACT_BOUNDARY_PREFIXES>"): (
            "a CLAUDE.md boundary-marker prefix constant"
        ),
        ("call", "<name imported from a project module staleness: SESSION_BOUNDARY_PREFIX>"): (
            "a CLAUDE.md boundary-marker prefix constant"
        ),
        ("call", "_unusable_identifier"): "validates an id value",
        ("call", "get_session_id_from_context_file"): (
            "detects the session id a saved memory records, not a project identity"
        ),
        ("call", "project_bucket_counts"): "counts the store's rows per project bucket for the census",
        ("env", "PWD"): (
            "session_anchor's fallback when the process directory is gone; it locates the session's "
            "CLAUDE.md for a sync, not a memory identity"
        ),
        # The store work a memory CLI command runs (`_DetectionGraph.instance_target`):
        # each runs after PACTMemory.__init__ has detected the identity
        # (`test_only_pact_memory_init_sets_the_identity_store_work_reads`), and no git
        # command that work runs takes a value one of these reads
        # (`test_no_git_question_takes_a_store_work_value`).
        ('call', 'bucket_session_counts'): (
            "counts a legacy bucket's rows by the session id they carry, as evidence for the read scope; it reads the store, not git or the identity" + _AFTER_DETECTION
        ),
        ('call', 'count_project_file_paths'): (
            "counts a bucket's tracked file paths in the store for the bucket census" + _AFTER_DETECTION
        ),
        ('call', 'count_project_memories'): (
            "counts a bucket's rows in the store for the bucket census" + _AFTER_DETECTION
        ),
        ('call', 'create_memory'): (
            "writes a saved memory's row to the store" + _AFTER_DETECTION
        ),
        ('call', 'db_connection'): (
            'opens a connection to the store' + _AFTER_DETECTION
        ),
        ('call', 'delete_memory'): (
            "deletes a memory's row from the store" + _AFTER_DETECTION
        ),
        ('call', 'ensure_initialized'): (
            "creates the store's schema if it is missing" + _AFTER_DETECTION
        ),
        ('call', 'ensure_memory_ready'): (
            'prepares the memory system (dependencies, migration, embedding catch-up) before the store is used' + _AFTER_DETECTION
        ),
        ('call', 'load_sqlite_vec'): (
            'loads the vector extension into a store connection' + _AFTER_DETECTION
        ),
        ('call', 'env_record_project_dir_disagreement'): (
            "compares CLAUDE_PROJECT_DIR with the session record's project directory so a write can refuse when they disagree; it changes no answer the identity reads" + _AFTER_DETECTION_ASKS
        ),
        ('call', 'format_project_dir_disagreement'): (
            'formats that disagreement for the refusal' + _AFTER_DETECTION_ASKS
        ),
        ('call', 'generate_embedding'): (
            "computes a memory's embedding vector" + _AFTER_DETECTION
        ),
        ('call', 'generate_embedding_text'): (
            "builds the text a memory's embedding is computed from" + _AFTER_DETECTION
        ),
        ('call', 'get_db_path'): (
            "names the store's database file" + _AFTER_DETECTION
        ),
        ('call', 'get_embedding_catchup_status'): (
            "reports the embedding catch-up's state for the status command" + _AFTER_DETECTION
        ),
        ('call', 'get_files_for_memory'): (
            'reads the file paths linked to a memory from the store' + _AFTER_DETECTION
        ),
        ('call', 'get_graph_stats'): (
            "counts the store's file-graph rows for the status command" + _AFTER_DETECTION
        ),
        ('call', 'get_memory'): (
            "reads a memory's row from the store" + _AFTER_DETECTION
        ),
        ('call', 'get_memory_count'): (
            "counts the store's rows in the read scope for the status command" + _AFTER_DETECTION
        ),
        ('call', 'get_search_capabilities'): (
            'reports which search modes the installed extensions allow' + _AFTER_DETECTION
        ),
        ('call', 'graph_enhanced_search'): (
            "searches the store's rows" + _AFTER_DETECTION
        ),
        ('call', 'legacy_candidate_plausibility'): (
            "judges from the store's rows whether a legacy bucket belongs to this project, for the read scope; it asks git nothing" + _AFTER_DETECTION
        ),
        ('call', 'link_memory_to_paths'): (
            'links a saved memory to its file paths in the store' + _AFTER_DETECTION
        ),
        ('call', 'list_memories'): (
            "lists the store's rows in the read scope" + _AFTER_DETECTION
        ),
        ('call', 'memory_from_db_row'): (
            'builds a memory object from a store row' + _AFTER_DETECTION
        ),
        ('call', 'project_memories_to_claude_md'): (
            "writes the project's memories to the session's CLAUDE.md" + _AFTER_DETECTION
        ),
        ('call', 'read_only_scope'): (
            'declares a block that must not write the store' + _AFTER_DETECTION
        ),
        ('call', 'reconstruct_session_dir'): (
            "rebuilds a session's directory path from the config directory, to read the session's context record" + _AFTER_DETECTION
        ),
        ('call', 'resolve_db_path'): (
            "names the store's database file" + _AFTER_DETECTION
        ),
        ('call', 'resolve_memory_id_prefix'): (
            "resolves a memory id prefix against the store's rows" + _AFTER_DETECTION
        ),
        ('call', 'store_path_origin'): (
            "names where the store's path came from (an override, the environment, the home)" + _AFTER_DETECTION
        ),
        ('call', 'sync_retrieved_to_claude_md'): (
            "writes retrieved memories to the session's CLAUDE.md" + _AFTER_DETECTION
        ),
        ('call', 'sync_to_claude_md'): (
            "writes a saved memory to the session's CLAUDE.md" + _AFTER_DETECTION
        ),
        ('call', 'track_file'): (
            "records a file path in the store's file graph" + _AFTER_DETECTION
        ),
        ('call', 'update_memory'): (
            "updates a memory's row in the store" + _AFTER_DETECTION
        ),
        ('call', '<call to no function database: a method of what _serialize_json_fields returns>'): (
            'reads the fields of the row dict _serialize_json_fields builds for an insert' + _AFTER_DETECTION
        ),
        ('call', '<call to no function database: current.get>'): (
            "reads an update's current list fields, to merge new items into them" + _AFTER_DETECTION
        ),
        ('call', '<call to no function database: normalized.items>'): (
            "iterates an update's normalized fields" + _AFTER_DETECTION
        ),
        ('call', '<call to no function database: normalized.keys>'): (
            "lists an update's normalized fields" + _AFTER_DETECTION
        ),
        ('call', '<call to no function database: working.items>'): (
            "iterates an update's fields to normalize them" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module database: Decision>'): (
            "models' record class, registered as the class of a decisions field" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module database: Entity>'): (
            "models' record class, registered as the class of an entities field" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module database: TaskItem>'): (
            "models' record class, registered as the class of an active_tasks field" + _AFTER_DETECTION
        ),
        ('call', '<call to no function embedding_catchup: a method of what get_memory returns>'): (
            'reads a field of a memory row the store returned, to re-embed it' + _AFTER_DETECTION
        ),
        ('call', '<call to no function embedding_catchup: store_sqlite3.connect>'): (
            'opens a read-only connection to the store for its integrity check' + _AFTER_DETECTION
        ),
        ('call', '<call to no function embedding_catchup: a method of what store_sqlite3.connect returns>'): (
            "runs the store's integrity check on that connection and closes it" + _AFTER_DETECTION
        ),
        ('call', '<call to no function embedding_catchup: logger.debug>'): (
            "embedding_catchup's module logger" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module embedding_catchup: MIN_CATCHUP_RAM_MB>'): (
            'the memory floor below which the embedding catch-up does not run, a constant' + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module embedding_catchup: SQLITE_EXTENSIONS_ENABLED>'): (
            "whether this Python's sqlite3 can load extensions, a constant" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module embedding_catchup: store_sqlite3>'): (
            "database's sqlite3 module, used to open the store and catch its errors" + _AFTER_DETECTION
        ),
        ('call', '<call to no function embeddings: MemoryObject.from_dict>'): (
            'builds a memory object from a memory dict, to read its searchable text' + _AFTER_DETECTION
        ),
        ('call', '<call to no function embeddings: a method of what MemoryObject.from_dict returns>'): (
            "reads a memory object's searchable text" + _AFTER_DETECTION
        ),
        ('call', '<call to no function embeddings: get_embedding_service().generate>'): (
            'computes an embedding vector with the embedding service' + _AFTER_DETECTION
        ),
        ('call', '<call to no function embeddings: a method of what get_embedding_service returns>'): (
            'asks the embedding service whether it is available' + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module embeddings: MemoryObject>'): (
            "models' memory class, whose from_dict builds the object a memory dict describes" + _AFTER_DETECTION
        ),
        ('call', '<call to no function graph: logger.debug>'): (
            "graph's module logger" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: a method of what get_memory returns>'): (
            'reads a field of a memory row the store returned' + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: a method of what get_search_capabilities returns>'): (
            'reads the search mode the extensions allow' + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: a method of what project_bucket_counts returns>'): (
            "reads a bucket's row count from the bucket census" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: back.is_absolute>'): (
            "a method of the path a worktree's gitdir file names, which the census cannot type; it checks that a listed worktree points back at its marker, for the read scope" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: back.resolve>'): (
            "a method of the path a worktree's gitdir file names, which the census cannot type; it checks that a listed worktree points back at its marker, for the read scope" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: before_snapshot.get>'): (
            "reads a field of a memory's row from before an update" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: bucket.casefold>'): (
            'compares a bucket name with the legacy name, ignoring case' + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: _UNPROVEN_CAUSES[reason].format>'): (
            "fills the clause an unproven bucket's warning prints with the bucket's session counts" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: _LOCKED_READS_ONLY.get>'): (
            "the flag that makes a read-only open of the store use SQLite's locking; it chooses how the store is opened, not what is read"
        ),
        ('call', '<call to no function memory_api: _LOCKED_READS_ONLY.set>'): (
            "_stable_read_only sets the locking flag while it repeats a read the store moved under"
        ),
        ('call', '<call to no function memory_api: _LOCKED_READS_ONLY.reset>'): (
            "_stable_read_only clears the locking flag after the repeated read"
        ),
        ('call', '<call to no function memory_api: _STABLE_READ_ACTIVE.get>'): (
            "the flag that tells a self-wrapping read it already runs inside _stable_read_only"
        ),
        ('call', '<call to no function memory_api: _STABLE_READ_ACTIVE.set>'): (
            "_stable_read_only marks that a read runs inside it"
        ),
        ('call', '<call to no function memory_api: _STABLE_READ_ACTIVE.reset>'): (
            "_stable_read_only clears its mark after the read"
        ),
        ('call', '<call to no function memory_api: logger.info>'): (
            "memory_api's module logger" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: memory_dict.get>'): (
            'reads a field of a memory row the store listed' + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_api: read_project_bucket_census(...).get>'): (
            "reads a bucket's row count from the bucket census" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module memory_api: AmbientSyncRefused>'): (
            "working_memory's error class, caught when a CLAUDE.md sync is refused" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module memory_api: DERIVED_STORE_ORIGINS>'): (
            'the store-path origins config derives, a constant set' + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module memory_api: MAX_WORKING_MEMORIES>'): (
            'how many memories the Working Memory section holds, a constant' + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module memory_api: MEMORY_ID_LENGTH>'): (
            'the length of a full memory id, a constant' + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module memory_api: PRUNE_LEDGER_NAME>'): (
            "the prune ledger's file name, a constant" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module memory_api: SQLITE_EXTENSIONS_ENABLED>'): (
            "whether this Python's sqlite3 can load extensions, a constant" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module memory_api: SyncResult>'): (
            "working_memory's sync-result class, whose status constants a save records" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module memory_api: _UNSAFE_SLUG_CHARS_RE>'): (
            "pact_context's compiled literal pattern for unsafe slug characters" + _AFTER_DETECTION
        ),
        ('env', 'CI'): (
            "memory_init's CI check, which skips installing dependencies in CI; it changes no answer the identity reads" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_init: a method of what _UNSAFE_SLUG_CHARS_RE.sub returns>'): (
            "encodes the slug that pattern's substitution returns, to hash it" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_init: a method of what get_embedding_service returns>'): (
            'computes an embedding vector with the embedding service, to re-embed a memory' + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_init: logger.debug>'): (
            "memory_init's module logger" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_init: logger.info>'): (
            "memory_init's module logger" + _AFTER_DETECTION
        ),
        ('call', '<call to no function memory_init: logger.warning>'): (
            "memory_init's module logger" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module memory_init: EMBEDDING_DIM>'): (
            "the embedding vector's dimension, a constant" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module memory_init: _UNSAFE_SLUG_CHARS_RE>'): (
            "pact_context's compiled literal pattern for unsafe slug characters" + _AFTER_DETECTION
        ),
        ('call', '<call to no function models: a method of what _validate_from_dict_keys returns>'): (
            'reads a field of the dict _validate_from_dict_keys checked' + _AFTER_DETECTION
        ),
        ('call', '<call to no function models: logger.warning>'): (
            "models' module logger" + _AFTER_DETECTION
        ),
        ('call', '<call to no function search: a method of what check_embedding_availability returns>'): (
            "reads the embedding backend's status for the search capabilities" + _AFTER_DETECTION
        ),
        ('call', '<call to no function search: a method of what get_memory returns>'): (
            'reads a field of a memory row the store returned' + _AFTER_DETECTION
        ),
        ('call', '<call to no function search: logger.debug>'): (
            "search's module logger" + _AFTER_DETECTION
        ),
        ('call', '<call to no function search: logger.warning>'): (
            "search's module logger" + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module search: SQLITE_EXTENSIONS_ENABLED>'): (
            "whether this Python's sqlite3 can load extensions, a constant" + _AFTER_DETECTION
        ),
        ('call', '<call to no function shared.claude_md_markers: _COMMENT_START_RE.match>'): (
            'a method of the HTML comment start pattern, a compiled literal taken from the _HTML_BLOCKS '
            'table, matched against a CLAUDE.md line' + _AFTER_DETECTION
        ),
        ('call', '<call to no function shared.claude_md_markers: a method of what _LINE_RE.finditer returns>'): (
            "a method of that pattern's match" + _AFTER_DETECTION
        ),
        ('call', '<call to no function shared.claude_md_markers: a method of what _OPENER_RE.fullmatch returns>'): (
            "a method of that pattern's match" + _AFTER_DETECTION
        ),
        ('call', '<call to no function shared.claude_md_markers: content.startswith>'): (
            "tests a CLAUDE.md line's text for a byte-order mark" + _AFTER_DETECTION
        ),
        ('call', '<call to no function shared.claude_md_markers: end_re.search>'): (
            "a method of a CLAUDE.md fence's end pattern, built from the fence it closes" + _AFTER_DETECTION
        ),
        ('call', '<call to no function shared.claude_md_markers: html_end.search>'): (
            'a method of the HTML comment end pattern, built for the line scan' + _AFTER_DETECTION
        ),
        ('call', '<call to no function shared.claude_md_markers: start_re.match>'): (
            "a method of a CLAUDE.md fence's start pattern" + _AFTER_DETECTION
        ),
        ('call', '<call to no function working_memory: a method of what _resolve_display_claude_md_with_base returns>'): (
            'a method of the CLAUDE.md path a sync writes to, which the census cannot type' + _AFTER_DETECTION
        ),
        ('call', '<call to no function working_memory: a method of what parse returns>'): (
            'a method of the parsed CLAUDE.md document a sync edits' + _AFTER_DETECTION
        ),
        ('call', '<call to no function working_memory: after_section.startswith>'): (
            'tests the CLAUDE.md text after a section for a leading newline' + _AFTER_DETECTION
        ),
        ('call', '<call to no function working_memory: content.endswith>'): (
            "tests a section's text for a final newline" + _AFTER_DETECTION
        ),
        ('call', '<call to no function working_memory: logger.debug>'): (
            "working_memory's module logger" + _AFTER_DETECTION
        ),
        ('call', '<call to no function working_memory: logger.info>'): (
            "working_memory's module logger" + _AFTER_DETECTION
        ),
        ('call', '<call to no function working_memory: logger.warning>'): (
            "working_memory's module logger" + _AFTER_DETECTION
        ),
        ('call', '<call to no function working_memory: trimmed_entries.pop>'): (
            'drops the last retrieved-context entry to keep the section under its token budget' + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module working_memory: STORE_ORIGIN_HOME>'): (
            'the store-path origin that names the home directory, a constant' + _AFTER_DETECTION
        ),
        ('call', '<name imported from a project module working_memory: State>'): (
            "claude_md_markers' section-state constants, compared with a found section's state" + _AFTER_DETECTION
        ),
    }

    def test_the_table_names_only_what_the_code_cannot_explain(self, route_driven_conditions):
        """NOT_A_ROUTE holds only judgement: no row names a condition whose
        reason the census computes from its code (`_derived_reason`), and no
        row's reason is one the census computes (`_KIND_REASONS`), so a row
        cannot claim a build or a pattern the code is not. Control: the
        computed reasons cover a record build, a compiled pattern, a class in
        an annotation, a class of its own module and a class statement's
        base, and none is a condition a route drives."""
        derived = _derived_reasons_for(_source_fingerprint())
        assert set(self.NOT_A_ROUTE) & set(derived) == set(), sorted(set(self.NOT_A_ROUTE) & set(derived))
        claimed = {condition for condition, reason in self.NOT_A_ROUTE.items() if reason in _KIND_REASONS.values()}
        assert claimed == set(), claimed
        assert set(derived) & set(route_driven_conditions) == set(), set(derived) & set(route_driven_conditions)
        reasons = set(derived.values())
        assert {_KIND_REASONS["build"], _KIND_REASONS["pattern"], _INERT_BASE} <= reasons, reasons  # control
        assert any(reason.endswith("named in an annotation") for reason in reasons), reasons  # control
        assert any(reason.endswith("used as a value") for reason in reasons), reasons  # control

    def test_a_computed_reason_follows_the_code(self, tmp_path, monkeypatch):
        """`_derived_reason` on scratch code: a fields-only NamedTuple build,
        a compiled literal pattern's method and a hook-free class in an
        annotation get their reasons; the same build with a method, the
        pattern compiled from a non-literal, and the class with an
        `__init_subclass__` get none, so the census fails until NOT_A_ROUTE
        names them."""
        import sys

        package = tmp_path / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "m.py").write_text(
            "import re\nfrom typing import NamedTuple\n\nPATTERN = re.compile(r'x', re.I)\n"
            "SOURCE = 'x'\nLOOSE = re.compile(SOURCE)\n\n"
            "class Record(NamedTuple):\n    name: str = ''\n\n"
            "class Busy(NamedTuple):\n    name: str = ''\n\n    def size(self):\n        return 1\n\n"
            "class Plain:\n    pass\n\n"
            "class Hooked:\n    def __init_subclass__(cls):\n        pass\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path,))
        graph = _DetectionGraph()
        assert graph.load("pkg.m")
        _pass_openings(graph)
        own = "<class of its own module used as a value pkg.m: {} in an annotation>"
        rows = {
            "<call to no function pkg.m: Record>": _KIND_REASONS["build"],
            "<call to no function pkg.m: PATTERN.match>": _KIND_REASONS["pattern"],
            own.format("Plain"): "m's class, with no __init_subclass__, __class_getitem__ or metaclass in its "
                                 "project chain, named in an annotation",
            "<call to no function pkg.m: Busy>": None,
            "<call to no function pkg.m: LOOSE.match>": None,
            own.format("Hooked"): None,
        }
        assert {text: _derived_reason(graph, ("call", text)) for text in rows} == rows


    def test_a_class_statement_on_a_project_class_is_reported(self):
        """A base, keyword value or decorator of a class statement reached
        through a project name is reported: a class of the same module, an
        imported class, subscripted or not. A standard-library name is not,
        and neither is a project function, which `references` follows."""
        import ast

        graph = _DetectionGraph()
        assert graph.load("memory_api")
        function = next(name for module, name in sorted(graph.defs) if module == "memory_api" and "." not in name)

        def reported(source):
            node = ast.parse(source).body[0]
            return [graph.class_statement_name(expression, "memory_api")
                    for _role, expression in _class_statement_names(node)]

        assert "PACTMemory" in graph.classes["memory_api"]  # control: a same-module class
        assert reported("class X(PACTMemory): pass") == [True]
        assert reported("class X(PACTMemory[int]): pass") == [True]
        assert reported("class X(metaclass=PACTMemory): pass") == [True]
        assert reported("@PACTMemory\nclass X: pass") == [True]
        assert reported("class X(ProjectScopeDisagreementError): pass") == [True]
        assert reported("@dataclass\nclass X(RuntimeError, metaclass=type): pass") == [False, False, False]
        assert reported(f"@{function}\nclass X: pass") == [False]
        global_name = next(name for name in sorted(graph.globals["memory_api"]) if name not in graph.classes["memory_api"])
        assert reported(f"class X(*{global_name}): pass") == [True]
        assert reported("class X(*(RuntimeError,)): pass") == [False]

    def test_a_class_of_its_own_module_used_as_a_value_is_reported(self):
        """A load of any class the same module defines, at any depth and by
        its qualified name, is reported wherever it is not the callee of a
        call: handed to `type` or `types.new_class`, tested with
        `isinstance`, named in an annotation, or inside a class base,
        keyword or decorator expression. A name two classes share gives
        both. A standard-library class and a local are not."""
        import ast

        graph = _DetectionGraph()
        assert graph.load("memory_api")

        def loads(source, module="memory_api"):
            tree = ast.parse(source)
            callees = {id(sub.func) for sub in ast.walk(tree) if isinstance(sub, ast.Call)}
            return sorted(
                name
                for sub in ast.walk(tree)
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and id(sub) not in callees
                for name in graph.own_class_value(sub, module) or ["-"]
            )

        assert "PACTMemory" in graph.classes["memory_api"]  # control: a same-module class
        assert loads("X = type('X', (PACTMemory,), {})") == ["PACTMemory"]
        assert loads("X = types.new_class('X', (PACTMemory,))") == ["-", "PACTMemory"]
        assert loads("ok = isinstance(value, PACTMemory)") == ["-", "PACTMemory"]
        assert loads("def f(memory: PACTMemory): pass") == ["PACTMemory"]
        assert loads("class X(type(PACTMemory)): pass") == ["PACTMemory"]
        assert loads("class X(*(PACTMemory,)): pass") == ["PACTMemory"]
        assert loads("@wrap(PACTMemory)\nclass X: pass") == ["PACTMemory"]
        assert loads("X = type('X', (RuntimeError,), {})") == ["-"]
        assert loads("memory = PACTMemory()") == []

        nested = ast.parse(
            "class Outer:\n    class Inner:\n        pass\n"
            "def build():\n    class Inner:\n        pass\n    class Local:\n        pass\n"
        )
        graph.class_names["probe"] = _class_names(nested)
        graph.aliases["probe"] = {}
        assert {simple: sorted(found) for simple, found in graph.class_names["probe"].items()} == {
            "Outer": ["Outer"], "Inner": ["Outer.Inner", "build.Inner"], "Local": ["build.Local"],
        }
        assert loads("Made = type('Made', (Inner,), {})", "probe") == ["Outer.Inner", "build.Inner"]
        assert loads("class Sub(Local): pass", "probe") == ["build.Local"]
        assert graph.project_name("Local", "probe") == ("probe", "Local")

    def test_a_class_inside_a_class_head_expression_is_reported(self, tmp_path, monkeypatch):
        """The value scan (`_own_class_values`) reports a class of the same
        module wherever it stands inside a class base, keyword or decorator
        expression, nested classes included by qualified name; a callee is
        not a value."""
        import ast
        import sys

        package = tmp_path / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "heads.py").write_text(
            "class Base:\n    pass\n\n"
            "def wrap(base):\n    return lambda cls: cls\n\n"
            "class X(type(Base)):\n    pass\n\n"
            "class Y(*(Base,)):\n    pass\n\n"
            "@wrap(Base)\nclass Z:\n    pass\n\n"
            "class Outer:\n    class Inner:\n        pass\n\n    class Sub(Inner):\n        pass\n\n"
            "    Made = type('Made', (Inner,), {})\n\n"
            "class Built(dict[str, Base]):\n    pass\n\n"
            "made = Base()\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path,))
        graph = _DetectionGraph()
        assert graph.load("pkg.heads")
        code = graph.import_time["pkg.heads"]
        callees = {id(sub.func) for sub in ast.walk(code) if isinstance(sub, ast.Call)}
        found = sorted((shown, line) for shown, line in _own_class_values(graph, "pkg.heads", code, callees, set()))
        assert found == [
            ("Base", 7), ("Base", 10), ("Base", 13), ("Base", 26),
            ("Outer.Inner", 21), ("Outer.Inner", 24),
        ], found

    def test_every_entry_condition_is_driven_or_excluded(self, route_driven_conditions):
        conditions = route_entry_conditions()
        covered = set(route_driven_conditions) | set(_derived_reasons_for(_source_fingerprint()))
        unclassified = {
            condition: sites for condition, sites in conditions.items()
            if condition not in covered and condition not in self.NOT_A_ROUTE
        }
        assert unclassified == {}, (
            f"the resolver reads an input no route drives and no exclusion names: {unclassified}"
        )

    def test_every_route_input_is_read_by_the_resolver(self, route_driven_conditions):
        conditions = route_entry_conditions()
        unread = {
            condition: sorted(routes) for condition, routes in route_driven_conditions.items()
            if condition not in conditions
        }
        assert unread == {}, f"a route sets an input the resolver no longer reads: {unread}"

    def test_no_exclusion_is_stale_or_driven(self, route_driven_conditions):
        conditions = route_entry_conditions()
        stale = set(self.NOT_A_ROUTE) - set(conditions)
        assert stale == set(), f"an exclusion names an input the resolver no longer reads: {stale}"
        driven = set(self.NOT_A_ROUTE) & set(route_driven_conditions)
        assert driven == set(), f"an exclusion names an input a route drives: {driven}"

    def test_only_pact_memory_init_sets_the_identity_store_work_reads(self):
        """The pin under `_AFTER_DETECTION`: of the attributes
        `PACTMemory.__init__` sets, only the cwd repository's memo, the
        last-operation statuses and the read-scope disclosure are set again
        by another method, and nothing sets a `PACTMemory` attribute by
        `setattr`. So the detected identity a command's store work reads is
        the one `__init__` detected. An identity attribute set anywhere else
        in the memory scripts, on any object, fails here too."""
        import ast

        tree = ast.parse((SCRIPTS / "memory_api.py").read_text())
        memory = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PACTMemory")

        def assigned(node):
            for sub in ast.walk(node):
                targets = (
                    sub.targets if isinstance(sub, ast.Assign)
                    else [sub.target] if isinstance(sub, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr))
                    else [item.optional_vars for item in sub.items] if isinstance(sub, (ast.With, ast.AsyncWith))
                    else [sub.target] if isinstance(sub, (ast.For, ast.AsyncFor))
                    else []
                )
                for target in targets:
                    for part in ast.walk(target) if target is not None else ():
                        if isinstance(part, ast.Attribute):
                            yield part

        methods = [item for item in memory.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
        init = next(item for item in methods if item.name == "__init__")
        identity = {part.attr for part in assigned(init)
                    if isinstance(part.value, ast.Name) and part.value.id == "self"}
        again = {part.attr for item in methods if item is not init for part in assigned(item)
                 if isinstance(part.value, ast.Name) and part.value.id == "self"}
        assert again & identity == {
            "_cwd_cache_dir", "_cwd_repo_id", "_cwd_repo_root",
            "_last_cascade_skipped", "_last_embedding_status", "_last_project_scope", "_last_sync_status",
            "_legacy_scope_state",
        }, again & identity
        identity -= again
        assert {"_project_id", "_project_identity_root", "_project_common_dir",
                "_detection_common_dirs", "_detection_listings"} <= identity, identity  # control
        setters = [ast.unparse(node) for node in ast.walk(memory) if isinstance(node, ast.Call)
                   and ast.unparse(node.func) in ("setattr", "object.__setattr__", "vars", "self.__dict__.update")]
        assert setters == [], setters
        elsewhere = []
        for path in sorted(SCRIPTS.rglob("*.py")):
            module = tree if path == SCRIPTS / "memory_api.py" else ast.parse(path.read_text())
            skip = {id(part) for part in assigned(init)} if module is tree else set()
            elsewhere += [f"{path.name}:{part.lineno} {ast.unparse(part)}" for part in assigned(module)
                          if part.attr in identity and id(part) not in skip]
        assert elsewhere == [], elsewhere

    def test_no_git_question_takes_a_store_work_value(self):
        """The pin under the second half of `_AFTER_DETECTION`: no argument
        of a git command the memory CLI's command work runs (argv, cwd or
        env) is reached by a value a store-work condition reads, that is, by
        a call at a site of a condition whose NOT_A_ROUTE reason carries
        `_AFTER_DETECTION` or `_AFTER_DETECTION_ASKS`, and the trace back
        from each argument (`_traced_sources`: locals, writes into
        containers, attributes, parameters, project functions' returns,
        module-level names) meets no form it cannot follow. The one flow
        allowed, and measured here, is `_AFTER_DETECTION_ASKS`': the
        disagreement a write refuses on is handed to
        `format_project_dir_disagreement`, whose kind check asks git two
        questions about the recorded project directory, `rev-parse
        --show-toplevel` and then `--git-common-dir`. Each call handing it
        over sits in a
        `PACTMemory` method other than `__init__`, so it runs after the
        identity is detected, and only `__init__` sets the identity
        (`test_only_pact_memory_init_sets_the_identity_store_work_reads`).
        Its limits are the NOT COVERED and ADVERSARIAL-ONLY lists in
        `_traced_sources`."""
        import ast

        questions, problems, allowed, graph = _store_work_taint_for(_source_fingerprint())
        assert problems == [], problems
        # Control: the allowed flow is the measured one, at each write path's
        # refusal, and each hands the value over from a PACTMemory method
        # other than __init__.
        assert {condition for condition, _site in allowed} == {("call", "env_record_project_dir_disagreement")}, allowed
        handing = set()
        for _condition, site in allowed:
            module, _, line = site.rpartition(".py:")
            owner = next(name for (owned, name), node in graph.defs.items() if owned == module and any(
                getattr(sub, "lineno", None) == int(line) for sub in ast.walk(node)) and "." in name)
            handing.add(owner)
        assert handing and all(name.startswith("PACTMemory.") and name != "PACTMemory.__init__"
                               for name in handing), handing
        # Control: the git commands are found, and the read scope's question
        # traces back to its call in `_read_project_scopes`.
        assert len(questions) >= 10, len(questions)
        common = [(module, key, call) for module, key, call in questions
                  if key == ("memory_api", "main_repo_common_dir") and ast.unparse(call.func) == "subprocess.run"]
        assert len(common) == 1, common
        module, key, call = common[0]
        caller = next(node for (owner, name), node in graph.defs.items()
                      if (owner, name) == ("memory_api", "PACTMemory._read_project_scopes"))
        site = next(sub.lineno for sub in ast.walk(caller) if isinstance(sub, ast.Call)
                    and ast.unparse(sub.func) == "main_repo_common_dir")
        sources = {f"{owner}.py:{source.lineno}" for owner, source in _value_sources(graph, module, graph.defs[key], call.args[0])}
        assert f"memory_api.py:{site}" in sources, sorted(sources)

    def test_the_recorded_directory_allowance_names_its_conditions(self):
        """Pin for `_flow_allowed`: the allowance is keyed to the exact
        conditions of `_ASKS_ABOUT_RECORDED_DIRECTORY`, whose reasons, and
        only theirs, carry `_AFTER_DETECTION_ASKS`. A third condition given
        the same reason text is not allowed through the sink, and a listed
        condition is not allowed through any other def."""
        carrying = {condition for condition, reason in self.NOT_A_ROUTE.items()
                    if reason.endswith(_AFTER_DETECTION_ASKS)}
        assert carrying == set(_ASKS_ABOUT_RECORDED_DIRECTORY), carrying
        sink = ("pact_session", "format_project_dir_disagreement")
        for condition in carrying:
            assert _flow_allowed(self.NOT_A_ROUTE, condition, sink)
            assert not _flow_allowed(self.NOT_A_ROUTE, condition, ("shared.project_scope", "recorded_main_root"))
        third = ("call", "count_project_memories")
        forged = {**self.NOT_A_ROUTE, third: "counts the rows" + _AFTER_DETECTION_ASKS}
        assert not _flow_allowed(forged, third, sink)
        unsuffixed = {**self.NOT_A_ROUTE, ("call", "env_record_project_dir_disagreement"): "compares" + _AFTER_DETECTION}
        assert not _flow_allowed(unsuffixed, ("call", "env_record_project_dir_disagreement"), sink)

    def test_the_census_reads_the_declaration_routes(self, route_driven_conditions):
        """Control: the known declaration inputs are found on both sides."""
        conditions = route_entry_conditions()
        for known in (("env", "CLAUDE_PROJECT_DIR"), ("call", "get_project_dir_from_session_record")):
            assert known in conditions and known in route_driven_conditions, known

    def test_only_the_exact_main_guard_is_left_out_of_import_time_code(self):
        """Control for `_import_time_code`: a read inside a module-level `if
        __name__ == "__main__":` with no else branch is not import-time
        code; the same read under any other test that mentions `__name__`,
        or in the guard's else branch, is."""
        import ast

        def reads(source):
            code = _import_time_code(ast.parse(source))
            return any(
                isinstance(node, ast.Call) and ast.unparse(node.func) == "os.environ.get"
                for node in ast.walk(code)
            )

        read = 'os.environ.get("X")'
        assert not reads(f'import os\nif __name__ == "__main__":\n    {read}\n')
        assert reads(f'import os\nif __name__ == "__main__" or X:\n    {read}\n')
        assert reads(f'import os\nif "__main__" == __name__:\n    {read}\n')
        assert reads(f'import os\nif __name__ == "__main__":\n    pass\nelse:\n    {read}\n')
        assert reads(f'import os\nif __name__ == "__main__":\n    pass\n{read}\n')

    @pytest.mark.parametrize(
        "source",
        [
            'NAMES = sorted(["A"], key=lambda name: os.environ.get("X", name))\n',
            'DEFAULT = (lambda: os.environ.get("X"))()\n',
            'class Box:\n    root = (lambda: os.environ.get("X"))()\n',
            '@(lambda cls: os.environ.get("X") and cls)\nclass Box:\n    pass\n',
            'BIGGEST = max([1], key=lambda _: bool(os.environ.get("X")))\n',
            'def f(value=(lambda: os.environ.get("X"))()):\n    return value\n',
            'TABLE = {"k": lambda: os.environ.get("X")}\nTABLE["k"]()\n',
            'class Box:\n    def method(self, value=(lambda: os.environ.get("X"))()):\n        pass\n',
        ],
        ids=[
            "sorted key", "called at once", "class body", "decorator", "max key",
            "function default", "table entry called at import", "method default",
        ],
    )
    def test_a_lambda_body_is_import_time_code(self, source):
        """A lambda's body is kept in `_import_time_code`, wherever the lambda
        stands in code that runs on import: a key function, a lambda called
        at once, a class body, a decorator, a default value, or a table
        entry called at import. A lambda inside a function body is not, since
        the function's body is not run on import."""
        import ast

        def reads(text):
            code = _import_time_code(ast.parse("import os\n" + text))
            return any(
                isinstance(node, ast.Call) and ast.unparse(node.func) == "os.environ.get"
                for node in ast.walk(code)
            )

        assert reads(source)
        # Controls: a lambda inside a function body, and a docstring naming the read.
        assert not reads('def f():\n    return (lambda: os.environ.get("X"))()\n')
        assert not reads('"""(lambda: os.environ.get("X"))()"""\n')

    def test_the_graph_resolves_relative_imports_and_fails_closed(self, tmp_path, monkeypatch):
        """Control for `_DetectionGraph.load`: a relative import resolves
        against the importing module's package, and one that names a missing
        module or climbs above the package is recorded as unresolved, which
        `route_inputs` turns into a condition no exclusion names. A call
        through a relative name that reaches no function (a class, a package
        re-export the graph cannot follow) is unread too, and so is a method
        of a module-level compiled pattern."""
        import ast
        import sys

        package = tmp_path / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("from os.path import join as reexported\n")
        (package / "helper.py").write_text(
            "import os\nimport re\n\nPATTERN = re.compile('x')\n\n"
            "class Thing:\n    pass\n\n"
            "def read():\n    return os.environ.get('X')\n"
        )
        (package / "main.py").write_text(
            "from .helper import read, Thing, PATTERN\n"
            "from . import helper, reexported\n"
            "from .missing import gone\n"
            "from ...outside import far\n\n"
            "def entry():\n"
            "    read()\n"
            "    helper.read()\n"
            "    gone()\n"
            "    far()\n"
            "    Thing()\n"
            "    helper.Thing()\n"
            "    reexported('a', 'b')\n"
            "    PATTERN.sub('', 'y')\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path,))
        graph = _DetectionGraph()
        assert graph.load("pkg.main")
        assert graph.aliases["pkg.main"]["read"] == "pkg.helper.read"
        assert graph.aliases["pkg.main"]["helper"] == "pkg.helper"
        assert graph.unresolved["pkg.main"] == {"gone", "far"}
        entry = graph.defs[("pkg.main", "entry")]
        targets = {target for _node, target in graph.references(entry, "pkg.main")}
        assert targets == {("pkg.helper", "read")}
        unread = {
            call.lineno for call in ast.walk(entry)
            if isinstance(call, ast.Call) and graph.unread_call(call, "pkg.main")
        }
        # Thing(), helper.Thing(), reexported() and PATTERN.sub(): lines 11-14.
        assert unread == {11, 12, 13, 14}, unread

    def test_only_a_literal_table_and_a_direct_call_are_read(self, tmp_path, monkeypatch):
        """Control for what the census admits: a method on a module-level
        literal table, and a direct call to a project function. Every other
        use of a project object is reported: a table built from the
        environment or rebound or captured, a compiled pattern (a literal one
        included), any project class build (a dataclass, a plain class, a
        class with a method), a method on a local or a `self` attribute that
        holds a project object otherwise, and a method on what a project
        function made. A method the class defines, called on a local whose every
        binding builds that plain class, is followed into the class."""
        import ast
        import sys

        package = tmp_path / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "m.py").write_text(
            "import os\n"
            "import re\n"
            "from dataclasses import dataclass, field\n"
            "TABLE = {'k': os.environ.get('X')}\n"
            "PATTERN = re.compile(os.environ.get('X', 'x'))\n"
            "CLEAN = {'k': 1}\n"
            "CLEAN_RE = re.compile('x', re.I)\n"
            "REBOUND = {'k': 1}\n"
            "try:\n"
            "    pass\n"
            "except Exception as REBOUND:\n"
            "    pass\n"
            "CAPTURED = {'k': 1}\n"
            "match 0:\n"
            "    case CAPTURED if False:\n"
            "        pass\n\n"
            "@dataclass\n"
            "class Box:\n"
            "    value: str = field(default_factory=lambda: os.environ.get('X', ''))\n\n"
            "class Plain:\n"
            "    pass\n\n"
            "class Reader:\n"
            "    def read(self):\n"
            "        return os.environ.get('X')\n\n"
            "class Holder:\n"
            "    def __init__(self):\n"
            "        self.reader = Reader()\n\n"
            "    def go(self):\n"
            "        return self.reader.read()\n\n"
            "def make():\n"
            "    return Reader()\n\n"
            "def entry():\n"
            "    TABLE.get('k')\n"
            "    PATTERN.match('x')\n"
            "    CLEAN.get('k')\n"
            "    CLEAN_RE.match('x')\n"
            "    REBOUND.get('k')\n"
            "    CAPTURED.get('k')\n"
            "    Box()\n"
            "    Plain()\n"
            "    reader = Reader()\n"
            "    reader.read()\n"
            "    made = make()\n"
            "    made.read()\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path,))
        graph = _DetectionGraph()
        assert graph.load("pkg.m")
        entry = graph.defs[("pkg.m", "entry")]
        unread = sorted(
            ast.unparse(call.func) for call in ast.walk(entry)
            if isinstance(call, ast.Call) and graph.unread_call(call, "pkg.m", entry)
        )
        assert unread == [
            "Box", "CAPTURED.get", "CLEAN_RE.match", "PATTERN.match", "Plain", "REBOUND.get",
            "Reader", "TABLE.get", "made.read",
        ], unread
        followed = {target for _node, target in graph.references(entry, "pkg.m")}
        assert followed == {("pkg.m", "make"), ("pkg.m", "Reader.read")}, followed
        go = graph.defs[("pkg.m", "Holder.go")]
        assert [ast.unparse(call.func) for call in ast.walk(go)
                if isinstance(call, ast.Call) and graph.unread_call(call, "pkg.m", go)] == ["self.reader.read"]
        assert {target for _node, target in graph.references(go, "pkg.m")} == set()

    def test_a_local_method_is_read_only_when_every_binding_form_is(self, tmp_path, monkeypatch):
        """Control for `_DetectionGraph._bindings` and `instance_method`: a
        method on a local bound in any form (an annotated `=`, `:=`, `with
        ... as`, a loop or comprehension element, an unpacked part, `+=`,
        `except ... as`, a `match` capture, a nested `def`'s result) to what a
        project function made is reported, as is one on a name with no
        binding at all, one on any `self` attribute, and every build of a
        project class; the same forms over standard-library values, a
        parameter and an import are not, and neither is a method on a local
        whose one binding builds the plain class that defines it, which
        `instance_target` follows into the class."""
        import ast
        import sys

        package = tmp_path / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "forms.py").write_text(
            "import os\n\n"
            "class Reader:\n"
            "    def read(self):\n"
            "        return os.environ.get('X')\n\n"
            "class ProjectError(Exception):\n"
            "    def read(self):\n"
            "        return os.environ.get('X')\n\n"
            "def make():\n"
            "    return Reader()\n\n"
            "def makes():\n"
            "    return [Reader()]\n\n"
            "def annotated():\n"
            "    reader: object = make()\n"
            "    reader.read()\n\n"
            "def walrus():\n"
            "    if (reader := make()):\n"
            "        reader.read()\n\n"
            "def with_as():\n"
            "    with make() as reader:\n"
            "        reader.read()\n\n"
            "def loop():\n"
            "    for reader in makes():\n"
            "        reader.read()\n\n"
            "def comprehension():\n"
            "    return [reader.read() for reader in makes()]\n\n"
            "def unpacked():\n"
            "    reader, _count = make(), 0\n"
            "    reader.read()\n\n"
            "def starred():\n"
            "    reader, *_rest = makes()\n"
            "    reader.read()\n\n"
            "def augmented():\n"
            "    reader = 0\n"
            "    reader += make()\n"
            "    reader.read()\n\n"
            "def except_as():\n"
            "    try:\n"
            "        pass\n"
            "    except ProjectError as reader:\n"
            "        reader.read()\n\n"
            "def match_capture():\n"
            "    match make():\n"
            "        case reader:\n"
            "            reader.read()\n\n"
            "def nested_def():\n"
            "    def helper():\n"
            "        return Reader()\n"
            "    reader = helper()\n"
            "    reader.read()\n\n"
            "def unbound():\n"
            "    nowhere.read()\n\n"
            "class WithHolder:\n"
            "    def __init__(self):\n"
            "        self.reader = Reader()\n\n"
            "    def reset(self):\n"
            "        with make() as self.reader:\n"
            "            pass\n\n"
            "    def go(self):\n"
            "        return self.reader.read()\n\n"
            "class PairHolder:\n"
            "    def __init__(self):\n"
            "        self.reader = Reader()\n\n"
            "    def reset(self):\n"
            "        self.reader, self.count = make(), 0\n\n"
            "    def go(self):\n"
            "        return self.reader.read()\n\n"
            "class AnnotatedHolder:\n"
            "    def __init__(self):\n"
            "        self.reader: Reader = Reader()\n\n"
            "    def go(self):\n"
            "        return self.reader.read()\n\n"
            "def controls(text: str, path):\n"
            "    name: str = text.strip()\n"
            "    name.upper()\n"
            "    if (size := len(text)):\n"
            "        size.bit_length()\n"
            "    with open(path) as handle:\n"
            "        handle.read()\n"
            "    for line in text.splitlines():\n"
            "        line.strip()\n"
            "    [part.strip() for part in text.split()]\n"
            "    first, second = text, path\n"
            "    first.lower()\n"
            "    total = 0\n"
            "    total += 1\n"
            "    total.bit_length()\n"
            "    try:\n"
            "        pass\n"
            "    except OSError as error:\n"
            "        error.with_traceback(None)\n"
            "    match text:\n"
            "        case captured:\n"
            "            captured.upper()\n"
            "    import json\n"
            "    json.dumps(1)\n"
            "    path.exists()\n"
            "    reader: Reader = Reader()\n"
            "    reader.read()\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path,))
        graph = _DetectionGraph()
        assert graph.load("pkg.forms")
        reported = {}
        for (owner, qualified), node in graph.defs.items():
            if owner != "pkg.forms":
                continue
            unread = sorted(
                ast.unparse(call.func) for call in ast.walk(node)
                if isinstance(call, ast.Call) and graph.unread_call(call, "pkg.forms", node)
            )
            if unread:
                reported[qualified] = unread
        forms = [
            "annotated", "walrus", "with_as", "loop", "comprehension", "unpacked", "starred",
            "augmented", "except_as", "match_capture", "nested_def",
        ]
        builds = ["make", "makes", "WithHolder.__init__", "PairHolder.__init__", "AnnotatedHolder.__init__"]
        assert reported == {
            **{name: ["reader.read"] for name in forms if name != "nested_def"},
            "nested_def": ["Reader", "reader.read"],
            **{name: ["Reader"] for name in builds},
            "unbound": ["nowhere.read"],
            **{f"{holder}.go": ["self.reader.read"] for holder in ("WithHolder", "PairHolder", "AnnotatedHolder")},
            "controls": ["Reader"],
        }, reported
        followed = {
            qualified for (owner, qualified), node in graph.defs.items() if owner == "pkg.forms"
            and ("pkg.forms", "Reader.read") in {target for _node, target in graph.references(node, "pkg.forms")}
        }
        assert followed == {"controls"}, followed

    def test_a_module_object_is_data_only_when_every_binding_is(self, tmp_path, monkeypatch):
        """Control for `_is_data`: a module-level name is a data object only
        when every binding of it (`_module_bindings`) is a literal, whichever
        binding comes last; a compiled pattern is not one."""
        import sys

        package = tmp_path / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "binds.py").write_text(
            "import re\n"
            "ONE = {'k': 1}\n"
            "PATTERN = re.compile('x')\n"
            "TWICE = dict(a=1)\n"
            "TWICE = {'k': 1}\n"
            "AUG = {'k': 1}\n"
            "AUG |= {'j': 2}\n"
            "GLOBAL = {'k': 1}\n\n"
            "def rebind():\n"
            "    global GLOBAL\n"
            "    GLOBAL = dict()\n\n"
            "if ONE:\n"
            "    NESTED = {'k': 1}\n"
            "else:\n"
            "    NESTED = dict()\n"
            "STORED = {'k': 1}\n"
            "STORED['j'] = len\n"
            "LOOPED = {}\n"
            "for LOOPED in ():\n"
            "    pass\n"
            "PAIR, OTHER = {}, {}\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path,))
        graph = _DetectionGraph()
        assert graph.load("pkg.binds")
        names = ["ONE", "PATTERN", "TWICE", "AUG", "GLOBAL", "NESTED", "STORED", "LOOPED", "PAIR"]
        assert {name: graph._is_data("pkg.binds", name) for name in names} == {
            "ONE": True, "PATTERN": False, "TWICE": False, "AUG": False, "GLOBAL": False,
            "NESTED": False, "STORED": False, "LOOPED": False, "PAIR": False,
        }

    def test_every_environment_occurrence_is_read_or_reported(self, tmp_path, monkeypatch):
        """Control for `_env_reads`: each occurrence of the environment in
        the fixture is read as its constant key, accepted as an alias
        binding or an identity test, or reported; a same-named local, a
        string mentioning the anchor and a parameter key are not reports."""
        import ast
        import sys

        package = tmp_path / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "envmod.py").write_text(
            "import os\n"
            "import string\n"
            "from os import environ as E\n"
            "_ENV = os.environ\n\n"
            "def f(env=None, key='K'):\n"
            "    env = os.environ if env is None else env\n"
            "    os.environ.get('A')\n"
            "    os.environb.get(b'B')\n"
            "    E['C']\n"
            "    'D' in os.environ\n"
            "    os.getenv('G')\n"
            "    os.getenvb(b'H')\n"
            "    os.path.expandvars('$I/${J}')\n"
            "    env.get('SECOND')\n"
            "    _ENV.pop('L', None)\n"
            "    os.environ.get(key)\n"
            "    dict(os.environ)\n"
            "    string.Template('$X').substitute(os.environ)\n"
            "    {**os.environ}\n"
            "    os.path.expandvars(key)\n"
            "    environ = {}\n"
            "    environ.get('Z')\n"
            "    'os.environ.get(Q) is text'\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "_PROJECT_ROOTS", (tmp_path,))
        graph = _DetectionGraph()
        assert graph.load("pkg.envmod")
        yields = list(_env_reads(graph.defs[("pkg.envmod", "f")], "pkg.envmod", "f", graph))
        read = sorted(key.value for key, _shown in yields if isinstance(key, ast.Constant))
        assert read == ["A", "B", "C", "D", "G", "H", "I", "J", "L", "SECOND"], read
        assert [key.id for key, _shown in yields if isinstance(key, ast.Name)] == ["key"]
        reported = sorted(shown for key, shown in yields if key is None)
        assert reported == sorted([
            "dict(os.environ)",
            "string.Template('$X').substitute(os.environ)",
            "{**os.environ}",
            "os.path.expandvars(key)",
        ]), reported
