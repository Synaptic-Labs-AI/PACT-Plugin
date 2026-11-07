# PACT Mission Control — build contract

This file is the interface contract for the visualizer. It is the source of
truth for the HTTP payloads, DOM shell, and JS module boundaries.

## Product boundaries

- Read-only. Never write to `~/.claude/pact-sessions`, `~/.claude/tasks`,
  `~/.claude/teams`, or `~/.claude/pact-memory`.
- Local-only. The server binds `127.0.0.1` only. No `--host`, no
  `--allow-remote`, no external network calls from the server or the page.
- Keyed. Each server start mints a random launch key. Every `/api/*` request
  must carry it in the `X-PACT-Key` header; without it the server answers 401
  before any route work. The key reaches the page only in the URL fragment
  (`#k=`), so it never reaches a server log or a Referer. Static files and `/`
  need no key; they carry no run data. A custom header cannot be sent
  cross-origin (`OPTIONS` is 405), so another web page cannot read the API.
  Local users who cannot read the 0600 state file cannot either.
- Redacted. Every string derived from journals, task records and team files
  (titles, details, subjects, handoff fields, raw payloads, the session list's
  `team` and `project_dir`) passes `classify.redact` before it is bounded: credential-shaped values (GitHub, Anthropic/OpenAI-style,
  AWS, Slack tokens, bearer tokens, PEM private keys) become `[redacted]`,
  `name=value` assignments for password/secret/token/api key/authorization
  names keep the name and lose the value, and a raw mapping key naming a
  credential (token, secret, password, authorization, api key, credential,
  cookie) has its whole value replaced. Redaction is unconditional.
- Terminal-safe. `--list` and `--status` strip C0/C1 control characters from
  every field they print.
- Python 3.12+, stdlib only. Front-end is plain ES modules, no build step.
- Runtime uses no model/API tokens. `/PACT:visualize` and direct
  `bin/pact-viz` are two equal launch paths over the same server.

## Package layout (final)

```
visualizer/
  __init__.py  __main__.py  cli.py  settings.py  discovery.py  journal.py
  roster.py  classify.py  model.py  demo.py  server.py
  CONTRACT.md  README.md  assets/hero.png
  static/index.html  static/favicon.svg  static/dev-demo.json
  static/css/{base,stage,panels}.css
  static/js/{api,dom,app,sprites,stage,panels,timeline,charts,audio,achievements}.js
```

## CLI lifecycle

One server per state directory (default `<config>/pact-viz/`) serves every
session of its roots. `--demo`, `--project LABEL`, `--project-dir PATH` and
`--session ID` never change what the server serves; they only choose what the
printed URL opens (§ URL selection). `--project` must fully match
`[A-Za-z0-9._-]{1,128}` and `--session` `[A-Za-z0-9-]{8,64}`; otherwise the
CLI prints `pact-viz: --project must match [A-Za-z0-9._-]` (or the `--session`
equivalent) and exits 2. `--project-dir` uses the hooks' on-disk slug of the
resolved directory name. With `--list`, `--project` and `--session` filter the
printed table instead.

The state file `<state-dir>/server.json` (0600, in a 0700 directory) records
the port, URL, pid, the process start time where the OS exposes it, the random
`instance_id`, the launch `key`, the roots, the plugin root and the version.
Every CLI health check sends the key, and an instance counts as ours only when
the keyed `/api/health` reports the recorded `instance_id` and pid (and start
time, when recorded).

A successful launch prints, on stdout: a banner line, `URL: <url>` and
`Stop: <command>`. The URL carries the selection query and the key fragment:
`http://127.0.0.1:<port>/?project=<label>#k=<key>`. The Stop command is
runnable in any shell: the launcher's absolute path, or the interpreter form
when the module was run directly, plus `--stop --state-dir <absolute dir>`,
every part shell-quoted. `--open` writes `<state-dir>/launch.html` (0600), a
redirect to the keyed URL, and opens that file, so the key never appears in a
process argument list.

Reuse, when a confirmed instance is already recorded:

1. Another plugin build (different plugin root or version): the CLI stops it
   with a verified stop, prints
   `PACT Mission Control: replaced an instance from another plugin build`,
   and starts a fresh server. Only if that stop does not take effect does it
   exit 1 with `pact-viz: a running instance from another plugin build did not
   stop; stop it first with <Stop command>`.
2. Different roots: refused, exit 1, with
   `pact-viz: a running instance serves different roots (<running>) than
   requested (<wanted>); stop it first with <Stop command>`.
3. An explicit `--port` that differs: refused, exit 1, with
   `pact-viz: a running instance is on port <p> but port <q> was requested;
   stop it first with <Stop command>`.
