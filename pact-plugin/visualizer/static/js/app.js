/* PACT Mission Control — app shell and orchestration.
 * Reads local journals via same-origin API only. No external requests. */

import { sessions, session, demo, health, USE_MOCK } from "./api.js";
import {
  el,
  clear,
  fmtClock,
  clamp,
  missionSecondsAt,
  rootChangeAction,
  modeBadge,
  requestedFilterLabel,
  pollClearsError,
  PROJECT_ALTERED_NOTE,
} from "./dom.js";
import { Stage } from "./stage.js";
import { Panels } from "./panels.js";
import { createTimeline } from "./timeline.js";
import { createAudio } from "./audio.js";
import { createAchievements } from "./achievements.js";

const $ = (id) => document.getElementById(id);

const els = {
  root: $("root-select"),
  project: $("project-select"),
  session: $("session-select"),
  refresh: $("btn-refresh"),
  random: $("btn-random"),
  demo: $("btn-demo"),
  live: $("live-badge"),
  crewToggle: $("btn-crew"),
  commsToggle: $("btn-comms"),
  clock: $("mission-clock"),
  sound: $("btn-sound"),
  fullscreen: $("btn-fullscreen"),
  help: $("btn-help"),
  about: $("btn-about"),
  left: $("left-rail"),
  right: $("right-rail"),
  stageCanvas: $("stage"),
  stageCaption: $("stage-caption"),
  recorder: $("recorder"),
  modal: $("modal-root"),
  toast: $("toast-root"),
  announcer: $("live-announcer"),
  loading: $("loading"),
  loadingSub: $("loading-sub"),
  error: $("error-banner"),
  errorText: $("error-text"),
  errorRetry: $("error-retry"),
  errorDemo: $("error-demo"),
};

const SPEEDS = { 1: 2, 2: 5, 4: 12, 8: 30 };
const AUDIO_KINDS = {
  dispatch: "dispatch",
  handoff: "handoff",
  commit: "commit",
  finding: "bug",
  pr: "pr",
  alert: "alert",
};
const TIPS = [
  "Variety is complexity that must be matched with response capacity.",
  "A handoff keeps six canonical fields: produced, decisions, reasoning, uncertainty, integration, open questions.",
  "The session journal is append-only; it survives compaction and task GC.",
  "Algedonic signals bypass the normal triage path to reach the user.",
  "The auditor observes the CODE phase concurrently instead of waiting for a review.",
  "Teachbacks make a specialist restate the mission before they start.",
  "Everything here is derived from local JSONL. No model is watching.",
  "A checkpoint is a save point, not a verdict. The evidence is the event stream.",
];

const state = {
  roots: [],
  sessions: [],
  current: null,
  model: null,
  mode: "idle", // idle | live | replay | demo
  cursor: 0,
  playing: false,
  speed: 1,
  filter: "all",
  selectedAgent: null,
  sound: false,
  followLive: false,
  etag: null,
  lastError: "",
  errorSource: "",
  demoAvailable: true,
  requestedFilter: "",
  suppress: false,
  pollTimer: 0,
  raf: 0,
  lastFrame: 0,
  acc: 0,
  lastClock: 0,
  discon: false,
};

let stage;
let panels;
let timeline;
let audio;
let achievements;

/* One live poll at a time: a slow response must not overlap the next tick. */
let pollBusy = false;
/* Monotonic token: a slow openSession must not overwrite a later selection. */
let sessionRequest = 0;

function setLoading(on, sub) {
  els.loading.hidden = !on;
  if (sub) els.loadingSub.textContent = sub;
}

/* `source` records which operation raised the banner: only a poll may clear a
 * poll banner, so a failed navigation or refresh survives the next poll. */
function showError(message, retry, source = "") {
  state.lastError = message || "Something went wrong";
  state.errorSource = source;
  els.errorText.textContent = state.lastError;
  els.error.hidden = false;
  els.errorRetry.onclick = () => {
    hideError();
    if (typeof retry === "function") retry();
    else refreshAll();
  };
}

function hideError() {
  els.error.hidden = true;
  state.lastError = "";
  state.errorSource = "";
}

function toast(title, body, kind = "info") {
  if (!panels) return;
  panels.toast({ title, body, kind });
}

const ANNOUNCE_MIN_MS = 4000;
let lastAnnouncement = 0;

/* Polite live-region text for screen readers. Only live polling calls this:
 * replay scrubbing must stay silent, or every tick would speak. */
