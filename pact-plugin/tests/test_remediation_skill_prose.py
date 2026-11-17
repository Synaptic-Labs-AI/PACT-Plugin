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
    """The secretary's Working Memory step names the key the store uses."""

    def test_the_projection_is_keyed_by_repository_identity(self):
        assert (
            "The projection is keyed by the repository identity "
            "(host/owner/repo from the repository's override file or its "
            "origin remote) when one resolves, and by the project root's "
            "folder name when there is no override file and the origin is "
            "absent or a local path, except in a submodule."
        ) in _body(SECRETARY)

    def test_an_unrepresentable_origin_declines_rather_than_falling_back(self):
        body = _body(SECRETARY)
        assert (
            "Every other case declines instead, including a present origin "
            "that cannot be represented and a broken, redirected or bare .git: "
            "sync reports empty with a null project_id, and nothing is "
            "projected."
        ) in body
        assert "folder name otherwise" not in body.lower()
        assert "origin is absent or local." not in body
        # `unresolved` names a missing CLAUDE.md, not an identity cause.
        start = body.index("Every other case declines instead")
        assert "reports unresolved" not in body[start:start + 200]

    def test_the_legacy_bucket_is_read_only_on_evidence(self):
        body = _body(SECRETARY)
        assert (
            "This checkout's legacy folder-name bucket is read beside the "
            "repository identity while it holds rows, but only when a file row "
            "places it under this repository or its linked worktrees, or a "
            "recorded session from this repository wrote into it."
        ) in body
        assert (
            "An admitted bucket is shared with every checkout whose root "
            "folder has that name"
        ) in body
        assert "until it is rekeyed" not in body.lower()

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

        body = _body(SKILL)
        assert (
            "The host is lowercased but the path keeps its case, so "
            "https://GitHub.com/Owner/Repo and https://github.com/owner/repo "
            "are two ids even on a host that ignores path case: project-id "
            "show lists the other spelling as an ordinary bucket with no "
            "command, and only an explicit project-id rekey --from=<id> "
            "--to=<id> joins them."
        ) in body
        assert normalize_remote_url("https://GitHub.com/Owner/Repo") == "github.com/Owner/Repo"
        assert normalize_remote_url("https://github.com/owner/repo") == "github.com/owner/repo"

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
            "A recorded session counts only when it ran in this repository or "
            "one of its linked worktrees, and <git-common-dir>/description is a "
            "regular file whose ctime is earlier than the session's start."
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
            "A repository that was moved, copied, restored or cloned again, a "
            "repository with no description file, and a folder that is not a "
            "repository all read as unproven too."
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
            "unproven: no file row places it and no recorded session from this "
            "repository wrote into it (session_evidence is false). It is not "
            "read, and its rekey command is printed once per process"
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
        aliases = import_aliases(tree)
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
        constants = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.defs[(module, node.name)] = node
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.defs[(module, f"{node.name}.{item.name}")] = item
            elif (
                isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = node.value.value
        self.constants[module] = constants
        return True

    def follow(self, qualified: str, depth: int = 0):
        """The (module, def) a dotted name refers to, through re-exports."""
        owner, _, name = qualified.rpartition(".")
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
        or an attribute of `self` (`self.reader.read()`). No such method is
        followed into a class.

        "skip" (not this rule's to report): a local that is a parameter only
        (a method on a parameter is not covered), a name the module rule
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
        or a call into a project function that `returns_foreign`. Anything
        else, a project class build or an unannotated parameter included, is
        not proved."""
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

        if self.target(call, module) is not None:
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
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                found = self.target(sub, module)
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


def route_inputs() -> dict:
    """{condition: sorted sites} for every input the detection path reads, from
    `_detect_project_identity_with_legacy` through every function it calls in
    the project's own modules (the memory scripts and `hooks/`), helpers and
    re-exports included, and the code that runs when the modules it reaches
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
      a project function's result only when it `returns_foreign`).
    REPORTED, as a condition NOT_A_ROUTE must name with its reason: every
    other call through a project name. That is every build of a project
    class (a nested one included), in a function or at import, whatever
    the class defines; a method
    of any other project object (a compiled pattern, a logger, a context
    variable, an instance); a method on any `self` attribute; and a method
    on a local holding anything else; every class statement on a project
    name; and every use of a class the same module defines, at any depth,
    as a value. No method
    is followed into a class.

    The process and home directories are each ONE condition, `("cwd",)` and
    `("home",)`, whichever function reads them, so a new reader of either is
    absorbed by the condition already there: a new rung keyed on the process
    directory is the rung-parity census's to catch, and a new setting read
    from the home directory is not caught here.

    NOT COVERED: a call through `getattr`, `exec` or `importlib`; modules
    outside the plugin (the standard library, third-party packages); an
    environment read reached only through such a call; a read through a
    C extension (`ctypes`), which no source anchor can see; a method called
    on a function's parameter, or on a local proved a standard-library
    value only by taking a parameter as one, whose type the census cannot
    see; a class statement whose base, keyword value or decorator is a
    local or a parameter; and a plain attribute read, `with`, `for` or
    formatting of an object a project function returned, which runs no code
    the census sees.
    Each such object is built somewhere, and every project class build is
    reported, so its origin is a named condition. A condition names a
    callee, not a site: a further call of an already named callee is
    absorbed by that name.
    """
    import ast

    from fixtures.identity_git import qualified_name

    graph = _DetectionGraph()
    for module in DETECTION_MODULES:
        graph.load(module)
    reached, pending, outward = set(), [DETECTION_ENTRY], {}
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
                    owner = node.module or ""
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
        read_module(key[0])
        follow(key[0], graph.defs[key])

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
            elif isinstance(read, ast.Name) and read.id in graph.constants[module]:
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
            elif isinstance(argument, ast.Name) and argument.id in constants:
                add(("env", constants[argument.id]), f"{key[0]}.py:{node.lineno}", target)
            else:
                add(("env", f"<{target[0]}.{target[1]}: argument at {key[0]}.py:{node.lineno}>"),
                    f"{key[0]}.py:{node.lineno}", target)
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


class TestEveryRouteEntryConditionIsARoute:
    """Every input that selects a resolver route is a route the matrices
    measure.

    `route_inputs` reads the inputs from the detection path's code, helper
    modules included, and fails closed on any use of the environment it
    cannot resolve to a variable; `route_driven_conditions` measures what
    each route in `identity_routes.ROUTES` sets. A call into a helper counts
    as driven only when a route replaces it: an input a route drives that is
    read inside a helper does not drive the helper's other inputs.
    Every input must be driven or be listed in NOT_A_ROUTE, and every route
    input must still be read. A new route through any rung, a new reader of
    a record or setting in another project module, or a new environment
    variable in any form therefore reds an arm here until a route drives it
    or an exclusion names it. A new reader of the process or home directory
    joins the one `("cwd",)` or `("home",)` condition already there and
    reds nothing here. A new rung call site is the rung-parity test's census
    (every `_root_guard` and `_identity_at_root` call site must run).
    """

    # Inputs the detection path reads that no route sets, each with the reason
    # the matrices do not need one to.
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
        ("call", "<call to no function memory_api: _DetectedIdentity>"): (
            "builds a record class (fields only)"
        ),
        ("call", "<call to no function project_identity: OriginRead>"): "builds a record class (fields only)",
        ("call", "<call to no function project_identity: OverrideRead>"): (
            "builds a record class (fields only)"
        ),
        ("call", "<call to no function project_identity: RootIdentity>"): (
            "builds a record class (fields only)"
        ),
        ("call", "<call to no function memory_api: logger.debug>"): (
            "a logger method; every project object built on the path is a named build"
        ),
        ("call", "<call to no function memory_api: logger.warning>"): (
            "a logger method; every project object built on the path is a named build"
        ),
        ("call", "<call to no function database: CANONICAL_ID_RE.fullmatch>"): (
            "a method of a compiled literal pattern"
        ),
        ("call", "<call to no function database: a method of what CANONICAL_ID_RE.fullmatch returns>"): (
            "a method of that pattern's match"
        ),
        ("call", "<call to no function project_identity: _SCHEME_PREFIX_RE.match>"): (
            "a method of a compiled literal pattern"
        ),
        ("call", "<call to no function project_identity: a method of what _SCHEME_PREFIX_RE.match returns>"): (
            "a method of that pattern's match"
        ),
        ("call", "<call to no function project_identity: _SCP_FORM_RE.match>"): (
            "a method of a compiled literal pattern"
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
        ("call", "<call to no function shared.project_scope: _GIT_INT_RE.fullmatch>"): (
            "a method of a compiled literal pattern"
        ),
        ("call", "<call to no function shared.project_scope: a method of what _GIT_INT_RE.fullmatch returns>"): (
            "a method of that pattern's match"
        ),
        ("call", "<call to no function shared.project_scope: digits.strip>"): (
            "a method on a local the census cannot type; every project object built on "
            "the path is a named build"
        ),
        ("call", "<call to no function shared.project_scope: _UNSAFE_SLUG_CHARS_RE.sub>"): (
            "a method of a compiled literal pattern"
        ),
        ("call", "store_scope"): (
            "the _with_store_scope decorator runs each PACTMemory method inside "
            "store_scope(self._db_path), which binds the store file; get_user_memory_dir, "
            "which the user-setting route replaces, sets that binding aside"
        ),
        ("call", "<call to no function shared.merge_guard_common: _ReadVerbSpec>"): (
            "builds a record class (fields only)"
        ),
        ("call", "<call to no function shared.merge_guard_common: _WrapperGrammar>"): (
            "builds a record class (fields only)"
        ),
        ("call", "<call to no function shared.pact_context: TeamAlignment>"): (
            "builds a record class (fields only)"
        ),
        ("call", "<call to no function shared.session_state: SAFE_PATH_COMPONENT_RE.fullmatch>"): (
            "a method of a compiled literal pattern"
        ),
        ("call", "<name imported from a project module memory_api: MemoryObject in an annotation>"): (
            "models' @dataclass class with methods, named in an annotation"
        ),
        ("call", "<name imported from a project module memory_api: ProjectScopeDisagreementError>"): (
            "pact_session's error class, the base of a class memory_api defines"
        ),
        ("call", "<class statement on a project name memory_api: "
                 "ProjectIdentityUnresolvedError base ProjectScopeDisagreementError>"): (
            "a base whose project classes define no __init_subclass__ or __class_getitem__ "
            "and name no metaclass"
        ),
        ("call", "<name imported from a project module project_identity: CANONICAL_ID_MAX_LENGTH>"): (
            "database's id length constant, which the importing function returns"
        ),
        ("call", "<name imported from a project module search: MemoryObject in an annotation>"): (
            "models' @dataclass class with methods, named in an annotation"
        ),
        ("call", "<name imported from a project module shared.claude_md_manager: Document in an annotation>"): (
            "claude_md_markers' class with methods, named in an annotation"
        ),
        ("call", "<name imported from a project module shared.claude_md_manager: Located in an annotation>"): (
            "claude_md_markers' NamedTuple class, named in an annotation"
        ),
        ("call", "<name imported from a project module shared.claude_md_manager: SessionRoot in an annotation>"): (
            "project_scope's NamedTuple class, named in an annotation"
        ),
        ("call", "<name imported from a project module shared.project_scope: _UNSAFE_SLUG_CHARS_RE>"): (
            "pact_context's compiled pattern, whose sub replaces unsafe characters in the session id"
        ),
        ("call", "<name imported from a project module shared.session_resume: Document in an annotation>"): (
            "claude_md_markers' class with methods, named in an annotation"
        ),
        ("call", "<name imported from a project module shared.session_resume: ManagedDocument in an annotation>"): (
            "claude_md_manager's NamedTuple class, named in an annotation"
        ),
        ("call", "<name imported from a project module shared.session_resume: SessionRoot in an annotation>"): (
            "project_scope's NamedTuple class, named in an annotation"
        ),
        ("call", "<name imported from a project module working_memory: Document in an annotation>"): (
            "claude_md_markers' class with methods, named in an annotation"
        ),
        ("call", "<name imported from a project module working_memory: Located in an annotation>"): (
            "claude_md_markers' NamedTuple class, named in an annotation"
        ),
        ("call", "<name imported from a project module graph: _connection_sqlite3>"): (
            "the sqlite3 module database imports, read for its IntegrityError class in a module-level tuple"
        ),
        ('call', '<class of its own module used as a value embeddings: EmbeddingService in an annotation>'): (
            "embeddings' class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value memory_api: PACTMemory in an annotation>'): (
            "memory_api's class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value memory_api: PACTMemory>'): (
            "memory_api's class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, used as a value"
        ),
        ('call', '<class of its own module used as a value models: Decision in an annotation>'): (
            "models' @dataclass class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value models: Decision>'): (
            "models' @dataclass class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, used as a value"
        ),
        ('call', '<class of its own module used as a value models: Entity in an annotation>'): (
            "models' @dataclass class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value models: Entity>'): (
            "models' @dataclass class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, used as a value"
        ),
        ('call', '<class of its own module used as a value models: MemoryObject in an annotation>'): (
            "models' @dataclass class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value models: TaskItem in an annotation>'): (
            "models' @dataclass class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value models: TaskItem>'): (
            "models' @dataclass class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, used as a value"
        ),
        ('call', '<class of its own module used as a value pin_caps: CapViolation in an annotation>'): (
            "pin_caps' NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value pin_caps: Pin in an annotation>'): (
            "pin_caps' NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value project_identity: OriginRead in an annotation>'): (
            "project_identity's NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value project_identity: OverrideRead in an annotation>'): (
            "project_identity's NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value project_identity: RootIdentity in an annotation>'): (
            "project_identity's NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.claude_md_manager: GateTarget in an annotation>'): (
            "claude_md_manager's NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.claude_md_manager: ManagedDocument in an annotation>'): (
            "claude_md_manager's NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.claude_md_markers: Cause in an annotation>'): (
            "claude_md_markers' Enum class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.claude_md_markers: Cause>'): (
            "claude_md_markers' Enum class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, used as a value"
        ),
        ('call', '<class of its own module used as a value shared.claude_md_markers: Document in an annotation>'): (
            "claude_md_markers' class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.claude_md_markers: Kind in an annotation>'): (
            "claude_md_markers' Enum class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.claude_md_markers: Line in an annotation>'): (
            "claude_md_markers' NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.claude_md_markers: Located in an annotation>'): (
            "claude_md_markers' NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.claude_md_markers: State in an annotation>'): (
            "claude_md_markers' Enum class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.merge_guard_common: _PyScope in an annotation>'): (
            "merge_guard_common's class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.pact_context: TeamAlignment in an annotation>'): (
            "pact_context's NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value shared.project_scope: SessionRoot in an annotation>'): (
            "project_scope's NamedTuple class, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ('call', '<class of its own module used as a value working_memory: SyncResult in an annotation>'): (
            "working_memory's class with methods, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, named in an annotation"
        ),
        ("call", "is_canonical_id"): "validates an id value",
        ("call", "validate_identifier_for_write"): "validates an id value",
    }

    def test_each_named_build_and_pattern_is_what_its_reason_says(self):
        """Computes the two properties NOT_A_ROUTE's reasons state: an entry
        whose reason says "builds a record class (fields only)" builds a
        class that is a NamedTuple whose body holds only annotated fields and
        a docstring (no method, decorator or class keyword), and one that says
        "a method of a compiled literal pattern" calls a method on a module
        name (followed through imports) whose every binding is `re.compile`
        of literals or `re` flags. Every entry of either shape carries that
        reason, and no other entry does."""
        import ast
        import re as regex

        from fixtures.identity_git import qualified_name

        graph = _DetectionGraph()
        shape = regex.compile(r"<call to no function ([\w.]+): (\w+)(?:\.(\w+))?>")

        def literal(value):
            try:
                ast.literal_eval(value)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                return False
            return True

        def fields_only(node):
            return (
                [ast.unparse(base) for base in node.bases] in (["NamedTuple"], ["typing.NamedTuple"])
                and not node.decorator_list and not node.keywords
                and all(
                    (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
                     and (item.value is None or literal(item.value)))
                    or (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant))
                    for item in node.body
                )
            )

        def compiled_literal(owner, name):
            aliases = graph.aliases[owner]
            values = graph.bindings[owner].get(name, [])

            def flag(value):
                if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
                    return flag(value.left) and flag(value.right)
                return (qualified_name(value, aliases) or "").startswith("re.")

            return bool(values) and all(
                isinstance(value, ast.Call) and qualified_name(value.func, aliases) == "re.compile"
                and all(literal(argument) or flag(argument)
                        for argument in [*value.args, *(kw.value for kw in value.keywords)])
                for value in values
            )

        builds, patterns, wrong = set(), set(), {}
        for condition, reason in self.NOT_A_ROUTE.items():
            found = shape.fullmatch(condition[1]) if condition[0] == "call" else None
            kind = None
            if found:
                module, name, method = found.groups()
                assert graph.load(module), module
                owned = graph.project_name(name, module)
                if owned is not None and owned[1]:
                    owner, leaf = owned
                    graph.load(owner)
                    node = graph.classes.get(owner, {}).get(leaf)
                    if method is None and node is not None:
                        kind = "build"
                        (builds if fields_only(node) else wrong.setdefault("not fields only", set())).add(condition)
                    elif method is not None and compiled_literal(owner, leaf):
                        kind = "pattern"
                        patterns.add(condition)
            says = (
                "build" if reason == "builds a record class (fields only)"
                else "pattern" if reason == "a method of a compiled literal pattern"
                else None
            )
            if says != kind:
                wrong.setdefault(f"reason says {says}, the code is {kind}", set()).add(condition)
        assert wrong == {}, wrong
        assert len(builds) >= 1 and len(patterns) >= 1, (builds, patterns)  # control

    def test_each_class_reason_states_the_class_it_names(self):
        """Each NOT_A_ROUTE reason for a class is the text computed from the
        class: for a name used in an annotation, its module, whether it is a
        NamedTuple, a @dataclass, an Enum or a plain class, and whether its
        body defines a method; for a class statement on a project base, that
        the base and every project class it derives from define no
        `__init_subclass__` or `__class_getitem__` and name no metaclass; for
        a class of its own module used as a value, its form as for an
        annotation, that same hook-free chain, and whether the use is an
        annotation. Any other form computes no reason, so it fails here until
        the arm reads it."""
        import ast
        import re as regex

        graph = _DetectionGraph()
        annotated = regex.compile(r"<name imported from a project module ([\w.]+): (\w+) in an annotation>")
        statement = regex.compile(r"<class statement on a project name ([\w.]+): \w+ (base|keyword \w+|decorator) (\w+)>")
        own = regex.compile(r"<class of its own module used as a value ([\w.]+): ([\w.]+?)( in an annotation)?>")
        inert = "a base whose project classes define no __init_subclass__ or __class_getitem__ and name no metaclass"

        def class_node(name, module):
            assert graph.load(module), module
            owned = graph.project_name(name, module)
            if owned is None or not owned[1] or not graph.load(owned[0]):
                return None, None
            return owned[0], graph.classes.get(owned[0], {}).get(owned[1])

        def described(owner, node):
            """`<module>'s <form> class[ with methods]`, or None for a form
            the arm does not read."""
            bases = [ast.unparse(base) for base in node.bases]
            decorators = [ast.unparse(decorator) for decorator in node.decorator_list]
            form = (
                "NamedTuple " if bases in (["NamedTuple"], ["typing.NamedTuple"]) and not decorators
                else "Enum " if bases in (["Enum"], ["enum.Enum"]) and not decorators
                else "@dataclass " if decorators in (["dataclass"], ["dataclasses.dataclass"]) and not bases
                else "" if not bases and not decorators
                else None
            )
            if form is None or node.keywords:
                return None
            short = owner.rpartition(".")[2]
            methods = any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) for item in node.body)
            return (f"{short}{chr(39) if short.endswith('s') else chr(39) + 's'} {form}class"
                    f"{' with methods' if methods else ''}")

        def annotation_reason(owner, node):
            text = described(owner, node)
            return None if text is None else f"{text}, named in an annotation"

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

        computed, wrong = 0, {}
        for condition, reason in self.NOT_A_ROUTE.items():
            text = condition[1] if condition[0] == "call" else ""
            if not (annotated.fullmatch(text) or text.startswith(("<class statement", "<class of its own module"))):
                continue
            expected = None
            found = annotated.fullmatch(text)
            if found:
                owner, node = class_node(found.group(2), found.group(1))
                expected = annotation_reason(owner, node) if node is not None else None
            found = statement.fullmatch(text)
            if found:
                module, role, name = found.groups()
                owner, node = class_node(name, module)
                expected = inert if role == "base" and node is not None and no_hooks(owner, node) else None
            found = own.fullmatch(text)
            if found:
                module, name, in_annotation = found.groups()
                assert graph.load(module), module
                node = next(
                    (found[name] for found in graph.class_names.get(module, {}).values() if name in found),
                    None,
                )
                text = described(module, node) if node is not None and no_hooks(module, node) else None
                expected = None if text is None else (
                    f"{text}, with no __init_subclass__, __class_getitem__ or metaclass in its project chain, "
                    + ("named in an annotation" if in_annotation else "used as a value")
                )
            computed += 1
            if reason != expected:
                wrong[condition] = (reason, expected)
        assert wrong == {}, wrong
        assert computed >= 2, computed  # control

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
        covered = set(route_driven_conditions)
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
        holds a project object, and a method on what a project function
        made. No method is followed into a class."""
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
            "Reader", "TABLE.get", "made.read", "reader.read",
        ], unread
        followed = {target for _node, target in graph.references(entry, "pkg.m")}
        assert followed == {("pkg.m", "make")}, followed
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
        parameter and an import are not."""
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
            "controls": ["Reader", "reader.read"],
        }, reported
        followed = {
            target for (owner, _qualified), node in graph.defs.items() if owner == "pkg.forms"
            for _node, target in graph.references(node, "pkg.forms")
        }
        assert ("pkg.forms", "Reader.read") not in followed, followed

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
