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

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SKILL = PLUGIN_ROOT / "skills" / "pact-memory" / "SKILL.md"
SECRETARY = PLUGIN_ROOT / "agents" / "pact-secretary.md"
SCRIPTS = PLUGIN_ROOT / "skills" / "pact-memory" / "scripts"

# The decline causes `project-id show` and `status` can carry in
# `decline_reason.reason`.
DECLINE_TOKENS = (
    "origin_unreadable",
    "origin_unrepresentable",
    "gitfile_redirect",
    "broken_git_marker",
    "no_working_tree",
    "submodule_checkout",
    "declaration_unresolved",
)


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

    @pytest.mark.parametrize("token", DECLINE_TOKENS)
    def test_each_documented_cause_is_one_the_code_emits(self, token):
        """A renamed token reds here instead of leaving the prose stale."""
        source = "".join(
            (SCRIPTS / name).read_text(encoding="utf-8")
            for name in ("memory_api.py", "project_identity.py")
        )
        assert f'"{token}"' in source

    def test_the_documented_causes_match_the_code_registry_when_present(self):
        """Once the code declares its cause set, the prose lists exactly it."""
        from scripts import project_identity

        registry = getattr(project_identity, "DECLINE_REASONS", None)
        if registry is None:
            pytest.skip("the code declares no DECLINE_REASONS registry yet")
        assert set(registry) == set(DECLINE_TOKENS)


class TestExitCodeThreeCoversBothVerbs:
    def test_row_three_names_project_file_and_rekey(self):
        row = _table_row(SKILL, "3")
        assert "project-file: this session's project memory file is unavailable" in row
        assert "project-id rekey: an operational failure; nothing was moved" in row

    def test_the_rekey_one_and_two_meanings_are_stated(self):
        assert (
            "project-id rekey exits 1 when it refuses — --to is omitted and no "
            "canonical identity resolves, or --to is omitted and --from is not "
            "this checkout's own legacy candidate that is neither excluded nor "
            "unproven, or --db-path names a store that is not present — and 2 "
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
            "config.json → \"project_id\" in the user memory directory, the "
            "directory that holds the default memory.db named under Storage — "
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
def matrix(tmp_path_factory):
    """{(cause, layout): (emitted reason, emitted override_path)}."""
    import json
    import os
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

    run(["setup"], base, None, False)
    result = {}
    for (cause, layout), (cwd, declared, shimmed) in TestNullOverridePathIsMeasured._layouts(base).items():
        out = run(["project-id", "show"], cwd, cwd if declared is None else declared, shimmed)
        record = json.loads(out.stdout)["result"]["decline_reason"] or {}
        result[(cause, layout)] = (record.get("reason"), record.get("override_path"))
    return result



class TestNullOverridePathIsMeasured:
    """When `override_path` is null is MEASURED, never restated.

    Every cause the code defines (`DECLINE_REASONS`) is produced in every
    layout that can produce it, and `project-id show` is run on each. The
    emitted envelope decides: a cause that is null in every layout belongs in
    the SKILL's cause list, and a layout that nulls a cause which elsewhere
    carries a path is a location route the SKILL clause must name. A new
    null route, a new cause, or a dropped route therefore reds an arm here
    without anyone editing a list.
    """

    BAD_ORIGIN = "https://host/own%ZZr/repo.git"

    # The phrase the SKILL clause uses for each location route. A measured
    # route with no phrase here fails `test_every_location_route_is_named`.
    LAYOUT_PHRASES = {
        "external": "when the checkout's git directory lies outside it",
    }

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
        causes = {cause for cause, _ in matrix}
        return {
            cause for cause in causes
            if all(path is None for (c, _), (_, path) in matrix.items() if c == cause)
        }

    def _location_routes(self, matrix) -> set:
        always = self._always_null(matrix)
        return {
            layout for (cause, layout), (_, path) in matrix.items()
            if path is None and cause not in always
        }

    def test_each_cell_declines_for_the_cause_it_was_built_for(self, matrix):
        wrong = {cell: got for cell, (got, _) in matrix.items() if got != cell[0]}
        assert wrong == {}, f"a layout builder produced another cause: {wrong}"

    def test_every_cause_the_code_defines_is_measured(self, matrix):
        from scripts.project_identity import DECLINE_REASONS

        assert {cause for cause, _ in matrix} == set(DECLINE_REASONS)

    def test_the_documented_causes_are_the_always_null_causes(self, matrix):
        assert self._documented_causes() == self._always_null(matrix)

    def test_every_location_route_is_named(self, matrix):
        routes = self._location_routes(matrix)
        unnamed = routes - set(self.LAYOUT_PHRASES)
        assert unnamed == set(), f"a layout nulls override_path with no SKILL phrase: {unnamed}"
        clause = self._clause()
        for layout in routes:
            assert self.LAYOUT_PHRASES[layout] in clause, layout

    def test_no_unmeasured_location_route_is_claimed(self, matrix):
        routes = self._location_routes(matrix)
        clause = self._clause()
        for layout, phrase in self.LAYOUT_PHRASES.items():
            if layout not in routes:
                assert phrase not in clause, f"{layout} is documented but measures no null"

    def test_the_withheld_path_is_the_users_decision(self):
        assert (
            "a pin there may remedy the decline, but the path is withheld "
            "because a forged pointer looks the same, so pinning that git "
            "directory is the user's decision"
        ) in self._clause()

    DOC_LEAD = "It is None for "

    def test_the_decline_record_docstring_lists_the_always_null_causes(self, matrix):
        from scripts.project_identity import decline_record

        doc = _flatten(decline_record.__doc__ or "")
        assert doc.count(self.DOC_LEAD) == 1, "the null-cause sentence must appear once"
        start = doc.index(self.DOC_LEAD) + len(self.DOC_LEAD)
        listed = doc[start:doc.index(".", start)]
        names = {n.strip() for n in listed.replace(" and ", ", ").split(",") if n.strip()}
        assert names == self._always_null(matrix)
        assert "when no repository was found" not in doc

    DOC_LOCATION = (
        "It is also None for every other cause when the checkout's .git "
        "points at a git directory outside the checkout"
    )

    def test_the_decline_record_docstring_names_the_location_route(self, matrix):
        from scripts.project_identity import decline_record

        doc = _flatten(decline_record.__doc__ or "")
        if "external" in self._location_routes(matrix):
            assert self.DOC_LOCATION in doc
        else:
            assert self.DOC_LOCATION not in doc

    def test_a_redirect_decline_can_never_carry_a_path(self):
        from scripts.project_identity import decline_record

        with pytest.raises(ValueError):
            decline_record("gitfile_redirect", "/r/.git/pact-project-id")
