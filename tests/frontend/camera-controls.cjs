// Small DOM contract harness: no browser, streams, dependencies or real network access.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.dataset = {}; this.events = {};
    this.style = {}; this.inert = false; this.attributes = {}; this.disabled = false;
    this.classList = {
      add: (name) => { this.className = [...new Set((this.className || "").split(" ").concat(name))].join(" "); },
      remove: (name) => { this.className = (this.className || "").split(" ").filter((value) => value !== name).join(" "); },
      contains: (name) => (this.className || "").split(" ").includes(name),
    };
  }
  append(...nodes) {
    for (const node of nodes) { node.remove?.(); node.parent = this; this.children.push(node); }
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
    this.parent = null;
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, fn) { (this.events[key] ||= []).push(fn); }
  dispatch(key, event = {}) {
    return Promise.all((this.events[key] || []).map((fn) => fn({ stopPropagation() {}, preventDefault() {}, ...event })));
  }
  click() { if (!this.disabled) return this.dispatch("click"); }
  focus() { document.activeElement = this; }
  get options() { return this.children; }
  get isConnected() { return this === document.body || Boolean(this.parent?.isConnected); }
  querySelectorAll() {
    return walk(this).filter((node) => ["button", "select"].includes(node.tagName) && !node.disabled);
  }
  querySelector() { return this.querySelectorAll()[0] || null; }
}
function walk(root) { return root.children.flatMap((node) => [node, ...walk(node)]); }
const el = (tag, props = {}, ...children) => {
  const node = Object.assign(new Element(tag), props); node.append(...children); return node;
};
const nav = el("button");
global.document = { body: el("body"), querySelector: () => nav, activeElement: null };
global.window = { confirm: () => true };
let dialogObserver;
global.MutationObserver = class {
  constructor(callback) { this.callback = callback; dialogObserver = this; }
  observe() {}
  disconnect() { this.disconnected = true; }
};
const state = { cameras: [], rec: {} };
const requests = [];
let pending = null;
const api = async (...args) => { requests.push(args); return pending ? await pending : {}; };
const t = (key) => key;
function load(file, bindings, exported) {
  const source = fs.readFileSync(path.join(__dirname, "../../frontend/modules", file), "utf8")
    .replace(/^import .*;\n/gm, "").replace(/export function /g, "function ");
  return new Function(...Object.keys(bindings), source + `\nreturn ${exported};`)(...Object.values(bindings));
}
const controlWidgets = load("camera-control-actions.js", { el, api, t }, "controlWidgets");
let audioButtons = 0;
const audioButton = () => { audioButtons++; return el("button"); };
const cameraControls = load("camera-controls.js", {
  el, state, t, controlWidgets, audioMessageButton: audioButton, pushToTalkButton: audioButton,
}, "cameraControls");

(async () => {
  const cam = { id: "cam_test", name: "Test", controls: {}, capabilities: {} };
  const app = el("main"), alreadyInert = el("aside", { inert: true });
  document.body.style.overflow = "auto";
  document.body.append(app, alreadyInert);
  const trigger = cameraControls(cam);
  app.append(trigger);
  assert.equal(requests.length, 0);
  await trigger.click();
  let shell = document.body.children.at(-1);
  assert.equal(walk(shell).filter((item) => item.tagName === "h3").length, 5);
  assert.equal(walk(shell).filter((item) => item.disabled).length, 11);
  assert.equal(audioButtons, 0);
  assert.equal(requests.length, 0);
  assert.equal(app.inert, true);
  assert.equal(document.body.style.overflow, "hidden");
  await shell.click();
  assert.equal(shell.isConnected, true); // backdrop cannot dismiss
  const close = walk(shell).find((item) => item.textContent === "×");
  assert.equal(document.activeElement, close);
  const panel = shell.children[0];
  const last = panel.querySelectorAll().at(-1);
  let prevented = false;
  await panel.dispatch("keydown", { key: "Tab", shiftKey: true, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(document.activeElement, last);
  await panel.dispatch("keydown", { key: "Tab", shiftKey: false });
  assert.equal(document.activeElement, close);
  const nestedClose = el("button");
  const nestedDialog = el("div", { className: "modal" }, nestedClose);
  document.body.append(nestedDialog);
  dialogObserver.callback();
  assert.equal(shell.children[0].inert, true);
  assert.equal(document.activeElement, nestedClose);
  nestedDialog.remove();
  dialogObserver.callback();
  assert.equal(shell.children[0].inert, false);
  assert.equal(document.activeElement, close);
  await close.click();
  assert.equal(dialogObserver.disconnected, true);
  assert.equal(shell.isConnected, false);
  assert.equal(app.inert, false);
  assert.equal(alreadyInert.inert, true);
  assert.equal(document.body.style.overflow, "auto");
  assert.equal(document.activeElement, trigger);

  // Reopening uses a fresh catalogue, not the original tile's capabilities.
  state.cameras = [{ ...cam, audio_messages: true, audio_streams: true, controls: {
    speaker_volume: { kind: "choice", writable: true, options: ["50", "75"] },
    night_vision: { kind: "choice", writable: true, options: ["automatic"] },
  } }];
  await trigger.click();
  shell = document.body.children.at(-1);
  assert.equal(audioButtons, 2);
  assert.equal(requests.length, 0);
  const volume = walk(shell).find((item) => item.dataset.controlKey === "speaker_volume");
  assert.deepEqual(volume.options.map((item) => item.value), ["", "50", "75"]);
  const night = walk(shell).find((item) => item.dataset.controlKey === "night_vision");
  assert.deepEqual(night.options.map((item) => item.value), ["", "automatic"]);
  volume.value = "50";
  let complete;
  pending = new Promise((resolve) => { complete = resolve; });
  const applying = volume.dispatch("change");
  const status = walk(shell).find((item) => item.classList.contains("camera-control-status"));
  assert.equal(status.classList.contains("applying"), true);
  assert.equal(status.textContent, "control.applying");
  assert.equal(volume.disabled, true);
  complete({});
  await applying;
  pending = null;
  assert.equal(status.classList.contains("applying"), false);
  assert.equal(volume.disabled, false);
  assert.equal(requests.length, 1);
  assert.equal(requests[0][0], "/cameras/cam_test/controls/speaker_volume");
  assert.equal(requests[0][1].body, '{"value":50}');
  let fail;
  pending = new Promise((resolve, reject) => { fail = reject; });
  const failing = volume.dispatch("change");
  fail(new Error("test failure"));
  await failing;
  pending = null;
  assert.equal(status.classList.contains("applying"), false);
  assert.equal(status.classList.contains("error"), true);
  assert.equal(volume.disabled, false);
  let navigated = false;
  nav.addEventListener("click", () => { navigated = true; });
  await walk(shell).find((item) => item.textContent === "panel.openRecordings").click();
  assert.equal(state.rec.cameraId, cam.id);
  assert.equal(state.rec.page, 0);
  assert.equal(navigated, true);
  assert.equal(app.inert, false);
  assert.equal(shell.isConnected, false);
  console.log("Camera panel contracts passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
