/* PACT Mission Control — app shell and orchestration.
 * Reads local journals via same-origin API only. No external requests. */

import {
  sessions,
  session,
  demo,
  health,
  USE_MOCK,
  takeLaunchKey,
  setKeyRejectedHandler,
  KEY_REQUIRED_MESSAGE,
} from "./api.js";
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
  pollMayShowError,
  PROJECT_ALTERED_NOTE,
  PROJECT_COLLISION_NOTE,
  runKey,
  cursorAfterRefresh,
  launchSelection,
} from "./dom.js";
import { Stage } from "./stage.js";
import { Panels } from "./panels.js";
import { createTimeline } from "./timeline.js";
import { createAudio } from "./audio.js";
import { createAchievements } from "./achievements.js";

const $ = (id) => document.getElementById(id);

/* Storage access can throw (blocked site data, some private modes). A missing
 * preference falls back to its default; it must never stop the page. */
function readPreference(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

function writePreference(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (_) {
    /* the preference lasts for this page only */
  }
}


const els = {
  root: $("root-select"),
  project: $("project-select"),
  session: $("session-select"),
  refresh: $("btn-refresh"),
  listNote: $("list-note"),
  showAll: $("btn-show-all"),
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
  /* The selection the URL asked for ({project, session}); every list refresh
   * sends it so the server narrows the list the same way each time. */
  listQuery: {},
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
/* Monotonic token: every view switch (openSession, openDemo, clearRunView)
 * takes a new value, and each async switch checks it after its await, so a
 * slow response never overwrites a later selection. */
let sessionRequest = 0;
/* Bumped whenever polling stops or a run is (re)opened: a poll that was in
 * flight at that moment belongs to an older view and must not apply. */
let pollGeneration = 0;

function setLoading(on, sub) {
  els.loading.hidden = !on;
  if (sub) els.loadingSub.textContent = sub;
}

/* `source` records which operation raised the banner: only a poll may clear a
 * poll banner, so a failed navigation or refresh survives the next poll. */
function showError(message, retry, source = "") {
  /* The launch-link banner outranks every other: until the page has its key,
   * nothing else can succeed. */
  if (state.errorSource === "key" && source !== "key") return;
  state.lastError = message || "Something went wrong";
  state.errorSource = source;
  els.errorText.textContent = state.lastError;
  els.error.hidden = false;
  els.errorRetry.hidden = source === "key";
  els.errorDemo.hidden = source === "key" || !state.demoAvailable;
  els.errorRetry.onclick = () => {
    hideError();
    if (typeof retry === "function") retry();
    else refreshAll();
  };
}

function hideError() {
  if (state.errorSource === "key") return;
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
function announce(message) {
  if (!els.announcer || !message) return;
  const now = Date.now();
  if (now - lastAnnouncement < ANNOUNCE_MIN_MS) return;
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
      live: following(),
    });
  }
  if (panels) panels.setCursor(next);
  if (timeline) timeline.setCursor(next);
  updateClock();

  if (opts.fromUser && following()) {
    state.followLive = false;
    setModeLabel();
    if (panels) panels.setLive(false);
    if (timeline) timeline.setMode({ live: false, playing: state.playing, speed: state.speed });
    toast("Live follow paused", "You moved off the live head. Press L or the LIVE badge to follow it again.", "info");
  }

  if (next > prev && audio && state.sound) {
    const fresh = model.events.slice(prev, next).filter((e) => AUDIO_KINDS[e.category]);
    const last = fresh[fresh.length - 1];
    if (last) audio.play(AUDIO_KINDS[last.category]);
  }
}

/* The page follows the live head only while the run is open in live mode AND
 * the user has not scrubbed away from it. Every LIVE flag the page shows
 * (badge, recorder button, rails, stage) reads this one predicate. */
function following() {
  return state.mode === "live" && state.followLive;
}

function setPlaying(playing) {
  const next = Boolean(playing);
  if (next && !state.playing) state.acc = 0;
  state.playing = next;
  if (timeline) timeline.setMode({ live: following(), playing: state.playing, speed: state.speed });
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
    setModeLabel();
    if (panels) panels.setLive(false);
  }
  if (state.cursor >= model.events.length) setCursor(0, { animate: false });
  setPlaying(true);
  updateTimeline();
}

