"""Execute every `python3 -c` route the harvest skill tells the agent to run.

Each route in a `bash` fence of `skills/pact-handoff-harvest/SKILL.md` that
imports from `shared.` is run as written, in a child interpreter, with its
`$VAR` placeholders filled from the fixture table below. A route whose import
path, call shape or output no longer works reddens here; a route with no
fixture, or a fixture with no route, reddens the coverage arm.

The child gets an environment built from nothing but a temporary config root
and home, so no route can read the real `~/.claude`; the read_task_json arms
are the control that the child resolves the fixture store.
"""

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN / "hooks"))

from shared import handoff_merge as hm  # noqa: E402

SKILL_TEXT = (
    _PLUGIN / "skills" / "pact-handoff-harvest" / "SKILL.md"
).read_text(encoding="utf-8")
_IMPORT = re.compile(r"from shared\.\w+ import ([\w, ]+);")
# Names a route imports only to feed its primary call.
_SUPPORT = {"positioned", "store_epochs", "OldFormatOutcome"}

TEAM, OTHER = "session-routes", "session-elsewhere"
H = {"produced": "p", "decisions": "d", "uncertainty": "u",
     "integration": "i", "open_questions": "q"}
T1 = "2026-11-01T00:00:01Z"


def _routes():
    """{primary name: command line} for every route in a bash fence."""
    routes, in_bash = {}, False
    for line in SKILL_TEXT.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_bash = stripped == "```bash"
            continue
        if in_bash and stripped.startswith("python3 -c") and "from shared." in stripped:
            names = [n.strip() for n in _IMPORT.search(stripped).group(1).split(",")]
            primary = [n for n in names if n not in _SUPPORT]
            assert len(primary) == 1, stripped
            assert primary[0] not in routes, f"two routes for {primary[0]}"
            routes[primary[0]] = stripped
    return routes


ROUTES = _routes()


def _ev(handoff, ts=T1, team=TEAM, task_id="5"):
    return {"type": "agent_handoff", "task_id": task_id, "agent": "coder",
            "task_subject": "s", "team": team, "handoff": handoff, "ts": ts}


_JOURNAL = [{"type": "task_created", "task_id": "5", "team": TEAM, "ts": T1},
            _ev(H), _ev(dict(H, produced="q"))]
_IDENTITY = hm.disk_identity("coder", "s")


def _json(expected):
    return lambda out: json.loads(out) == expected


