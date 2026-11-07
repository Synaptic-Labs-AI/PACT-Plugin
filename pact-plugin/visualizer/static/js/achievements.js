/* PACT Mission Control — achievements derived from real run metrics only.
 * Nothing unlocks from watching the animation: each test reads counters the
 * backend derived from the session journal. Unlocks are persisted per session
 * in localStorage so re-evaluating the same model never announces twice, and
 * replay/demo reconciliation (live === false) records silently instead of
 * firing toasts for a historical run. */

const VERSION = 1;
const PREFIX = "pact-viz:achievements";

const CANONICAL_PHASES = ["PREPARE", "ARCHITECT", "CODE", "TEST"];

const ACHIEVEMENTS = [
  {
    id: "first_handoff",
    title: "First Contact",
    body: "A specialist produced the first canonical handoff.",
    test(model) {
      return metricNumber(model, "handoffs") >= 1 || modelList(model, "handoffs").length >= 1;
    },
  },
  {
    id: "gate_advisories_5",
    title: "Advisory Board",
    body: "Five or more lifecycle gate advisories have been recorded.",
    test(model) {
      const gates = model && model.gates ? model.gates : {};
      return number(gates.advisory) >= 5 || metricNumber(model, "gates_advisory") >= 5;
    },
  },
  {
    id: "findings_3",
    title: "Bug Hunter",
    body: "Three or more review findings landed on the run.",
    test(model) {
      return metricNumber(model, "findings") >= 3 || modelList(model, "findings").length >= 3;
    },
  },
  {
    id: "first_commit",
    title: "First Commit",
    body: "The run wrote its first commit.",
    test(model) {
      return metricNumber(model, "commits") >= 1 || modelList(model, "commits").length >= 1;
    },
  },
  {
    id: "pr_ready",
    title: "Launch Window",
    body: "A pull request reached the ready state.",
    test(model) {
      /* Only a recorded pr_ready counts. A wrap-up naming a PR number says
       * nothing about readiness: a closed or abandoned PR carries one too. */
      if (metricNumber(model, "prs_ready") >= 1) return true;
      const events = modelList(model, "events");
      return events.some((event) => event && event.type === "pr_ready");
    },
  },
  {
    id: "four_phases",
    title: "Four-Phase Orbit",
    body: "PREPARE, ARCHITECT, CODE and TEST all completed.",
    test(model) {
      /* The four canonical families, whatever extra phases the run also had;
       * a count of completed phases would accept any four. */
      const phases = model && model.phases ? model.phases : {};
      const completed = Array.isArray(phases.completed) ? phases.completed : [];
      return CANONICAL_PHASES.every((name) => completed.includes(name));
    },
  },
  {
    id: "first_alert",
    title: "Red Phone",
    body: "An algedonic alert reached mission control.",
    test(model) {
      return metricNumber(model, "alerts") >= 1 || modelList(model, "alerts").length >= 1;
    },
  },
  {
    id: "events_500",
    title: "Long Haul",
    body: "The journal passed five hundred events.",
    test(model) {
      const meta = model && model.meta ? model.meta : {};
      if (number(meta.event_count) >= 500) return true;
      if (metricNumber(model, "events_total") >= 500) return true;
      return modelList(model, "events").length >= 500;
    },
  },
  {
    id: "wrapup_merged",
    title: "Shipped",
    body: "Wrap-up recorded the pull request as merged.",
    test(model) {
      const wrapup = model && model.wrapup;
      if (!wrapup) return false;
      return String(wrapup.pr_state || "").toUpperCase() === "MERGED";
    },
  },
];

let cachedStorage;

function getStorage() {
  if (cachedStorage !== undefined) return cachedStorage;
  try {
    if (typeof window === "undefined" || !window.localStorage) throw new Error("no storage");
    cachedStorage = window.localStorage;
  } catch (_) {
    cachedStorage = null;
  }
  return cachedStorage;
}

const memoryStore = new Map();

