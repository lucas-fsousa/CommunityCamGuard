// No microphone, network or camera access: direct intercom UI/lifetime contracts.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
class Element {
  constructor(tag, props = {}) {
    Object.assign(this, props); this.tagName = tag; this.children = []; this.events = {};
    this.style = {}; this.attributes = {}; this.dataset = {};
    this.classList = {
      add: value => this.className = `${this.className || ""} ${value}`,
      remove: value => this.className = (this.className || "").split(" ").filter(v => v !== value).join(" "),
      toggle: (value, on) => this.classList[on ? "add" : "remove"](value),
    };
  }
  append(...nodes) { nodes.forEach(node => { node.remove?.(); node.parent = this; this.children.push(node); }); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); this.parent = null; }
  setAttribute(k, v) { this.attributes[k] = v; }
  addEventListener(k, fn) { (this.events[k] ||= []).push(fn); }
  async dispatch(k, props = {}) {
    for (const fn of this.events[k] || []) await fn({ preventDefault() {}, stopPropagation() {}, ...props });
  }
  get isConnected() { return this === document.body || Boolean(this.parent?.isConnected); }
  focus() { document.activeElement = this; }
}
const el = (tag, props = {}, ...nodes) => { const node = new Element(tag, props); node.append(...nodes); return node; };
const t = key => key;
function load(file, bindings, names) {
  const code = fs.readFileSync(path.join(__dirname, "../../frontend/modules", file), "utf8")
    .replace(/^import .*;\n/gm, "").replace(/export (async )?function /g, "$1function ");
  return new Function(...Object.keys(bindings), code + `\nreturn ${names};`)(...Object.values(bindings));
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const ends = new Set(), windowEvents = new Map(), documentEvents = new Map();
global.document = { body: el("body"), hidden: false,
  addEventListener: (k, fn) => documentEvents.set(k, fn), removeEventListener: k => documentEvents.delete(k) };
const window = { addEventListener: (k, fn) => windowEvents.set(k, fn), removeEventListener: k => windowEvents.delete(k) };
let observer;
class Observer {
  constructor(fn) { this.callback = fn; observer = this; }
  observe() {} disconnect() { this.disconnected = true; }
}
const onSessionEnd = fn => { ends.add(fn); return () => ends.delete(fn); };
{
  let permitted = true, creations = 0;
  const quick = load("camera-quick-audio.js", {
    el, t, allowed: () => permitted, permittedCamera: cam => cam,
    pushToTalkButton: (_cam, options) => { assert.equal(options.inline, true); creations++; return el("button"); },
    audioMessageButton: () => { creations++; return el("button"); },
  }, "quickAudioControls");
  assert.equal(quick({}), null);
  const cam = { audio_streams: true, audio_messages: true };
  permitted = false; assert.equal(quick(cam), null); assert.equal(creations, 0);
  permitted = true;
  const group = quick(cam); assert.equal(group.children.length, 2);
  assert.equal(group.children[1].textContent, "intercom.open");
  assert.equal(quick({ audio_streams: true }).children.length, 1);
  assert.equal(quick({ audio_messages: true }).children.length, 1);
  assert.equal(cam.audio_messages, true); // Never mutate driver evidence.
}
(async () => {
  const [button, Session] = load("push-to-talk.js", {
    el, t, onSessionEnd, window, document, MutationObserver: Observer,
  }, "[pushToTalkButton, PushToTalkSession]");
  let starts = 0, stops = 0, cancels = 0;
  Session.prototype.start = async function() { starts++; this.onStatus("talk.active"); };
  Session.prototype.stop = async function() { stops++; this.finished = true; };
  Session.prototype.cancel = function() { cancels++; this.finished = true; };
  const widget = button({ id: "synthetic" }, { inline: true }); document.body.append(widget);
  assert.equal(widget.className, "quick-talk"); assert.equal(starts, 0);
  const talk = widget.children[0];
  await talk.dispatch("pointerdown", { button: 2 }); assert.equal(starts, 0);
  await talk.dispatch("pointerdown", { button: 0, pointerId: 1 }); await flush();
  assert.equal(starts, 1); assert.equal(talk.disabled, false);
  await talk.dispatch("pointerup"); await flush(); assert.equal(stops, 1);
  await talk.dispatch("keydown", { key: " " }); await talk.dispatch("keydown", { key: " ", repeat: true });
  await flush(); assert.equal(starts, 2);
  await talk.dispatch("keyup", { key: " " }); await flush(); assert.equal(stops, 2);
  await talk.dispatch("pointerdown", { button: 0 }); await flush();
  windowEvents.get("blur")(); await flush(); assert.equal(stops, 3);
  await talk.dispatch("pointerdown", { button: 0 }); await flush();
  widget.remove(); observer.callback(); assert.equal(cancels, 1);
  assert(observer.disconnected); assert.equal(ends.size, 0);
  assert.equal(windowEvents.size, 0); assert.equal(documentEvents.size, 0);
  await talk.dispatch("pointerdown", { button: 0 }); await flush(); assert.equal(starts, 4);
  const second = button({ id: "synthetic" }, { inline: true }); document.body.append(second);
  await second.children[0].dispatch("pointerdown", { button: 0 }); await flush();
  for (const fn of [...ends]) fn(); assert.equal(cancels, 2); assert(!second.isConnected);
  console.log("Quick audio capability, permission, inline hold/release and cleanup contracts passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
