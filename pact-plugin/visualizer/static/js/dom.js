/* Tiny DOM helpers. No innerHTML anywhere: all data becomes text nodes. */

/* User-facing explanation for a project label the server had to sanitize. */
export const PROJECT_ALTERED_NOTE =
  "The directory name contains characters that are not shown verbatim; this label was altered for display.";

/* User-facing explanation for a label two different directory names share. */
export const PROJECT_COLLISION_NOTE =
  "Two different directory names share this label; check the run's journal path to tell them apart.";

export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null) continue;
    if (key === "class" || key === "className") {
      node.className = String(value);
    } else if (key === "text") {
      node.textContent = String(value);
    } else if (key === "style" && typeof value === "object") {
      Object.assign(node.style, value);
    } else if (key === "dataset" && typeof value === "object") {
      for (const [dk, dv] of Object.entries(value)) node.dataset[dk] = String(dv);
    } else if (key === "on" && typeof value === "object") {
      for (const [type, fn] of Object.entries(value)) {
        if (typeof fn === "function") node.addEventListener(type, fn);
      }
    } else if (key === "attrs" && typeof value === "object") {
      for (const [ak, av] of Object.entries(value)) {
        if (av === false || av === undefined || av === null) continue;
        // Event-handler and URL attributes are the only attribute names that
        // can execute; every real call site uses a literal name, so reject
        // these rather than let a future computed key become a sink.
        if (/^(on|href$|src$|srcdoc$|xlink:href$)/i.test(ak)) {
          throw new Error(`dom.el: refusing attribute '${ak}'`);
        }
        node.setAttribute(ak, av === true ? "" : String(av));
      }
    } else if (key === "value") {
      node.value = value;
    } else {
      // Unknown props fail loudly: a generic node[key] = value fallback
      // would let a markup property slip past review instead of throwing.
      throw new Error(`dom.el: unsupported prop '${key}'; use class, text, style, dataset, on, attrs, or value`);
    }
  }
  append(node, children);
  return node;
}

function append(node, children) {
  if (children === undefined || children === null) return;
  if (Array.isArray(children)) {
    for (const child of children) append(node, child);
  } else if (children instanceof Node) {
    node.appendChild(children);
  } else {
    node.appendChild(document.createTextNode(String(children)));
  }
}

export function clear(node) {
  if (!node) return node;
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function text(node, value) {
  if (!node) return node;
  node.textContent = value === undefined || value === null ? "" : String(value);
  return node;
}

export function toggleClass(node, name, on) {
  if (!node) return;
  if (node.classList && typeof node.classList.toggle === "function") {
    node.classList.toggle(name, Boolean(on));
    return;
  }
  const set = new Set(String(node.className || "").split(/\s+/).filter(Boolean));
  if (on) set.add(name);
  else set.delete(name);
  node.className = [...set].join(" ");
}

export function on(node, type, fn, opts) {
  if (!node || typeof fn !== "function") return () => {};
  node.addEventListener(type, fn, opts);
  return () => node.removeEventListener(type, fn, opts);
}

export function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, Number(n) || 0));
}

export function fmtClock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
}