function updateTimeline() {
  if (!timeline) return;
  timeline.setMode({ live: following(), playing: state.playing, speed: state.speed });
}

function setSpeed(speed) {
  const n = Number(speed);
  if (!SPEEDS[n]) return;
  state.speed = n;
  updateTimeline();
}

/* An open real run is always polled: about every 1.2 s while it is live and
 * every 10 s otherwise, so a run that resumes or goes idle updates on its own.
 * The server answers an unchanged journal cheaply. */
const POLL_LIVE_MS = 1200;
const POLL_IDLE_MS = 10000;

function stopPolling() {
  pollGeneration += 1;
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = 0;
  }
}

function startPolling() {
  stopPolling();
  schedulePoll();
}

function schedulePoll() {
  if (!state.current || state.current.kind !== "real" || !state.model) return;
  const generation = pollGeneration;
  const live = Boolean(state.model.meta && state.model.meta.status === "live");
  state.pollTimer = setTimeout(async () => {
    state.pollTimer = 0;
    await pollLive();
    /* A stop or a new run since this timer was set owns the schedule now. */
    if (generation === pollGeneration) schedulePoll();
  }, live ? POLL_LIVE_MS : POLL_IDLE_MS);
}

async function pollLive() {
  if (pollBusy) return;
  if (!state.current || state.current.kind !== "real") return;
  const ref = state.current;
  const generation = pollGeneration;
  /* A stale poll belongs to a view that is gone (another run, or the same run
   * reopened since this request left): it must not touch the error banner
   * either. */
  const stale = () => generation !== pollGeneration || !state.current || state.current.key !== ref.key;
  pollBusy = true;
  try {
    const data = await session({
      root: ref.root,
      slug: ref.slug,
      session_id: ref.session_id,
      since: state.etag,
    });
    if (stale()) return;
    /* Clear only a banner this poll raised: a failed navigation or refresh
     * banner must survive a successful poll. */
    if (state.lastError && pollClearsError(state.errorSource)) hideError();
    if (!data || data.unchanged) return;
    const follow = following();
    const priorCursor = state.cursor;
    const priorEvents = state.model && Array.isArray(state.model.events) ? state.model.events : [];
    const priorLastSeq = priorEvents.length ? Number(priorEvents[priorEvents.length - 1].seq) : -1;
    const wasLive = Boolean(state.model && state.model.meta && state.model.meta.status === "live");
    const atHead = priorCursor >= priorEvents.length;
    state.etag = data.etag || null;
    state.model = data;
    if (stage) stage.setModel(data);
    if (panels) panels.setModel(data);
    if (timeline) timeline.setModel(data);
    const total = data.events.length;
    if (follow) setCursor(total, { animate: true });
    else setCursor(cursorAfterRefresh(priorEvents, priorCursor, data.events), { animate: false });
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
    const nowLive = Boolean(data.meta && data.meta.status === "live");
    state.current.is_live = nowLive;
    if (nowLive !== wasLive) {
      /* A run that resumes is followed again only if the reader was at its
       * head; one that ends drops to replay. The toast root is a polite live
       * region, so each toast is also the announcement. */
      state.mode = nowLive ? "live" : "replay";
      state.followLive = nowLive && atHead && !state.playing;
      if (state.followLive) setCursor(total, { animate: false });
      setModeLabel();
      if (panels) panels.setLive(following());
      updateTimeline();
      if (nowLive) toast("Run resumed", "The journal shows new activity on this run.", "info");
      else toast("Run ended", "The journal shows this run has ended.", "success");
    }
  } catch (err) {
    /* Mirror the success arm: surface a failure only while this poll still
     * belongs to the displayed view. */
    if (stale()) return;
    /* A poll failure is silent while a navigation or refresh banner is
     * showing: that banner owns the retry, and this poll keeps retrying on its
     * own timer. */
    if (pollMayShowError(state.errorSource)) {
      showError(`Live poll failed: ${err.message}`, pollLive, "poll");
    }
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
  /* A selection names an agent of the run it was made in; a different run
   * starts with none, in every view at once. */
  if (runKey(model) !== runKey(state.model)) {
    state.selectedAgent = null;
    if (stage) stage.setSelectedAgent(null);
    if (panels) panels.setSelectedAgent(null);
  }
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
  if (stage) stage.setCursor(cursor, { animate: false, live: following() });
  if (panels) panels.setCursor(cursor);
  if (timeline) timeline.setCursor(cursor);
  updateClock();
  setModeLabel();
  if (panels) panels.setLive(following());
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
  /* project_display is the original directory name (control characters
   * stripped); the constrained project label is the fallback. */
  const shown = rows.find((s) => s && typeof s.project_display === "string" && s.project_display);
  const named = rows.find((s) => s && typeof s.project === "string" && s.project);
  return {
    label: shown ? shown.project_display : named ? named.project : slug,
    altered: rows.some((s) => s && s.project_altered === true),
    collision: rows.some((s) => s && s.project_collision === true),
  };
}

function populateProjects(rootId) {
  const projects = projectsFor(rootId);
  clear(els.project);
  for (const p of projects) {
    const info = projectInfoFor(rootId, p);
    const notes = [
      info.altered ? PROJECT_ALTERED_NOTE : "",
      info.collision ? PROJECT_COLLISION_NOTE : "",
    ].filter(Boolean);
    els.project.appendChild(el("option", {
      value: p,
      text: notes.length ? `${info.label} ⚠` : info.label,
      attrs: notes.length
        ? { title: notes.join(" "), "aria-label": `${info.label}. ${notes.join(" ")}` }
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

/* Add sessions from a narrower fetch without duplicating any already listed. */
function mergeSessions(list) {
  if (!Array.isArray(list)) return;
  const known = new Set(state.sessions.map(sessionKey));
  for (const entry of list) {
    if (entry && !known.has(sessionKey(entry))) {
      state.sessions.push(entry);
      known.add(sessionKey(entry));
    }
  }
}

/* "showing N of total" and the hidden-trivial toggle, from the list response. */
function updateListNote(data) {
  if (!els.listNote || !els.showAll) return;
  const shown = Array.isArray(data && data.sessions) ? data.sessions.length : 0;
  const total = Number(data && data.total);
  const hidden = Number(data && data.hidden_trivial) || 0;
  const capped = Number.isFinite(total) && total > shown;
  els.listNote.hidden = !capped;
  els.listNote.textContent = capped ? `showing ${shown} of ${total}` : "";
  const all = Boolean(state.listQuery.all);
  els.showAll.hidden = !all && hidden <= 0;
  els.showAll.textContent = all ? "HIDE TRIVIAL" : `SHOW ${hidden} HIDDEN`;
  els.showAll.setAttribute("aria-pressed", all ? "true" : "false");
  els.showAll.title = all
    ? "Hide short headless runs again"
    : `${hidden} short headless run${hidden === 1 ? " is" : "s are"} hidden; show them`;
}

async function refreshList(preferredRoot) {
  const data = await sessions(state.listQuery);
  state.roots = Array.isArray(data.roots) ? data.roots : [];
  state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
  updateListNote(data);
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
      const root = state.roots.find((entry) => entry.id === rootId);
      showEmptyFilter(state.requestedFilter || `root ${(root && root.label) || rootId}`);
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
    const pick = launchSelection(window.location.search);
    /* `demo_available` is the capability: only it decides whether the demo
     * affordance exists. */
    try {
      const info = await health();
      state.demoAvailable = Boolean(info && info.demo_available !== false);
    } catch (_) {
      /* Keep the default: the demo is also the degraded-path fallback. */
    }
    els.demo.hidden = !state.demoAvailable;
    state.listQuery = pick.query || {};
    const data = await refreshList(pick.root || undefined);
    state.requestedFilter = requestedFilterLabel(data && data.filters);
    const rootId = els.root.value;
    if (pick.kind === "demo" || (USE_MOCK && pick.kind === "default")) {
      await openDemo();
    } else if (pick.kind === "deep") {
      await openSession({ kind: "real", root: pick.root, slug: pick.slug, session_id: pick.session_id, is_live: false });
    } else if (pick.kind === "session") {
      const matches = state.sessions.filter((s) => s.session_id === pick.query.session || s.session_id.startsWith(pick.query.session));
      if (matches.length === 1) {
        await openSession(matches[0]);
      } else {
        selectRoot(rootId, { open: false });
        showError(
          matches.length
            ? `Session ${pick.query.session} matches ${matches.length} runs; use a longer id.`
            : `No run matches session ${pick.query.session}.`,
          null,
          "navigation",
        );
      }
    } else {
      /* project or default: the list is already narrowed by the server */
      const scope = pick.kind === "project" ? state.sessions : state.sessions.filter((s) => s.root === rootId);
      const newest = (list) => list.slice().sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))[0];
      const choice = newest(scope.filter((s) => s.is_live)) || newest(scope);
      if (choice) {
        await openSession(choice);
      } else {
        selectRoot(rootId, { open: false });
        if (state.requestedFilter) showEmptyFilter(state.requestedFilter);
        else await openDemo();
      }
    }
  } catch (err) {
    setLoading(false);
    if (err && err.status === 401) return; /* the launch-link banner is up */
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
  const token = ++sessionRequest; /* supersede any in-flight load */
  setLoading(true, "loading training simulation");
  try {
    const model = await demo();
    /* A later selection (or a cleared view) owns the screen now. */
    if (token !== sessionRequest) return;
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
    if (token === sessionRequest) showError(`Demo failed to load: ${err.message}`, openDemo, "navigation");
  } finally {
    if (token === sessionRequest) setLoading(false);
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
    startPolling();
  } catch (err) {
    if (token === sessionRequest) {
      showError(`Could not open run: ${err.message}`, () => openSession(ref), "navigation");
      /* The previous run is still on screen, and this load stopped its
       * polling: resume it. startPolling() is a no-op for the demo. */
      startPolling();
      /* Resync only when a run is still displayed; with no run open there is
       * nothing to point the selectors at (syncSelectors no-ops on null). */
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
    p.set("session_id", ref.session_id);
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

/* A list refresh re-populates the selectors. When a view switch happened
 * while it was in flight, the refresh must not act on the intent it captured
 * before its await; it only points the selectors back at what is shown. */
function superseded(token) {
  if (token === sessionRequest) return false;
  if (state.current) syncSelectors(state.current);
  return true;
}

async function refreshAll() {
  hideError();
  const token = sessionRequest;
  try {
    const keep = state.current && state.current.kind === "real" ? state.current : null;
    await refreshList(keep ? keep.root : els.root.value);
    if (superseded(token)) return;
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
    if (!superseded(token)) showError(`Refresh failed: ${err.message}`, refreshAll, "refresh");
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
    const token = sessionRequest;
    try {
      const rootId = els.root.value;
      await refreshList(rootId);
      if (superseded(token)) return;
      selectRoot(rootId);
    } catch (err) {
      if (!superseded(token)) showError(`Could not switch root: ${err.message}`, () => refreshAll(), "refresh");
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
    /* The list is capped across all projects; fetch this project's own list
     * so its runs past the cap are reachable too. */
    try {
      const data = await sessions(Object.assign({}, state.listQuery, { project: slug }));
      mergeSessions(data && data.sessions);
      if (els.project.value === slug && !state.suppress) {
        state.suppress = true;
        const keep = els.session.value;
        populateSessions(root, slug);
        if ([...els.session.options].some((o) => o.value === keep)) els.session.value = keep;
        state.suppress = false;
      }
    } catch (_) {
      /* the capped list still works */
    }
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

  els.showAll.addEventListener("click", async () => {
    const all = !state.listQuery.all;
    state.listQuery = Object.assign({}, state.listQuery);
    if (all) state.listQuery.all = true;
    else delete state.listQuery.all;
    try {
      await refreshList(els.root.value);
      if (state.current && state.current.kind === "real") syncSelectors(state.current);
      else selectRoot(els.root.value, { open: false });
    } catch (err) {
      showError(`Could not refresh the run list: ${err.message}`, () => refreshAll(), "refresh");
    }
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

const DRAWERS = [
  { cls: "show-crew", button: () => els.crewToggle, rail: () => els.left },
  { cls: "show-comms", button: () => els.commsToggle, rail: () => els.right },
];

function toggleDrawer(cls, button) {
  measureHeaderHeight(); /* content changes can re-wrap the header */
  const open = !document.body.classList.contains(cls);
  document.body.classList.toggle(cls, open);
  if (button) button.setAttribute("aria-expanded", open ? "true" : "false");
  const drawer = DRAWERS.find((entry) => entry.cls === cls);
  const rail = drawer && drawer.rail();
  /* The drawer sits later in the DOM than its toggle; move focus into it so
   * Tab continues inside the panel the user just opened. */
  if (open && rail && typeof rail.focus === "function") {
    try { rail.focus({ preventScroll: true }); } catch (_) { /* focus is best-effort */ }
  }
}

function closeDrawers() {
  const active = document.activeElement;
  for (const drawer of DRAWERS) {
    const button = drawer.button();
    const rail = drawer.rail();
    const hadFocus = Boolean(rail && active && typeof rail.contains === "function" && rail.contains(active));
    document.body.classList.remove(drawer.cls);
    if (button) button.setAttribute("aria-expanded", "false");
    /* A closed overlay drawer is display:none, which would drop its focus to
     * <body>. Hand focus back to the toggle that opened it. Above the drawer
     * breakpoint the rail stays visible and keeps its focus. */
    if (hadFocus && button && rail.offsetParent === null && typeof button.focus === "function") {
      try { button.focus(); } catch (_) { /* focus is best-effort */ }
    }
  }
}

function openDebrief(tab) {
  if (panels) panels.openDebrief(tab);
}

function toggleSound() {
  state.sound = !state.sound;
  writePreference("pact-viz:sound", state.sound ? "1" : "0");
  els.sound.setAttribute("aria-pressed", state.sound ? "true" : "false");
  if (audio) audio.setEnabled(state.sound);
  toast("Sound", state.sound ? "8-bit comms enabled." : "Sound muted.", "info");
}

function toggleFullscreen() {
  const refused = () => toast("Fullscreen unavailable", "Your browser refused the fullscreen request.", "warning");
  try {
    /* Both calls return a promise; a refusal arrives as a rejection, not a throw. */
    const request = !document.fullscreenElement
      ? document.documentElement.requestFullscreen()
      : document.exitFullscreen();
    Promise.resolve(request).catch(refused);
  } catch (_) {
    refused();
  }
}

function bindKeyboard() {
  window.addEventListener("keydown", (event) => {
    const target = event.target;
    const tag = (target && target.tagName) || "";
    /* A widget that handled the key (a row activated by Space or Enter, a
     * toast dismissed by Escape) has already acted on it. */
    if (event.defaultPrevented) return;
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
    /* Browser and OS chords keep their own meaning, and while the debrief
     * dialog is open the page behind it must not react. */
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (els.modal && !els.modal.hidden) return;
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
    if (event.ctrlKey || event.metaKey || event.altKey) return;
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
  state.sound = readPreference("pact-viz:sound") === "1";
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

/* The page without its launch key makes no /api call: it says how to get
 * the link instead. A 401 later (a restarted server) shows the same banner. */
function showKeyBanner() {
  setLoading(false);
  stopPolling();
  showError(KEY_REQUIRED_MESSAGE, null, "key");
}

async function boot() {
  trackHeaderHeight();
  initModules();
  bindHeader();
  bindKeyboard();
  setModeLabel(); /* the static HTML badge starts as LIVE; the app starts idle */
  updateTimeline();
  state.raf = requestAnimationFrame(frame);
  if (!USE_MOCK) {
    setKeyRejectedHandler(showKeyBanner);
    if (!takeLaunchKey()) {
      showKeyBanner();
      return;
    }
  }
  try {
    await loadInitial();
  } catch (err) {
    setLoading(false);
    showError(String(err && err.message ? err.message : err), loadInitial, "load");
  }
  setLoading(false);
}

boot();
