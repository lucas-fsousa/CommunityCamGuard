const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const source = name => fs.readFileSync(path.join(__dirname, "../../frontend/modules/" + name + ".js"), "utf8")
  .replace(/^import .*;$/gm, "").replaceAll("export function", "function");
class Element {
  constructor(tag, props = {}) { this.tag = tag; this.children = []; this.events = {}; this.value = ""; Object.assign(this, props); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(k, v) { this[k] = v; }
  addEventListener(k, fn) { this.events[k] = fn; }
  fire(k) { let prevented = false; this.events[k]?.({ preventDefault() { prevented = true; } }); return prevented; }
  reportValidity() { return true; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  remove() { this.removed = true; }
  focus() {}
}
const walk = e => [e, ...e.children.flatMap(walk)];
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function harness(canManage = true) {
  const requests = [], timers = new Map(); let next = 0, changes = 0;
  const api = (url, opts) => new Promise((resolve, reject) => requests.push({ url, opts, resolve, reject }));
  const el = (tag, props, ...children) => { const e = new Element(tag, props); e.append(...children); return e; };
  const document = { body: el("body"), documentElement: { lang: "en" } };
  const scope = { api, el, t: key => key, state: { canManage }, document, window: { confirm: () => true },
    setTimeout: (fn, delay) => { timers.set(++next, { fn, delay }); return next; }, clearTimeout: n => timers.delete(n) };
  const evaluate = (name, result) => new Function(...Object.keys(scope), source(name) + ";return " + result)(...Object.values(scope));
  const open = evaluate("access-key-dialog", "openAccessKeyDialog");
  scope.openAccessKeyDialog = open;
  const render = evaluate("access-keys", "renderAccessKeys");
  return { requests, timers, document, render, changes: () => changes,
    open: () => open(() => changes++), find: predicate => walk(document.body).find(predicate), el };
}
function fill(h) {
  h.find(e => e.id === "access-key-name").value = "Guest";
  const boxes = walk(h.document.body).filter(e => e.type === "checkbox");
  boxes[0].checked = true; boxes[0].fire("change");
  boxes[1].checked = true; // live only
}
(async () => {
  {
    const h = harness(); const dispose = h.open();
    h.find(e => e.id === "access-key-name").value = "Timed";
    const expiry = h.find(e => e.id === "access-key-expiry");
    expiry.value = "2036-01-02T12:30";
    const form = h.find(e => e.tag === "form");
    form.fire("submit"); assert.equal(h.requests.length, 0); // No implicit grant.
    const boxes = walk(h.document.body).filter(e => e.type === "checkbox");
    boxes[3].checked = true; // PTZ, independently of live/recordings.
    form.fire("submit");
    assert.deepEqual(JSON.parse(h.requests[0].opts.body), { label: "Timed",
      expires_at: new Date("2036-01-02T12:30").toISOString(), permissions: ["ptz"] });
    h.requests[0].resolve({ secret: "synthetic", login_enabled: true }); await flush();
    assert(!h.find(e => e.textContent === "keys.stagedCreated")); dispose();
  }
  {
    const h = harness(); const dispose = h.open(); fill(h);
    const form = h.find(e => e.tag === "form");
    form.fire("submit"); form.fire("submit");
    assert.equal(h.requests.length, 1);
    assert.deepEqual(JSON.parse(h.requests[0].opts.body), { label: "Guest", expires_at: null, permissions: ["live"] });
    assert(h.find(e => e.tag === "dialog").fire("cancel"));
    h.requests[0].resolve({ secret: "synthetic-one-time", login_enabled: false }); await flush();
    const secret = h.find(e => e.readOnly);
    assert.equal(secret.value, "synthetic-one-time"); assert(form.hidden);
    assert.equal(h.changes(), 1);
    assert(h.find(e => e.textContent === "keys.stagedCreated"));
    dispose(); assert.equal(secret.value, ""); assert.equal(h.timers.size, 0);
  }
  {
    const h = harness(); const dispose = h.open(); fill(h);
    h.find(e => e.tag === "form").fire("submit");
    dispose(); assert(h.requests[0].opts.signal.aborted);
    h.requests[0].resolve({ secret: "late" }); await flush();
    assert(!h.find(e => e.readOnly)); assert.equal(h.changes(), 0);
  }
  {
    const h = harness(); const dispose = h.open(); fill(h);
    const form = h.find(e => e.tag === "form"); form.fire("submit");
    h.requests[0].reject(new Error("uncertain")); await flush(); form.fire("submit");
    assert.equal(h.requests.length, 1); assert(h.find(e => e.textContent === "keys.createFailed")); dispose();
  }
  {
    const h = harness(); const dispose = h.open(); fill(h);
    h.find(e => e.tag === "form").fire("submit");
    h.requests[0].resolve({ secret: "synthetic", login_enabled: false }); await flush();
    const secret = h.find(e => e.readOnly);
    [...h.timers.values()].find(timer => timer.delay === 60000).fn();
    assert.equal(secret.value, ""); dispose();
  }
  {
    const h = harness(false); const root = h.el("div"); h.render(root)();
    assert.equal(root.children.length, 0); assert.equal(h.requests.length, 0);
  }
  {
    const h = harness(); const root = h.document.body; const dispose = h.render(root);
    assert.equal(h.requests.length, 0); // Metadata loaded on demand.
    h.find(e => e.textContent === "keys.refresh").fire("click");
    h.requests[0].resolve({ items: [{ id: "a".repeat(32), label: "<b>literal</b>", expires_at: null, status: "active", permissions: ["live"] }] });
    await flush(); assert(h.find(e => e.textContent === "<b>literal</b>"));
    const revoke = h.find(e => e.textContent === "keys.revoke"); revoke.fire("click"); revoke.fire("click");
    assert.equal(h.requests.length, 2); assert.equal(h.requests[1].opts.body, "{}");
    h.requests[1].reject(new Error("lost response")); await flush();
    assert(revoke.disabled); revoke.fire("click"); assert.equal(h.requests.length, 2);
    dispose(); assert.equal(h.timers.size, 0);
  }
  console.log("Delegated access UI contracts passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