function announce(message, { force = false } = {}) {
  if (!els.announcer || !message) return;
  const now = Date.now();
  if (!force && now - lastAnnouncement < ANNOUNCE_MIN_MS) return;
  lastAnnouncement = now;
  /* A live region speaks a CHANGE; an identical repeated string is silent.
   * Toggle a zero-width space so the same sentence still re-announces. */
  els.announcer.textContent = els.announcer.textContent === message ? `${message}\u200b` : message;
}

function setModeLabel() {
  const model = state.model;
  const badge = modeBadge({
    hasModel: Boolean(model),
    mode: state.mode,
    followLive: state.followLive,
    status: model && model.meta ? model.meta.status : "",
  });
  els.live.textContent = badge.label;
  els.live.className = badge.variant ? `live-badge ${badge.variant}` : "live-badge";
}

function updateClock() {
  if (!state.model) {
    els.clock.textContent = "00:00:00";
    return;
  }
  els.clock.textContent = fmtClock(missionSecondsAt(state.model, state.cursor));
}

function setCursor(index, opts = {}) {
  const model = state.model;
  if (!model) return;
  const total = model.events.length;
  const next = clamp(index, 0, total);
  const prev = state.cursor;
  state.cursor = next;
  if (stage) {
    stage.setCursor(next, {
      animate: Boolean(opts.animate),
      live: state.mode === "live" && state.followLive,
    });
  }
  if (panels) panels.setCursor(next);
  if (timeline) timeline.setCursor(next);
  updateClock();

  if (opts.fromUser && state.mode === "live" && state.followLive) {
    state.followLive = false;
    stopPolling();
    setModeLabel();
    if (panels) panels.setLive(false);
    if (timeline) timeline.setMode({ live: false, playing: state.playing, speed: state.speed });
    toast("Replay buffered", "You are off the live head. Tap the LIVE badge to return.", "info");
  }

  if (next > prev && audio && state.sound) {
    const fresh = model.events.slice(prev, next).filter((e) => AUDIO_KINDS[e.category]);
    const last = fresh[fresh.length - 1];
    if (last) audio.play(AUDIO_KINDS[last.category]);
  }
}

function setPlaying(playing) {
  const next = Boolean(playing);
  if (next && !state.playing) state.acc = 0;
  state.playing = next;
  if (timeline) timeline.setMode({ live: state.mode === "live", playing: state.playing, speed: state.speed });
}

function togglePlay() {
  const model = state.model;
  if (!model || !model.events.length) return;
  if (state.playing) {
    setPlaying(false);
    return;
  }
  if (state.mode === "live") {
    state.mode = "replay";
    state.followLive = false;
    stopPolling();
    setModeLabel();
    if (panels) panels.setLive(false);
  }
  if (state.cursor >= model.events.length) setCursor(0, { animate: false });
  setPlaying(true);
  updateTimeline();
}

function updateTimeline() {
  if (!timeline) return;
  timeline.setMode({ live: state.mode === "live", playing: state.playing, speed: state.speed });
}

function setSpeed(speed) {
  const n = Number(speed);
  if (!SPEEDS[n]) return;
  state.speed = n;
  updateTimeline();
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = 0;
  }
}

function startPolling() {
  stopPolling();
  if (state.mode !== "live" || !state.current || state.current.kind === "demo") return;
  if (!state.model || state.model.meta.status !== "live") return;
  state.pollTimer = setInterval(pollLive, 1200);
}

