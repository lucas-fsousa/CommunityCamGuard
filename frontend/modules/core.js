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
  selected: null,
  rec: { mac: "", from: "", to: "", page: 0, pageSize: 50 },
  candidates: [],
  camFilter: "all",
  provisioning: null,
};

let unauthorizedHandler = () => {};

export function onUnauthorized(handler) {
  unauthorizedHandler = handler;
}

export async function api(path, opts = {}) {
  const response = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (response.status === 401) {
    unauthorizedHandler();
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || response.statusText);
  }
  return response.status === 204 ? null : response.json();
}