export function fmtDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  if (total < 60) return `${total}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/* Mission time at a cursor that indexes the VISIBLE event window. Cursor 0
 * is the journal start, 0s by definition, unless the event cap dropped the
 * start; an empty window must not fall back to wall-clock time (that reads
 * as a plausible wrong duration). */
export function missionSecondsAt(model, cursor) {
  if (!model || !model.meta || !model.meta.started_at) return 0;
  const start = new Date(model.meta.started_at).getTime();
  if (Number.isNaN(start)) return 0;
  const events = Array.isArray(model.events) ? model.events : [];
  if (!events.length) return 0;
  /* When the event cap or a tail-only parse of an oversized journal dropped
   * the journal's start, cursor 0 is the window's first event, not the
   * journal start. */
  const cuts = Array.isArray(model.truncated_by) ? model.truncated_by : [];
  const truncated = cuts.includes("events") || cuts.includes("journal_bytes");
  if (cursor <= 0 && !truncated) return 0;
  const event = events[clamp(cursor - 1, 0, events.length - 1)];
  const end = event && event.ts ? new Date(event.ts).getTime() : NaN;
  if (Number.isNaN(end)) return 0;
  return Math.max(0, Math.round((end - start) / 1000));
}

export function shortSha(sha) {
  if (!sha) return "";
  return String(sha).slice(0, 7);
}

/* What a root change should do. A root with no sessions must not leave the
 * previous root's run on screen (that run belongs to another root), filtered
 * or not: the view clears and names what is empty. Only with nothing open and
 * no filter does the demo stand in. */
export function rootChangeAction({ hasSession, requestedFilter, hasRun }) {
  if (hasSession) return "open";
  if (requestedFilter || hasRun) return "empty";
  return "demo";
}

/* Display label for the launch filters the server echoes. The page never
 * invents a filter from a query param: only the launch filters narrowed the
 * list, so only they can explain an empty one. */
export function requestedFilterLabel(filters) {
  const project = filters && typeof filters.project === "string" ? filters.project : "";
  const session = filters && typeof filters.session === "string" ? filters.session : "";
  const parts = [];
  if (project) parts.push(`project ${project}`);
  if (session) parts.push(`session ${session}`);
  return parts.join(" and ");
}

/* A successful poll may clear only a banner the poll itself raised. A failed
 * navigation or refresh must survive the poll that follows it. */
export function pollClearsError(errorSource) {
  return errorSource === "poll";
}

/* A failing poll may take the banner only when it is empty or poll-authored.
 * A navigation or refresh banner carries a retry a poll must not displace. */
export function pollMayShowError(currentSource) {
  return !currentSource || currentSource === "poll";
}

/* The feed cache must notice a SHIFTED visible window, not just a new run:
 * above MAX_EVENTS the array length is constant while events[0].seq advances,
 * so a length-only key leaves the rows frozen. */
export function feedKey(model) {
  const events = model && Array.isArray(model.events) ? model.events : [];
  const head = events.length && events[0] ? events[0].seq : null;
  return `${runKey(model)}#${head === null || head === undefined ? "" : head}`;
}

/* The mode badge is the page's claim about what is on screen, so it is a pure
 * decision over the run state. No model means IDLE, never the static LIVE. */
export function modeBadge({ hasModel, mode, followLive, status }) {
  if (!hasModel) return { label: "● IDLE", variant: "" };
  if (mode === "demo") return { label: "◆ DEMO", variant: "is-demo" };
  if (mode === "live") {
    return followLive && status === "live"
      ? { label: "● LIVE", variant: "is-live" }
      : { label: "◼ REPLAY", variant: "is-replay" };
  }
  if (status === "paused") return { label: "◼ PAUSED", variant: "is-replay" };
  if (status === "refreshed") return { label: "◼ REFRESHED", variant: "is-replay" };
  if (status === "live") return { label: "◼ REPLAY", variant: "is-replay" };
  return { label: "◼ ENDED", variant: "is-replay" };
}

/* A run's identity is its coordinates, never its journal revision: a live
 * poll must not read as a different run. Shared by the stage and panels. */
export function runKey(model) {
  if (!model) return "";
  const source = model.source || {};
  const meta = model.meta || {};
  return [
    source.kind || "",
    source.root || "",
    source.slug || "",
    source.session_id || "",
    meta.team || "",
  ].join("|");
}

/* Journal `seq` values are physical line numbers, not positions in the
 * visible event array (the server keeps only the newest MAX_EVENTS). Build the
 * mapping once per model: firstIndexAtOrAfter(seq) returns the first visible
 * index whose seq is >= the target, clamped to events.length, or null when
 * `seq` is absent (a pending phase). Ascending seq is a contract guarantee. */
export function createSeqIndex(model) {
  const events = model && Array.isArray(model.events) ? model.events : [];
  const seqs = new Array(events.length);
  for (let index = 0; index < events.length; index += 1) {
    seqs[index] = Number(events[index] && events[index].seq);
  }
  return function firstIndexAtOrAfter(seq) {
    if (seq === undefined || seq === null) return null;
    const target = Number(seq);
    if (!Number.isFinite(target)) return null;
    let lo = 0;
    let hi = seqs.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (Number.isFinite(seqs[mid]) && seqs[mid] < target) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  };
}

/* Where a reader who is NOT following the live head stays when a poll
 * replaces the events: on the same event, found by seq, because above
 * MAX_EVENTS the window slides and the same index names a different event.
 * An event that slid out of the window puts the cursor at the window start. */