async function pollLive() {
  if (pollBusy) return;
  if (state.mode !== "live" || !state.current || state.current.kind === "demo") return;
  const ref = state.current;
  pollBusy = true;
  try {
    const data = await session({
      root: ref.root,
      slug: ref.slug,
      session_id: ref.session_id,
      since: state.etag,
    });
    /* A stale poll belongs to a view that is gone: it must not touch the
     * error banner either. */
    if (!state.current || state.current.key !== ref.key) return;
    /* Clear only a banner this poll raised: a failed navigation or refresh
     * banner must survive a successful poll. */
    if (state.lastError && pollClearsError(state.errorSource)) hideError();
    if (!data || data.unchanged) return;
    const follow = state.followLive;
    const priorCursor = state.cursor;
    const priorEvents = state.model && Array.isArray(state.model.events) ? state.model.events : [];
    const priorLastSeq = priorEvents.length ? Number(priorEvents[priorEvents.length - 1].seq) : -1;
    state.etag = data.etag || null;
    state.model = data;
    if (stage) stage.setModel(data);
    if (panels) panels.setModel(data);
    if (timeline) timeline.setModel(data);
    const total = data.events.length;
    if (follow) setCursor(total, { animate: true });
    else setCursor(clamp(priorCursor, 0, total), { animate: false });
    updateHeader(data);
    if (achievements) achievements.evaluate(data, { live: true });
    /* Count by seq, not length: above MAX_EVENTS the window length is
     * constant while the contents shift, so a length delta reads 0. */
    let added = 0;
    for (const event of data.events) {
      if (Number(event && event.seq) > priorLastSeq) added += 1;
    }
    if (follow && added > 0) {
      const phase = data.phases && data.phases.current ? ` in ${data.phases.current}` : "";
      announce(`${added} new event${added === 1 ? "" : "s"}${phase}`);
    }
    if (data.meta.status !== "live") {
      state.mode = "replay";
      state.followLive = false;
      stopPolling();
      setModeLabel();
      if (panels) panels.setLive(false);
      updateTimeline();
      toast("Run ended", "The journal recorded the end of this run.", "success");
      announce("Run ended. The journal is complete.", { force: true });
    }
  } catch (err) {
    showError(`Live poll failed: ${err.message}`, pollLive, "poll");
  } finally {
    pollBusy = false;
  }
}

function updateHeader(model) {
  if (!model) return;
  const meta = model.meta || {};
  const run = meta.project || meta.team || "PACT run";
  document.title = `${run} · PACT Mission Control`;
  if (model.phases && model.phases.current) {
    els.stageCaption.textContent = `${model.phases.current} · ${meta.run_kind || "run"} · ${meta.status || ""}`;
  }
  setModeLabel();
}

function applyModel(model, opts = {}) {
  if (!model) return;
  state.model = model;
  state.etag = model.etag || null;
  if (opts.mode) state.mode = opts.mode;
  if (stage) stage.setModel(model);
  if (panels) panels.setModel(model);
  if (timeline) timeline.setModel(model);
  updateHeader(model);

  const total = model.events.length;
  let cursor = total;
  if (opts.cursor === "start") cursor = 0;
  else if (typeof opts.cursor === "number") cursor = clamp(opts.cursor, 0, total);
  if (state.mode === "demo") cursor = 0;
  if (state.followLive) cursor = total;
  state.cursor = cursor;
  if (stage) stage.setCursor(cursor, { animate: false, live: state.followLive });
  if (panels) panels.setCursor(cursor);
  if (timeline) timeline.setCursor(cursor);
  updateClock();
  setModeLabel();
  if (panels) panels.setLive(state.mode === "live" && state.followLive);
  updateTimeline();
  if (achievements) achievements.evaluate(model, { live: state.mode === "live" || state.mode === "demo" });
}

function syncSelectors(ref) {
  if (!ref) return;
  state.suppress = true;
  if (ref.kind === "demo") {
    if (![...els.root.options].some((o) => o.value === "__demo__")) {
      els.root.appendChild(el("option", { value: "__demo__", text: "training simulation" }));
    }
    els.root.value = "__demo__";
    clear(els.project).appendChild(el("option", { value: "__demo__", text: "PACT-Demo" }));
    els.project.value = "__demo__";
    clear(els.session).appendChild(el("option", { value: "__demo__", text: "TRAINING SIMULATION" }));
    els.session.value = "__demo__";
  } else {
    if ([...els.root.options].some((o) => o.value === ref.root)) els.root.value = ref.root;
    populateProjects(ref.root);
    els.project.value = ref.slug;
    populateSessions(ref.root, ref.slug);
    const key = sessionKey(ref);
    if ([...els.session.options].some((o) => o.value === key)) els.session.value = key;
  }
  state.suppress = false;
}

function sessionKey(s) {
  return `${s.root}::${s.slug}::${s.session_id}`;
}

function populateRoots() {
  clear(els.root);
  for (const root of state.roots) {
    els.root.appendChild(
      el("option", {
        value: root.id,
        text: `${root.label}${typeof root.journals === "number" ? ` (${root.journals})` : ""}`,
      })
    );
  }
  if (state.demoAvailable) {
    els.root.appendChild(el("option", { value: "__demo__", text: "training simulation" }));
  }
}

function projectsFor(rootId) {
  const set = new Set();
  for (const s of state.sessions) if (s.root === rootId) set.add(s.slug);
  return [...set].sort((a, b) => a.localeCompare(b));
}

