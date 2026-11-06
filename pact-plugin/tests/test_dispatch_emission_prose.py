"""Structural pin tests for the per-dispatch `variety_assessed` emission sites.

Every dispatch path that stamps `metadata.variety` on a Task B also writes a
`variety_assessed` journal event keyed on that task's id, carrying the four
dimension scores and the total, with the TOP-LEVEL `"scope": "dispatch"`
discriminator present. The FEATURE-level assessment event carries NO `scope`
field, and journal readers key on that difference (session_state latest-ts
feature-level derivation and variety_divergence arc_start exclude dispatch-marked
events; the wrap-up Q5 join consumes them as an as-dispatched fallback
source). A hand-written emission that omits the field degrades to
feature-level — the legacy behavior — so the discriminator's presence on
every per-dispatch site is load-bearing and pinned here at the block level.

THE SLICE RULE, stated beside the arms that use it: the guarded unit is a
fenced block that contains a `variety_assessed` journal write. A per-dispatch
block MUST carry the discriminator in its JSON body AND a trap label naming
its own file; a feature-level block MUST carry neither a discriminator NOR a
`scope` key at the top level of its JSON body. File-wide token counts would
let a discriminator sitting in some unrelated prose satisfy an arm, so every
assertion is block-scoped.

COUNTS ARE FLOORS AND THEY MOVE ONLY DELIBERATELY. The per-dispatch site
count per file reflects the dispatch shapes the file teaches: orchestrate
carries four (PREPARE/ARCHITECT/CODE/TEST), comPACT two (concurrent +
single), peer-review/plan-mode/rePACT one each. A repair that fixed one
site of a file would leave the others dark while a presence-only check
stayed green, so the floors are exact counts and refuse movement in either
direction.
"""
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
COMMANDS = PLUGIN_ROOT / "commands"

# The per-file census: (file name, per-dispatch site count, feature-level
# site count). orchestrate's feature-level emission predates this change and
# is pinned UNCHANGED — its block must carry no discriminator.
SITES = [
    ("orchestrate.md", 4, 1),
    ("comPACT.md", 2, 1),
    ("peer-review.md", 1, 0),
    ("plan-mode.md", 1, 0),
    ("rePACT.md", 1, 1),
]

DISCRIMINATOR = '"scope": "dispatch"'
WRITE_FLAG = "--type variety_assessed"
TRAP_PREFIX = "[JOURNAL WRITE FAILED] "


def _fenced_blocks(text: str) -> list[str]:
    """Return the fenced ``` blocks of a markdown file, fence-stripped."""
    return re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.DOTALL)


def _variety_blocks(name: str) -> list[str]:
    """The fenced blocks of a command file that write variety_assessed."""
    text = (COMMANDS / name).read_text()
    return [b for b in _fenced_blocks(text) if WRITE_FLAG in b]


@pytest.mark.parametrize(
    "name,dispatch_count,feature_count", SITES,
    ids=[s[0] for s in SITES],
)
class TestEmissionSiteCensus:
    def test_block_census_matches(self, name, dispatch_count, feature_count):
        """The variety_assessed-writing blocks split exactly as declared:
        `dispatch_count` carrying the discriminator and `feature_count`
        without it. A new dispatch shape moves the census deliberately or
        fails here; a repair that fixes one site of a multi-site file fails
        here while the others still pass their own file."""
        blocks = _variety_blocks(name)
        marked = [b for b in blocks if DISCRIMINATOR in b]
        unmarked = [b for b in blocks if DISCRIMINATOR not in b]
        assert len(marked) == dispatch_count, (
            f"{name}: expected {dispatch_count} dispatch-marked "
            f"variety_assessed blocks, found {len(marked)}"
        )
        assert len(unmarked) == feature_count, (
            f"{name}: expected {feature_count} feature-level "
            f"variety_assessed blocks, found {len(unmarked)}"
        )

    def test_every_block_names_its_file_in_the_trap(self, name, dispatch_count,
                                                    feature_count):
        """Each emission block's ERR trap names its own file, so a failed
        write is attributable to the site that failed. Pinned per block,
        because a trap label borrowed from a sibling file misroutes the
        failure while a file-wide count stays green."""
        for block in _variety_blocks(name):
            assert f"{TRAP_PREFIX}{name}" in block, (
                f"{name}: a variety_assessed block's trap does not name "
                f"its own file"
            )

    def test_dispatch_blocks_mirror_the_stamp_shape(self, name,
                                                     dispatch_count,
                                                     feature_count):
        """Every dispatch-marked block carries the stamp mirror's JSON body
        shape: the dispatch task id key, the discriminator, and the five
        score keys (four dimensions plus total, rationales omitted)."""
        for block in _variety_blocks(name):
            if DISCRIMINATOR not in block:
                continue
            assert '"task_id": "{taskId}"' in block
            assert '"variety": {"novelty": N, "scope": N, ' \
                   '"uncertainty": N, "risk": N, "total": N}' in block

    def test_feature_blocks_carry_no_top_level_discriminator(
        self, name, dispatch_count, feature_count,
    ):
        """A feature-level block keeps the legacy shape: no top-level
        `scope` key in its JSON body. The polarity readers key on is
        ABSENT-means-feature-level, so a discriminator leaking into a
        feature block would silently exclude the arc's own assessment
        from arc_start and feature derivation."""
        for block in _variety_blocks(name):
            if DISCRIMINATOR in block:
                continue
            body = next(
                line for line in block.splitlines()
                if line.lstrip("> ").lstrip().startswith('{"task_id"')
            )
            # The check is the DISCRIMINATOR LITERAL, not the bare key: the
            # nested variety dict legitimately carries a "scope" DIMENSION
            # (`"scope": N`, unquoted). A top-level discriminator would be
            # the quoted value — and it is exactly that degradation (a
            # feature block accidentally marked dispatch) this arm rejects.
            assert DISCRIMINATOR not in body, (
                f"{name}: a feature-level variety_assessed body carries "
                f"the top-level dispatch discriminator: {body.strip()}"
            )


