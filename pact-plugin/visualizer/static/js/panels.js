/* PACT Mission Control — left rail, comms feed and debrief modal.
 * Panels is a pure view of the model: every action is reported through the
 * callbacks it was constructed with, and all journal text is rendered as text
 * nodes (never markup). No header elements are queried from this module. */

import {
  el,
  clear,
  fmtTime,
  fmtDate,
  fmtDuration,
  shortSha,
  clamp,
  toggleClass,
  createSeqIndex,
  runKey,
} from "./dom.js";
import { drawRadar, drawSparkline } from "./charts.js";

const FEED_LIMIT = 240;
const RAW_EVENT_LIMIT = 300;
const MAX_TOASTS = 2;
const TOAST_KINDS = new Set(["info", "success", "warning", "danger", "achievement"]);
const TOAST_MS = { info: 4500, success: 4500, warning: 5500, danger: 7000, achievement: 7000 };

const VARIETY_DIMS = ["novelty", "scope", "uncertainty", "risk"];

const FILTERS = [
  { id: "all", label: "ALL", cats: null },
  { id: "dispatch", label: "DISPATCH", cats: ["dispatch"] },
  { id: "handoff", label: "HANDOFF", cats: ["handoff"] },
  { id: "commit", label: "COMMIT", cats: ["commit"] },
  { id: "review", label: "REVIEW", cats: ["review", "pr"] },
  { id: "bugs", label: "BUGS", cats: ["finding", "remediation"] },
  { id: "gates", label: "GATES", cats: ["gate"] },
  { id: "alerts", label: "ALERTS", cats: ["alert"] },
  { id: "session", label: "SESSION", cats: ["session", "phase", "checkpoint", "compact", "wake", "task", "teachback", "variety", "raw"] },
];

const FILTER_ALIASES = {
  findings: "bugs",
  finding: "bugs",
  bug: "bugs",
  gate: "gates",
  alert: "alerts",
  commit: "commit",
  commits: "commit",
  handoffs: "handoff",
  dispatches: "dispatch",
  reviews: "review",
  phase: "session",
  task: "session",
  telemetry: "gates",
};

const TABS = [
  { id: "tasks", label: "TASKS" },
  { id: "handoffs", label: "HANDOFFS" },
  { id: "gates", label: "GATES" },
  { id: "reviews", label: "REVIEWS" },
  { id: "variety", label: "VARIETY" },
  { id: "raw", label: "RAW LOG" },
  { id: "help", label: "HELP" },
];

const TAB_ALIASES = {
  about: "help",
  legend: "help",
  keys: "help",
  shortcuts: "help",
  telemetry: "gates",
  gates_telemetry: "gates",
  prs: "reviews",
  wrapup: "reviews",
  "wrap-up": "reviews",
  review: "reviews",
  findings: "reviews",
  task: "tasks",
  handoff: "handoffs",
  rawlog: "raw",
  log: "raw",
};

const STATUS_GROUPS = [
  { id: "active", label: "ACTIVE", statuses: ["active", "in_progress", "in-progress", "working", "doing", "claimed", "running"] },
  { id: "handoff", label: "HANDOFF / REVIEW", statuses: ["handoff", "review", "reviewing", "awaiting_review", "awaiting-review", "in_review"] },
  { id: "pending", label: "PENDING", statuses: ["pending", "todo", "queued", "blocked", "open", "backlog", "draft"] },
  { id: "completed", label: "COMPLETED", statuses: ["completed", "complete", "done", "passed", "cancelled", "canceled", "closed"] },
];

const HANDOFF_FIELDS = [
  { key: "produced", label: "PRODUCED", aliases: ["produced_artifacts", "artifacts"] },
  { key: "decisions", label: "DECISIONS", aliases: ["decision"] },
  { key: "reasoning_chain", label: "REASONING CHAIN", aliases: ["reasoning", "reasoningChain", "chain"] },
  { key: "uncertainty", label: "UNCERTAINTY", aliases: ["uncertainties"] },
  { key: "integration", label: "INTEGRATION", aliases: ["integration_points", "integrationPoints"] },
  { key: "open_questions", label: "OPEN QUESTIONS", aliases: ["openQuestions", "questions", "open_question"] },
];

const SEVERITY_ORDER = ["blocking", "critical", "major", "high", "danger", "warning", "moderate", "minor", "low", "suggestion", "nit", "info", "trivial"];

const ICON_GLYPHS = {
  phase: "▸",
  dispatch: "»",
  teachback: "↺",
  task: "☰",
  handoff: "⇄",
  commit: "◆",
  checkout: "◇",
  checkpoint: "▣",
  save: "▣",
  bug: "▲",
  finding: "▲",
  review: "◎",
  pr: "↥",
  gate: "⊞",
  alert: "⚑",
  session: "◉",
  compact: "≡",
  wake: "◈",
  variety: "◐",
  raw: "·",
};

const SHORTCUTS = [
  ["SPACE", "Play or pause the replay"],
  ["← / →", "Step one event (hold SHIFT for 20)"],
  ["L", "Return to the live head"],
  ["D", "Open the training simulation"],
  ["G", "Open the gates debrief"],
  ["T", "Open the task board"],
  ["?", "Open this help panel"],
  ["ESC", "Close the debrief"],
];

const LEGEND = [
  ["◉", "SESSION", "phases, checkpoints, compaction"],
  ["»", "DISPATCH", "agent dispatches and gate decisions"],
  ["⇄", "HANDOFF", "six-field handoffs"],
  ["◆", "COMMIT", "commits and shas"],
  ["◎", "REVIEW", "review dispatches, PR ready"],
  ["▲", "BUGS", "findings and remediation"],
  ["⊞", "GATES", "lifecycle gates and telemetry"],
  ["⚑", "ALERTS", "algedonic signals and blockers"],
];

/* --- small helpers ------------------------------------------------------ */

function isFunction(value) {
  return typeof value === "function";
}

function toNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function firstNumber(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return 0;
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function visible(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

/* clockTime() follows the browser locale; strip a trailing meridiem so the
 * narrow comms column stays compact and monospaced. */
function clockTime(iso) {
  const value = fmtTime(iso);
  if (!value) return "";
  return value.replace(/\s*[AP]M$/i, "").trim();
}

function safeClass(value, fallback = "run") {
  const cleaned = String(value === undefined || value === null ? "" : value)
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "");
  return cleaned || fallback;
}

function initials(name) {
  const parts = String(name || "?").split(/[^a-zA-Z0-9]+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
}

function iconGlyph(icon) {
  return ICON_GLYPHS[String(icon || "").toLowerCase()] || ICON_GLYPHS.raw;
}

function phaseToneClass(phase) {
  const raw = String(phase || "").toLowerCase();
  return ["prepare", "architect", "code", "test"].includes(raw) ? `phase-${raw}` : "phase-other";
}

function severityToneClass(severity) {
  return `sev-${safeClass(severity || "info", "info")}`;
}

function durationBetween(startIso, endIso) {
  const start = Date.parse(startIso || "");
  const end = Date.parse(endIso || "");
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return "";
  return fmtDuration(Math.round((end - start) / 1000));
}

function normalizeFilterId(value) {
  const raw = String(value === undefined || value === null ? "all" : value).trim().toLowerCase();
  if (!raw) return "all";
  if (FILTERS.some((filter) => filter.id === raw)) return raw;
  return FILTER_ALIASES[raw] || raw;
}

function filterPredicate(id) {
  const definition = FILTERS.find((filter) => filter.id === id);
  if (definition) {
    if (!definition.cats) return () => true;
    const cats = new Set(definition.cats);
    return (event) => Boolean(event) && cats.has(String(event.category || ""));
  }
  return (event) => Boolean(event) && String(event.category || "") === id;
}

function normalizeTabId(value) {
  const raw = String(value === undefined || value === null ? "" : value).trim().toLowerCase();
  if (!raw) return "tasks";
  if (TABS.some((tab) => tab.id === raw)) return raw;
  return TAB_ALIASES[raw] || "tasks";
}

function statusGroupFor(status) {
  const raw = String(status || "").toLowerCase();
  return STATUS_GROUPS.find((group) => group.statuses.includes(raw)) || null;
}

function fieldValue(fields, definition) {
  if (!fields || typeof fields !== "object") return "";
  const keys = [definition.key, ...(definition.aliases || [])];
  for (const key of keys) {
    const value = fields[key];
    if (value === undefined || value === null) continue;
    let text = "";
    if (typeof value === "string") text = value.trim();
    else if (Array.isArray(value)) text = value.map((item) => String(item)).join(", ");
    else text = String(value);
    if (text) return text;
  }
  return "";
}

function groupFindings(findings) {
  const groups = new Map();
  for (const finding of findings) {
    const severity = String((finding && finding.severity) || "unknown").toLowerCase();
    if (!groups.has(severity)) groups.set(severity, []);
    groups.get(severity).push(finding);
  }
  return [...groups.keys()]
    .sort((left, right) => {
      const leftRank = SEVERITY_ORDER.indexOf(left);
      const rightRank = SEVERITY_ORDER.indexOf(right);
      return (leftRank === -1 ? 99 : leftRank) - (rightRank === -1 ? 99 : rightRank) || left.localeCompare(right);
    })
    .map((severity) => ({ severity, items: groups.get(severity) }));
}

/* --- Panels ------------------------------------------------------------- */

export class Panels {
  constructor(options = {}) {
    this.left = options.left || null;
    this.right = options.right || null;
    this.modal = options.modal || null;
    this.toastRoot = options.toast || null;
    this.cb = {
      onSelectAgent: isFunction(options.onSelectAgent) ? options.onSelectAgent : null,
      onSeek: isFunction(options.onSeek) ? options.onSeek : null,
      onFilter: isFunction(options.onFilter) ? options.onFilter : null,
      onClose: isFunction(options.onClose) ? options.onClose : null,
    };
    this.model = null;
    this.cursor = 0;
    this.filter = "all";
    this.selectedAgent = null;
    this.live = false;
    this.status = "";
    this.modalTab = null;
    this.modalView = "tab";
    this.detailEvent = null;
    this.error = null;
    this.notice = "";
    this.toasts = [];
    this._toastTimers = new Map();
    this._leftLivePills = [];
    this._rightLivePills = [];
    this._chipNodes = new Map();
    this._crewRows = new Map();
    this._phaseRows = [];
    this._feed = { key: null, filter: "all", count: 0, matched: 0, rendered: 0, list: null, capNote: null };
    this._nodes = {};
    this._chartJobs = [];
    this._lastFocus = null;
    this._focusOnOpen = false;
    this._lastFocusKey = "";
    this._seqIndex = () => null;

    if (this.modal) {
      this.modal.setAttribute("role", "dialog");
      this.modal.setAttribute("aria-modal", "true");
      this.modal.setAttribute("aria-label", "PACT run debrief");
      this.modal.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          this.closeModal();
          return;
        }
        if (event.key !== "Tab") return;
        /* aria-modal promises the background is inert; without a Tab wrap,
         * focus still walks into the page behind the dialog. */
        const focusables = [...this.modal.querySelectorAll(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        )].filter((node) => !node.disabled && node.offsetParent !== null);
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const inside = this.modal.contains(document.activeElement);
        if (event.shiftKey && (!inside || document.activeElement === first)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (!inside || document.activeElement === last)) {
          event.preventDefault();
          first.focus();
        }
      });
      this.modal.addEventListener("focusin", (event) => {
        const key = event.target && event.target.dataset ? event.target.dataset.focusKey : "";
        if (key) this._lastFocusKey = key;
      });
      this.modal.addEventListener("click", (event) => {
        if (event.target === this.modal) this.closeModal();
      });
    }

    this.renderLeft();
    this.renderFeed();
  }

  /* --- public interface -------------------------------------------------- */

  setModel(model) {
    const next = model && typeof model === "object" ? model : null;
    const sameRun = Boolean(this.model && next) && runKey(this.model) === runKey(next);
    this.model = next;
    this._seqIndex = createSeqIndex(next);
    this.cursor = clamp(this.cursor, 0, this._eventTotal());
    this.renderLeft();
    if (sameRun && this._feed.list) {
      /* A live poll grows the same run: update the feed in place instead of
       * rebuilding up to 240 rows (and destroying the focused row) each tick. */
      this._updateFeed();
    } else {
      this.renderFeed();
    }
    if (this.modalTab) this._renderModal();
  }

  setCursor(cursor) {
    const total = this._eventTotal();
    this.cursor = clamp(Math.round(Number(cursor) || 0), 0, total);
    this._updateCursorBits();
    this._updateFeed();
  }

  setFilter(category) {
    const next = normalizeFilterId(category);
    const changed = next !== this.filter;
    this.filter = next;
    for (const [id, node] of this._chipNodes) {
      const active = id === this.filter;
      toggleClass(node, "is-active", active);
      node.setAttribute("aria-pressed", active ? "true" : "false");
    }
    if (!changed || !this.right) return;
    /* Rebuild only the feed list so the active chip keeps keyboard focus. */
    const feed = this._nodes.feed;
    if (feed) {
      this._rebuildFeed(feed);
    } else {
      this.renderFeed();
    }
  }

  setSelectedAgent(name) {
    this.selectedAgent = name ? String(name) : null;
    for (const [agent, node] of this._crewRows) {
      const selected = agent === this.selectedAgent;
      toggleClass(node, "is-selected", selected);
      node.setAttribute("aria-pressed", selected ? "true" : "false");
    }
  }

  setLive(isLive) {
    this.live = Boolean(isLive);
    this._applyLivePills(this._leftLivePills);
    this._applyLivePills(this._rightLivePills);
    const statusNode = this._nodes.statusValue;
    if (statusNode) statusNode.textContent = this.status || (this.live ? "FOLLOWING" : "STANDBY");
  }

  toast(options = {}) {
    if (!this.toastRoot) return null;
    const title = visible(options.title) || "Notice";
    const body = visible(options.body);
    const kind = TOAST_KINDS.has(options.kind) ? options.kind : "info";
    const card = el("div", { class: `toast toast-${kind}`, attrs: { role: "status", tabindex: "0", title: "Dismiss" } }, [
      el("div", { class: "toast-title", text: title }),
      body ? el("div", { class: "toast-body", text: body }) : null,
    ]);
    const dismiss = () => this._dismissToast(card);
    card.addEventListener("click", dismiss);
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " " || event.key === "Escape") {
        event.preventDefault();
        dismiss();
      }
    });
    this.toastRoot.appendChild(card);
    this.toasts.push(card);
    while (this.toasts.length > MAX_TOASTS) this._dismissToast(this.toasts[0]);
    const timer = setTimeout(dismiss, TOAST_MS[kind] || TOAST_MS.info);
    this._toastTimers.set(card, timer);
    return card;
  }

  showError(message, options = {}) {
    const retry = isFunction(options) ? options : isFunction(options.retry) ? options.retry : null;
    this.error = { message: visible(message) || "Something went wrong", retry };
    if (!this.right) {
      this.toast({ title: "Link fault", body: this.error.message, kind: "danger" });
      return;
    }
    this._renderErrorCard();
  }

  openDebrief(tab) {
    if (!this.modal) return;
    this.modalTab = normalizeTabId(tab);
    this.modalView = "tab";
    this.detailEvent = null;
    if (this.modal.hidden) {
      this._lastFocus = document.activeElement || null;
      this._focusOnOpen = true;
      this.modal.hidden = false;
    }
    this._renderModal();
  }

  closeModal() {
    if (!this.modal || this.modal.hidden) return;
    this.modal.hidden = true;
    clear(this.modal);
    this.modalTab = null;
    this.modalView = "tab";
    this.detailEvent = null;
    this._nodes.modalClose = null;
    this._nodes.modalCard = null;
    this._nodes.modalBody = null;
    this._chartJobs = [];
    const previous = this._lastFocus;
    this._lastFocus = null;
    this._lastFocusKey = "";
    this._focusOnOpen = false;
    if (previous && typeof previous.focus === "function" && previous.isConnected !== false) {
      try {
        previous.focus();
      } catch (_) {
        /* focus is best-effort */
      }
    }
    if (this.cb.onClose) this.cb.onClose();
  }

  setStatus(status) {
    this.status = visible(status);
    const node = this._nodes.statusValue;
    if (node) {
      node.textContent = this.status || (this.live ? "FOLLOWING" : "STANDBY");
      toggleClass(node, "is-live", this.live);
    }
  }

  /* A neutral empty state for "the requested thing is not here", as opposed
   * to an error. It replaces the generic awaiting-telemetry card until a run
   * loads. */
  setNotice(message) {
    this.notice = visible(message);
    if (!this.model) this.renderLeft();
  }

  /* --- shared internals -------------------------------------------------- */

  _eventTotal() {
    const events = this.model && Array.isArray(this.model.events) ? this.model.events : [];
    return events.length;
  }

  _applyLivePills(pills) {
    for (const pill of pills) {
      if (!pill) continue;
      pill.textContent = this.live ? "● LIVE" : "◼ REPLAY";
      toggleClass(pill, "is-live", this.live);
    }
  }

  _livePill(rail) {
    const pill = el("span", { class: `mc-pill live-pill${this.live ? " is-live" : ""}`, text: this.live ? "● LIVE" : "◼ REPLAY" });
    if (rail === "right") this._rightLivePills.push(pill);
    else this._leftLivePills.push(pill);
    return pill;
  }

  _dismissToast(card) {
    if (!card) return;
    const timer = this._toastTimers.get(card);
    if (timer) clearTimeout(timer);
    this._toastTimers.delete(card);
    const index = this.toasts.indexOf(card);
    if (index >= 0) this.toasts.splice(index, 1);
    if (card.parentNode) card.parentNode.removeChild(card);
  }

  _renderErrorCard() {
    if (!this.right) return;
    const existing = this._nodes.errorCard;
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
    this._nodes.errorCard = null;
    if (!this.error) return;
    const actions = [];
    if (this.error.retry) {
      actions.push(el("button", {
        class: "mini-btn",
        text: "RETRY",
        attrs: { type: "button" },
        on: {
          click: () => {
            const retry = this.error && this.error.retry;
            this._clearError();
            if (retry) retry();
          },
        },
      }));
    }
    actions.push(el("button", {
      class: "mini-btn",
      text: "DISMISS",
      attrs: { type: "button" },
      on: { click: () => this._clearError() },
    }));
    const card = el("div", { class: "rail-error", attrs: { role: "alert" } }, [
      el("div", { class: "rail-error-title", text: "LINK FAULT" }),
      el("p", { class: "rail-error-msg", text: this.error.message }),
      el("div", { class: "rail-error-actions" }, actions),
    ]);
    this._nodes.errorCard = card;
    this.right.insertBefore(card, this.right.firstChild);
  }

  _clearError() {
    this.error = null;
    const card = this._nodes.errorCard;
    if (card && card.parentNode) card.parentNode.removeChild(card);
    this._nodes.errorCard = null;
  }

  /* --- left rail --------------------------------------------------------- */

  renderLeft() {
    const left = this.left;
    if (!left) return;
    const scrollTop = Number(left.scrollTop) || 0;
    const focusKey = this._focusKeyWithin(left);
    clear(left);
    this._leftLivePills = [];
    this._crewRows = new Map();
    this._phaseRows = [];
    this._nodes.eventsVal = null;
    this._nodes.statusValue = null;
    const model = this.model;
    if (!model) {
      left.appendChild(
        this.notice
          ? this._emptyCard("NO SESSIONS", this.notice)
          : this._emptyCard("AWAITING TELEMETRY", "Choose a run in the header, or open the training simulation.")
      );
      left.appendChild(this._statusLine());
      return;
    }
    left.appendChild(this._summaryCard());
    left.appendChild(this._phaseCard());
    left.appendChild(this._crewCard());
    left.appendChild(this._statusLine());
    left.scrollTop = scrollTop;
    this._restoreFocusWithin(left, focusKey);
  }

  _focusKeyWithin(root) {
    const active = document.activeElement;
    if (!root || !active || !root.contains(active) || !active.dataset) return "";
    return active.dataset.focusKey || "";
  }

  _restoreFocusWithin(root, key) {
    if (!root || !key) return;
    const target = [...root.querySelectorAll("[data-focus-key]")]
      .find((node) => node.dataset.focusKey === key);
    if (target && typeof target.focus === "function") {
      try {
        target.focus({ preventScroll: true });
      } catch (_) {
        try { target.focus(); } catch (err) { /* focus is best-effort */ }
      }
    }
  }

  _emptyCard(title, body) {
    return el("div", { class: "rail-card rail-empty", attrs: { role: "status" } }, [
      el("div", { class: "rail-kicker", text: title }),
      el("p", { class: "rail-empty-text", text: body }),
    ]);
  }

  _statusLine() {
    const value = el("span", { class: "rail-status-value", text: this.status || (this.live ? "FOLLOWING" : "STANDBY") });
    this._nodes.statusValue = value;
    return el("div", { class: "rail-status" }, [
      el("span", { class: "rail-status-label", text: "READ-ONLY · LOCAL-ONLY" }),
      value,
    ]);
  }

  _summaryCard() {
    const model = this.model;
    const meta = model.meta || {};
    const metrics = model.metrics || {};
    const card = el("section", { class: "rail-card run-card", attrs: { "aria-label": "Run summary" } });

    card.appendChild(el("div", { class: "rail-kicker-row" }, [
      el("span", { class: "rail-kicker", text: "RUN SUMMARY" }),
      this._livePill("left"),
    ]));

    const title = visible(meta.project) || visible(meta.team) || "Unnamed run";
    card.appendChild(el("div", {
      class: "run-title",
      text: title,
      attrs: { title: visible(meta.project_dir) || title },
    }));
    const subBits = [visible(meta.team), visible(meta.run_kind), visible(model.source && model.source.kind)].filter(Boolean);
    card.appendChild(el("div", { class: "run-sub", text: subBits.join(" · ") || "local journal" }));

    card.appendChild(el("div", { class: "badge-row" }, [
      this._statusPill(meta.status),
      el("span", { class: `mc-pill ${meta.cloud ? "is-cloud" : "is-local"}`, text: meta.cloud ? "CLOUD" : "LOCAL" }),
      this._wrapupPill(model, metrics),
    ]));

    const grid = el("div", { class: "summary-grid" });
    const eventsValue = el("span", { class: "summary-val", text: `${this.cursor} / ${this._eventTotal()}` });
    this._nodes.eventsVal = eventsValue;
    grid.appendChild(el("div", { class: "summary-cell" }, [
      el("span", { class: "summary-key", text: "EVENTS" }),
      eventsValue,
    ]));
    const cells = [
      ["DURATION", meta.duration_s !== undefined && meta.duration_s !== null ? fmtDuration(meta.duration_s) : "—"],
      ["STARTED", fmtDate(meta.started_at) || "—"],
      ["CREW", String(firstNumber(metrics.agents, asArray(model.agents).length))],
      ["TASKS", String(firstNumber(metrics.tasks, asArray(model.tasks).length))],
      ["HANDOFFS", String(firstNumber(metrics.handoffs, asArray(model.handoffs).length))],
      ["COMMITS", String(firstNumber(metrics.commits, asArray(model.commits).length))],
      ["ALERTS", String(firstNumber(metrics.alerts, asArray(model.alerts).length))],
      ["GATE ADV.", String(firstNumber(metrics.gates_advisory, model.gates && model.gates.advisory))],
    ];
    for (const [key, value] of cells) {
      grid.appendChild(el("div", { class: "summary-cell" }, [
        el("span", { class: "summary-key", text: key }),
        el("span", { class: "summary-val", text: value }),
      ]));
    }
    card.appendChild(grid);
    card.appendChild(this._varietyBlock());

    const warnings = asArray(model.warnings);
    if (warnings.length || model.truncated) {
      card.appendChild(el("div", {
        class: "rail-note is-warning",
        text: model.truncated
          ? "Journal truncated for display; counters remain authoritative."
          : `${warnings.length} parser warning${warnings.length === 1 ? "" : "s"} recorded.`,
      }));
    }
    return card;
  }

  _statusPill(status) {
    const raw = String(status || "unknown").toLowerCase();
    const classes = {
      live: "is-live",
      ended: "is-ended",
      paused: "is-paused",
      refreshed: "is-refreshed",
      error: "is-danger",
    };
    const tone = classes[raw] || "is-muted";
    const label = raw === "live" ? "● LIVE" : raw.toUpperCase();
    return el("span", { class: `mc-pill status-pill ${tone}`, text: label, attrs: { title: `Run status: ${raw}` } });
  }

  _wrapupPill(model, metrics) {
    const wrapup = model.wrapup;
    if (wrapup && visible(wrapup.pr_state)) {
      const state = visible(wrapup.pr_state).toUpperCase();
      const shipped = state === "MERGED";
      const label = shipped ? `SHIPPED · PR ${wrapup.pr_number || "?"}` : `PR ${wrapup.pr_number || "?"} · ${state}`;
      const tone = shipped ? "is-shipped" : state === "OPEN" || state === "DRAFT" ? "is-warning" : "is-muted";
      return el("span", {
        class: `mc-pill pr-pill ${tone}`,
        text: label,
        attrs: { title: `Wrap-up PR state: ${state}` },
      });
    }
    if (firstNumber(metrics.prs_ready) > 0) {
      return el("span", { class: "mc-pill pr-pill is-warning", text: "PR READY" });
    }
    return el("span", { class: "mc-pill pr-pill is-muted", text: "NO WRAP-UP" });
  }

  _varietyBlock() {
    const model = this.model;
    const variety = model.variety && typeof model.variety === "object" ? model.variety : {};
    const latest = variety.latest && typeof variety.latest === "object" ? variety.latest : {};
    const feature = variety.feature && typeof variety.feature === "object" ? variety.feature : {};
    const metrics = model.metrics || {};
    const total = firstNumber(latest.total, feature.total, metrics.variety_total);
    const hasData = VARIETY_DIMS.some((key) => latest[key] !== undefined || feature[key] !== undefined);
    const block = el("div", { class: "variety-block" });
    block.appendChild(el("div", { class: "variety-head" }, [
      el("span", { class: "variety-label", text: "VARIETY" }),
      el("span", { class: "variety-total", text: hasData ? `${total} / 16` : "—" }),
    ]));
    const bar = el("div", { class: "variety-bar", attrs: { role: "img", "aria-label": `Variety total ${total} of 16` } });
    const fill = el("span");
    fill.style.width = `${hasData ? clamp((total / 16) * 100, 0, 100) : 0}%`;
    bar.appendChild(fill);
    block.appendChild(bar);
    const dims = el("div", { class: "variety-dims" });
    for (const key of VARIETY_DIMS) {
      const source = latest[key] !== undefined ? latest[key] : feature[key];
      const value = clamp(toNumber(source), 0, 4);
      const meter = el("span", { class: "variety-dim-bar" });
      const meterFill = el("span");
      meterFill.style.width = `${(value / 4) * 100}%`;
      meter.appendChild(meterFill);
      const shortKey = { novelty: "NOV", scope: "SCO", uncertainty: "UNC", risk: "RSK" }[key] || key.slice(0, 3).toUpperCase();
      dims.appendChild(el("div", { class: "variety-dim", attrs: { title: key.toUpperCase() } }, [
        el("span", { class: "variety-dim-key", text: shortKey }),
        meter,
        el("span", { class: "variety-dim-val", text: source === undefined || source === null ? "—" : String(value) }),
      ]));
    }
    block.appendChild(dims);
    return block;
  }

  _phaseCard() {
    const model = this.model;
    const phases = model.phases && typeof model.phases === "object" ? model.phases : {};
    const card = el("section", { class: "rail-card phase-card", attrs: { "aria-label": "Phase track" } });
    const order = asArray(phases.order).length ? asArray(phases.order).slice() : ["PREPARE", "ARCHITECT", "CODE", "TEST"];
    const completed = firstNumber((model.metrics || {}).phases_completed, asArray(phases.completed).length);
    card.appendChild(el("div", { class: "rail-kicker-row" }, [
      el("span", { class: "rail-kicker", text: "PHASE TRACK" }),
      el("span", { class: "rail-kicker-note", text: `${completed} / ${Math.max(order.length, 4)}` }),
    ]));
    const list = el("div", { class: "phase-list" });
    const items = asArray(phases.items);
    const byName = new Map();
    for (const item of items) {
      const name = visible(item.name) || visible(item.family);
      if (!name) continue;
      if (!byName.has(name)) byName.set(name, []);
      byName.get(name).push(item);
    }
    const rows = [];
    const seen = new Set();
    for (const name of order) {
      seen.add(name);
      const entries = byName.get(name) || [];
      if (entries.length) {
        for (const item of entries) rows.push({ name, item });
      } else {
        rows.push({ name, item: null });
      }
    }
    for (const item of items) {
      const name = visible(item.name) || visible(item.family);
      if (name && !seen.has(name)) {
        seen.add(name);
        rows.push({ name, item });
      }
    }
    for (const row of rows) list.appendChild(this._phaseRow(row.name, row.item, phases.current));
    card.appendChild(list);
    return card;
  }

  _phaseRow(name, item, currentName) {
    let statusClass = "is-pending";
    const status = item ? String(item.status || "").toLowerCase() : "";
    if (item) {
      if (["skipped", "skip"].includes(status)) statusClass = "is-skipped";
      else if (["current", "active", "in_progress", "in-progress", "running"].includes(status)) statusClass = "is-current";
      else if (["completed", "complete", "done", "passed"].includes(status)) statusClass = "is-completed";
    }
    const run = item ? firstNumber(item.run) : 0;
    /* phase boundaries are journal seqs; compare them to the cursor only
     * after mapping them onto the visible event window. */
    const startIndex = item ? this._seqIndex(item.started_seq) : null;
    const glyph = statusClass === "is-completed" ? "✓" : statusClass === "is-current" ? "▶" : statusClass === "is-skipped" ? "–" : "·";
    const metaBits = [];
    if (!item) {
      metaBits.push("PENDING");
    } else {
      metaBits.push(
        statusClass === "is-completed" ? "COMPLETED"
          : statusClass === "is-current" ? "ACTIVE"
            : statusClass === "is-skipped" ? "SKIPPED"
              : status ? status.toUpperCase() : "PENDING"
      );
      const duration = durationBetween(item.started_at, item.ended_at);
      if (duration) metaBits.push(duration);
      if (visible(item.skip_reason)) metaBits.push(visible(item.skip_reason));
    }
    const nameRow = el("span", { class: "phase-name-row" }, [
      el("span", { class: "phase-name", text: name }),
      run > 1 ? el("span", { class: "phase-run", text: `RUN ${run}` }) : null,
      statusClass !== "is-completed" && String(currentName || "").toUpperCase() === String(name).toUpperCase()
        ? el("span", { class: "phase-current-tag", text: "CURRENT" })
        : null,
    ]);
    const step = el("div", {
      class: `phase-step ${statusClass}${run > 1 ? " is-rerun" : ""}`,
      attrs: { title: item && item.skip_reason ? String(item.skip_reason) : String(name) },
    }, [
      el("span", { class: "phase-node", text: glyph }),
      el("span", { class: "phase-body" }, [
        nameRow,
        el("span", { class: "phase-meta", text: metaBits.join(" · ") }),
      ]),
    ]);
    if (startIndex !== null && this.cursor <= startIndex) step.className += " is-ahead";
    this._phaseRows.push({ node: step, startIndex });
    return step;
  }

  _crewCard() {
    const model = this.model;
    const agents = asArray(model.agents).slice();
    const card = el("section", { class: "rail-card crew-card", attrs: { "aria-label": "Crew roster" } });
    card.appendChild(el("div", { class: "rail-kicker-row" }, [
      el("span", { class: "rail-kicker", text: "CREW" }),
      el("span", { class: "rail-kicker-note", text: `${agents.length} ${agents.length === 1 ? "OPERATIVE" : "OPERATIVES"}` }),
    ]));
    if (!agents.length) {
      card.appendChild(el("p", { class: "rail-empty-text", text: "No roster recorded for this run." }));
      return card;
    }
    agents.sort((left, right) => {
      const leadRank = (agent) => (
        String(agent.kind || "").toLowerCase() === "lead" || String(agent.role || "").toLowerCase() === "lead" ? -1 : 0
      );
      const rank = leadRank(left) - leadRank(right);
      if (rank) return rank;
      const leftSeen = Date.parse(left.first_seen || "") || 0;
      const rightSeen = Date.parse(right.first_seen || "") || 0;
      return leftSeen - rightSeen || String(left.name || "").localeCompare(String(right.name || ""));
    });
    const list = el("div", { class: "crew-list" });
    for (const agent of agents) list.appendChild(this._crewRow(agent));
    card.appendChild(list);
    return card;
  }

  _crewRow(agent) {
    const name = visible(agent.name) || "unknown";
    const toneClass = `tone-${safeClass(agent.color || "muted", "muted")}`;
    const state = String(agent.state || "idle").toLowerCase();
    const stateClass = ["active", "working", "in_progress", "in-progress", "running", "dispatching"].includes(state)
      ? "is-active"
      : ["done", "complete", "completed", "ended"].includes(state) ? "is-done" : "is-idle";
    const stats = agent.stats && typeof agent.stats === "object" ? agent.stats : {};
    const tasks = asArray(this.model.tasks);
    const task = agent.task_id !== undefined && agent.task_id !== null
      ? tasks.find((candidate) => String(candidate.id) === String(agent.task_id))
      : null;
    const taskText = task
      ? `TASK #${task.id} · ${visible(task.subject) || "untitled"}`
      : visible(agent.latest && agent.latest.summary) || "No task recorded";
    const selected = String(this.selectedAgent || "") === name;
    const row = el("div", {
      class: `crew-item${selected ? " is-selected" : ""}`,
      dataset: { focusKey: `crew:${name}` },
      attrs: { role: "button", tabindex: "0", "aria-pressed": selected ? "true" : "false", title: `${name} · ${visible(agent.role) || "agent"}` },
    }, [
      el("span", { class: `crew-badge ${toneClass}`, text: initials(name), attrs: { "aria-hidden": "true" } }),
      el("span", { class: "crew-main" }, [
        el("span", { class: "crew-top" }, [
          el("span", { class: "crew-name", text: name }),
          agent.cloud ? el("span", { class: "crew-cloud", text: "CLOUD" }) : null,
          el("span", { class: `crew-state ${stateClass}`, text: state.toUpperCase() }),
        ]),
        el("span", { class: "crew-role", text: [visible(agent.role), visible(agent.kind), visible(agent.phase)].filter(Boolean).join(" · ") }),
        el("span", { class: "crew-task", text: taskText }),
        el("span", {
          class: "crew-stats",
          text: `ev ${firstNumber(stats.events)} · d ${firstNumber(stats.dispatches)} · h ${firstNumber(stats.handoffs)} · f ${firstNumber(stats.findings)} · c ${firstNumber(stats.commits)}`,
        }),
      ]),
    ]);
    const activate = () => {
      if (this.cb.onSelectAgent) this.cb.onSelectAgent(name);
      else this.setSelectedAgent(String(this.selectedAgent || "") === name ? null : name);
    };
    row.addEventListener("click", activate);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
    this._crewRows.set(name, row);
    return row;
  }

  _updateCursorBits() {
    const eventsValue = this._nodes.eventsVal;
    if (eventsValue) eventsValue.textContent = `${this.cursor} / ${this._eventTotal()}`;
    for (const row of this._phaseRows) {
      if (row.startIndex === null) continue;
      toggleClass(row.node, "is-ahead", this.cursor <= row.startIndex);
    }
  }

  /* --- right rail -------------------------------------------------------- */

  renderFeed() {
    const right = this.right;
    if (!right) return;
    const scrollTop = Number(right.scrollTop) || 0;
    clear(right);
    this._rightLivePills = [];
    this._chipNodes = new Map();
    this._nodes.feed = null;
    this._nodes.feedCount = null;
    if (this.error) this._renderErrorCard();

    const countNode = el("span", { class: "comms-count", text: "0" });
    this._nodes.feedCount = countNode;
    const head = el("div", { class: "comms-head" }, [
      el("div", { class: "comms-title-wrap" }, [
        el("span", { class: "comms-title", text: "COMMS" }),
        el("span", { class: "comms-sub", text: "journal feed" }),
      ]),
      el("div", { class: "comms-meta" }, [this._livePill("right"), countNode]),
    ]);
    right.appendChild(head);

    const chips = el("div", { class: "filter-chips", attrs: { role: "group", "aria-label": "Filter comms" } });
    for (const filter of FILTERS) {
      const active = filter.id === this.filter;
      const chip = el("button", {
        class: `filter-chip${active ? " is-active" : ""}`,
        text: filter.label,
        attrs: { type: "button", "aria-pressed": active ? "true" : "false" },
      });
      chip.addEventListener("click", () => {
        this.setFilter(filter.id);
        if (this.cb.onFilter) this.cb.onFilter(filter.id);
      });
      this._chipNodes.set(filter.id, chip);
      chips.appendChild(chip);
    }
    right.appendChild(chips);

    const feed = el("div", { class: "feed", attrs: { role: "list", "aria-label": "Comms feed" } });
    this._nodes.feed = feed;
    right.appendChild(feed);
    this._rebuildFeed(feed);
    right.scrollTop = scrollTop;
  }

  _feedRow(event, index) {
    const severity = ["info", "success", "warning", "danger", "muted"].includes(event.severity) ? event.severity : "info";
    const row = el("div", {
      class: `feed-row sev-${severity}`,
      attrs: {
        role: "button",
        tabindex: "0",
        title: `${visible(event.type) || "event"} · press Enter for detail`,
      },
    }, [
      el("span", { class: "feed-time", text: clockTime(event.ts) || "--:--:--" }),
      el("span", { class: `feed-icon cat-${safeClass(event.category || "other", "other")}`, text: iconGlyph(event.icon) }),
      el("span", { class: "feed-body" }, [
        el("span", { class: "feed-title", text: visible(event.title) || visible(event.type) || "Event" }),
        visible(event.detail) ? el("span", { class: "feed-detail", text: visible(event.detail) }) : null,
      ]),
    ]);
    const activate = () => this._openEvent(event, index);
    row.addEventListener("click", activate);
    row.addEventListener("keydown", (keyEvent) => {
      if (keyEvent.key === "Enter" || keyEvent.key === " ") {
        keyEvent.preventDefault();
        activate();
      }
    });
    return row;
  }

  _feedEmpty() {
    const total = this._eventTotal();
    let title = "NO TRAFFIC YET";
    let body = "The recorder is parked at t=0. Press play or drag the timeline.";
    if (!this.model) {
      title = "NO RUN LOADED";
      body = "Pick a run in the header, or open the training simulation.";
    } else if (total === 0) {
      title = "NO EVENTS";
      body = "This journal did not yield any visible events.";
    } else if (this.cursor > 0 && this.filter !== "all") {
      title = `NO ${this.filter.toUpperCase()} TRAFFIC`;
      body = "No matching events up to the current replay point.";
    }
    return el("div", { class: "feed-empty" }, [
      el("div", { class: "feed-empty-glyph", text: "⊘" }),
      el("div", { class: "feed-empty-title", text: title }),
      el("div", { class: "feed-empty-text", text: body }),
    ]);
  }

  _rebuildFeed(feed) {
    if (!feed) return;
    clear(feed);
    const events = asArray(this.model && this.model.events);
    const end = clamp(this.cursor, 0, events.length);
    const match = filterPredicate(this.filter);
    const matches = [];
    for (let index = 0; index < end; index += 1) {
      const event = events[index];
      if (event && match(event)) matches.push({ event, index });
    }
    this._feed = {
      key: runKey(this.model),
      filter: this.filter,
      count: this.cursor,
      matched: matches.length,
      rendered: 0,
      list: feed,
      capNote: null,
    };
    const start = Math.max(0, matches.length - FEED_LIMIT);
    const shown = matches.slice(start);
    for (let index = shown.length - 1; index >= 0; index -= 1) {
      feed.appendChild(this._feedRow(shown[index].event, shown[index].index));
    }
    this._feed.rendered = shown.length;
    if (!shown.length) {
      feed.appendChild(this._feedEmpty());
    } else {
      this._ensureCapNote();
    }
    this._updateFeedCount();
  }

  _ensureCapNote() {
    const feed = this._nodes.feed;
    if (!feed) return;
    const hidden = Math.max(0, this._feed.matched - FEED_LIMIT);
    if (!hidden) {
      if (this._feed.capNote && this._feed.capNote.parentNode) this._feed.capNote.parentNode.removeChild(this._feed.capNote);
      this._feed.capNote = null;
      return;
    }
    if (this._feed.capNote && this._feed.capNote.parentNode) return;
    const note = el("div", {
      class: "feed-cap",
      text: `… ${hidden} earlier matching event${hidden === 1 ? "" : "s"} hidden (latest ${FEED_LIMIT} shown)`,
    });
    feed.appendChild(note);
    this._feed.capNote = note;
  }

  _updateFeedCount() {
    const node = this._nodes.feedCount;
    if (!node) return;
    const filter = this.filter === "all" ? "" : ` · ${this.filter.toUpperCase()}`;
    node.textContent = `${this._feed.matched}${filter}`;
  }

  _updateFeed() {
    const feed = this._nodes.feed;
    if (!feed) {
      this.renderFeed();
      return;
    }
    const events = asArray(this.model && this.model.events);
    if (this._feed.key !== runKey(this.model) || this._feed.filter !== this.filter || !this._feed.list) {
      this._rebuildFeed(feed);
      return;
    }
    const total = events.length;
    const next = clamp(this.cursor, 0, total);
    const previous = clamp(this._feed.count, 0, total);
    if (next === previous) {
      this._updateFeedCount();
      return;
    }
    if (Math.abs(next - previous) > 120) {
      this._rebuildFeed(feed);
      return;
    }
    const match = filterPredicate(this.filter);
    if (next > previous) {
      if (this._feed.matched === 0) {
        this._rebuildFeed(feed);
        return;
      }
      const added = [];
      for (let index = previous; index < next; index += 1) {
        const event = events[index];
        if (event && match(event)) added.push({ event, index });
      }
      for (let index = added.length - 1; index >= 0; index -= 1) {
        feed.insertBefore(this._feedRow(added[index].event, added[index].index), feed.firstChild);
      }
      this._feed.matched += added.length;
      this._feed.rendered += added.length;
    } else {
      if (this._feed.matched > FEED_LIMIT) {
        this._rebuildFeed(feed);
        return;
      }
      let removed = 0;
      for (let index = next; index < previous; index += 1) {
        const event = events[index];
        if (event && match(event)) removed += 1;
      }
      for (let count = 0; count < removed; count += 1) {
        const first = feed.firstChild;
        if (!first) break;
        feed.removeChild(first);
      }
      this._feed.matched = Math.max(0, this._feed.matched - removed);
      this._feed.rendered = Math.max(0, this._feed.rendered - removed);
    }
    this._feed.count = next;
    while (this._feed.rendered > FEED_LIMIT) {
      if (this._feed.capNote && feed.lastChild === this._feed.capNote) {
        feed.removeChild(this._feed.capNote);
        this._feed.capNote = null;
      }
      const last = feed.lastChild;
      if (!last || last === this._feed.capNote) break;
      feed.removeChild(last);
      this._feed.rendered -= 1;
    }
    if (this._feed.rendered === 0) {
      this._rebuildFeed(feed);
      return;
    }
    this._ensureCapNote();
    this._updateFeedCount();
  }

  /* --- event detail / debrief modal -------------------------------------- */

  _openEvent(event, index) {
    if (!this.modal || !event) return;
    this.modalTab = this.modalTab || "tasks";
    this.detailEvent = { event, index };
    this.modalView = "event";
    if (this.modal.hidden) {
      this._lastFocus = document.activeElement || null;
      this._focusOnOpen = true;
      this.modal.hidden = false;
    }
    this._renderModal();
  }

  _runLabel() {
    const model = this.model;
    if (!model) return "no run loaded";
    const meta = model.meta || {};
    const source = model.source || {};
    const name = visible(meta.project) || visible(source.slug) || "PACT run";
    const bits = [name];
    if (visible(meta.team) && visible(meta.team) !== name) bits.push(meta.team);
    if (visible(source.kind)) bits.push(source.kind);
    return bits.join(" · ");
  }

  _renderModal() {
    if (!this.modal || this.modal.hidden) return;
    const tab = this.modalTab || "tasks";
    /* A live poll re-renders this dialog while the reader is inside it.
     * Preserve the scroll position and return focus to the same control. */
    const scrollTop = this._nodes.modalBody ? Number(this._nodes.modalBody.scrollTop) || 0 : 0;
    const restoreKey = this._focusOnOpen ? "" : this._lastFocusKey;
    clear(this.modal);
    this._chartJobs = [];
    if (this.modalView !== "event") this.detailEvent = null;
    const card = el("div", { class: "debrief" });
    card.appendChild(this._modalHeader(tab));
    const body = el("div", { class: "debrief-body" });
    if (this.modalView === "event" && this.detailEvent) {
      body.appendChild(this._eventDetail(this.detailEvent.event, this.detailEvent.index));
    } else {
      body.appendChild(this._renderTabContent(tab));
    }
    card.appendChild(body);
    card.appendChild(this._modalFooter());
    this.modal.appendChild(card);
    this._nodes.modalCard = card;
    this._nodes.modalBody = body;
    body.scrollTop = scrollTop;
    if (this._focusOnOpen && this._nodes.modalClose && typeof this._nodes.modalClose.focus === "function") {
      this._focusOnOpen = false;
      try {
        this._nodes.modalClose.focus();
      } catch (_) {
        /* focus is best-effort */
      }
    } else if (restoreKey) {
      const target = [...this.modal.querySelectorAll("[data-focus-key]")]
        .find((node) => node.dataset.focusKey === restoreKey);
      if (target && typeof target.focus === "function") {
        try {
          target.focus({ preventScroll: true });
        } catch (_) {
          try { target.focus(); } catch (err) { /* focus is best-effort */ }
        }
      }
    }
    if (this.modalView === "tab" && tab === "variety") {
      this._drawCharts();
      if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => this._drawCharts());
    }
  }

  _modalHeader(tab) {
    const head = el("div", { class: "debrief-head" });
    head.appendChild(el("div", { class: "debrief-title-wrap" }, [
      el("div", { class: "debrief-title" }, [
        el("span", { class: "debrief-title-glyph", text: "▚" }),
        el("span", { text: this.modalView === "event" ? "EVENT RECORD" : "DEBRIEF" }),
      ]),
      el("div", { class: "debrief-run", text: this._runLabel() }),
    ]));
    const tabs = el("div", { class: "debrief-tabs", attrs: { role: "tablist", "aria-label": "Debrief sections" } });
    for (const definition of TABS) {
      const active = this.modalView === "tab" && definition.id === tab;
      const button = el("button", {
        class: `debrief-tab${active ? " is-active" : ""}`,
        text: definition.label,
        dataset: { focusKey: `tab:${definition.id}` },
        attrs: { type: "button", role: "tab", "aria-selected": active ? "true" : "false" },
      });
      button.addEventListener("click", () => {
        this.modalTab = definition.id;
        this.modalView = "tab";
        this.detailEvent = null;
        this._renderModal();
      });
      tabs.appendChild(button);
    }
    head.appendChild(tabs);
    const close = el("button", {
      class: "debrief-close",
      text: "✕",
      dataset: { focusKey: "close" },
      attrs: { type: "button", title: "Close debrief (Esc)", "aria-label": "Close debrief" },
    });
    close.addEventListener("click", () => this.closeModal());
    this._nodes.modalClose = close;
    head.appendChild(close);
    return head;
  }

  _modalFooter() {
    return el("div", { class: "debrief-foot" }, [
      el("span", { class: "debrief-hint", text: "ESC close · SPACE play/pause · ←/→ scrub · T tasks · G gates" }),
      el("span", { class: "debrief-privacy", text: "read-only · local-only · no network" }),
    ]);
  }

  _renderTabContent(tab) {
    switch (tab) {
      case "handoffs":
        return this._handoffsTab();
      case "gates":
        return this._gatesTab();
      case "reviews":
        return this._reviewsTab();
      case "variety":
        return this._varietyTab();
      case "raw":
        return this._rawTab();
      case "help":
        return this._helpTab();
      case "tasks":
      default:
        return this._tasksTab();
    }
  }

  _section(title, note, children = []) {
    const section = el("section", { class: "debrief-section" });
    section.appendChild(el("div", { class: "section-head" }, [
      el("h3", { class: "section-title", text: title }),
      note ? el("span", { class: "section-note", text: String(note) }) : null,
    ]));
    section.appendChild(el("div", { class: "section-body" }, children));
    return section;
  }

  _stats(items) {
    const strip = el("div", { class: "stat-strip" });
    for (const item of items) {
      strip.appendChild(el("div", { class: `stat${item.tone ? ` tone-${item.tone}` : ""}` }, [
        el("div", { class: "stat-key", text: item.key }),
        el("div", { class: "stat-val", text: String(item.value) }),
        item.sub ? el("div", { class: "stat-sub", text: item.sub }) : null,
      ]));
    }
    return strip;
  }

  _emptyBlock(title, body) {
    return el("div", { class: "empty-block" }, [
      el("div", { class: "empty-block-glyph", text: "∅" }),
      el("div", { class: "empty-block-title", text: title }),
      el("div", { class: "empty-block-text", text: body }),
    ]);
  }

  _phaseChip(phase) {
    const name = visible(phase);
    if (!name) return null;
    return el("span", { class: `mc-chip phase-chip ${phaseToneClass(name)}`, text: name.toUpperCase() });
  }

  _severityChip(severity) {
    const name = visible(severity) || "info";
    return el("span", { class: `mc-chip sev-chip ${severityToneClass(name)}`, text: name.toUpperCase() });
  }

  /* --- debrief: tasks ---------------------------------------------------- */

  _tasksTab() {
    const model = this.model || {};
    const tasks = asArray(model.tasks);
    const metrics = model.metrics || {};
    const wrap = el("div");
    const countGroup = (id) => tasks.filter((task) => {
      const group = statusGroupFor(task.status);
      return Boolean(group) && group.id === id;
    }).length;
    wrap.appendChild(this._stats([
      { key: "TASKS", value: firstNumber(metrics.tasks, tasks.length) },
      { key: "ACTIVE", value: countGroup("active"), tone: countGroup("active") ? "ok" : "dim" },
      { key: "HANDOFF", value: countGroup("handoff"), tone: countGroup("handoff") ? "warn" : "dim" },
      { key: "COMPLETED", value: countGroup("completed"), tone: countGroup("completed") ? "ok" : "dim" },
      { key: "TEACHBACKS", value: firstNumber(metrics.teachbacks) },
      { key: "VARIETY TOTAL", value: firstNumber(metrics.variety_total) },
    ]));
    if (!tasks.length) {
      wrap.appendChild(this._emptyBlock("NO TASKS RECORDED", "Task metadata appears once the run dispatches work."));
      return wrap;
    }
    const groups = new Map();
    for (const group of STATUS_GROUPS) groups.set(group.id, []);
    const other = [];
    for (const task of tasks) {
      const group = statusGroupFor(task.status);
      if (group) groups.get(group.id).push(task);
      else other.push(task);
    }
    const board = el("div", { class: "task-board" });
    for (const group of STATUS_GROUPS) {
      const list = groups.get(group.id);
      if (list.length) board.appendChild(this._taskColumn(group.label, list, group.id));
    }
    if (other.length) board.appendChild(this._taskColumn("OTHER", other, "other"));
    wrap.appendChild(board);
    return wrap;
  }

  _taskColumn(label, tasks, groupId) {
    const sorted = tasks.slice().sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
    const column = el("div", { class: `task-col task-col-${safeClass(groupId)}` });
    column.appendChild(el("div", { class: "task-col-head" }, [
      el("span", { class: "task-col-label", text: label }),
      el("span", { class: "task-col-count", text: String(sorted.length) }),
    ]));
    for (const task of sorted) column.appendChild(this._taskCard(task));
    return column;
  }

  _taskCard(task) {
    const variety = task.variety && typeof task.variety === "object" ? task.variety : null;
    const total = variety
      ? firstNumber(
        variety.total,
        firstNumber(variety.novelty) + firstNumber(variety.scope) + firstNumber(variety.uncertainty) + firstNumber(variety.risk)
      )
      : 0;
    const flags = [];
    if (task.teachback) flags.push(el("span", { class: "task-flag is-ok", text: "TEACHBACK" }));
    if (task.handoff) flags.push(el("span", { class: "task-flag", text: "HANDOFF" }));
    const card = el("article", { class: "task-card" }, [
      el("div", { class: "task-card-top" }, [
        el("span", { class: "task-id", text: `#${visible(task.id) || "?"}` }),
        el("span", { class: "task-status", text: (visible(task.status) || "unknown").toUpperCase() }),
      ]),
      el("div", { class: "task-subject", text: visible(task.subject) || "(untitled task)" }),
      el("div", { class: "task-meta-row" }, [this._phaseChip(task.phase), ...flags]),
      el("div", { class: "task-time", text: `upd ${clockTime(task.updated_at) || "—"} · ${visible(task.owner) || "unowned"}${variety ? ` · variety ${total}/16` : ""}` }),
    ]);
    if (variety) {
      const bar = el("div", { class: "task-variety" });
      const fill = el("span");
      fill.style.width = `${clamp((total / 16) * 100, 0, 100)}%`;
      bar.appendChild(fill);
      card.appendChild(bar);
    }
    return card;
  }

  /* --- debrief: handoffs ------------------------------------------------- */

  _handoffsTab() {
    const model = this.model || {};
    const handoffs = asArray(model.handoffs);
    const metrics = model.metrics || {};
    const wrap = el("div");
    const complete = handoffs.filter((handoff) => handoff && handoff.schema_complete).length;
    wrap.appendChild(this._stats([
      { key: "HANDOFFS", value: firstNumber(metrics.handoffs, handoffs.length) },
      { key: "SCHEMA COMPLETE", value: `${complete} / ${handoffs.length}`, tone: handoffs.length && complete === handoffs.length ? "ok" : "warn" },
      { key: "CANONICAL FIELDS", value: "6" },
    ]));
    if (!handoffs.length) {
      wrap.appendChild(this._emptyBlock("NO HANDOFFS RECORDED", "A handoff appears when a specialist hands work on with the six canonical fields."));
      return wrap;
    }
    const grid = el("div", { class: "handoff-grid" });
    for (const handoff of handoffs) grid.appendChild(this._handoffCard(handoff));
    wrap.appendChild(grid);
    if (firstNumber(metrics.handoffs) > handoffs.length) {
      wrap.appendChild(el("p", { class: "section-note", text: `Showing ${handoffs.length} of ${firstNumber(metrics.handoffs)} recorded handoffs.` }));
    }
    return wrap;
  }

  _handoffCard(handoff) {
    const schemaComplete = Boolean(handoff && handoff.schema_complete);
    const fields = handoff && handoff.fields && typeof handoff.fields === "object" ? handoff.fields : {};
    const card = el("article", { class: `handoff-card ${schemaComplete ? "is-complete" : "is-incomplete"}` });
    card.appendChild(el("div", { class: "handoff-head" }, [
      el("div", { class: "handoff-who" }, [
        el("span", { class: "handoff-agent", text: visible(handoff && handoff.agent) || "unknown" }),
        el("span", {
          class: "handoff-task",
          text: handoff && handoff.task_id
            ? `TASK #${handoff.task_id}${handoff.task_subject ? ` · ${handoff.task_subject}` : ""}`
            : visible(handoff && handoff.task_subject) || "unattached",
        }),
      ]),
      el("div", { class: "handoff-when" }, [
        el("span", { class: `handoff-badge ${schemaComplete ? "is-ok" : "is-warn"}`, text: schemaComplete ? "SCHEMA COMPLETE" : "SCHEMA INCOMPLETE" }),
        el("span", { class: "handoff-time", text: clockTime(handoff && handoff.ts) || "—" }),
      ]),
    ]));
    const grid = el("div", { class: "handoff-fields" });
    for (const definition of HANDOFF_FIELDS) {
      const value = fieldValue(fields, definition);
      const wide = ["decisions", "reasoning_chain", "open_questions"].includes(definition.key);
      grid.appendChild(el("div", {
        class: `handoff-field${value ? "" : " is-missing"}${wide ? " is-wide" : ""}`,
      }, [
        el("span", { class: "hf-key", text: definition.label }),
        el("span", { class: "hf-val", text: value || "—" }),
      ]));
    }
    card.appendChild(grid);
    return card;
  }

  /* --- debrief: gates ---------------------------------------------------- */

  _gatesTab() {
    const model = this.model || {};
    const gates = model.gates && typeof model.gates === "object" ? model.gates : {};
    const metrics = model.metrics || {};
    const allowed = firstNumber(gates.allowed, metrics.gates_allowed);
    const advisory = firstNumber(gates.advisory, metrics.gates_advisory);
    const decisions = allowed + advisory;
    const share = decisions ? Math.round((advisory / decisions) * 1000) / 10 : 0;
    const failures = asArray(gates.health_failures);
    const wrap = el("div");
    wrap.appendChild(this._stats([
      { key: "GATE ALLOWED", value: allowed, tone: "ok" },
      { key: "GATE ADVISORY", value: advisory, tone: advisory ? "warn" : "ok" },
      { key: "ADVISORY SHARE", value: `${share}%`, sub: `${decisions} decisions`, tone: share > 5 ? "warn" : "ok" },
      { key: "HEALTH FAILURES", value: firstNumber(metrics.gate_health_failures, failures.length), tone: failures.length ? "danger" : "ok" },
    ]));

    const topRules = asArray(gates.top_rules);
    if (topRules.length) {
      const maxCount = Math.max(...topRules.map((rule) => firstNumber(rule.count)), 0);
      const rows = topRules.slice(0, 12).map((rule) => {
        const count = firstNumber(rule.count);
        const meter = el("span", { class: "gate-rule-bar" });
        const meterFill = el("span");
        meterFill.style.width = `${maxCount ? clamp((count / maxCount) * 100, 0, 100) : 0}%`;
        meter.appendChild(meterFill);
        return el("div", { class: "gate-rule" }, [
          el("span", { class: "gate-rule-name", text: visible(rule.rule) || "unknown rule" }),
          el("span", { class: "gate-rule-count", text: String(count) }),
          meter,
        ]);
      });
      wrap.appendChild(this._section("TOP ADVISORY RULES", `${topRules.length} rule${topRules.length === 1 ? "" : "s"}`, rows));
    } else {
      wrap.appendChild(this._section("TOP ADVISORY RULES", "no advisories", [
        this._emptyBlock("NO ADVISORY RULES", "Lifecycle gates did not flag any rules."),
      ]));
    }

    const byTool = gates.by_tool && typeof gates.by_tool === "object" ? gates.by_tool : {};
    const toolNames = Object.keys(byTool);
    if (toolNames.length) {
      const table = el("table", { class: "gate-table" });
      table.appendChild(el("thead", {}, [
        el("tr", {}, [
          el("th", { text: "TOOL" }),
          el("th", { text: "ALLOW" }),
          el("th", { text: "ADVISORY" }),
        ]),
      ]));
      const tbody = el("tbody");
      for (const tool of toolNames) {
        const entry = byTool[tool] && typeof byTool[tool] === "object" ? byTool[tool] : {};
        tbody.appendChild(el("tr", {}, [
          el("td", { text: tool }),
          el("td", { text: String(firstNumber(entry.allow)) }),
          el("td", { text: String(firstNumber(entry.advisory)) }),
        ]));
      }
      table.appendChild(tbody);
      wrap.appendChild(this._section("DECISIONS BY TOOL", `${toolNames.length} tool${toolNames.length === 1 ? "" : "s"}`, [table]));
    }

    const failureNodes = failures.slice(0, 20).map((failure) => el("div", { class: "gate-fail" }, [
      el("div", { class: "gate-fail-head" }, [
        el("span", { class: "gate-fail-hook", text: visible(failure.hook) || "unknown hook" }),
        el("span", { class: "gate-fail-stage", text: [visible(failure.stage), clockTime(failure.ts)].filter(Boolean).join(" · ") }),
      ]),
      el("div", { class: "gate-fail-error", text: visible(failure.error) || "(no error text)" }),
    ]));
    wrap.appendChild(this._section(
      "GATE HEALTH",
      failures.length ? `${failures.length} failure${failures.length === 1 ? "" : "s"}` : "all hooks healthy",
      failures.length ? failureNodes : [this._emptyBlock("NO HEALTH FAILURES", "Gate hooks reported cleanly for this run.")]
    ));

    const telemetry = gates.telemetry && typeof gates.telemetry === "object" ? gates.telemetry : {};
    wrap.appendChild(this._section("TELEMETRY GAPS", "best-effort sinks", [
      this._stats([
        {
          key: "EMIT SKIPPED",
          value: firstNumber(telemetry.journal_emit_skipped),
          sub: "journal_emit_skipped",
          tone: firstNumber(telemetry.journal_emit_skipped) ? "warn" : "ok",
        },
        {
          key: "REFUSAL DEGRADED",
          value: firstNumber(telemetry.handoff_refusal_degraded),
          sub: "handoff_refusal_degraded",
          tone: firstNumber(telemetry.handoff_refusal_degraded) ? "warn" : "ok",
        },
      ]),
    ]));

    const alerts = asArray(model.alerts);
    wrap.appendChild(this._section(
      "ALERTS",
      alerts.length ? `${alerts.length} recorded` : "none",
      alerts.length
        ? alerts.map((alert) => el("div", { class: "alert-item" }, [
          el("div", { class: "alert-line" }, [
            el("span", { class: `alert-level ${String(alert.level || "alert").toLowerCase() === "alert" ? "is-alert" : "is-warn"}`, text: String(alert.level || "alert").toUpperCase() }),
            el("span", { class: "alert-kind", text: String(alert.kind || "signal").toUpperCase() }),
            el("span", { class: "alert-cat", text: String(alert.category || "GENERAL").toUpperCase() }),
            el("span", { class: "alert-time", text: clockTime(alert.ts) || "—" }),
          ]),
          el("div", { class: "alert-summary", text: visible(alert.summary) || "(no summary)" }),
        ]))
        : [this._emptyBlock("NO ALERTS", "No algedonic signals or blockers were recorded.")]
    ));
    return wrap;
  }

  /* --- debrief: reviews + wrap-up ---------------------------------------- */

  _reviewsTab() {
    const model = this.model || {};
    const reviews = asArray(model.reviews);
    const findings = asArray(model.findings);
    const commits = asArray(model.commits);
    const wrapup = model.wrapup && typeof model.wrapup === "object" ? model.wrapup : null;
    const metrics = model.metrics || {};
    const wrap = el("div");
    const state = wrapup ? visible(wrapup.pr_state).toUpperCase() : "";
    const shipped = state === "MERGED";
    const stamp = el("div", {
      class: `pr-stamp${shipped ? " is-shipped" : state ? " is-open" : " is-muted"}`,
      text: shipped ? "SHIPPED" : state || "NO PR",
    });
    const prLine = wrapup
      ? `PR #${wrapup.pr_number === undefined || wrapup.pr_number === null ? "?" : wrapup.pr_number} · ${state || "UNKNOWN"} · ${clockTime(wrapup.ts) || "no timestamp"}`
      : firstNumber(metrics.prs_ready) ? "PR ready; no wrap-up event recorded" : "No pull request recorded";
    wrap.appendChild(el("div", { class: "pr-banner" }, [
      stamp,
      el("div", { class: "pr-info" }, [
        el("div", { class: "pr-title", text: shipped ? "WRAP-UP RECORDED · PR MERGED" : "PULL REQUEST / WRAP-UP" }),
        el("div", { class: "pr-sub", text: prLine }),
      ]),
    ]));

    const grouped = groupFindings(findings);
    const findingNodes = grouped.map((group) => el("div", { class: "finding-group" }, [
      el("div", { class: "finding-group-head" }, [
        this._severityChip(group.severity),
        el("span", { class: "finding-count", text: String(group.items.length) }),
      ]),
      ...group.items.map((finding) => el("div", { class: "finding" }, [
        el("div", { class: "finding-text", text: visible(finding.finding) || "(no finding text)" }),
        el("div", {
          class: "finding-meta",
          text: [visible(finding.reviewer) || "unknown reviewer", finding.task_id ? `task #${finding.task_id}` : "", clockTime(finding.ts)]
            .filter(Boolean)
            .join(" · "),
        }),
      ])),
    ]));
    wrap.appendChild(this._section(
      "REVIEW FINDINGS",
      findings.length ? `${findings.length} across ${grouped.length} severit${grouped.length === 1 ? "y" : "ies"}` : "none",
      findings.length ? findingNodes : [this._emptyBlock("NO FINDINGS", "Reviewers did not record any findings.")]
    ));

    wrap.appendChild(this._section(
      "REVIEWS",
      reviews.length ? `${reviews.length} recorded` : "none",
      reviews.length
        ? reviews.map((review) => el("div", { class: "review-row" }, [
          el("span", { class: "review-pr", text: review.pr_number ? `PR #${review.pr_number}` : "review" }),
          el("span", { class: "review-who", text: asArray(review.reviewers).join(", ") || "unassigned" }),
          el("span", { class: "review-time", text: clockTime(review.ts) || "—" }),
        ]))
        : [this._emptyBlock("NO REVIEWS", "No review dispatch was recorded for this run.")]
    ));

    wrap.appendChild(this._section(
      "COMMITS",
      commits.length ? `${commits.length} recorded` : "none",
      commits.length
        ? commits.slice().reverse().map((commit) => el("div", { class: "commit-row" }, [
          el("span", { class: "commit-sha", text: shortSha(commit.sha) || "-------" }),
          el("span", { class: "commit-msg", text: visible(commit.message) || "(no message)" }),
          this._phaseChip(commit.phase),
          el("span", { class: "commit-time", text: clockTime(commit.ts) || "—" }),
        ]))
        : [this._emptyBlock("NO COMMITS", "The run did not record any commits.")]
    ));

    wrap.appendChild(this._section("WRAP-UP", wrapup ? "session end" : "not recorded", [
      this._stats([
        { key: "PR READY", value: firstNumber(metrics.prs_ready) },
        { key: "TEACHBACKS", value: firstNumber(metrics.teachbacks) },
        { key: "CHECKPOINTS", value: firstNumber(metrics.checkpoints) },
        { key: "COMPACTIONS", value: firstNumber(metrics.compactions) },
        { key: "PHASES COMPLETE", value: firstNumber(metrics.phases_completed) },
        { key: "PHASES SKIPPED", value: firstNumber(metrics.phases_skipped) },
      ]),
    ]));
    return wrap;
  }

  /* --- debrief: variety -------------------------------------------------- */

  _varietyTab() {
    const model = this.model || {};
    const variety = model.variety && typeof model.variety === "object" ? model.variety : {};
    const feature = variety.feature && typeof variety.feature === "object" ? variety.feature : {};
    const latest = variety.latest && typeof variety.latest === "object" ? variety.latest : {};
    const history = asArray(variety.history);
    const metrics = model.metrics || {};
    const dims = VARIETY_DIMS.map((key) => ({ key, label: key.toUpperCase(), value: firstNumber(feature[key]) }));
    const sparkValues = history.map((point) => firstNumber(point && point.total));
    if (!sparkValues.length) sparkValues.push(firstNumber(latest.total, metrics.variety_total));
    const wrap = el("div");
    wrap.appendChild(this._stats([
      { key: "FEATURE VARIETY", value: firstNumber(feature.total), sub: "declared at launch" },
      { key: "LATEST VARIETY", value: firstNumber(latest.total), sub: "most recent signal" },
      { key: "METRICS TOTAL", value: firstNumber(metrics.variety_total) },
      { key: "HISTORY POINTS", value: history.length },
    ]));
    const grid = el("div", { class: "variety-grid" });

    const radarCanvas = el("canvas", {
      class: "chart-canvas radar-canvas",
      attrs: {
        role: "img",
        "aria-label": `Variety radar: ${VARIETY_DIMS.map((key) => `${key} ${firstNumber(feature[key])}`).join(", ")}`,
      },
    });
    const dimList = el("div", { class: "dim-list" });
    for (const key of VARIETY_DIMS) {
      const featureValue = firstNumber(feature[key]);
      const latestValue = firstNumber(latest[key]);
      const meter = el("span", { class: "dim-bar" });
      const meterFill = el("span");
      meterFill.style.width = `${clamp((featureValue / 4) * 100, 0, 100)}%`;
      meter.appendChild(meterFill);
      dimList.appendChild(el("div", { class: "dim-row" }, [
        el("span", { class: "dim-key", text: key.toUpperCase() }),
        meter,
        el("span", { class: "dim-value", text: `${featureValue} / 4` }),
        el("span", { class: "dim-latest", text: `latest ${latestValue}` }),
      ]));
    }
    grid.appendChild(el("div", { class: "chart-card" }, [
      el("div", { class: "chart-card-head" }, [
        el("span", { class: "chart-card-title", text: "FEATURE VARIETY" }),
        el("span", { class: "chart-card-note", text: "4 axes · 0–4" }),
      ]),
      el("div", { class: "chart-canvas-wrap" }, [radarCanvas]),
      dimList,
    ]));

    const sparkCanvas = el("canvas", {
      class: "chart-canvas spark-canvas",
      attrs: { role: "img", "aria-label": `Variety history totals: ${sparkValues.join(", ")}` },
    });
    let historyNode;
    if (history.length) {
      const table = el("table", { class: "history-table" });
      table.appendChild(el("thead", {}, [
        el("tr", {}, [
          el("th", { text: "SEQ" }),
          el("th", { text: "TOTAL" }),
          el("th", { text: "SOURCE" }),
          el("th", { text: "TIME" }),
        ]),
      ]));
      const tbody = el("tbody");
      for (const point of history.slice(-12).reverse()) {
        tbody.appendChild(el("tr", {}, [
          el("td", { text: String(point.seq === undefined ? "—" : point.seq) }),
          el("td", { text: String(firstNumber(point.total)) }),
          el("td", { text: visible(point.source) || "—" }),
          el("td", { text: clockTime(point.ts) || "—" }),
        ]));
      }
      table.appendChild(tbody);
      historyNode = table;
    } else {
      historyNode = this._emptyBlock("NO HISTORY", "Variety history appears as checkpoints and dispatches record it.");
    }
    grid.appendChild(el("div", { class: "chart-card" }, [
      el("div", { class: "chart-card-head" }, [
        el("span", { class: "chart-card-title", text: "VARIETY HISTORY" }),
        el("span", { class: "chart-card-note", text: `${history.length} signal${history.length === 1 ? "" : "s"}` }),
      ]),
      el("div", { class: "chart-canvas-wrap chart-canvas-short" }, [sparkCanvas]),
      historyNode,
    ]));
    wrap.appendChild(grid);
    this._chartJobs = [
      { type: "radar", canvas: radarCanvas, dims },
      { type: "spark", canvas: sparkCanvas, values: sparkValues },
    ];
    return wrap;
  }

  _drawCharts() {
    const jobs = this._chartJobs || [];
    for (const job of jobs) {
      try {
        if (job.type === "radar") drawRadar(job.canvas, job.dims, { max: 4 });
        else drawSparkline(job.canvas, job.values, { min: 0, max: 16 });
      } catch (_) {
        /* charts are decorative; never break the debrief */
      }
    }
  }

  /* --- debrief: raw ------------------------------------------------------ */

  _rawTab() {
    const model = this.model;
    const wrap = el("div");
    if (!model) {
      wrap.appendChild(this._emptyBlock("NO RUN LOADED", "Open a run to inspect its JSON snapshot."));
      return wrap;
    }
    const events = asArray(model.events);
    const end = clamp(this.cursor, 0, events.length);
    const visibleEvents = events.slice(0, end);
    const start = Math.max(0, visibleEvents.length - RAW_EVENT_LIMIT);
    const slice = visibleEvents.slice(start);
    let pretty = "";
    try {
      pretty = JSON.stringify(Object.assign({}, model, { events: slice }), null, 2) || "";
    } catch (_) {
      pretty = "// model could not be serialised";
    }
    const toolbar = el("div", { class: "raw-toolbar" }, [
      el("span", { class: "section-title", text: "RAW MODEL" }),
      el("span", { class: "section-note", text: `${visibleEvents.length} / ${events.length} events visible · showing last ${slice.length}` }),
    ]);
    if (typeof navigator !== "undefined" && navigator && navigator.clipboard && isFunction(navigator.clipboard.writeText)) {
      toolbar.appendChild(el("button", {
        class: "mini-btn",
        text: "COPY JSON",
        dataset: { focusKey: "copy" },
        attrs: { type: "button" },
        on: {
          click: () => {
            navigator.clipboard.writeText(pretty)
              .then(() => this.toast({ title: "Copied", body: "Raw JSON copied to the clipboard.", kind: "success" }))
              .catch(() => this.toast({ title: "Copy failed", body: "The browser blocked clipboard access.", kind: "warning" }));
          },
        },
      }));
    }
    wrap.appendChild(toolbar);
    const pre = el("pre", {
      class: "raw-pre",
      dataset: { focusKey: "raw-pre" },
      attrs: { tabindex: "0", "aria-label": "Raw JSON model snapshot" },
    });
    pre.textContent = pretty;
    wrap.appendChild(pre);
    wrap.appendChild(el("p", { class: "raw-note", text: "This is the model payload the page rendered, filtered to the current replay point. Read-only: nothing is written back." }));
    return wrap;
  }

  /* --- debrief: about / help -------------------------------------------- */

  _helpTab() {
    const model = this.model;
    const wrap = el("div");
    const grid = el("div", { class: "help-grid" });

    const keyList = el("div", { class: "help-list" });
    for (const [keys, description] of SHORTCUTS) {
      keyList.appendChild(el("div", { class: "help-row" }, [
        el("kbd", { class: "keycap", text: keys }),
        el("span", { class: "help-desc", text: description }),
      ]));
    }
    grid.appendChild(el("div", { class: "help-card" }, [
      el("h3", { class: "help-card-title", text: "KEYBOARD" }),
      keyList,
    ]));

    const legendList = el("div", { class: "help-list" });
    for (const [glyph, label, description] of LEGEND) {
      legendList.appendChild(el("div", { class: "legend-row" }, [
        el("span", { class: `legend-glyph cat-${safeClass(label.toLowerCase())}`, text: glyph }),
        el("span", { class: "legend-text" }, [
          el("span", { class: "legend-label", text: label }),
          el("span", { class: "legend-desc", text: description }),
        ]),
      ]));
    }
    grid.appendChild(el("div", { class: "help-card" }, [
      el("h3", { class: "help-card-title", text: "EVENT LEGEND" }),
      legendList,
    ]));

    const privacy = el("div", { class: "help-card help-card-wide" });
    privacy.appendChild(el("h3", { class: "help-card-title", text: "DATA & PRIVACY" }));
    privacy.appendChild(el("p", {
      class: "privacy-text",
      text: "Mission Control is a local, read-only observer. It renders session journals that PACT already wrote on this machine. The page makes no external network requests, loads no fonts or images from the internet, and never writes run data.",
    }));
    privacy.appendChild(el("p", {
      class: "privacy-text",
      text: "The Python server binds 127.0.0.1 only. The only things this page stores are your sound preference and achievement unlocks, kept in this browser's localStorage. The demo is synthetic sample data.",
    }));
    privacy.appendChild(el("p", {
      class: "privacy-text",
      text: "When a field is absent from a journal it is shown as a dash rather than inferred. Counts come from the run itself; the canvas is a view, not a source of truth.",
    }));
    grid.appendChild(privacy);
    wrap.appendChild(grid);

    const source = model && model.source ? model.source : {};
    const meta = model && model.meta ? model.meta : {};
    const aboutRows = [
      ["VIEWER", "PACT Mission Control"],
      ["VERSION", visible(model && model.version) || "—"],
      ["RUN", [visible(meta.project), visible(meta.team)].filter(Boolean).join(" · ") || "no run loaded"],
      ["SOURCE", [visible(source.kind), visible(source.root), visible(source.slug)].filter(Boolean).join(" · ") || "—"],
      ["SESSION", visible(source.session_id) || "—"],
      ["JOURNAL", visible(source.journal) || "—"],
      ["ETAG", visible(model && model.etag) || "—"],
      ["MODE", source.kind === "demo" ? "synthetic training simulation" : "local journal replay"],
    ];
    const aboutList = el("div", { class: "help-list" });
    for (const [key, value] of aboutRows) {
      aboutList.appendChild(el("div", { class: "help-row help-row-wide" }, [
        el("span", { class: "help-key", text: key }),
        el("span", { class: "help-value", text: value }),
      ]));
    }
    const about = el("div", { class: "help-card help-card-wide" });
    about.appendChild(el("h3", { class: "help-card-title", text: "ABOUT" }));
    about.appendChild(aboutList);
    wrap.appendChild(about);
    return wrap;
  }

  /* --- debrief: event detail -------------------------------------------- */

  _eventDetail(event, index) {
    const total = this._eventTotal();
    const wrap = el("div", { class: "event-detail" });
    wrap.appendChild(el("div", { class: "event-detail-head" }, [
      el("span", { class: "event-seq", text: `EVENT ${event.seq === undefined ? index : event.seq}` }),
      this._severityChip(event.severity),
      el("span", { class: "event-type", text: visible(event.type) || "unknown" }),
    ]));
    wrap.appendChild(el("h2", { class: "event-title", text: visible(event.title) || "(untitled event)" }));
    if (visible(event.detail)) {
      wrap.appendChild(el("p", { class: "event-detail-text", text: visible(event.detail) }));
    }
    const chips = el("div", { class: "event-chips" });
    const chipPairs = [
      ["TIME", clockTime(event.ts) || "—"],
      ["CATEGORY", visible(event.category) || "—"],
      ["PHASE", visible(event.phase) || "—"],
      ["AGENT", visible(event.agent) || "—"],
      ["TASK", event.task_id === null || event.task_id === undefined ? "—" : `#${event.task_id}`],
      ["SEQ", String(event.seq === undefined ? index : event.seq)],
    ];
    for (const [key, value] of chipPairs) {
      if (!value) continue;
      chips.appendChild(el("span", { class: "event-chip" }, [
        el("span", { class: "event-chip-key", text: key }),
        el("span", { class: "event-chip-val", text: value }),
      ]));
    }
    wrap.appendChild(chips);
    const actions = el("div", { class: "event-actions" });
    if (this.cb.onSeek) {
      actions.appendChild(el("button", {
        class: "mini-btn is-primary",
        text: "SEEK HERE",
        dataset: { focusKey: "seek" },
        attrs: { type: "button", title: "Move the recorder to this event" },
        on: {
          click: () => {
            this.cb.onSeek(clamp(index + 1, 1, Math.max(1, total)));
            this.closeModal();
          },
        },
      }));
    }
    actions.appendChild(el("button", {
      class: "mini-btn",
      text: "BACK TO COMMS",
      dataset: { focusKey: "back" },
      attrs: { type: "button" },
      on: { click: () => this.closeModal() },
    }));
    wrap.appendChild(actions);
    let rawText = "";
    try {
      rawText = JSON.stringify(event.raw !== undefined ? event.raw : event, null, 2) || "";
    } catch (_) {
      rawText = "// payload could not be serialised";
    }
    const pre = el("pre", { class: "raw-pre event-raw-pre" });
    pre.textContent = rawText;
    wrap.appendChild(this._section("RAW PAYLOAD", "exactly as received", [pre]));
    return wrap;
  }
}