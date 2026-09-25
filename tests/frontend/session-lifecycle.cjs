const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const source = name => fs.readFileSync(path.join(__dirname, "../../frontend/modules", name), "utf8")
  .replace(/^import .*;\n/gm, "").replace(/export (async )?function /g, "$1function ")
  .replace(/export const /g, "const ");
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject }; };
function watchHarness() {
  const timers = new Map(), listeners = new Map(), requests = []; let id = 0, invalid = 0;
  const create = new Function("setTimeout", "clearTimeout", source("session-watch.js") + "\nreturn createSessionWatch;")(
    (fn, delay) => { timers.set(++id, { fn, delay }); return id; }, key => timers.delete(key));
  const events = { addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: key => listeners.delete(key) };
  const watch = create(signal => { const request = { ...deferred(), signal }; requests.push(request); return request.promise; },
    () => invalid++, events);
  const tick = delay => { const [key, timer] = [...timers].find(([, item]) => item.delay === delay); timers.delete(key); timer.fn(); };
  return { watch, timers, listeners, requests, tick, invalid: () => invalid };
}
(async () => {
  {
    const h = watchHarness(); h.watch.start(); h.listeners.get("focus")();
    assert.equal(h.requests.length, 1); // Focus cannot overlap the in-flight check.
    h.requests[0].resolve({ authenticated: true }); await flush(); h.tick(1000);
    h.requests[1].resolve({ authenticated: false }); await flush();
    assert.equal(h.invalid(), 1); assert.equal(h.timers.size, 0); assert.equal(h.listeners.size, 0);
  }
  {
    const h = watchHarness(); h.watch.start(); h.watch.stop(); h.watch.start();
    assert(h.requests[0].signal.aborted);
    h.requests[0].resolve({ authenticated: false }); await flush(); assert.equal(h.invalid(), 0);
    h.requests[1].reject(new Error("offline")); await flush();
    assert.equal(h.invalid(), 0); h.tick(1000); h.tick(5000);
    assert(h.requests[2].signal.aborted);
    h.requests[2].reject(new DOMException("timeout", "AbortError")); await flush();
    assert.equal(h.timers.size, 1); h.watch.stop(); assert.equal(h.timers.size, 0);
  }
  {
    const response = deferred(), json = deferred(); let unauthorized = 0, closed = 0;
    const core = new Function("fetch", source("core.js") + "\nreturn {api, endSession, onSessionEnd, onUnauthorized, state};")(
      () => response.promise);
    core.onUnauthorized(() => unauthorized++);
    core.state.canManage = true;
    core.onSessionEnd(() => { throw new Error("one broken cleanup must not block others"); });
    core.onSessionEnd(() => closed++);
    const request = core.api("/cameras");
    response.resolve({ ok: true, status: 200, json: () => json.promise }); await flush();
    core.endSession(); json.resolve([]);
    await assert.rejects(request, { name: "AbortError" });
    assert.equal(closed, 1); assert.equal(core.state.canManage, false);
    core.endSession(); assert.equal(closed, 1); assert.equal(unauthorized, 0);
  }
  {
    const response = deferred(); let unauthorized = 0;
    const core = new Function("fetch", source("core.js") + "\nreturn {api, endSession, onUnauthorized};")(() => response.promise);
    core.onUnauthorized(() => unauthorized++);
    const request = core.api("/old-request"); core.endSession();
    response.resolve({ status: 401 });
    await assert.rejects(request, { name: "AbortError" }); assert.equal(unauthorized, 0);
  }
  // A microphone prompt may resolve after logout. No AudioContext/socket may then be created.
  for (const [file, name] of [["audio-message.js", "PcmRecorder"], ["push-to-talk.js", "PushToTalkSession"]]) {
    const permission = deferred(); let stopped = 0;
    const Class = new Function("navigator", "window", source(file) + `\nreturn ${name};`)(
      { mediaDevices: { getUserMedia: () => permission.promise } },
      { AudioWorkletNode: true, AudioContext: class { constructor() { throw new Error("Late audio context opened"); } } });
    const session = new Class("test", () => {}, () => {});
    const starting = session.start(() => {});
    await session.cancel();
    permission.resolve({ getTracks: () => [{ stop: () => stopped++ }] });
    await assert.rejects(starting, { name: "AbortError" }); assert.equal(stopped, 1);
  }
  console.log("Session watch, stale responses and audio cancellation contracts passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