4. Otherwise: `PACT Mission Control: reusing the running instance`, then the
   URL (stored key, this invocation's selection) and the Stop line. Reuse never
   mints a new key.

An instance that keyed health cannot confirm (no answer, a 401, or another
instance id, pid or start time) is never reused and never refused: the CLI
starts a fresh server, and its record replaces the old one.

`--status` prints `PACT Mission Control: running`, the keyed `URL:`, `PID:`
(from the server's own health answer) and `Instance:`, or
`PACT Mission Control: not running`.

`--stop` acts only on the recorded instance and prints exactly one `Stop:` line:

- `Stop: stopped pid <pid>`, then `PACT Mission Control: stopped`. The
  instance stopped answering; exit 0.
- `Stop: not running`. Nothing confirmed was running, and a stale record was
  removed; exit 0.
- `Stop: unconfirmed pid <pid>`, in two cases, both exit 1:
  - The server did not answer its health check, but its pid is alive and its
    port still accepts connections (a stopped or overloaded server). Nothing is
    signalled and the record is kept; the next line is `PACT Mission Control:
    the server did not answer its health check; the record is kept — run the
    Stop line again once it responds`.
  - The process was signalled but did not confirm exit; the next line is
    `PACT Mission Control: stop was signalled but the process did not confirm
    exit`.
- `Stop: refused`. The server answers with this instance's key and id but
  under another pid or start time. Nothing is signalled; the next line is
  `PACT Mission Control: state file pid <state-pid> does not match the
  server's pid <server-pid>; refusing to signal — delete <state-file> to clear
  the record` (the path is shell-quoted). The state file is kept; exit 1.
  Also `Stop: refused` when `server.json` exists but is not trusted (below):
  the next line is `PACT Mission Control: <state-file> is not a private file of
  this user, so it was neither used nor removed`; exit 1.

One recorded instance per state directory is deliberate; a second would
overwrite the record and orphan the first. Launches take an exclusive lock on
`<state-dir>/.launch.lock`, so concurrent `--detach` runs share one server. The
lock file must be owned by the user (else `lock file <path> is not owned by
this user; remove it`), and a launch waits at most 5 s for it, then exits 1
with `another launch holds <path>; retry in a moment, or stop the process
holding it`.

Trusted files: `server.json`, and `server.log` when its tail is quoted, are read
only when they are a regular file (opened without following a symlink), owned by
the user, and not writable by group or others. Anything else gets one stderr
line, `pact-viz: warning: ignoring <path>: <reason>`, and is treated as no
record: it is never reused, stopped or signalled from. `--stop` and every
cleanup path leave it in place; a new launch replaces it with its own record.
The CLI's own 0600 record stays trusted inside a group-writable directory.

State directory: a directory the CLI creates (the default `<config>/pact-viz`
included) is made 0700. An existing one is never chmod-ed, but it must be a
real directory (not a symlink) owned by the user, and not world-writable.
Otherwise the CLI exits 1 with `state directory <dir> is not a real
directory`, `… is not owned by this user`, or `… is world-writable (mode
<mode>); run chmod go-w <dir>` (a `1777` directory included). A group-writable
one is accepted with one stderr line, `pact-viz: warning: state directory <dir>
is group-writable (mode <mode>); run chmod go-w <dir>`, and the launch
continues. The launch page `launch.html` is
unlinked and recreated with `O_EXCL | O_NOFOLLOW`, mode 0600.

`--open` is skipped on a headless session (`SSH_CONNECTION` or `SSH_TTY` set,
or Linux with neither `DISPLAY` nor `WAYLAND_DISPLAY`) and never holds the CLI
more than 3 s. A detached child that exits before it is healthy is reported as
`the server exited before it was ready`, with the tail of its log. `--list`
numbers the roots (`#1`, `#2`, …) in its header and ROOT column, and exits
quietly when its output pipe closes. A `--root` that does not exist, or that
names a `pact-sessions` directory itself, gets one stderr warning
(`pact-viz: root <path> does not exist` / `… looks like a pact-sessions
directory; pass its parent`).

## HTTP API

All responses are JSON, `Content-Type: application/json; charset=utf-8`.
Errors are `{"ok": false, "error": "<short reason>"}` with 400 (bad
parameters), 401 (`key required`: the `X-PACT-Key` header is missing or wrong;
checked right after the Host check, before any route work, on every `/api/*`
route including health), 403 (a `Host` header other than exactly
`127.0.0.1` or `localhost` with an optional `:port`), 404, 405 (any method but
GET, and HEAD on `/api/*`), or 500 (an unhandled handler error). `limit` must be
ASCII digits. An HTTP/0.9 request line is closed with no response. JSON never
carries NaN or Infinity; they become `null`.

Server bounds: at most 2 model builds run at once; the 4 most recent encoded
session bodies are cached by (journal, etag); at most 32 connections are open. At the cap, the
server closes the oldest connection that is not serving a key-verified `/api`
request (one still sending headers, an idle keep-alive, or an unkeyed static
request) and admits the new one. It refuses a new connection only when every
slot is serving a keyed request, so unkeyed connections can never lock out
the key holder. Under a sustained connection flood from a local process,
unkeyed requests can also be evicted, including the page's static assets on
its first load. Keyed `/api` requests always get through, so a page that is
already open keeps updating. A journal is parsed
incrementally: a poll reads only the bytes appended since the last parse of
the same file (same device and inode), and reparses in full when the file
shrinks or is replaced. The `Server` header is `pact-mission-control/<version>`.

### GET /api/health

```json
{
  "ok": true,
  "name": "pact-mission-control",
  "version": "4.9.0",
  "plugin_root": "/home/x/.claude/plugins/cache/pact-plugin/PACT/4.9.0",
  "pid": 12345,
  "instance_id": "random-hex",
  "uptime_s": 12.3,
  "roots": [{"id": "~/.claude", "label": ".claude", "path": "/home/x/.claude", "journals": 291, "default": true}],
  "demo_available": true,
  "generated_at": "2026-11-03T00:00:00Z"
}
```

Served only with the key; without it, 401 like every other `/api/*` route.
`version` is the plugin version from `.claude-plugin/plugin.json` and, with
`plugin_root`, is the build identity the CLI compares on reuse.
`demo_available` reports that the scripted demo can be opened; there is no
demo launch mode.

### GET /api/sessions?limit=500&project=…&session=…

```json
{
  "ok": true,
  "generated_at": "2026-11-03T00:00:00Z",
  "default_root": "~/.claude",
  "filters": {},
  "total": 612,
  "limit": 500,
  "hidden_trivial": 117,
  "roots": [{"id": "~/.claude", "label": ".claude", "path": "/Users/x/.claude", "journals": 291, "default": true}],
  "sessions": [{
    "key": "~/.claude::PACT-prompt::d0964a9f",
    "root": "~/.claude",
    "slug": "PACT-prompt",
    "session_id": "d0964a9f-1de9-4ed1-b845-5cf88b2d8a55",
    "project": "PACT-prompt",
    "project_dir": "/Users/x/Sites/PACT-prompt",
    "team": "session-d0964a9f",
    "started_at": "2026-10-29T10:00:00Z",
    "updated_at": "2026-10-29T12:00:00Z",
    "size_bytes": 10193885,
    "is_live": false,
    "status": "ended",
    "ended_by": "session_end",
    "last_event_type": "session_end",
    "last_phase": "TEST",
    "run_kind": "orchestrate",
    "trivial": false,
    "project_display": "PACT-prompt",
    "project_collision": false
  }]
}
```

`project` and `session` are request filters: `project` must fully match
`[A-Za-z0-9._-]{1,128}` and `session` `[A-Za-z0-9-]{8,64}`, else 400.
`project` matches tier by tier, exact first: the slug, then the constrained
label, then the raw directory basename; only when all three miss does a
case-insensitive pass over the same names run, never a substring. `session`
matches an exact id or an id prefix. `filters` echoes the applied filters
(`{}` when none), so the page can tell an empty root from a selection that
matched nothing.

List entries are deliberately cheap: no full journal parse. `started_at` may be
null when unavailable. `last_phase` reflects the last `phase_transition` within
512KB of the journal's end; older transitions yield `""`. Sorted newest
`updated_at` first, capped by `limit` (default 500, at most 2000).

- `total` is the number of sessions that matched (after the filters and the
  trivial-session rule), before `limit`; the page shows "showing N of total"
  when it is larger than the list.
- A session is `trivial` when it is not an orchestration, its journal is under
  2048 bytes, and it is not live. Trivial sessions are left out unless the
  request has `all=1`; `hidden_trivial` counts the ones left out, and
  `filters` echoes `all` when it was given. `--list` always shows them.
- `project_display` is the original directory basename (control characters
  removed, redacted, at most 200 chars) for display. `project_collision` is
  true when two different names fold to the same `project` label.

`project` is constrained to `[A-Za-z0-9._-]`; a basename carrying any other
character has those characters replaced with `_` and the entry carries
`project_altered: true` (the session is never dropped).

Run status — one rule, shared by the list and the full model:

- The run is ended by its own `session_end` only when that is the newest
  lifecycle event (`session_start` / `session_end`) and only end trailers
  (`session_end`, `cleanup_summary`) follow it. Then `status` is `paused` or
  `refreshed` when a `session_paused` / `session_refreshed` lies after the
  newest `session_start`, else `ended`; `ended_by` is `session_end`.
- Otherwise the activity clock decides: the newest mtime of the journal,
  `tasks/<team>/` and its task files, and `teams/<team>/config.json` and
  `pact-roster.jsonl` (paths the containment rule accepts). Within 300 s of
  now the run is `live` with `ended_by: null`; later it is `ended` with
  `ended_by: "idle"`, and it returns to `live` when activity resumes.
- A resumed or compacted session appends `session_start` to the same journal
  after the previous process's `session_end`, so the run is live again.

The list reads the lifecycle from a bounded backward walk of the journal's
tail and the model from the full parse. Both views agree: a `session_end` far
from the end of the file always has other events after it.

`run_kind` in the session list is derived from the first 64KB (at most its
first 40 valid events) and the last 512KB of the journal; a run whose only
orchestration markers fall outside both windows lists as `session`, while
`/api/session` (a full parse) reports `orchestrate`.

### GET /api/session?root=…&slug=…&session_id=…&since=…

`root` is a root id from `/api/sessions`; omit for the default root.
`since` is an etag previously returned. The etag token is
`<journal mtime_ns>-<journal size>-<activity mtime_ns>-<status>-<ended_by or ->-<build version>`,
computed from stats and the cached lifecycle walk without parsing the journal.
It changes on any journal append, any task or team file change, and when the
status flips (for example, a quiet run crossing the live window). When `since`
equals the current token, return:

```json
{"ok": true, "unchanged": true, "etag": "1780000000000000000-10193885-1780000000000000000-ended-session_end-0.1.0"}
```

The JSON `etag` field carries the bare token; the HTTP `ETag` header carries
it quoted (`"…"`). Send the JSON value back as `since`.

Otherwise return the full model:

```json
{
  "ok": true,
  "version": "0.1.0",
  "etag": "1780000000000000000-10193885-1780000000000000000-ended-session_end-0.1.0",
  "source": {
    "kind": "real",
    "root": "~/.claude",
    "slug": "PACT-prompt",
    "session_id": "d0964a9f-...",
    "journal": "pact-sessions/PACT-prompt/d0964a9f-.../session-journal.jsonl",
    "mtime": 1780000000.0,
    "size": 10193885
  },
  "meta": {
    "project": "PACT-prompt",
    "project_dir": "/Users/x/Sites/PACT-prompt",
    "team": "session-d0964a9f",
    "started_at": "2026-10-29T10:00:00Z",
    "ended_at": "2026-10-29T12:00:00Z",
    "status": "ended",
    "ended_by": "session_end",
    "activity_at": "2026-10-29T12:00:00Z",
    "duration_s": 7200,
    "run_kind": "orchestrate",
    "cloud": false,
    "lead_address": "",
    "event_count": 13133,
    "warnings": []
  },
  "phases": {
    "order": ["PREPARE", "ARCHITECT", "CODE", "TEST"],
    "current": "TEST",
    "completed": ["PREPARE", "ARCHITECT", "CODE", "TEST"],
    "pending": [],
    "items": [{
      "name": "PREPARE",
      "family": "PREPARE",
      "status": "completed",
      "started_seq": 1,
      "ended_seq": 40,
      "started_at": "2026-10-29T10:01:00Z",
      "ended_at": "2026-10-29T10:30:00Z",
      "run": 1,
      "skip_reason": ""
    }]
  },
  "agents": [{
    "name": "preparer",
    "role": "preparer",
    "agent_type": "pact-preparer",
    "color": "green",
    "model": "claude-opus-4-8",
    "cloud": false,
    "kind": "specialist",
    "state": "done",
    "phase": "PREPARE",
    "task_id": "8",
    "first_seen": "2026-10-29T10:02:00Z",
    "last_seen": "2026-10-29T10:31:00Z",
    "stats": {"events": 12, "dispatches": 1, "handoffs": 1, "commits": 0, "findings": 0},
    "latest": {"seq": 40, "ts": "2026-10-29T10:31:00Z", "type": "agent_handoff", "summary": "preparer handed off task #8"}
  }],
  "tasks": [{
    "id": "8",
    "key": "8:3f2a9c1e",
    "occupant": "3f2a9c1e",
    "subject": "preparer: research the spec",
    "owner": "preparer",
    "phase": "PREPARE",
    "status": "handoff",
    "kind": "task",
    "variety": {"novelty": 2, "scope": 2, "uncertainty": 3, "risk": 1, "total": 8},
    "teachback": true,
    "handoff": true,
    "updated_at": "2026-10-29T10:31:00Z",
    "source": "derived"
  }],
  "events": [{
    "seq": 40,
    "ts": "2026-10-29T10:31:00Z",
    "type": "agent_handoff",
    "category": "handoff",
    "phase": "PREPARE",
    "phase_family": "PREPARE",
    "agent": "preparer",
    "task_id": "8",
    "title": "preparer handed off task #8",
    "detail": "produced: docs/preparation/spec.md",
    "severity": "info",
    "icon": "handoff",
    "raw": {"v": 1, "type": "agent_handoff", "agent": "preparer"}
  }],
  "handoffs": [{
    "task_key": "8:3f2a9c1e",
    "seq": 40,
    "ts": "2026-10-29T10:31:00Z",
    "agent": "preparer",
    "task_id": "8",
    "task_subject": "preparer: research the spec",
    "fields": {
      "produced": "docs/preparation/spec.md",
      "decisions": "Chose the narrow scope",
      "reasoning_chain": "Narrow because ...",
      "uncertainty": "[MEDIUM] pricing page unknown",
      "integration": "architect consumes the doc",
      "open_questions": "None"
    },
    "schema_complete": true
  }],
  "commits": [{"seq": 88, "ts": "2026-10-29T11:00:00Z", "sha": "94c2da8", "message": "Add PREPARE research", "phase": "PREPARE"}],
  "reviews": [{"seq": 120, "ts": "2026-10-29T11:30:00Z", "pr_number": 1151, "pr_url": "https://github.com/...", "reviewers": ["architect-reviewer"]}],
  "findings": [{"seq": 122, "ts": "2026-10-29T11:31:00Z", "severity": "blocking", "finding": "missing auth check", "reviewer": "security-engineer", "task_id": "30"}],
  "alerts": [{"seq": 130, "ts": "2026-10-29T11:35:00Z", "kind": "algedonic", "level": "halt", "category": "META-BLOCK", "category_key": "meta-block", "summary": "...", "task_id": "30", "task_key": "30:9d1b2c4a", "status": "active", "resolved": false, "resolution": null}],
  "checkpoints": [{"seq": 41, "ts": "2026-10-29T10:32:00Z", "phase": "PREPARE", "completed_phases": ["PREPARE"], "active_agents": [{"name": "preparer", "task_id": "8", "note": "completed"}], "variety": {"novelty": 2, "scope": 2, "uncertainty": 3, "risk": 1, "total": 8}}],
  "gates": {
    "allowed": 33986,
    "advisory": 8943,
    "by_tool": {"TaskUpdate": {"allow": 28107, "advisory": 239}},
    "top_rules": [{"rule": "reasoning_reconstruction_band_unresolvable", "count": 12}],
    "health_failures": [{"seq": 55, "ts": "2026-10-29T10:40:00Z", "hook": "task_lifecycle_gate", "stage": "runtime", "error": "AttributeError: ..."}],
    "telemetry": {"journal_emit_skipped": 3, "handoff_refusal_degraded": 1}
  },
  "wrapup": {"source": "wrap-up", "pr_state": "MERGED", "pr_number": 1151, "ts": "2026-10-29T12:00:00Z"},
  "variety": {
    "feature": {"novelty": 3, "scope": 2, "uncertainty": 3, "risk": 1, "total": 9},
    "latest": {"novelty": 2, "scope": 2, "uncertainty": 3, "risk": 1, "total": 8},
    "history": [{"seq": 10, "ts": "2026-10-29T10:05:00Z", "total": 9, "source": "feature"}]
  },
  "metrics": {
    "events_total": 13133,
    "events_visible": 4200,
    "agents": 8,
    "tasks": 42,
    "handoffs": 120,
    "commits": 12,
    "reviews": 3,
    "findings": {"total": 9, "blocking": 1, "suggestion": 4},
    "gates_allowed": 33986,
    "gates_advisory": 8943,
    "gate_health_failures": 731,
    "alerts": 2,
    "teachbacks": 9,
    "checkpoints": 5,
    "compactions": 3,
    "phases_completed": 4,
    "phases_skipped": 0,
    "prs_ready": 1,
    "variety_total": 8
  },
  "truncated": false,
  "truncated_by": [],
  "warnings": []
}
```

`meta.project` follows the same `[A-Za-z0-9._-]` constraint as the session
list's `project`, and `meta.project_altered: true` marks a label that had
characters replaced.

Field rules:

- `version` is the server's payload version; the About tab reports it.
- `events` is ordered by `seq` ascending. `seq` is the journal line index
  (0-based) and must be stable across polls. An event type outside the visual
  set is counted in `warnings` and omitted from `events`, so it appears in
  neither the feed nor the raw-log view.
- `title` / `detail` are plain text, no HTML, redacted, then truncated to
  240/500 chars.
- `phase_family` ∈ `PREPARE|ARCHITECT|CODE|TEST|REVIEW|OTHER`, from the
  event's own `phase`, else the run's current phase at that seq (the newest
  `phase_transition` before it). Mapping, upper-cased with `_` read as `-`:
  PREPARE, PLAN, INVESTIGATE → PREPARE; ARCHITECT → ARCHITECT; CODE, EXECUTE,
  OPERATOR-DEV, DEPLOY → CODE; TEST, QA, VERIFY → TEST; anything starting
  REVIEW, and PEER-REVIEW → REVIEW; anything else, or no phase → OTHER.
- `agent` names the event's actor from the writer's own field: `agent` for most
  types, `reviewer` for `review_finding`, the spawned `name` for
  `dispatch_decision` (null for an unnamed spawn), `fixer` for `remediation`,
  `owner` for `task_metadata_snapshot`. `dispatch_site` and `teachback_ack`
  carry no actor; they take the owner of the task card they name (the gate
  task, for an ack), else null. `review_dispatch` has no actor; it carries
  `agents`, its `reviewers` list, and every reviewer it names joins the crew.
- `session_end`: a bare record (the SessionEnd hook, a process exit) has
  category `session`, severity `muted` and title `Session process ended`. A
  record with `source` `wrap-up` has category `pr`, icon `pr`,
  `wrapup: true`, and title `SHIPPED · wrap-up recorded` when the PR is
  MERGED, else `Wrap-up recorded` (detail `no PR` for NONE/UNKNOWN).
- `dispatch_site` renders as `Dispatch site · task #<id>`, with detail
  `variety <total>` when it carries one.
- Writer field names, as emitted: a `commit` detail is its `note`; a
  `review_finding` detail is its `finding`; `session_paused` shows
  `worktree_path` and `session_refreshed` its `worktrees` list;
  `pin_prune_skipped` shows its `reason`; `pin_marker_write` is classified
  (category `memory`, title `Pin marker · <outcome>`); a `variety_assessed` is
  titled "Dispatch variety" only when its `scope` is `dispatch`; an old
  `{score, rationale}` variety dimension reads its `score`; a dispatch-gate
  `WARN` has severity `warning`.
- Caps keep the NEWEST entries. `truncated_by` names every cap that applied:
  `journal_bytes` (a journal over 64 MiB is parsed from its last 64 MiB only,
  dropping the partial first line, with a warning), `events` (6000),
  `tasks` (400), `handoffs` (400), `commits` (2000), `reviews` (300),
  `findings` (500), `alerts` (200), `checkpoints` (500), `agents` (200).
  `truncated` is true exactly when `truncated_by` is non-empty.
- `severity` ∈ `info|success|warning|danger|muted`. `icon` is a short token
  (`phase`, `dispatch`, `handoff`, `commit`, `review`, `bug`, `pr`, `gate`,
  `alert`, `save`, `session`, `compact`, `wake`, `variety`, `task`,
  `teachback`, `raw`).
- `handoffs[].fields` always carries all six canonical keys; absent values are
  `null` (renderer shows a dash). Legacy spellings are resolved server-side.
  `schema_complete` is true when the five REQUIRED keys are present;
  `reasoning_chain` is recommended in the PACT schema, so a five-key handoff is
  complete.
- `meta.status` ∈ `live|paused|refreshed|ended`; `meta.ended_by` ∈
  `session_end|idle|null` (null while live); `meta.activity_at` is the activity
  clock as ISO UTC, or null.
- `meta.cloud` is true when the LATEST `session_start` that carries a
  `lead_address` has `"main"` (the session context's `lead_address` decides
  only when no start carries one), or when `teams/<team>/pact-roster.jsonl`
  exists as a regular file the containment rule accepts. A cloud run resumed
  locally is a local run. `"team-lead"` or no address means local, and a team
  directory without `config.json` is never cloud on its own.
- Cloud crew: when the team directory holds `pact-roster.jsonl` and no
  `config.json`, the crew comes from the roster's `v: 1` records. The kind is
  read from `k` (legacy `kind` only when `k` is absent); the first `intent`
  per `tu` names the agent, and `bind`/`confirm` attach its `aid`.
- One lead: the run has exactly one lead entry. It is named by `lead_address`
  when that is `team-lead` or `main`. A config member with `agentType`
  `team-lead`, or any member or event named `team-lead`, `main` or the
  lead address, folds onto that entry.
- `wrapup` is the LATEST `session_end` whose `source` is `wrap-up`, or `null`.
  A bare `session_end` (the SessionEnd hook's record of a process exit) is never
  a wrap-up.
- Task identity: a task id is reused after the task store drains, so one id can
  name several tasks. The snapshots of an id form segments; a new segment (a
  new card) starts at a snapshot whose `subject` differs from the current
  card's. A blank subject (empty, or the writers' `(no subject)` placeholder)
  means no change. It never starts a card, and a card that began blank takes
  the first real subject without splitting. An owner change never splits a card. A segment begins at its
  boundary: the newest `session_start` before its first snapshot and after the
  previous card's last one (the store drain), or that first snapshot when there
  is no such restart. A record without an occupant joins the card whose span
  contains it; one before the first boundary joins the first card, and a live
  task file joins the newest card. `key` is `<id>:<occupant of the card's first
  snapshot>`. When an earlier card of the same id already holds that
  occupant, `:<segment index>` (counted from 0) is appended. `occupant`
  carries the same term, suffix included, so `key` is always
  `<id>:<occupant>`. `id` alone is not unique. Cards are ordered by numeric id, then
  first appearance. When capped to `MAX_TASKS`, blocker and algedonic cards are
  always kept, and the remaining slots go to the newest other cards.
- `tasks[].status` is one of `pending|active|handoff|completed`.
  - Task-file statuses map `pending` → pending, `in_progress` → active,
    `completed` → completed. A `deleted` live task drops its card.
  - Journal evidence only raises the status, never lowers it: `agent_dispatch`,
    `dispatch_site` or `remediation` → active; `agent_handoff` → handoff;
    `teachback_ack` → completed, on its own gate card only.
  - Free-text checkpoint statuses never become a task status. They appear as
    `checkpoints[].active_agents[].note`.
- `tasks[].kind` is `task` unless the task's metadata `type` names another
  kind (`blocker`, `algedonic`, `feature`, …; a signal kind is never
  overwritten). A non-signal task whose subject contains `TEACHBACK` is a
  `gate`. A `teachback_ack` is written when the lead accepts a
  teachback, by completing its gate task. So an ack raises its own task's card
  to `completed` and marks it `kind: "gate"`, whatever that card's subject
  says. It never touches the work task. A task the gate's live file lists in
  `blocks` gets `teachback: true`.
- `handoffs[].task_key` links a handoff to its card.
- `alerts` come from signal tasks: a task whose metadata `type` (or snapshot
  `task_type`) is `blocker` or `algedonic`, read from both live task files and
  snapshots.
  - `level` is lower-cased (`halt`, `alert`); a blocker without one is `alert`.
  - `category` is kept as written for display (it defaults to the kind);
    `category_key` is its lower-cased copy, which the alerts list exposes as
    each item's `data-category` attribute for matching.
  - `resolved` is true when a resolution is recorded or the task is completed.
    `resolution` is bounded text (a dict becomes compact JSON) or null.
  - `task_key` names the alert's card. When no card with that key is on the
    board (the task cap dropped it), the alert still shows its summary and
    status, without a card link.
  - The `algedonic_signal` and `blocker_raised` journal types are accepted as
    forward-compatible inputs to the same shape. No current writer emits them.
- `warnings` is a short bounded list; never crash on malformed input.

### GET /api/demo?since=…

Same model shape, `source.kind="demo"`, `source.root="demo"`, `version="demo"`
(a fixed script reports no build, so a version bump does not change it),
`etag="demo-v1"`; `since=demo-v1` returns `{ok:true,unchanged:true,etag:"demo-v1"}`.
Available even when no real sessions exist. Deterministic except base time.

### Static routes

- `GET /` → `static/index.html` (200).
- `GET /static/<path>` → only files under `static/`, MIME by extension.
- Root-relative asset aliases (page-relative refs when the page is served at
  `/`): `GET /css/<file>`, `GET /js/<file>`, `GET /favicon.svg`, and
  `GET /dev-demo.json` → the same files under `static/`, with the same
  containment and MIME rules. These are aliases only; no other root path is
  served.
- Anything else → 404.
- Headers: `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, `Cross-Origin-Resource-Policy: same-origin`, `Cross-Origin-Opener-Policy: same-origin`, on every response including errors.
- `HEAD` on `/` and on the static routes answers 200 with the GET headers and no body.

## DOM shell (index.html)

IDs and their owners:

```
#topbar                  static frame
  #root-select           app.js populates
  #project-select        app.js populates
  #session-select        app.js populates
  #btn-refresh #btn-random #btn-demo #btn-live #btn-sound
  #btn-fullscreen #btn-help #btn-about
  #live-badge #mission-clock
#layout
  #left-rail             panels.js renders
  #stage-shell
    #stage-frame > canvas#stage    stage.js draws
    #stage-caption       stage.js or app.js sets text
    #stage-legend        static legend
  #right-rail            panels.js renders
#recorder                timeline.js renders
#modal-root              panels.js renders, hidden when empty
#toast-root              panels.js/app.js appends
#live-announcer          app.js announces through it (visually hidden)
#loading                 app.js shows/hides
#error-banner            app.js shows/hides
```

Header controls are bound by `app.js`; panels/timeline/stage **must not** query
the header. They only render their own roots and call callbacks.

## JS module interfaces

### dom.js

```js
export const PROJECT_ALTERED_NOTE;  // user-facing explanation for a sanitised project label
export function el(tag, props = {}, children = []); // props: class, text, attrs, on, style, dataset, value (unknown props throw)
export function clear(node);
export function text(node, value);
export function toggleClass(node, name, on);
export function on(node, type, fn, opts);  // adds listener
export function clamp(n, lo, hi);
export function fmtClock(seconds);   // "01:23:45"
export function fmtDuration(seconds);
export function fmtTime(iso);        // local HH:MM:SS
export function fmtDate(iso);        // short local date
export function missionSecondsAt(model, cursor);
export function shortSha(sha);
export function rootChangeAction({ hasSession, requestedFilter, hasRun });
export function requestedFilterLabel(filters);  // named-empty label from the server's echoed filters
export function pollClearsError(errorSource);   // a successful poll may CLEAR the banner only when it is poll-authored
export function pollMayShowError(currentSource); // a failing poll may SHOW its error only over an empty or poll-authored banner
export function feedKey(model);        // feed cache key: run identity + first visible event seq (the capped window slides while its length stays fixed)
export function modeBadge({ hasModel, mode, followLive, status });
export function runKey(model);
export function createSeqIndex(model);
export function cssToken(name);
export function debounce(fn, ms = 120);
```

`el` must set text/attributes without innerHTML. Children may be strings,
nodes, or arrays; strings become text nodes.

### api.js

```js
export const USE_MOCK; // true when location.search has mock=1
export const KEY_STORAGE;           // sessionStorage key holding the launch key
export const KEY_REQUIRED_MESSAGE;  // the launch-link banner text
export function takeLaunchKey();    // fragment #k= -> sessionStorage, strip the fragment; "" when absent
export function setKeyRejectedHandler(fn); // called on any 401
export async function health();
export async function sessions(filters = {}); // {project, session} -> /api/sessions query
export async function session({root, slug, session_id, since}); // 304-equivalent -> {unchanged,etag}
export async function demo({since});
```

### stage.js

```js
export class Stage {
  constructor(canvas, opts = {}); // opts: {onAgentClick(name), onRoomClick(family), onMascotClick()}
  setModel(model);                 // full snapshot; roster, phases, events
  setCursor(index, {animate = false, live = false} = {});
  setSelectedAgent(name);
  setReducedMotion(bool);
  resize();
  destroy();
}
```

`stage.js` imports `sprites.js`. It may read `model.events` up to the cursor to
derive room/agent state; `setModel` + `setCursor` are the only state inputs.
Lifecycle and wrap-up are evaluated AT THE CURSOR: the crew goes to the lounge
only while the newest lifecycle event at or before the cursor is a
`session_end` and only end trailers (`session_end`, `cleanup_summary`) follow
it up to the cursor, the same rule as the server's run status (a later
`session_start` or any other event brings it back); the SHIPPED stamp
fires only for a wrap-up `session_end` (`source` is `wrap-up`), the PR stamp
uses the wrap-up at or before the cursor, and confetti only a MERGED one.
Placement at the cursor, per agent:
- Room: the agent's latest event's `phase_family` when it is PREPARE,
  ARCHITECT, CODE or TEST. An event whose family is REVIEW or OTHER keeps the
  agent in the last room it had.
- Review deck: the agent stands on the deck while its latest event has
  category `review` or `finding` (or `phase_family` REVIEW), or while a
  `review_dispatch` names it in `agents` and it has no later event of its own.
  Real reviews seldom open a REVIEW phase, so their events usually inherit
  TEST; the category, not the phase family, keeps a reviewer on the deck.
- Every reviewer a `review_dispatch` names is on the floor, even before it acts.
- New-event detection uses journal `seq`, not array position. Events with a
  seq above the last one shown are new; a cursor moving back by seq is a
  rewind. Effects therefore keep firing after the capped window starts
  sliding.
Canvas is internally laid out at 960×440 logical pixels and scaled by CSS; use
devicePixelRatio for sharpness. It owns `#stage-caption` text.

### panels.js

```js
export class Panels {
  constructor({left, right, modal, toast, onSelectAgent, onSeek, onFilter, onClose});
  setModel(model);
  setCursor(index);
  setFilter(category);            // "all" | category
  setSelectedAgent(name);
  setLive(isLive);
  toast({title, body, kind});      // achievement/notice
  setNotice(message);              // neutral "not here" empty state
  showError(message, {retry});
  openDebrief(tab);                // "tasks" | "handoffs" | "gates" | "reviews" | "variety" | "raw"
  closeModal();
  setStatus(status);               // optional footer badge
}
```

Panels renders: left rail (run summary, phase track, variety gauge, crew list),
right rail (feed controls + feed list), and the full modal/debrief. It listens
to its own rendered nodes and invokes the callbacks. It must not query header
elements.

### timeline.js

```js
export function createTimeline(root, {onSeek, onPlayPause, onSpeed, onToggleLive});
// returns { setModel(model), setCursor(index), setMode({live, playing, speed}) }
```

Renders transport controls, phase bands, event markers, cursor, and mission
time into `#recorder`.

### charts.js

```js
export function drawRadar(canvas, dims, opts = {});
export function drawSparkline(canvas, values, opts = {});
```

### audio.js

```js
export function createAudio();
// { setEnabled(bool), play(kind), enabled }
// kind: "dispatch" | "handoff" | "commit" | "bug" | "pr" | "alert" | "achievement"
// WebAudio only; must be silent and allocation-free until enabled.
```

### achievements.js

```js
export function createAchievements(storageKey, onUnlock);
// { evaluate(model, {live}) }
```

Achievements from real metrics only: first handoff, 5+ gate advisories,
3+ findings, first commit, pr_ready, all four phases completed, first alert,
500+ events, wrap-up MERGED. Persist per session in `localStorage`.

### app.js

Wires everything. It owns:
- root/project/session selects and header buttons;
- `state = {roots, sessions, current, model, mode, cursor, playing, speed, filter, selectedAgent, sound, followLive, etag, …}`,
  where `mode` ∈ `idle|live|replay|demo` and `followLive` is true only while
  the cursor rides the newest event of a live run;
- polling with ETag/`since`: an open real run is always polled, about every
  1.2 s while `meta.status` is `live` and every 10 s otherwise, so a resumed or
  idle run comes back on its own. A poll keeps the cursor on the same event
  `seq` even when the capped window slides; the demo is never polled;
- replay clock (event-paced at 1× = 2 events/s, 2× = 5, 4× = 12, 8× = 30);
- URL selection, read once at boot (`dom.launchSelection`): `?demo=1` opens the
  demo; `?root=…&slug=…&session_id=…` opens that run; `?session=ID` fetches the
  list narrowed to it and opens the single match (zero or several matches show
  an error); `?project=X` fetches the list narrowed to X and opens its newest
  live run, else its newest run (none → the named-empty notice); no selection
  opens the default root's newest live run, else its newest run. `?mock=1` is
  the offline preview;
- the launch key: without one (`takeLaunchKey()` returns "") the page shows
  the launch-link banner and makes no `/api` call; a 401 shows the same banner;
- error/loading states;
- calling `stage.setModel/setCursor`, `panels.setModel/setCursor`,
  `timeline.setModel/setCursor/setMode`, `audio.play`, `achievements.evaluate`;
- keyboard shortcuts (`space`, `←/→`, `l`, `d`, `g`, `t`, `?`, `Escape`);
- Konami code disco mode.

## Visual tokens (base.css)

```
--bg: #070b16; --panel: #0e1628; --panel-2: #131f36; --line: #24324f;
--text: #e8eefc; --muted: #8fa0c4; --dim: #7a8cb0;
--prepare: #2dd4bf; --architect: #60a5fa; --code: #fbbf24; --test: #f472b6;
--success: #4ade80; --warning: #fbbf24; --danger: #fb7185; --accent: #a3e635;
--font-mono: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
```

Desktop grid: 264px / 1fr / 340px, header 64px, footer 108px. At ≤1320px the
rails narrow to 232px / 306px. At ≤1180px the left rail becomes a crew drawer
opened by the header toggle and the footer drops to 96px. At ≤980px the right
rail becomes a comms drawer, the layout goes single-column, and the header and
footer drop to 58px / 92px. Panels and stage must remain usable at 1024×768.

## Data roots and demo

- Real roots follow `--root` → `$CLAUDE_CONFIG_DIR` → `~/.claude`;
  `--all-roots` adds `~/.claude*` siblings containing `pact-sessions/`.
- Containment: one predicate decides what is visible and readable. Under
  each root, `pact-sessions/`, `teams/` and `tasks/` may themselves be
  symlinks (their resolved paths are the base), but any symlink below them,
  whether a slug or session directory, a journal, a team directory, a
  `config.json`, a `pact-roster.jsonl` or a task file, is skipped. The
  resolved path must stay under the resolved base. Listing, fetch and the
  roster/task loaders apply the same rule, so a session is listed exactly
  when it can be opened.
- Demo is always available; the UI should start demo with `?demo=1`.
- The demo must cover: four phases, 8+ agents, dispatches, handoffs,
  teachbacks, commits, findings/remediation, gate advisory + gate-health
  failure, alert, checkpoint, compaction, dispatch decisions, team realign,
  wrap-up MERGED `session_end`, telemetry gap. No real data.

## Testing expectations

- Server tests use a temporary config root and ephemeral loopback port.
- No test or screenshot reads the operator's real `~/.claude` content;
  numeric-only real-journal smoke checks are allowed.
- Asset tests: no external `http(s)://` URLs, no `innerHTML`/`eval`, no
  external `fetch`/WebSocket/EventSource, CSP present, `bin/pact-viz`
  executable, hero exists, accessibility hooks (`aria-live`, focus styles).
- Python files under `visualizer/` must parse at Python 3.12 and have no
  unused imports (CI + suite gates).
