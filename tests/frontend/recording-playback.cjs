const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../../frontend/modules/recording-playback.js"), "utf8")
  .replace("export function", "function");
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function harness() {
  const timers = new Map(); let timerId = 0;
  const create = new Function("setTimeout", "clearTimeout", source + "\nreturn createRecordingPlayback;")(
    (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
    (id) => timers.delete(id));
  const events = () => ({
    listeners: new Map(),
    addEventListener(key, fn) { this.listeners.set(key, fn); },
    removeEventListener(key) { this.listeners.delete(key); },
    emit(key) { this.listeners.get(key)?.(); },
  });
  const player = Object.assign(events(), {
    loads: 0, plays: 0, pauses: 0, src: "", currentTime: 0,
    load() { this.loads++; }, pause() { this.pauses++; },
    removeAttribute(key) { if (key === "src") this.src = ""; },
    play() { this.plays++; return this.reject ? Promise.reject({ name: this.reject }) : Promise.resolve(); },
  });
  const status = {}; const retry = Object.assign(events(), { hidden: true });
  const requests = [];
  const api = (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
  const controller = create(player, status, retry, api, (key) => key);
  return { controller, player, status, retry, requests, timers };
}
(async () => {
  {
    const h = harness(); h.controller.select("a"); h.controller.select("a");
    assert.equal(h.requests.length, 1); // No duplicate preparation on repeated click.
    h.requests[0].resolve({ ready: true, cached: true }); await flush();
    assert.equal(h.player.plays, 1); // No loadedmetadata event needed to request play.
    const loads = h.player.loads; h.player.currentTime = 42;
    h.controller.select("a"); await flush();
    assert.equal(h.player.loads, loads); assert.equal(h.player.currentTime, 42);
    assert.equal(h.requests.length, 1); assert.equal(h.player.plays, 2);
    h.player.emit("playing"); assert.equal(h.status.textContent, "");
    h.controller.dispose(); assert.equal(h.player.src, "");
    assert.equal(h.timers.size, 0); assert.equal(h.player.listeners.size, 0);
    h.controller.select("b"); assert.equal(h.requests.length, 1);
  }
  {
    const h = harness(); h.controller.select("a"); h.controller.select("b");
    assert(h.requests[0].options.signal.aborted);
    h.requests[1].resolve({ ready: true }); await flush();
    h.requests[0].resolve({ ready: true }); await flush();
    assert(h.player.src.endsWith("path=b")); assert.equal(h.player.plays, 1);
    h.controller.dispose();
  }
  {
    const h = harness(); h.controller.select("a"); h.player.reject = "NotAllowedError";
    h.requests[0].resolve({ ready: true }); await flush();
    assert.equal(h.retry.hidden, false); assert.equal(h.status.textContent, "rec.readyPressPlay");
    h.player.reject = null; h.retry.emit("click"); await flush(); h.player.emit("playing");
    assert.equal(h.player.plays, 2); assert.equal(h.retry.hidden, true);
    assert.equal(h.requests.length, 1); h.controller.dispose();
  }
  {
    const h = harness(); h.controller.select("a");
    h.requests[0].resolve({ ready: false, transcoding: true }); await flush();
    assert.equal(h.timers.size, 1); assert.equal(h.player.plays, 0);
    const [id, timer] = [...h.timers][0]; assert.equal(timer.delay, 1000);
    h.timers.delete(id); timer.fn();
    assert(h.requests[1].url.includes("playback-status"));
    h.controller.dispose(); assert(h.requests[1].options.signal.aborted);
    h.requests[1].resolve({ ready: true }); await flush();
    assert.equal(h.player.plays, 0); assert.equal(h.timers.size, 0);
  }
  {
    const h = harness(); h.controller.select("a");
    h.requests[0].reject(new Error("offline")); await flush();
    assert.equal(h.status.textContent, "rec.playbackFailed");
    assert.equal(h.timers.size, 0);
    h.controller.select("a"); assert.equal(h.requests.length, 2);
    h.controller.dispose(); h.requests[1].resolve({ ready: true }); await flush();
  }
  {
    const h = harness(); h.controller.select("a");
    const [id, timer] = [...h.timers][0]; assert.equal(timer.delay, 15000);
    h.timers.delete(id); timer.fn(); assert(h.requests[0].options.signal.aborted);
    h.requests[0].reject({ name: "AbortError" }); await flush();
    assert.equal(h.status.textContent, "rec.playbackFailed");
    h.controller.dispose(); assert.equal(h.timers.size, 0);
  }
  console.log("Recording playback lifecycle contracts passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