# name: (argv values in placeholder order, stdin object or text, check)
FIXTURES = {
    "report_safe": (["a\nb c"], None, lambda out: out == "abc"),
    "disk_identity": (["coder", "s"], None, lambda out: out == _IDENTITY),
    "handoff_content_key": ([], H, lambda out: out == hm.handoff_content_key(H)),
    "union_preferring_task_file": (
        [], {"journal": H, "disk": dict(H, open_questions=""), "task_id": "5"},
        lambda out: json.loads(out)["handoff"]["open_questions"] == ""
        and len(json.loads(out)["reports"]) == 1),
    "latest_by_ts_then_journal_order": (
        [], [_ev(H), _ev(dict(H, produced="later line"))],
        lambda out: json.loads(out)["handoff"]["produced"] == "later line"),
    "group_events": (
        [], {"journal": _JOURNAL, "positions": [1, 2]},
        _json([[[_IDENTITY, [0, 0]], [1, 2]]])),
    "current_arc": ([], {"journal": _JOURNAL, "team": TEAM, "task_id": "5"},
                    _json([0, 0])),
    "owning_group": (
        [], {"groups": [[_IDENTITY, None], [_IDENTITY, [0, 0]]],
             "disk_id": _IDENTITY, "file_arc": [0, 0]},
        _json([_IDENTITY, [0, 0]])),
    "undivided_contents": ([], [_ev(H), _ev(dict(H, produced="x"))],
                           lambda out: out == "2"),
    "partition_destination": (
        [], {"event_team": OTHER, "this_team": TEAM, "from_teams": [OTHER]},
        lambda out: out == hm.PARTITION_FROM),
    "old_format_destination": (
        [], {"outcome": {"state": hm.OLD_FORMAT_OWNED, "team": OTHER, "report": None},
             "this_team": TEAM},
        lambda out: out == hm.OLD_FORMAT_DEST_FROM),
    "mixed_old_format_event": (
        [], {"task_id": "5", "agent": "coder", "subject": "s", "handoff": H,
             "candidates": [{"team": OTHER, "task": {
                 "owner": "coder", "subject": "s", "metadata": {"handoff": H}}}]},
        _json({"state": hm.OLD_FORMAT_OWNED, "team": OTHER, "report": None})),
    "safe_from_teams": (
        [], {"realign_events": [{"from": "../x"}, {"from": OTHER}, {"from": TEAM}],
             "this_team": TEAM},
        lambda out: json.loads(out)["teams"] == [OTHER]
        and len(json.loads(out)["reports"]) == 1),
    "unit_token": ([ "5", OTHER], H,
                   lambda out: out == f"{OTHER}/5#{hm.handoff_content_key(H)}"),
    "parse_ledger_tokens": (
        [], "Processed task IDs (this team): 5#deadbeef, 3, 7#xyz\n",
        _json(["5#deadbeef"])),
    "read_task_json": (["5", TEAM], None,
                       lambda out: json.loads(out).get("subject") == "fixture store"),
    "HANDOFF_LEGACY_ALIASES": ([], None, lambda out: "key_decisions" in out),
}


@pytest.fixture
def child_env(tmp_path):
    """An environment built from nothing: a fixture config root holding one
    task store, a fixture home, and no inherited variables."""
    config = tmp_path / "config"
    store = config / "tasks" / TEAM
    store.mkdir(parents=True)
    (store / "5.json").write_text(json.dumps(
        {"id": "5", "owner": "coder", "subject": "fixture store", "metadata": {}}))
    # A bare-base file the named-store read must never fall back to.
    (config / "tasks" / "6.json").write_text(json.dumps({"subject": "bare base"}))
    home = tmp_path / "home"
    home.mkdir()
    return {"CLAUDE_CONFIG_DIR": str(config), "HOME": str(home),
            "PYTHONDONTWRITEBYTECODE": "1"}


def _run(name, env, argv=None, stdin=None):
    """Run one route exactly as the skill writes it, placeholders filled."""
    tokens = shlex.split(ROUTES[name].replace("{plugin_root}", str(_PLUGIN)))
    assert tokens[:2] == ["python3", "-c"], tokens[:2]
    code, rest = tokens[2], tokens[3:]
    if "<" in rest:
        rest = rest[:rest.index("<")]
        assert stdin is not None, f"{name} reads stdin; the fixture gives none"
    values = list(argv if argv is not None else FIXTURES[name][0])
    assert all(t.startswith("$") for t in rest), rest
    assert len(rest) == len(values), (name, rest, values)
    payload = stdin if isinstance(stdin, str) or stdin is None else json.dumps(stdin)
    proc = subprocess.run([sys.executable, "-c", code, *values], input=payload or "",
                          capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, (name, proc.stderr)
    return proc.stdout.strip()


def test_every_route_has_a_fixture_and_every_fixture_a_route():
    assert set(ROUTES) == set(FIXTURES)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_route_runs_and_prints_what_its_text_says(name, child_env):
    argv, stdin, check = FIXTURES[name]
    out = _run(name, child_env, argv, stdin)
    assert check(out), (name, out)


def test_named_store_read_never_falls_back_to_the_bare_base(child_env):
    """Control: the child resolves the FIXTURE config root (the previous arm
    read its store), and a team with no store reads nothing even though a
    same-numbered file sits at the bare tasks directory."""
    assert json.loads(_run("read_task_json", child_env, ["6", OTHER])) == {}
