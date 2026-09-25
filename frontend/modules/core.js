// Shared browser primitives. Domain modules import these instead of reaching through app.js.
export const $ = (selector) => document.querySelector(selector);

export const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of children) node.append(child);
  return node;
};

export const svgIcon = (id) => `<svg class="icon"><use href="#${id}" /></svg>`;

export const state = {
  go2rtc: "",
  gridHdMax: 0,
  cameras: [],
  view: "grid",
  canManage: false,
  selected: null,
  rec: { cameraId: "", from: "", to: "", page: 0, pageSize: 50 },
  candidates: [],
  camFilter: "all",
  provisioning: null,
};

let unauthorizedHandler = () => {};
let sessionEpoch = 0;
const sessionCleanups = new Set();
export function onSessionEnd(cleanup) {
  sessionCleanups.add(cleanup);
  return () => sessionCleanups.delete(cleanup);
}
export function endSession() {
  sessionEpoch++;
  for (const cleanup of [...sessionCleanups]) {
    try { Promise.resolve(cleanup()).catch(() => {}); } catch { /* Continue other cleanup. */ }
  }
  sessionCleanups.clear();
  state.canManage = false;
}

export function onUnauthorized(handler) {
  unauthorizedHandler = handler;
}

export async function api(path, opts = {}) {
  const epoch = sessionEpoch;
  const response = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (epoch !== sessionEpoch) throw new DOMException("Stale session", "AbortError");
  if (response.status === 401) {
    unauthorizedHandler();
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body.detail || response.statusText);
    error.status = response.status;
    throw error;
  }
  const body = response.status === 204 ? null : await response.json();
  if (epoch !== sessionEpoch) throw new DOMException("Stale session", "AbortError");
  return body;
}
