/* Same-origin API client. In mock mode it reads ./dev-demo.json so the
 * front-end can be built without the Python server. No external requests. */

const params = new URLSearchParams(window.location.search);
export const USE_MOCK = params.get("mock") === "1";

let mockPromise = null;

function mockModel() {
  if (!mockPromise) {
    mockPromise = fetch("./dev-demo.json", { cache: "no-store" }).then((res) => {
      if (!res.ok) throw new Error(`mock data unavailable (${res.status})`);
      return res.json();
    });
  }
  return mockPromise;
}

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store", headers: { Accept: "application/json" } });
  let body = null;
  try {
    body = await res.json();
  } catch (err) {
    throw new Error(`bad JSON from ${url}`);
  }
  if (!res.ok) {
    throw new Error((body && body.error) || `request failed (${res.status})`);
  }
  return body;
}

function qs(obj) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(obj || {})) {
    if (value !== undefined && value !== null && value !== "") search.set(key, value);
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

export async function health() {
  if (USE_MOCK) {
    const model = await mockModel();
    return {
      ok: true,
      name: "pact-mission-control",
      version: model.version || "0.1.0",
      pid: 0,
      instance_id: "mock",
      uptime_s: 0,
      roots: [{ id: "mock", label: "mock", path: "mock", journals: 1, default: true }],
      /* `demo` is the actual mode; `demo_available` is the capability. The
       * mock preview is the demo, and the scripted demo is available. */
      demo: true,
      demo_available: true,
      mock: true,
    };
  }
  return getJSON("/api/health");
}

export async function sessions() {
  if (USE_MOCK) {
    const model = await mockModel();
    const meta = model.meta || {};
    return {
      ok: true,
      generated_at: new Date().toISOString(),
      default_root: "mock",
      roots: [{ id: "mock", label: "mock playground", path: "mock", journals: 1, default: true }],
      sessions: [
        {
          key: "mock::training::demo",
          root: "mock",
          slug: "training-simulation",
          session_id: "demo",
          project: meta.project || "PACT-Demo",
          project_dir: meta.project_dir || "",
          team: meta.team || "pact-demo",
          started_at: meta.started_at,
          updated_at: meta.ended_at || meta.started_at,
          size_bytes: 0,
          is_live: false,
          status: "ended",
          last_event_type: "session_end",
          last_phase: (model.phases && model.phases.current) || "",
          run_kind: meta.run_kind || "orchestrate",
        },
      ],
    };
  }
  return getJSON("/api/sessions");
}

export async function session({ root, slug, session_id, since } = {}) {
  if (USE_MOCK) {
    const model = await mockModel();
    if (since && model.etag && since === model.etag) {
      return { ok: true, unchanged: true, etag: model.etag };
    }
    return model;
  }
  return getJSON(`/api/session${qs({ root, slug, session_id, since })}`);
}

export async function demo({ since } = {}) {
  if (USE_MOCK) {
    const model = await mockModel();
    if (since && model.etag && since === model.etag) {
      return { ok: true, unchanged: true, etag: model.etag };
    }
    return model;
  }
  return getJSON(`/api/demo${qs({ since })}`);
}
