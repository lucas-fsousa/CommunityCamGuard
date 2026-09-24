const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../../frontend/modules/recording-playback.js"), "utf8")
  .replace("export function", "function");
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function harness(support = "") {
  const timers = new Map(); let timerId = 0;
  let now = Date.now();
  const create = new Function("setTimeout", "clearTimeout", "Date", source + "\nreturn createRecordingPlayback;")(
    (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
    (id) => timers.delete(id), { now: () => now });
  const events = () => ({
    listeners: new Map(),
    addEventListener(key, fn) { this.listeners.set(key, fn); },
    removeEventListener(key) { this.listeners.delete(key); },
    emit(key) { this.listeners.get(key)?.(); },
  });
  const player = Object.assign(events(), {
    canPlayType() { return support; },
    loads: 0, plays: 0, pauses: 0, src: "", currentTime: 0,
    load() { this.loads++; }, pause() { this.pauses++; },
    removeAttribute(key) { if (key === "src") this.src = ""; },
    play() { this.plays++; return this.reject ? Promise.reject({ name: this.reject }) : Promise.resolve(); },
  });
  const status = {};
  const requests = [];
  const api = (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
  const controller = create(player, status, api, (key) => key);
  return { controller, player, status, requests, timers, advanceTime(ms) { now += ms; } };
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
    assert.equal(h.status.textContent, "rec.readyPressPlay");
    h.player.reject = null; h.player.play(); await flush(); h.player.emit("playing");
    assert.equal(h.player.plays, 2); assert.equal(h.status.textContent, "");
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
  {
    const h = harness(); h.controller.select("a");
    h.requests[0].reject({ status: 429 }); await flush();
    assert.equal(h.status.textContent, "rec.playbackBusy");
    assert.equal(h.requests.length, 1); // No automatic overload retry storm.
    h.controller.select("a"); assert.equal(h.requests.length, 2);
    h.controller.dispose(); h.requests[1].resolve({ ready: true }); await flush();
  }
  {
    const h = harness("probably"); h.controller.select("native");
    assert(h.requests[0].url.endsWith("&native_hevc=true"));
    h.requests[0].resolve({ ready: true, original: true }); await flush();
    assert(h.player.src.endsWith("&original=true"));
    h.player.emit("playing"); assert.equal(h.timers.size, 0);
    assert.equal(h.requests.length, 1); h.controller.dispose();
  }
  for (const support of ["", "maybe"]) {
    const h = harness(support); h.controller.select("a");
    assert(!h.requests[0].url.includes("native_hevc")); h.controller.dispose();
  }
  for (const failure of ["decode", "unsupported", "timeout"]) {
    const h = harness("probably"); h.controller.select("native");
    if (failure === "unsupported") h.player.reject = "NotSupportedError";
    h.requests[0].resolve({ ready: true, original: true }); await flush();
    if (failure === "decode") {
      h.player.currentTime = 7; h.player.error = { code: 3 }; h.player.emit("error");
      h.player.emit("error"); // A second event must not create another job/request.
    }
    if (failure === "timeout") {
      const [id, timer] = [...h.timers][0]; assert.equal(timer.delay, 12000);
      h.timers.delete(id); timer.fn();
    }
    assert.equal(h.requests.length, 2);
    assert(!h.requests[1].url.includes("native_hevc"));
    h.player.reject = null;
    h.requests[1].resolve({ ready: true, cached: true }); await flush();
    assert(!h.player.src.includes("original=true"));
    h.player.currentTime = 0; h.player.duration = 60; h.player.emit("loadedmetadata");
    if (failure === "decode") assert.equal(h.player.currentTime, 7);
    h.player.error = { code: 3 }; h.player.emit("error");
    assert.equal(h.requests.length, 2); // No fallback loop.
    h.controller.dispose(); assert.equal(h.timers.size, 0);
  }
  for (const failure of ["NotAllowedError", "network"]) {
    const h = harness("probably"); h.controller.select("a");
    if (failure === "NotAllowedError") h.player.reject = failure;
    h.requests[0].resolve({ ready: true, original: true }); await flush();
    if (failure === "network") { h.player.error = { code: 2 }; h.player.emit("error"); }
    assert.equal(h.requests.length, 1); assert.equal(h.timers.size, 0);
    h.controller.dispose();
  }
  {
    const h = harness("probably"); h.controller.select("a");
    h.player.reject = "NotAllowedError";
    h.requests[0].resolve({ ready: true, original: true }); await flush();
    assert.equal(h.timers.size, 0);
    h.player.emit("play"); assert.equal(h.timers.size, 1); // Native manual play re-arms timeout.
    h.player.emit("pause"); assert.equal(h.timers.size, 0); // User pause must not convert.
    h.player.emit("play"); h.controller.select("b");
    assert.equal([...h.timers.values()].filter(t => t.delay === 12000).length, 0);
    h.controller.dispose(); assert.equal(h.timers.size, 0);
  }
  {
    const h = harness("probably"); h.controller.select("a");
    h.requests[0].resolve({ ready: true, original: true }); await flush();
    h.player.videoWidth = 0; h.player.emit("playing");
    assert.equal(h.requests.length, 2); // Audio-only is not successful video playback.
    h.controller.dispose();
  }
  {
    const h = harness("probably"); h.controller.select("a");
    h.requests[0].resolve({ ready: true, original: true }); await flush();
    h.player.emit("playing"); h.advanceTime(700000);
    h.player.error = { code: 3 }; h.player.emit("error");
    h.requests[1].resolve({ ready: false, transcoding: true }); await flush();
    assert.equal(h.status.textContent, "rec.preparingSeekable");
    assert.equal([...h.timers.values()][0].delay, 1000); // Fresh budget after late decode failure.
    h.controller.dispose();
  }
  console.log("Recording playback lifecycle contracts passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
