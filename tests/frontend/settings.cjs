const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../../frontend/modules/settings-preferences.js"), "utf8")
  .replace(/^import .*;$/gm, "").replaceAll("export function", "function");
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
class Element {
  constructor(tag, props) { this.tag = tag; this.children = []; this.events = {}; Object.assign(this, props); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { this.events[name] = fn; }
  reportValidity() { return true; }
  fire(name, extra = {}) { return this.events[name]?.({ preventDefault() {}, ...extra }); }
  focus() { this.focused = true; }
}
const walk = e => [e, ...e.children.flatMap(walk)];
function harness(canManage = true) {
  const requests = [], timers = new Map(); let id = 0;
  const state = { canManage, gridHdMax: 0 };
  const el = (tag, props = {}, ...children) => { const e = new Element(tag, props); e.append(...children); return e; };
  const api = (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
  const controller = new Function("api", "el", "state", "t", "setTimeout", "clearTimeout", "renderAccessKeys",
    source + "; let cleanup; return {renderSettings: container => { cleanup = renderPreferences(container); }, stopSettings: () => cleanup?.()};")(api, el, state, key => key,
    fn => { timers.set(++id, fn); return id; }, n => timers.delete(n), () => () => {});
  const container = el("div"); controller.renderSettings(container);
  const find = predicate => walk(container).find(predicate);
  return { ...controller, container, requests, timers, state, find };
}
const snapshot = (revision = 0, overrides = {}) => ({ revision, overrides,
  values: { grid_hd_max_cameras: 0, playback_cache_mb: 2048, ...overrides } });
async function loaded() {
  const h = harness(); h.requests[0].resolve(snapshot()); await flush(); return h;
}
function edit(h, name, value) {
  const field = h.find(e => e.children.some(child => child.id === "setting-" + name));
  const checkbox = walk(field).find(e => e.type === "checkbox");
  checkbox.checked = false; checkbox.fire("change");
  const input = walk(field).find(e => e.type === "number");
  input.value = value; input.fire("input");
  return checkbox;
}
(async () => {
  {
    const code = fs.readFileSync(path.join(__dirname, "../../frontend/modules/settings.js"), "utf8")
      .replace(/^import .*;$/gm, "").replaceAll("export function", "function");
    const root = new Element("div"), counts = { preferences: 0, access: 0, cleaned: 0 };
    const el = (tag, props = {}, ...children) => { const node = new Element(tag, props); node.append(...children); return node; };
    let listening = false;
    const media = { matches: false, addEventListener() { listening = true; }, removeEventListener() { listening = false; } };
    const controller = new Function("el", "state", "t", "window", "renderPreferences", "renderAccessKeys",
      code + ";return {renderSettings, stopSettings};")(el, { canManage: true }, key => key,
      { matchMedia: () => media }, () => { counts.preferences++; return () => counts.cleaned++; },
      () => { counts.access++; return () => counts.cleaned++; });
    controller.renderSettings(root);
    const find = id => walk(root).find(node => node.id === id);
    const settings = find("settings-tab-preferences"), access = find("settings-tab-access");
    assert.equal(counts.preferences, 1); assert.equal(counts.access, 0);
    assert.equal(settings["aria-selected"], "true"); assert(find("settings-panel-access").hidden);
    settings.fire("keydown", { key: "ArrowDown" });
    assert(access.focused); assert.equal(counts.access, 1); assert(find("settings-panel-preferences").hidden);
    access.fire("click"); assert.equal(counts.access, 1);
    access.fire("keydown", { key: "Home" });
    assert.equal(counts.cleaned, 1); assert.equal(counts.preferences, 1);
    settings.fire("keydown", { key: "End" }); assert.equal(counts.access, 2);
    controller.stopSettings(); assert.equal(counts.cleaned, 3); assert(!listening);
    settings.fire("click"); assert.equal(counts.preferences, 1);
  }
  {
    const h = harness(false);
    assert.equal(h.requests.length, 0); assert(!h.find(e => e.tag === "form"));
  }
  {
    const h = await loaded(); const save = h.find(e => e.type === "submit");
    assert(save.disabled); assert(h.find(e => e.type === "number").disabled);
    edit(h, "grid_hd_max_cameras", "3"); assert(!save.disabled);
    const form = h.find(e => e.tag === "form"); form.fire("submit"); form.fire("submit");
    assert.equal(h.requests.length, 2); assert(save.disabled);
    assert.deepEqual(JSON.parse(h.requests[1].options.body), { revision: 0, changes: { grid_hd_max_cameras: 3 } });
    h.requests[1].resolve(snapshot(1, { grid_hd_max_cameras: 3 })); await flush();
    assert.equal(h.state.gridHdMax, 3); assert(save.disabled);
    const checkbox = edit(h, "grid_hd_max_cameras", "3"); checkbox.checked = true; checkbox.fire("change");
    form.fire("submit");
    assert.deepEqual(JSON.parse(h.requests[2].options.body), { revision: 1, changes: { grid_hd_max_cameras: null } });
    h.stopSettings(); h.requests[2].resolve(snapshot(2)); await flush(); assert.equal(h.timers.size, 0);
  }
  for (const status of [409, 403, 503]) {
    const h = await loaded(); edit(h, "playback_cache_mb", "512");
    h.find(e => e.tag === "form").fire("submit"); h.requests[1].reject({ status }); await flush();
    assert(h.find(e => e.type === "submit").disabled);
    h.find(e => e.tag === "form").fire("submit"); assert.equal(h.requests.length, 2);
    h.find(e => e.type === "button").fire("click");
    assert.equal(h.requests.length, 3); h.requests[2].resolve(snapshot(2)); await flush();
    assert.equal(h.find(e => e.type === "number").value, "0"); h.stopSettings();
  }
  {
    const h = harness(); h.stopSettings(); assert(h.requests[0].options.signal.aborted);
    h.requests[0].resolve(snapshot()); await flush();
    assert(!h.find(e => e.type === "number")); assert.equal(h.timers.size, 0);
  }
  {
    const h = harness(); h.requests[0].reject({ status: 503 }); await flush();
    assert(h.find(e => e.type === "submit").disabled);
    assert(!h.find(e => e.type === "button").disabled); h.stopSettings();
  }
  console.log("Settings view contracts passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