function safeRead(key) {
  const storage = getStorage();
  if (storage) {
    try {
      const raw = storage.getItem(key);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_) {
      /* fall through to memory */
    }
  }
  return memoryStore.get(key) || null;
}

function safeWrite(key, value) {
  memoryStore.set(key, value);
  const storage = getStorage();
  if (!storage) return;
  try {
    storage.setItem(key, JSON.stringify(value));
  } catch (_) {
    /* quota/unavailable: memory fallback already holds it */
  }
}

function number(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function metricNumber(model, key) {
  const metrics = model && model.metrics ? model.metrics : {};
  const direct = metrics[key];
  if (typeof direct === "number") return direct;
  if (direct && typeof direct === "object") {
    if (typeof direct.total === "number") return direct.total;
    if (typeof direct.count === "number") return direct.count;
    if (Array.isArray(direct)) return direct.length;
  }
  return 0;
}

function modelList(model, key) {
  const value = model && model[key];
  return Array.isArray(value) ? value : [];
}

function sessionId(model) {
  const source = model && model.source ? model.source : {};
  const parts = [source.kind, source.root, source.slug, source.session_id].filter(Boolean);
  if (parts.length) return parts.join("::");
  const meta = model && model.meta ? model.meta : {};
  const fallback = [meta.project, meta.team, meta.started_at].filter(Boolean).join("::");
  return fallback || "anonymous-run";
}

function readStore(key) {
  const parsed = safeRead(key);
  if (!parsed || typeof parsed !== "object" || parsed.unlocked === null || typeof parsed.unlocked !== "object") {
    return { version: VERSION, unlocked: {} };
  }
  return { version: VERSION, unlocked: parsed.unlocked };
}

export function createAchievements(storageKey, onUnlock) {
  const notify = typeof onUnlock === "function" ? onUnlock : null;
  const prefix = String(storageKey || "pact-viz");
  let pendingTimers = [];
  let lastSessionKey = "";

  function cancelPending() {
    for (const timer of pendingTimers) clearTimeout(timer);
    pendingTimers = [];
  }

  /* A single unlock notifies immediately. A batch (for example the first look
   * at a finished demo run) is paced so every card is readable instead of
   * evicting its neighbours in the toast stack. */
  function announce(items) {
    if (!notify || !items.length) return;
    if (items.length === 1) {
      try {
        notify(items);
      } catch (_) {
        /* a throwing listener must not break polling */
      }
      return;
    }
    items.forEach((item, index) => {
      const timer = setTimeout(() => {
        try {
          notify([item]);
        } catch (_) {
          /* a throwing listener must not break polling */
        }
      }, index * 700);
      pendingTimers.push(timer);
    });
  }

  function evaluate(model, options = {}) {
    if (!model || typeof model !== "object") return [];
    const live = options && options.live !== undefined ? Boolean(options.live) : true;
    const key = `${prefix}:achievements:${sessionId(model)}`;
    if (key !== lastSessionKey) {
      cancelPending();
      lastSessionKey = key;
    }
    const store = readStore(key);
    const fresh = [];
    for (const achievement of ACHIEVEMENTS) {
      if (store.unlocked[achievement.id]) continue;
      let passed = false;
      try {
        passed = Boolean(achievement.test(model));
      } catch (_) {
        passed = false;
      }
      if (!passed) continue;
      const unlockedAt = Date.now();
      store.unlocked[achievement.id] = unlockedAt;
      fresh.push({
        id: achievement.id,
        title: achievement.title,
        body: achievement.body,
        kind: "achievement",
        unlocked_at: unlockedAt,
      });
    }
    if (fresh.length) {
      const trimmed = { version: VERSION, unlocked: {} };
      for (const achievement of ACHIEVEMENTS) {
        const unlockedAt = store.unlocked[achievement.id];
        if (unlockedAt) trimmed.unlocked[achievement.id] = unlockedAt;
      }
      safeWrite(key, trimmed);
      if (live) announce(fresh);
    }
    return fresh;
  }

  return { evaluate, achievements: ACHIEVEMENTS, keyPrefix: PREFIX };
}