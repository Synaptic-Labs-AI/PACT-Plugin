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
            "this checkout's own non-excluded legacy candidate — and 2 for bad "
            "input"
        ) in _body(SKILL)


class TestSecretaryProjectionKey:
    """The secretary's Working Memory step names the key the store uses."""

    def test_the_projection_is_keyed_by_repository_identity(self):
        assert (
            "The projection is keyed by the repository identity "
            "(host/owner/repo from the origin remote) when the checkout has a "
            "parseable one, and by the project root's folder name when the "
            "origin is absent or local."
        ) in _body(SECRETARY)

    def test_an_unrepresentable_origin_declines_rather_than_falling_back(self):
        body = _body(SECRETARY)
        assert (
            "A present origin that cannot be represented declines instead: "
            "sync reports empty with a null project_id, and nothing is "
            "projected."
        ) in body
        assert "folder name otherwise" not in body.lower()
        # `unresolved` names a missing CLAUDE.md, not an identity cause.
        start = body.index("A present origin that cannot be represented")
        assert "reports unresolved" not in body[start:start + 200]

    def test_the_legacy_bucket_is_named_as_read_beside_it(self):
        assert (
            "A legacy folder-name bucket is read beside the repository "
            "identity until it is rekeyed"
        ) in _body(SECRETARY)

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
    """The `override_path` clause names every cause where a pin cannot help."""

    NULL_CAUSES = (
        "gitfile_redirect",
        "no_working_tree",
        "broken_git_marker",
        "declaration_unresolved",
    )

    def _clause(self) -> str:
        row = _table_row(SKILL, "project-id show")
        start = row.index("override_path is the")
        return row[start:start + 600]

    def test_null_means_no_override_file_can_remedy(self):
        assert (
            "or null when no override file can remedy the decline"
        ) in self._clause()

    @pytest.mark.parametrize("cause", NULL_CAUSES)
    def test_each_null_cause_is_named_in_the_clause(self, cause):
        assert cause in self._clause()

    def test_the_retired_condition_is_gone(self):
        assert "null when no repository was found" not in _body(SKILL).lower()

    def test_the_redirect_remedy_is_the_code_remedy_and_never_a_pin(self):
        from scripts.memory_api import _REDIRECT_REMEDY

        clause = self._clause()
        assert "For gitfile_redirect, never write an override:" in clause
        assert _flatten(_REDIRECT_REMEDY) in clause


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
            "own legacy candidate and it is not excluded; otherwise the move "
            "is refused with REKEY_REFUSED"
        ) in _body(SKILL)

    def test_the_envelope_field_sentences_are_present(self):
        row = _table_row(SKILL, "project-id rekey --from=<id> [--to=<canonical>] [--dry-run] [--apply], or project-id rekey --candidates alone")
        assert (
            "A store path that does not exist answers store_absent: true and "
            "creates nothing; a --from bucket with no rows answers "
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
            "from that non-repository folder."
        ) in _body(SKILL)
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
            "WHERE project_id IN (SELECT value FROM json_each(?2))"
        ) in self._sql_block()

    def test_the_single_id_binding_is_gone(self):
        assert "project_id = ?2" not in self._sql_block()

    def test_the_placeholder_is_the_sync_read_scope(self):
        assert (
            "?2 to the read_scope array from this briefing's sync envelope, "
            "serialised as JSON"
        ) in _body(SECRETARY)

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
