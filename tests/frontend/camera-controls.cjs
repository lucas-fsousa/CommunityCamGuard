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
  get selectedIndex() { return this.children.findIndex((node) => node.selected); }
  set selectedIndex(index) { this.children.forEach((node, i) => { node.selected = i === index; }); }
  get value() { return this.tagName === "select" ? this.children[this.selectedIndex]?.value || "" : this._value; }
  set value(value) {
    if (this.tagName === "select") this.selectedIndex = this.children.findIndex((node) => node.value === value);
    else this._value = value;
  }
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
const notifications = [];
function load(file, bindings, exported) {
  bindings = { notify: (message, options) => notifications.push({ message, options }), ...bindings };
  const source = fs.readFileSync(path.join(__dirname, "../../frontend/modules", file), "utf8")
    .replace(/^import .*;\n/gm, "").replace(/export (async )?function /g, "$1function ")
    .replace(/export const /g, "const ");
  return new Function(...Object.keys(bindings), source + `\nreturn ${exported};`)(...Object.values(bindings));
}
const controlWidgets = load("camera-control-actions.js", { el, api, t }, "controlWidgets");
{
  const selected = [];
  const ptzControls = load("live-cameras.js", {
    document: { addEventListener() {} },
    finitePtzControls: (camera) => { selected.push(camera.id); return "shared-pad"; },
  }, "ptzControls");
  for (const camera of [{ id: "native", ptz_interaction: "step" },
    { id: "onvif", ptz_interaction: "hold" }, { id: "legacy" }]) {
    assert.equal(ptzControls(camera), "shared-pad");
  }
  assert.deepEqual(selected, ["native", "onvif", "legacy"]);
}
{
  const timers = new Map();
  let nextTimer = 0;
  const notify = load("notifications.js", {
    el, t,
    setTimeout: (fn, delay) => { assert.equal(delay, 5000); timers.set(++nextTimer, fn); return nextTimer; },
    clearTimeout: (id) => timers.delete(id),
  }, "notify");
  const host = el("section"); document.body.append(host);
  const anchor = { closest: () => host };
  notify("<b>plain text</b>", { anchor });
  const region = host.children[0];
  assert.equal(region.children[0].children[0].textContent, "<b>plain text</b>");
  assert.equal(region.children[0].children[1].attributes["aria-label"], "notification.dismiss");
  region.children[0].children[1].click();
  assert.equal(timers.size, 0); assert.equal(host.children.length, 0);
  for (let i = 0; i < 4; i++) notify(`message ${i}`, { anchor });
  assert.equal(host.children[0].children.length, 3); assert.equal(timers.size, 3);
  for (const callback of [...timers.values()]) callback();
  assert.equal(timers.size, 0); assert.equal(host.children.length, 0);
  host.remove();
  console.log("Toast contracts passed");
}
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
    night_vision: { kind: "choice", writable: true, options: ["automatic", "daytime"] },
    siren_pulse: { kind: "action", writable: true, options: ["2"] },
  } }];
  await trigger.click();
  shell = document.body.children.at(-1);
  assert.equal(audioButtons, 2);
  assert.equal(requests.length, 0);
  const volume = walk(shell).find((item) => item.dataset.controlKey === "speaker_volume");
  assert.deepEqual(volume.options.map((item) => item.value), ["", "50", "75"]);
  const night = walk(shell).find((item) => item.dataset.controlKey === "night_vision");
  assert.deepEqual(night.options.map((item) => item.value), ["", "automatic", "daytime"]);
  volume.value = "50";
  let complete;
  pending = new Promise((resolve) => { complete = resolve; });
  const applying = volume.dispatch("change");
  const status = walk(shell).find((item) => item.classList.contains("camera-control-status"));
  assert.equal(status.classList.contains("applying"), true);
  assert.equal(status.textContent, "control.applying");
  assert.equal(volume.disabled, true);
  complete({ verified: true, value: 50 });
  await applying;
  pending = null;
  assert.equal(status.classList.contains("applying"), false);
  assert.equal(volume.disabled, false);
  assert.equal(volume.value, "50");
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
  assert.equal(volume.value, "");
  night.value = "daytime";
  pending = Promise.resolve({ verified: true, value: "daytime" });
  await night.dispatch("change");
  assert.equal(night.value, "daytime");
  assert.equal(status.textContent, "");
  assert.equal(notifications.at(-1).message, "control.applied");
  for (const result of [{ verified: false, value: "automatic" }, { verified: true, value: "daytime" }, {}]) {
    night.value = "automatic";
    pending = Promise.resolve(result);
    await night.dispatch("change");
    assert.equal(night.value, "");
    assert.equal(status.classList.contains("error"), true);
    assert.equal(status.classList.contains("applying"), false);
    assert.equal(night.disabled, false);
  }
  const siren = walk(shell).find((item) => item.dataset.controlKey === "siren_pulse");
  siren.value = "2";
  pending = Promise.resolve({ verified: true, value: 2 });
  await siren.dispatch("change");
  assert.equal(siren.value, "");
  pending = null;
  let navigated = false;
  // Nested configuration dialogs must not mistake HTTP success for confirmed state.
  const choiceDialog = load("camera-control-actions.js", { el, api, t }, "openDynamicChoice");
  const scheduleDialog = load("camera-control-actions.js", { el, api, t }, "openProtectionSchedule");
  const dialogTrigger = el("button");
  document.body.append(dialogTrigger);
  const feedback = el("small");
  const strings = { loading: "loading", empty: "empty", placeholder: "choose", apply: "apply",
    title: "title", hint: "hint", required: "required" };
  for (const response of [new Error("network"), {}, { verified: false, value: "tone" },
    { verified: true, value: "other" }, { verified: true, value: "tone" }]) {
    pending = Promise.resolve({ options: [{ value: "tone", label: "Tone" }] });
    await choiceDialog(cam, "alarm_voice", feedback, dialogTrigger, strings);
    const overlay = document.body.children.at(-1);
    const select = walk(overlay).find((node) => node.tagName === "select");
    const apply = walk(overlay).find((node) => node.textContent === "apply");
    select.value = "tone";
    let finish;
    pending = new Promise((resolve, reject) => { finish = (value) => value instanceof Error ? reject(value) : resolve(value); });
    const writing = apply.click();
    assert.equal(select.disabled, true);
    const beforeDuplicate = requests.length;
    await apply.dispatch("click");
    assert.equal(requests.length, beforeDuplicate);
    finish(response);
    await writing;
    assert.equal(select.disabled, false);
    assert.equal(apply.disabled, false);
    assert.equal(overlay.isConnected, !(response.verified === true && response.value === "tone"));
    if (overlay.isConnected) {
      assert(walk(overlay).some((node) => node.classList.contains("error")));
      assert(!walk(overlay).some((node) => node.classList.contains("applying")));
      await walk(overlay).find((node) => node.textContent === "×").click();
    }
  }
  const schedule = { start: "08:00", end: "18:00", weekdays: ["mon", "tue"] };
  for (const response of [new Error("network"), {}, { verified: false, value: schedule },
    { verified: true, value: { ...schedule, end: "19:00" } },
    { verified: true, value: { ...schedule, weekdays: ["mon", "mon"] } },
    { verified: true, value: { ...schedule, weekdays: ["tue", "mon"] } }]) {
    pending = Promise.resolve({ value: schedule });
    await scheduleDialog(cam, feedback, dialogTrigger);
    const overlay = document.body.children.at(-1);
    const inputs = walk(overlay).filter((node) => node.tagName === "input");
    const save = walk(overlay).find((node) => node.textContent === "control.scheduleSave");
    let finish;
    pending = new Promise((resolve, reject) => { finish = (value) => value instanceof Error ? reject(value) : resolve(value); });
    const writing = save.click();
    assert(inputs.every((input) => input.disabled));
    const beforeDuplicate = requests.length;
    await save.dispatch("click");
    assert.equal(requests.length, beforeDuplicate);
    finish(response);
    await writing;
    assert(inputs.every((input) => !input.disabled));
    assert.equal(save.disabled, false);
    const success = response.value?.weekdays[0] === "tue";
    assert.equal(overlay.isConnected, !success);
    if (overlay.isConnected) {
      assert(walk(overlay).some((node) => node.classList.contains("error")));
      assert(!walk(overlay).some((node) => node.classList.contains("applying")));
      await walk(overlay).find((node) => node.textContent === "×").click();
    }
  }
  dialogTrigger.remove();
  pending = null;
  nav.addEventListener("click", () => { navigated = true; });
  await walk(shell).find((item) => item.textContent === "panel.openRecordings").click();
  assert.equal(state.rec.cameraId, cam.id);
  assert.equal(state.rec.page, 0);
  assert.equal(navigated, true);
  assert.equal(app.inert, false);
  assert.equal(shell.isConnected, false);
  console.log("Camera panel contracts passed");
  const finitePtzControls = load("step-ptz.js", { el, api, t }, "finitePtzControls");
  const arrows = finitePtzControls(cam);
  const arrowButtons = arrows.children.filter((node) => node.tagName === "button");
  const ptzStatus = arrows.children.at(-1);
  let finishStep;
  pending = new Promise((resolve) => { finishStep = resolve; });
  const beforeStep = requests.length;
  const moving = arrowButtons[3].click();
  assert(arrowButtons.every((node) => !node.disabled)); // keep pointer tracking active
  assert.equal(ptzStatus.textContent, "");
  assert.equal(ptzStatus.className, "ptz-feedback");
  assert(arrowButtons[3].classList.contains("ptz-pending"));
  assert.equal(arrowButtons[3].attributes["aria-busy"], "true");
  await arrowButtons[0].dispatch("click");
  assert.equal(requests.length, beforeStep + 1);
  assert.equal(requests.at(-1)[0], `/cameras/${encodeURIComponent(cam.id)}/ptz`);
  assert.equal(requests.at(-1)[1].body, '{"action":"step","direction":"right"}');
  finishStep({ ok: true });
  await moving;
  assert(arrowButtons.every((node) => !node.disabled));
  assert.equal(ptzStatus.textContent, "ptz.busy"); // rejected click was not silently lost
  assert(!arrowButtons[3].classList.contains("ptz-pending"));
  assert.equal(arrowButtons[3].attributes["aria-busy"], "false");
  pending = Promise.resolve({ ok: false });
  await arrowButtons[3].click();
  assert(ptzStatus.classList.contains("error"));
  assert(!arrowButtons[3].classList.contains("ptz-pending"));
  assert(arrowButtons.every((node) => !node.disabled));
  assert(!arrowButtons.some((node) => node.events.pointerdown));
  assert(arrows.events.pointerdown && arrows.events.pointermove);
  document.body.append(arrows);
  arrows.getBoundingClientRect = () => ({ left: 0, top: 0, width: 100, height: 100 });
  const beforeDrag = requests.length;
  pending = new Promise((resolve) => { finishStep = resolve; });
  await arrows.dispatch("pointerdown", { pointerId: 1, button: 0, clientX: 50, clientY: 50, target: arrowButtons[4] });
  assert.equal(requests.length, beforeDrag);
  await arrows.dispatch("pointermove", { pointerId: 1, clientX: 90, clientY: 50 });
  assert.equal(requests.length, beforeDrag + 1);
  await arrows.dispatch("pointermove", { pointerId: 1, clientX: 50, clientY: 10 });
  assert.equal(requests.length, beforeDrag + 1); // direction change is not queued
  await arrows.dispatch("pointerup", { pointerId: 1 });
  assert.equal(requests.length, beforeDrag + 2);
  assert.equal(requests.at(-1)[0], `/cameras/${encodeURIComponent(cam.id)}/ptz`);
  assert.equal(requests.at(-1)[1].body, '{"action":"stop"}');
  finishStep({ ok: true });
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(requests.length, beforeDrag + 2); // release prevents another direction
  arrows.remove();
  pending = null;
  console.log("Finite PTZ contracts passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
