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

from shared import handoff_merge as hm

_PLUGIN = Path(__file__).resolve().parents[1]

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
    "report_safe": (["a\nb\u2028c"], None, lambda out: out == "abc"),
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
    # The event after a re-creation: its arc is the second instance's, not
    # the first event's, so a route that reads the wrong position is caught.
    "arc_of": ([], {"journal": _JOURNAL + [
        {"type": "task_created", "task_id": "5", "team": TEAM, "ts": T1},
        _ev(H)], "position": 4}, _json([1, 3])),
    # Two snapshots of task 5: a team-less one and one of this team's dated
    # instance. A dated unit must take the dated one and never the other.
    "select_snapshot": (
        [], {"journal": _JOURNAL + [
            {"type": "task_metadata_snapshot", "task_id": "5", "occupant": _IDENTITY,
             "metadata": {"label": "legacy"}, "ts": T1},
            {"type": "task_metadata_snapshot", "task_id": "5", "occupant": _IDENTITY,
             "team": TEAM, "metadata": {"label": "dated"}, "ts": T1}],
            "task_id": "5", "occupant": _IDENTITY, "team": TEAM, "arc": [0, 0]},
        lambda out: json.loads(out)["snapshot"]["metadata"] == {"label": "dated"}
        and json.loads(out)["reports"] == []),
    "task_metadata": ([], {"metadata": {"sib": 1}}, _json({"sib": 1})),
    "disk_handoff": ([], {"metadata": {"handoff": H, "sib": 1}}, _json(H)),
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


def test_a_teamless_snapshot_never_has_a_dated_units_arc(child_env):
    """The Step 3.1 snapshot rule compares the `arc_of` route's output with
    the `group_events` route's group arc. Executed: a team-less snapshot's
    arc is null, so it never equals a dated group's arc, and a teamed
    snapshot of the same instance carries exactly that arc."""
    journal = _JOURNAL + [
        {"type": "task_metadata_snapshot", "task_id": "5", "metadata": {},
         "subject": "s", "occupant": _IDENTITY, "ts": T1},
        {"type": "task_metadata_snapshot", "task_id": "5", "metadata": {},
         "subject": "s", "occupant": _IDENTITY, "team": TEAM, "ts": T1},
    ]
    groups = json.loads(_run("group_events", child_env, [],
                             {"journal": journal, "positions": [1, 2]}))
    (group_key, _), = groups
    dated_arc = group_key[1]
    assert dated_arc is not None
    teamless = json.loads(_run("arc_of", child_env, [], {"journal": journal, "position": 3}))
    teamed = json.loads(_run("arc_of", child_env, [], {"journal": journal, "position": 4}))
    assert teamless is None and teamless != dated_arc
    assert teamed == dated_arc



# ---- class guards: the skill's routes agree with the code they describe ----

import ast  # noqa: E402
import builtins  # noqa: E402

import test_harvest_step3_exec as step3  # noqa: E402

# Names the block calls that the harness supplies itself, not a route.
_ACTIONS = {"report", "process", "read_task_json", "Path", "datetime", "timezone"}


def _block_call_names():
    """Names the Step 3 block CALLS that it does not bind itself."""
    tree = ast.parse(step3.BLOCK)
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            bound.add(node.name)
            bound.update(a.arg for a in node.args.args)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return called - bound - set(dir(builtins)) - _ACTIONS


def _route_imported_names():
    names = set()
    for line in SKILL_TEXT.splitlines():
        for match in _IMPORT.finditer(line):
            names.update(n.strip() for n in match.group(1).split(","))
    return names


def test_every_name_the_block_calls_is_routed_exported_and_in_the_harness():
    """G-A2: a helper the block starts calling must have a route (the agent
    can call it), be exported (the route can import it) and be in the exec
    harness (the block's arms can run it)."""
    called = _block_call_names()
    assert called, "the block parse found no helper calls"
    assert called <= _route_imported_names(), called - _route_imported_names()
    harness_callables = {n for n in step3.HELPERS if not n.isupper()}
    assert called == harness_callables, called ^ harness_callables
    assert called <= set(hm.__all__), called - set(hm.__all__)


def _top_level_keys(payload: str) -> set:
    """The keys of the outermost mapping in a documented payload."""
    keys, depth, i = set(), 0, 0
    while i < len(payload):
        ch = payload[i]
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == '"' and depth == 1:
            end = payload.index('"', i + 1)
            if re.match(r"\s*:", payload[end + 1:]):
                keys.add(payload[i + 1:end])
            i = end
        i += 1
    return keys