function sessionsFor(rootId, slug) {
  return state.sessions
    .filter((s) => s.root === rootId && (!slug || s.slug === slug))
    .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
}

/* The server constrains a session's project label to [A-Za-z0-9._-] and sets
 * project_altered when it substituted characters. The select value stays the
 * slug (directory name), which is what every caller compares against. */
function projectInfoFor(rootId, slug) {
  const rows = state.sessions.filter((s) => s.root === rootId && s.slug === slug);
  const named = rows.find((s) => s && typeof s.project === "string" && s.project);
  return {
    label: named ? named.project : slug,
    altered: rows.some((s) => s && s.project_altered === true),
  };
}

function populateProjects(rootId) {
  const projects = projectsFor(rootId);
  clear(els.project);
  for (const p of projects) {
    const info = projectInfoFor(rootId, p);
    els.project.appendChild(el("option", {
      value: p,
      text: info.altered ? `${info.label} ⚠` : info.label,
      attrs: info.altered
        ? { title: PROJECT_ALTERED_NOTE, "aria-label": `${info.label}. ${PROJECT_ALTERED_NOTE}` }
        : { title: info.label },
    }));
  }
  if (projects.length) els.project.value = projects[0];
}

function populateSessions(rootId, slug) {
  const list = sessionsFor(rootId, slug);
  clear(els.session);
  for (const s of list) {
    const status = s.status || (s.is_live ? "live" : "ended");
    const bits = [fmtDateShort(s.updated_at), s.session_id.slice(0, 8), status, s.last_phase || s.run_kind || ""];
    els.session.appendChild(
      el("option", {
        value: sessionKey(s),
        text: bits.filter(Boolean).join(" · "),
      })
    );
  }
  if (list.length) els.session.value = sessionKey(list[0]);
}

