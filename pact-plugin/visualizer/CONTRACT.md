# PACT Mission Control — build contract

This file is the interface contract for the visualizer. It is the source of
truth for the HTTP payloads, DOM shell, and JS module boundaries.

## Product boundaries

- Read-only. Never write to `~/.claude/pact-sessions`, `~/.claude/tasks`,
  `~/.claude/teams`, or `~/.claude/pact-memory`.
- Local-only. The server binds `127.0.0.1` only. No `--host`, no
  `--allow-remote`, no external network calls from the server or the page.
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

## HTTP API

All responses are JSON, `Content-Type: application/json; charset=utf-8`.
Errors are `{"ok": false, "error": "<short reason>"}` with 400/404/405.

### GET /api/health

```json
{
  "ok": true,
  "name": "pact-mission-control",
  "version": "0.1.0",
  "pid": 12345,
  "instance_id": "random-hex",
  "uptime_s": 12.3,
  "roots": [{"id": "~/.claude", "label": ".claude", "path": "/Users/x/.claude", "journals": 291, "default": true}],
  "demo": true
}
```

### GET /api/sessions?limit=500

```json
{
  "ok": true,
  "generated_at": "2026-11-03T00:00:00Z",
  "default_root": "~/.claude",
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
    "last_event_type": "session_end",
    "last_phase": "TEST",
    "run_kind": "orchestrate"
  }]
}
```

List entries are deliberately cheap: no full journal parse. `started_at` may be
null when unavailable. `last_phase` reflects the last `phase_transition` within
512KB of the journal's end; older transitions yield `""`. Sorted newest
`updated_at` first, capped by `limit`.

### GET /api/session?root=…&slug=…&session_id=…&since=…

`root` is a root id from `/api/sessions`; omit for the default root.
`since` is an etag previously returned. When it matches the current journal
identity, return:

```json
{"ok": true, "unchanged": true, "etag": "178...-10193885"}
```

Otherwise return the full model:

```json
{
  "ok": true,
  "etag": "178...-10193885",
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
    "agent": "preparer",
    "task_id": "8",
    "title": "preparer handed off task #8",
    "detail": "produced: docs/preparation/spec.md",
    "severity": "info",
    "icon": "handoff",
    "raw": {"v": 1, "type": "agent_handoff", "agent": "preparer"}
  }],
  "handoffs": [{
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
  "alerts": [{"seq": 130, "ts": "2026-10-29T11:35:00Z", "kind": "algedonic", "level": "alert", "category": "SECURITY", "summary": "..."}],
  "checkpoints": [{"seq": 41, "ts": "2026-10-29T10:32:00Z", "phase": "PREPARE", "completed_phases": ["PREPARE"], "active_agents": [{"name": "preparer", "task_id": "8", "status": "completed"}], "variety": {"novelty": 2, "scope": 2, "uncertainty": 3, "risk": 1, "total": 8}}],
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
  "warnings": []
}
```

Field rules:

- `events` is ordered by `seq` ascending. `seq` is the journal line index
  (0-based) and must be stable across polls. Unknown event types are included
  with category `raw` only if they are in the visual set; otherwise they are
  counted and omitted.
- `title` / `detail` are plain text, no HTML, truncated to 240/500 chars.
- `severity` ∈ `info|success|warning|danger|muted`. `icon` is a short token
  (`phase`, `dispatch`, `handoff`, `commit`, `review`, `bug`, `pr`, `gate`,
  `alert`, `save`, `session`, `compact`, `wake`, `variety`, `task`, `raw`).
- `handoffs[].fields` always carries all six canonical keys; absent values are
  `null` (renderer shows a dash). Legacy spellings are resolved server-side.
  `schema_complete` is true when the five REQUIRED keys are present;
  `reasoning_chain` is recommended in the PACT schema, so a five-key handoff is
  complete.
- `wrapup` is `null` unless a wrap-up `session_end` (or explicit PR state)
  exists.
- `warnings` is a short bounded list; never crash on malformed input.

### GET /api/demo?since=…

Same model shape, `source.kind="demo"`, `source.root="demo"`,
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
- Headers: `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-cache`.

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
#loading                 app.js shows/hides
#error-banner            app.js shows/hides
```

Header controls are bound by `app.js`; panels/timeline/stage **must not** query
the header. They only render their own roots and call callbacks.

## JS module interfaces

### dom.js

```js
export function el(tag, props = {}, children = []); // props: class, text, attrs, on, style, dataset, value (unknown props throw)
export function clear(node);
export function text(node, value);
export function fmtClock(seconds);   // "01:23:45"
export function fmtDuration(seconds);
export function fmtTime(iso);        // local HH:MM:SS
export function shortSha(sha);
export function clamp(n, lo, hi);
export function on(node, type, fn);  // adds listener
```

`el` must set text/attributes without innerHTML. Children may be strings,
nodes, or arrays; strings become text nodes.

### api.js

```js
export const USE_MOCK; // true when location.search has mock=1
export async function health();
export async function sessions();
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
- `state = {sessions, roots, model, cursor, live, playing, speed, filter, selectedAgent, sound}`;
- polling (`live` every ~1200ms; paused/ended only on demand), ETag/since;
- replay clock (event-paced at 1× = 2 events/s, 2× = 5, 4× = 12, 8× = 30);
- URL state (`?demo=1`, `?root=…&slug=…&session=…&mock=1`);
- error/loading states;
- calling `stage.setModel/setCursor`, `panels.setModel/setCursor`,
  `timeline.setModel/setCursor/setMode`, `audio.play`, `achievements.evaluate`;
- keyboard shortcuts (`space`, `←/→`, `l`, `d`, `g`, `t`, `?`, `Escape`);
- Konami code disco mode.

## Visual tokens (base.css)

```
--bg: #070b16; --panel: #0e1628; --panel-2: #131f36; --line: #24324f;
--text: #e8eefc; --muted: #8fa0c4; --dim: #5f7096;
--prepare: #2dd4bf; --architect: #60a5fa; --code: #fbbf24; --test: #f472b6;
--success: #4ade80; --warning: #fbbf24; --danger: #fb7185; --accent: #a3e635;
--font-mono: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
```

Desktop grid: 264px / 1fr / 340px, header 64px, footer 104px. At ≤1180px the
left rail collapses above the stage (CSS); at ≤900px both rails become
scrollable drawers/tabs. Panels and stage must remain usable at 1024×768.

## Data roots and demo

- Real roots follow `--root` → `$CLAUDE_CONFIG_DIR` → `~/.claude`;
  `--all-roots` adds `~/.claude*` siblings containing `pact-sessions/`.
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