def _route_docs():
    """(name, command line, documented payload or None) for every route."""
    lines = SKILL_TEXT.splitlines()
    out = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("python3 -c") and "from shared." in stripped):
            continue
        names = [n.strip() for n in _IMPORT.search(stripped).group(1).split(",")]
        name = [n for n in names if n not in _SUPPORT][0]
        payload = None
        for after in lines[index + 1:index + 6]:
            found = re.search(r"`\$PAYLOAD_JSON` holds `(\{.*\})`", after)
            if found:
                payload = found.group(1)
                break
        out.append((name, stripped, payload))
    return out


def test_every_route_documents_exactly_the_payload_and_argv_it_reads():
    """G-B1: the documented `$PAYLOAD_JSON` keys equal the `p['key']`
    subscripts the command reads, and the argv the command reads equals the
    `$VAR` arguments the line passes."""
    docs = _route_docs()
    assert len(docs) == len(ROUTES)
    payload_routes = 0
    for name, command, payload in docs:
        read_keys = set(re.findall(r"p\['(\w+)'\]", command))
        if read_keys:
            payload_routes += 1
            assert payload is not None, f"{name} reads {read_keys} and documents no payload"
            assert _top_level_keys(payload) == read_keys, (name, payload, read_keys)
        tokens = shlex.split(command)
        rest = tokens[3:]
        if "<" in rest:
            rest = rest[:rest.index("<")]
        indices = {int(i) for i in re.findall(r"sys\.argv\[(\d+)\]", tokens[2])}
        assert len(rest) == (max(indices) if indices else 0), (name, rest, indices)
    assert payload_routes >= 5, "the payload parse found too few routes to check"


STEP1_HEADING = "### Step 1: Task Discovery"
ORPHAN_HEADING = "## Orphaned Handoff Recovery"


def _journal_reads():
    """(section heading, form, type or None) for every journal read in the
    skill. `form` is "cli" for `session_journal.py read` and the function
    name for `read_events(...)` / `read_events_from(...)`. A MENTION is not
    a read and is skipped: a CLI form with no `--session-dir`, and a call
    whose arguments are a `...` placeholder."""
    joined = re.sub(r"\\\n\s*", " ", SKILL_TEXT)
    reads, section = [], ""
    for line in joined.splitlines():
        if re.match(r"#{2,3}\s", line):
            section = line.strip()
        for match in re.finditer(r"session_journal\.py\"?\s+read\b([^`)]*)", line):
            if "--session-dir" not in match.group(1):
                continue  # a prose mention of the subcommand, not a command
            typed = re.search(r"--type\s+(\w+)", match.group(1))
            reads.append((section, "cli", typed.group(1) if typed else None))
        # read_events(type) / read_events_from(session_dir, type), or event_type=.
        for match in re.finditer(r"\b(read_events(?:_from)?)\(([^)]*)\)", line):
            args = [a.strip() for a in match.group(2).split(",") if a.strip()]
            if args == ["..."]:
                continue
            keyword = [a for a in args if a.startswith("event_type=")]
            positional = [a for a in args if "=" not in a]
            if keyword:
                value = keyword[0].split("=", 1)[1]
            else:
                index = 1 if match.group(1) == "read_events_from" else 0
                value = positional[index] if len(positional) > index else None
            reads.append((section, match.group(1), value.strip("'\"") if value else None))
    return reads


def _unfiltered_reads_in(heading: str, form: str) -> list:
    assert heading in SKILL_TEXT.splitlines(), (
        f"heading {heading!r} moved: update the G-B2 section name, "
        f"do not drop the check"
    )
    return [r for r in _journal_reads() if r[0] == heading and r[1] == form and r[2] is None]


def test_no_journal_read_filters_a_type_the_journal_helpers_need():
    """G-B2: the journal-taking helpers need the WHOLE journal (positions and
    store epochs), so no read in the skill may filter to one of their types."""
    reads = _journal_reads()
    assert any(r[2] for r in reads), "no typed read found: the parse is not reading commands"
    filtered = [r for r in reads if r[2] in hm.JOURNAL_EVENT_TYPES]
    assert not filtered, filtered


def test_step1_reads_the_whole_journal():
    """The Step 3 block's `journal` input is Step 1's one unfiltered CLI read."""
    assert _unfiltered_reads_in(STEP1_HEADING, "cli"), (
        "Step 1 has no unfiltered `session_journal.py read`")


def test_orphan_recovery_reads_the_whole_journal():
    """Orphan Recovery feeds the block a prior session's WHOLE journal."""
    assert _unfiltered_reads_in(ORPHAN_HEADING, "read_events_from"), (
        "Orphaned Handoff Recovery has no unfiltered `read_events_from`")