function fmtDateShort(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

async function refreshList(preferredRoot) {
  const data = await sessions();
  state.roots = Array.isArray(data.roots) ? data.roots : [];
  state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
  if (!state.roots.length && state.sessions.length) {
    state.roots = [{ id: state.sessions[0].root, label: state.sessions[0].root, journals: 0, default: true }];
  }
  populateRoots();
  const wanted =
    preferredRoot ||
    (state.current && state.current.kind === "real" ? state.current.root : null) ||
    data.default_root ||
    (state.roots[0] && state.roots[0].id);
  if (wanted && [...els.root.options].some((o) => o.value === wanted)) els.root.value = wanted;
  return data;
}

function selectRoot(rootId, { open = true } = {}) {
  if (!rootId || rootId === "__demo__") return;
  const projects = projectsFor(rootId);
  populateProjects(rootId);
  const slug = projects[0] || "";
  populateSessions(rootId, slug);
  if (open) {
    const first = sessionsFor(rootId, slug)[0];
    const action = rootChangeAction({
      hasSession: Boolean(first),
      requestedFilter: state.requestedFilter,
      hasRun: Boolean(state.model),
    });
    if (action === "open") {
      openSession(first);
    } else if (action === "empty") {
      /* The displayed run belongs to the previous root; keeping it under a
       * NO SESSIONS caption would be the same lie in a different place. */
      clearRunView();
      showEmptyFilter(state.requestedFilter);
    } else if (action === "demo") {
      openDemo();
    }
  }
}

/* Drop the open run so the rails, recorder and caption all agree there is
 * nothing to show. Used when the selected root cannot show a run at all. */
function clearRunView() {
  /* Same invalidations the other view switches perform. A pending
   * openSession must not re-apply its journal over this empty state, and the
   * loading overlay it raised must not stay up: its finally block sees a
   * stale token and skips its own reset. */
  hideError();
  sessionRequest += 1;
  setLoading(false);
  stopPolling();
  state.current = null;
  state.model = null;
  state.etag = null;
  state.cursor = 0;
  state.playing = false;
  state.followLive = false;
  state.mode = "idle";
  state.selectedAgent = null;
  if (stage) stage.setModel(null);
  if (panels) {
    panels.setModel(null);
    panels.setSelectedAgent(null);
  }
  if (timeline) timeline.setModel(null);
  document.title = "PACT // Mission Control";
  updateUrl("", null);
  setModeLabel();
  updateClock();
  updateTimeline();
}

/* A launch-level filter that matches nothing must say so. Falling through to
 * the demo shows a different run as if it were the requested one. */
function showEmptyFilter(label) {
  const name = String(label || "").trim();
  if (!name) return;
  hideError();
  if (panels) {
    panels.setNotice(
      `No sessions for ${name}. Choose another project or session, or open the training simulation.`
    );
  }
  els.stageCaption.textContent = `NO SESSIONS FOR ${name.toUpperCase()}`;
  setModeLabel(); /* no run is open; the badge must not keep saying LIVE */
}

async function loadInitial() {
  setLoading(true, "reading local journals");
  try {
    const params = new URLSearchParams(window.location.search);
    /* `demo_available` is the capability; `demo` is the server's actual launch
     * mode. Only the capability decides whether the demo affordance exists. */
    try {
      const info = await health();
      state.demoAvailable = Boolean(info && info.demo_available !== false);
    } catch (_) {
      /* Keep the default: the demo is also the degraded-path fallback. */
    }
    els.demo.hidden = !state.demoAvailable;
    const data = await refreshList(params.get("root") || undefined);
    /* Only the server's launch filters narrowed the list, and the payload
     * echoes them; a query param here would name a filter that was never
     * applied to the response. */
    state.requestedFilter = requestedFilterLabel(data && data.filters);
    const rootId = els.root.value;
    if (params.has("demo") || (USE_MOCK && !params.has("session"))) {
      await openDemo();
    } else if (params.has("session")) {
      const ref = {
        kind: "real",
        root: params.get("root") || rootId || data.default_root || (state.roots[0] && state.roots[0].id),
        slug: params.get("slug") || "",
        session_id: params.get("session"),
        is_live: false,
      };
      await openSession(ref, { fromUrl: true });
    } else {
      const live = state.sessions.find((s) => s.is_live && s.root === rootId);
      const pick = live || sessionsFor(rootId, null)[0];
      if (pick) {
        populateProjects(rootId);
        els.project.value = pick.slug;
        populateSessions(rootId, pick.slug);
        await openSession(pick, { fromUrl: true });
      } else {
        selectRoot(rootId, { open: false });
        if (state.requestedFilter) showEmptyFilter(state.requestedFilter);
        else await openDemo();
      }
    }
  } catch (err) {
    setLoading(false);
    showError(`Could not read PACT runs: ${err.message}`, loadInitial, "load");
    /* The demo needs the same server. Only try it when health answers;
     * otherwise openDemo's failure would replace this error and its retry
     * with a dead end. */
    try {
      await health();
      try {
        await openDemo();
      } catch (_) {
        showError(`Could not read PACT runs: ${err.message}`, loadInitial, "load");
      }
    } catch (_) {
      /* API unreachable: keep the list error and its retry. */
    }
  } finally {
    setLoading(false);
  }
}

async function openDemo() {
  closeDrawers();
  hideError();
  sessionRequest += 1; /* supersede any in-flight openSession */
  setLoading(true, "loading training simulation");
  try {
    const model = await demo();
    state.current = {
      kind: "demo",
      key: "__demo__",
      root: "__demo__",
      slug: model.source && model.source.slug ? model.source.slug : "training-simulation",
      session_id: model.source && model.source.session_id ? model.source.session_id : "demo",
      is_live: false,
      status: "ended",
    };
    state.mode = "demo";
    state.followLive = false;
    stopPolling();
    state.playing = false;
    state.speed = 2;
    applyModel(model, { mode: "demo", cursor: "start" });
    syncSelectors(state.current);
    state.playing = true;
    setPlaying(true);
    updateTimeline();
    updateUrl("demo");
  } catch (err) {
    showError(`Demo failed to load: ${err.message}`, openDemo, "navigation");
  } finally {
    setLoading(false);
  }
}

async function openSession(ref, opts = {}) {
  if (!ref || !ref.root || !ref.slug || !ref.session_id) return;
  closeDrawers();
  if (!opts.since) stopPolling();
  hideError();
  setLoading(true, "decoding journal");
  const token = ++sessionRequest;
  try {
    const model = await session({
      root: ref.root,
      slug: ref.slug,
      session_id: ref.session_id,
      since: opts.since || undefined,
    });
    /* A later selection (or the demo) owns the screen now. */
    if (token !== sessionRequest) return;
    if (model && model.unchanged) {
      setLoading(false);
      return;
    }
    const live = Boolean(ref.is_live || (model.meta && model.meta.status === "live"));
    state.current = {
      kind: "real",
      key: sessionKey(ref),
      root: ref.root,
      slug: ref.slug,
      session_id: ref.session_id,
      is_live: live,
      status: model.meta ? model.meta.status : ref.status,
    };
    state.mode = live ? "live" : "replay";
    state.followLive = live;
    state.playing = false;
    applyModel(model, { mode: state.mode, cursor: "end" });
    syncSelectors(state.current);
    updateUrl("session", ref);
    if (live) startPolling();
    else stopPolling();
  } catch (err) {
    if (token === sessionRequest) {
      showError(`Could not open run: ${err.message}`, () => openSession(ref), "navigation");
      /* The previous run is still on screen, and this load stopped its
       * polling. If that run is live, resume it so the LIVE badge stays true;
       * startPolling() is a no-op for a demo, replay or ended run. */
      startPolling();
      /* The selectors must show what is on screen, not the failed choice. */
      if (state.current) syncSelectors(state.current);
    }
  } finally {
    if (token === sessionRequest) setLoading(false);
  }
}

function updateUrl(kind, ref) {
  const p = new URLSearchParams();
  if (USE_MOCK) p.set("mock", "1");
  if (kind === "demo") p.set("demo", "1");
  else if (ref) {
    p.set("root", ref.root);
    p.set("slug", ref.slug);
    p.set("session", ref.session_id);
  }
  const next = p.toString();
  try {
    history.replaceState(null, "", next ? `?${next}` : window.location.pathname);
  } catch (_) {
    /* history may be unavailable under some file:// setups */
  }
}

function selectAgent(name) {
  state.selectedAgent = state.selectedAgent === name ? null : name;
  if (stage) stage.setSelectedAgent(state.selectedAgent);
  if (panels) panels.setSelectedAgent(state.selectedAgent);
}

function returnToLive() {
  const model = state.model;
  if (!model) return;
  if (state.mode === "demo") {
    toast("Training simulation", "This is a recorded demo. Use the recorder to replay it.", "info");
    return;
  }
  if (!state.current || !state.current.is_live || model.meta.status !== "live") {
    toast("No live head", "This run has ended. Use the flight recorder to replay it.", "info");
    return;
  }
  state.mode = "live";
  state.followLive = true;
  setPlaying(false);
  setCursor(model.events.length, { animate: false });
  setModeLabel();
  if (panels) panels.setLive(true);
  updateTimeline();
  startPolling();
  toast("Back on the live head", "Following the journal again.", "success");
}

async function refreshAll() {
  hideError();
  try {
    const keep = state.current && state.current.kind === "real" ? state.current : null;
    await refreshList(keep ? keep.root : els.root.value);
    if (keep) {
      const still = state.sessions.find((s) => sessionKey(s) === keep.key);
      if (still) await openSession(still);
      else selectRoot(keep.root);
    } else if (state.current && state.current.kind === "demo") {
      await openDemo();
    } else {
      selectRoot(els.root.value);
    }
  } catch (err) {
    showError(`Refresh failed: ${err.message}`, refreshAll, "refresh");
  }
}

function randomRun() {
  if (!state.sessions.length) {
    openDemo();
    return;
  }
  const pool = state.sessions.filter((s) => !state.current || sessionKey(s) !== state.current.key);
  const pick = pool[Math.floor(Math.random() * pool.length)] || state.sessions[0];
  openSession(pick);
}

function bindHeader() {
  els.refresh.addEventListener("click", () => refreshAll());
  els.random.addEventListener("click", randomRun);
  els.demo.addEventListener("click", () => openDemo());
  els.live.addEventListener("click", returnToLive);
  els.crewToggle.addEventListener("click", () => toggleDrawer("show-crew", els.crewToggle));
  els.commsToggle.addEventListener("click", () => toggleDrawer("show-comms", els.commsToggle));
  els.sound.addEventListener("click", toggleSound);
  els.fullscreen.addEventListener("click", toggleFullscreen);
  els.help.addEventListener("click", () => openDebrief("help"));
  els.about.addEventListener("click", () => openDebrief("about"));

  els.root.addEventListener("change", async () => {
    if (state.suppress) return;
    if (els.root.value === "__demo__") {
      openDemo();
      return;
    }
    try {
      await refreshList(els.root.value);
      selectRoot(els.root.value);
    } catch (err) {
      showError(`Could not switch root: ${err.message}`, () => refreshAll(), "refresh");
    }
  });
  els.project.addEventListener("change", async () => {
    if (state.suppress) return;
    if (els.project.value === "__demo__") {
      openDemo();
      return;
    }
    const root = els.root.value;
    const slug = els.project.value;
    populateSessions(root, slug);
    const first = sessionsFor(root, slug)[0];
    if (first) openSession(first);
  });
  els.session.addEventListener("change", () => {
    if (state.suppress) return;
    if (els.session.value === "__demo__") {
      openDemo();
      return;
    }
    const found = state.sessions.find((s) => sessionKey(s) === els.session.value);
    if (found) openSession(found);
  });

  els.errorDemo.addEventListener("click", () => {
    hideError();
    openDemo();
  });

  /* Drawers are overlays below 1180px; a click anywhere else dismisses them. */
  document.addEventListener("pointerdown", (event) => {
    if (!document.body.classList.contains("show-crew") && !document.body.classList.contains("show-comms")) return;
    const target = event.target;
    if (!target || typeof target.closest !== "function") return;
    if (target.closest("#left-rail, #right-rail, #btn-crew, #btn-comms")) return;
    closeDrawers();
  });
}

let headerHeight = 0;

/* The topbar wraps below 980px, so its real height is content-dependent and
 * can exceed the static --header-h token. The overlay drawers must start
 * below the header, not below the token, or they cover the wrapped controls. */
function measureHeaderHeight() {
  const header = document.getElementById("topbar");
  if (!header) return;
  const height = Math.round(header.getBoundingClientRect().height || 0);
  if (height <= 0 || height === headerHeight) return;
  headerHeight = height;
  document.documentElement.style.setProperty("--header-real-h", `${height}px`);
}

function trackHeaderHeight() {
  measureHeaderHeight();
  const header = document.getElementById("topbar");
  if (!header) return;
  if (typeof ResizeObserver === "function") {
    new ResizeObserver(measureHeaderHeight).observe(header);
  } else {
    window.addEventListener("resize", measureHeaderHeight);
  }
}

function toggleDrawer(cls, button) {
  measureHeaderHeight(); /* content changes can re-wrap the header */
  const open = !document.body.classList.contains(cls);
  document.body.classList.toggle(cls, open);
  if (button) button.setAttribute("aria-pressed", open ? "true" : "false");
}

function closeDrawers() {
  for (const [cls, button] of [["show-crew", els.crewToggle], ["show-comms", els.commsToggle]]) {
    document.body.classList.remove(cls);
    if (button) button.setAttribute("aria-pressed", "false");
  }
}

function openDebrief(tab) {
  if (panels) panels.openDebrief(tab);
}

function toggleSound() {
  state.sound = !state.sound;
  localStorage.setItem("pact-viz:sound", state.sound ? "1" : "0");
  els.sound.setAttribute("aria-pressed", state.sound ? "true" : "false");
  if (audio) audio.setEnabled(state.sound);
  toast("Sound", state.sound ? "8-bit comms enabled." : "Sound muted.", "info");
}

function toggleFullscreen() {
  try {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen();
    else document.exitFullscreen();
  } catch (_) {
    toast("Fullscreen unavailable", "Your browser refused the fullscreen request.", "warning");
  }
}

function bindKeyboard() {
  window.addEventListener("keydown", (event) => {
    const target = event.target;
    const tag = (target && target.tagName) || "";
    /* Escape is a global dismissal, so it runs before the form-control guard:
     * a focused drawer toggle is a BUTTON, and the drawer must still close. */
    if (event.key === "Escape") {
      if (panels) panels.closeModal();
      closeDrawers();
      if (state.discon) toggleDisco(false);
      return;
    }
    if (["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(tag) || (target && target.isContentEditable)) {
      return;
    }
    if (event.key === " " || event.code === "Space") {
      event.preventDefault();
      togglePlay();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      setCursor(state.cursor - (event.shiftKey ? 20 : 1), { animate: false, fromUser: true });
      setPlaying(false);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setCursor(state.cursor + (event.shiftKey ? 20 : 1), { animate: false, fromUser: true });
      setPlaying(false);
    } else if (event.key === "l" || event.key === "L") {
      returnToLive();
    } else if (event.key === "d" || event.key === "D") {
      openDemo();
    } else if (event.key === "g" || event.key === "G") {
      openDebrief("gates");
    } else if (event.key === "t" || event.key === "T") {
      openDebrief("tasks");
    } else if (event.key === "?" || event.key === "/") {
      event.preventDefault();
      openDebrief("help");
    }
  });

  // Konami code: a small, local-only easter egg.
  const KONAMI = ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight", "b", "a"];
  let index = 0;
  state.discon = false;
  window.addEventListener("keydown", (event) => {
    const target = event.target;
    const tag = (target && target.tagName) || "";
    if (["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(tag) || (target && target.isContentEditable)) return;
    if (event.key === KONAMI[index]) {
      index += 1;
      if (index === KONAMI.length) {
        index = 0;
        toggleDisco(!state.discon);
      }
    } else {
      index = event.key === KONAMI[0] ? 1 : 0;
    }
  });
}

function toggleDisco(on) {
  state.discon = Boolean(on);
  document.body.classList.toggle("disco", state.discon);
  if (state.discon) toast("DISCO PROTOCOL ENGAGED", "The floor is yours. Press the code again to stop.", "success");
}

function frame(now) {
  const dt = state.lastFrame ? Math.min(0.5, (now - state.lastFrame) / 1000) : 0;
  state.lastFrame = now;
  // Idle gating: when nothing is playing there is no loop work — the clock
  // only changes via setCursor/applyModel, which already call updateClock.
  if (state.playing && state.model) {
    state.acc += dt * SPEEDS[state.speed];
    const step = Math.floor(state.acc);
    if (step > 0) {
      state.acc -= step;
      const total = state.model.events.length;
      const next = Math.min(total, state.cursor + step);
      setCursor(next, { animate: true });
      if (next >= total) {
        setPlaying(false);
        if (state.mode !== "demo") setModeLabel();
      }
    }
    if (now - state.lastClock > 250) {
      state.lastClock = now;
      updateClock();
    }
  }
  state.raf = requestAnimationFrame(frame);
}

function initModules() {
  stage = new Stage(els.stageCanvas, {
    onAgentClick: (name) => selectAgent(name),
    onRoomClick: (family) => {
      const model = state.model;
      if (!model) return;
      const count = model.events.filter((e) => e.category === "phase" && e.phase === family).length;
      toast(`${family} room`, `${count} phase events recorded here. Click a crew member or an event for detail.`, "info");
    },
    onMascotClick: () => {
      const tip = TIPS[Math.floor(Math.random() * TIPS.length)];
      toast("Sir Pactsalot says", tip, "info");
    },
  });
  /* The stage reads prefers-reduced-motion itself and keeps listening for
   * changes; calling setReducedMotion here marked it "explicit" and killed
   * that listener for the rest of the session. */

  panels = new Panels({
    left: els.left,
    right: els.right,
    modal: els.modal,
    toast: els.toast,
    onSelectAgent: (name) => selectAgent(name),
    onSeek: (index) => {
      setCursor(index, { animate: false, fromUser: true });
      setPlaying(false);
    },
    onFilter: (category) => {
      state.filter = category;
    },
    onClose: () => {},
  });

  timeline = createTimeline(els.recorder, {
    onSeek: (index) => {
      setCursor(index, { animate: false, fromUser: true });
      setPlaying(false);
    },
    onPlayPause: () => togglePlay(),
    onSpeed: (speed) => setSpeed(speed),
    onToggleLive: () => returnToLive(),
  });

  audio = createAudio();
  state.sound = localStorage.getItem("pact-viz:sound") === "1";
  audio.setEnabled(state.sound);
  els.sound.setAttribute("aria-pressed", state.sound ? "true" : "false");

  achievements = createAchievements("pact-viz", (unlocked) => {
    const list = Array.isArray(unlocked) ? unlocked : [unlocked];
    for (const item of list) {
      if (!item) continue;
      toast(`ACHIEVEMENT · ${item.title || item.name || "Unlocked"}`, item.body || item.description || "", "success");
    }
    if (audio && state.sound) audio.play("achievement");
  });

  const resize = () => {
    if (stage) stage.resize();
  };
  window.addEventListener("resize", resize);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(resize).catch(() => {});
}

async function boot() {
  trackHeaderHeight();
  initModules();
  bindHeader();
  bindKeyboard();
  setModeLabel(); /* the static HTML badge starts as LIVE; the app starts idle */
  updateTimeline();
  state.raf = requestAnimationFrame(frame);
  try {
    await loadInitial();
  } catch (err) {
    setLoading(false);
    showError(String(err && err.message ? err.message : err), loadInitial, "load");
  }
  setLoading(false);
}

boot();