class TestWrapUpQ5Consumption:
    """The reader-side plumbing pins: the Q5 prose filters the arc-scoped
    variety_assessed re-read on the discriminator and passes the subset as
    the join's third argument."""

    @pytest.fixture()
    def wrap_up(self) -> str:
        return (COMMANDS / "wrap-up.md").read_text()

    def test_q5_filters_on_the_discriminator(self, wrap_up):
        assert 'e.get("scope") == "dispatch"' in wrap_up

    def test_q5_passes_the_third_argument(self, wrap_up):
        assert "extract_final_dispatch_coverage(events, snapshot_events, " \
               "dispatch_assessed)" in wrap_up

    def test_q5_rereads_the_stream_arc_scoped(self, wrap_up):
        """The per-dispatch events must be arc-scoped before they join —
        the platform reuses task ids across arcs, so the third read carries
        the same --since value as the other two streams."""
        assert "--type variety_assessed --since '{arc_start}'" in wrap_up


class TestRecoveryReadLastUsesTheFilteredForm:
    """The post-compaction recovery read must exclude per-dispatch mirrors.

    orchestrate.md's state-recovery step reads the feature's
    `variety_assessed` via `read-last`, and per-dispatch mirrors land LATER
    in the journal than the feature-level assessment — without the
    `--exclude-scope dispatch` flag the reverse scan returns a Task-B
    dispatch total as the feature assessment, silently corrupting the
    variety-band gates rebuilt after compaction. The prose and the CLI
    flag are pinned against each other so neither can drift alone.
    """

    def test_recovery_read_carries_the_exclusion(self):
        text = (COMMANDS / "orchestrate.md").read_text()
        assert (
            "read-last --session-dir '{session_dir}' --type "
            "variety_assessed --exclude-scope dispatch" in text
        ), (
            "orchestrate.md's post-compaction variety recovery must use "
            "the --exclude-scope dispatch form; an unfiltered read-last "
            "returns a per-dispatch mirror as the feature assessment"
        )

    def test_the_flag_name_matches_the_cli_registration(self):
        """Prose/CLI spelling agreement: the flag the prose names is the
        flag the parser registers, so a rename on one side reddens here
        rather than silently stranding the other."""

        from shared import session_journal as sj

        parser = sj._build_cli()
        # argparse exposes each subparser's option strings; assert the
        # read-last subparser carries --exclude-scope.
        actions_by_command = {
            choice: sub
            for choice, sub in parser._subparsers._group_actions[0].choices.items()  # noqa: SLF001
        }
        last = actions_by_command["read-last"]
        option_strings = {
            opt for a in last._actions for opt in a.option_strings  # noqa: SLF001
        }
        assert "--exclude-scope" in option_strings


class TestDiscriminatorConstant:
    """The code-side spelling and the prose-side spelling agree. The
    consumers import VARIETY_ASSESSED_DISPATCH_SCOPE; the emission sites
    write the literal into journal events. A rename that moved one and not
    the other would silently degrade every per-dispatch event to
    feature-level — the exact corruption the discriminator exists to
    prevent — so the two spellings are pinned against each other."""

    def test_constant_value_matches_the_prose_literal(self):

        from shared.constants import VARIETY_ASSESSED_DISPATCH_SCOPE

        assert VARIETY_ASSESSED_DISPATCH_SCOPE == "dispatch"


class TestPopulationAndAuditorWording:
    def test_external_reviews_split_roster_from_dispatch_site_membership(self):
        paths = [
            COMMANDS / "wrap-up.md",
            PLUGIN_ROOT / "protocols" / "pact-protocols.md",
            PLUGIN_ROOT / "protocols" / "pact-variety.md",
            PLUGIN_ROOT / "hooks" / "task_lifecycle_gate.py",
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            assert "no journal witness" not in text, path
            assert "review_dispatch` roster event" in text or (
                "review_dispatch`` roster event" in text
            ), path
            assert "one event means one dispatch" not in text, path
            assert "witnesses a dispatch" in text, path

    def test_auditor_prohibition_names_real_consequences_and_unchanged_q5(self):
        text = (
            PLUGIN_ROOT / "agents" / "pact-orchestrator.md"
        ).read_text(encoding="utf-8")
        assert "self-completion authority and harvest propagation" in text
        assert "would NOT change Q5 `dispatch_site` membership" in text
        assert "would silently move those dispatches out of the Q5" not in text

    def test_t5_keys_on_owner_write_not_path_or_subject_heuristic(self):
        text = (
            PLUGIN_ROOT / "tests" / "test_dispatch_site_emit.py"
        ).read_text(encoding="utf-8")
        t5 = text.split("def test_T5_work_subject_containing_review", 1)[1]
        t5 = t5.split("def ", 1)[0]
        assert "owner-witnessing write rather than" in t5
        assert "owner write\n        alone" not in t5