export function cursorAfterRefresh(priorEvents, priorCursor, events) {
  const prior = Array.isArray(priorEvents) ? priorEvents : [];
  const next = Array.isArray(events) ? events : [];
  if (priorCursor <= 0 || !prior.length) return clamp(priorCursor, 0, next.length);
  const last = prior[Math.min(priorCursor, prior.length) - 1];
  const index = createSeqIndex({ events: next })(last && last.seq);
  if (index === null) return clamp(priorCursor, 0, next.length);
  const same = next[index] && Number(next[index].seq) === Number(last.seq);
  return clamp(same ? index + 1 : index, 0, next.length);
}

/* What the launch URL selects (newest first wins where several match):
 *   ?demo=1                        the training simulation
 *   ?root=&slug=&session_id=       that exact run (the page's own deep link)
 *   ?session=ID (with ?project=)   the one run whose id matches; zero or
 *                                  several matches are an error naming ID
 *   ?project=X                     that project's newest live run, else its
 *                                  newest run, else the named-empty notice
 *   nothing                        the newest live run on the default root,
 *                                  else its newest run
 * The server applies project and session to the list and echoes them in
 * `filters`, which is the only source for the named-empty label. */
export function launchSelection(search) {
  const params = new URLSearchParams(search || "");
  if (params.has("demo")) return { kind: "demo" };
  const root = params.get("root") || "";
  const slug = params.get("slug") || "";
  const sessionId = params.get("session_id") || "";
  if (root && slug && sessionId) return { kind: "deep", root, slug, session_id: sessionId };
  const query = {};
  if (params.get("project")) query.project = params.get("project");
  if (params.get("session")) query.session = params.get("session");
  if (query.session) return { kind: "session", root, query };
  if (query.project) return { kind: "project", root, query };
  return { kind: "default", root, query };
}

/* A session_end recorded by wrap-up (the server flags it; the raw source is
 * the fallback), as opposed to a bare process exit. */
export function isWrapupEvent(event) {
  if (!event || event.type !== "session_end") return false;
  if (event.wrapup === true) return true;
  return Boolean(event.raw && event.raw.source === "wrap-up");
}

/* The wrap-up fields carried by one wrap-up session_end event. */
export function wrapupFromEvent(event) {
  const raw = (event && event.raw) || {};
  const number = Number(raw.pr_number);
  return {
    pr_state: String(raw.pr_state || "").toUpperCase(),
    pr_number: raw.pr_number === undefined || raw.pr_number === null || !Number.isFinite(number) ? null : number,
    ts: event && event.ts,
  };
}

/* The latest wrap-up among the first `cursor` events, or null. Replay shows
 * this, never the whole run's final wrap-up. */
export function wrapupAtCursor(events, cursor) {
  const list = Array.isArray(events) ? events : [];
  let wrapup = null;
  for (let i = 0; i < cursor && i < list.length; i += 1) {
    if (isWrapupEvent(list[i])) wrapup = wrapupFromEvent(list[i]);
  }
  return wrapup;
}

/* The passes of one phase item, oldest first, each {start, end} as journal
 * seqs or null. phases.items[].runs lists every pass; an item without it is
 * one pass made of its own boundaries. A pass with no start is a phase
 * recorded as done without a start; an open pass has no end. */
export function phasePasses(item) {
  const seq = (value) => (value === null || value === undefined || !Number.isFinite(Number(value)) ? null : Number(value));
  const runs = item && Array.isArray(item.runs) && item.runs.length ? item.runs : [item || {}];
  return runs
    .filter((run) => run && typeof run === "object")
    .map((run) => ({ start: seq(run.started_seq), end: seq(run.ended_seq) }))
    .filter((pass) => pass.start !== null || pass.end !== null);
}

/* Canvas modules read design tokens off the document root. One fallback map
 * keeps timeline.js and charts.js painting the same colours in tests,
 * headless runs, and before the stylesheet has applied. */
const CSS_FALLBACK = {
  text: "#e8eefc",
  muted: "#8fa0c4",
  dim: "#7a8cb0",
  line: "#24324f",
  accent: "#a3e635",
  success: "#4ade80",
  warning: "#fbbf24",
  danger: "#fb7185",
  violet: "#a78bfa",
  prepare: "#2dd4bf",
  architect: "#60a5fa",
  code: "#fbbf24",
  test: "#f472b6",
};

export function cssToken(name) {
  const key = String(name || "").replace(/^--/, "");
  const fallback = CSS_FALLBACK[key] || CSS_FALLBACK.text;
  try {
    if (typeof getComputedStyle !== "function") return fallback;
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return value && value.trim() ? value.trim() : fallback;
  } catch (_) {
    return fallback;
  }
}

export function debounce(fn, ms = 120) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
