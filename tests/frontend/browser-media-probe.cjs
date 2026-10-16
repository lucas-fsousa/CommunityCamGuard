// Safety properties of the opt-in browser instrumentation; no network/camera.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const camera = "cam_" + "a".repeat(24);
const source = fs.readFileSync(path.join(__dirname, "../../scripts/browser_media_probe.js"), "utf8")
  .replace("__CAMERA_ID__", JSON.stringify(camera));
(async () => {
  let calls = 0, present = true;
  const preferences = new Map(), callbacks = [];
  const video = { requestVideoFrameCallback: fn => callbacks.push(fn), paused: false,
    getAttribute: () => "", srcObject: null };
  const player = { video, isConnected: true };
  class Socket {
    static CLOSED = 3;
    constructor() { this.readyState = 1; }
  }
  const window = { WebSocket: Socket, fetch: async () => {
    calls++;
    return new Response(JSON.stringify([{id: camera, mac: "test"}, {id: "other", mac: "other"}]));
  }};
  vm.runInNewContext(source, {window, URL, Request, Response,
    location: {origin: "https://example", host: "example", href: "https://example/"},
    localStorage: {setItem: (key, value) => preferences.set(key, value)},
    document: {querySelector: () => present ? player : null,
      querySelectorAll: () => present ? [player] : []}});
  const filtered = await (await window.fetch("/api/cameras")).json();
  assert.deepEqual(filtered, [{id: camera, mac: "test"}]);
  assert.equal(preferences.get("ccg.quality.test"), "smooth");
  await assert.rejects(window.fetch("/api/cameras/test/control", {method: "POST"}));
  assert.equal(calls, 1);
  for (const address of ["wss://example/api/go2rtc/ws?src=other_web",
    `wss://example/api/go2rtc/ws?src=${camera}_hd`,
    `wss://elsewhere/api/go2rtc/ws?src=${camera}_web`]) {
    assert.throws(() => new window.WebSocket(address));
  }
  const socket = new window.WebSocket(`wss://example/api/go2rtc/ws?src=${camera}_web`);
  const probe = window.__ccgMediaProbe;
  assert.equal(probe.sockets.length, 1);
  assert(probe.observe());
  callbacks.shift()(0, {mediaTime: 0});
  callbacks.shift()(0, {mediaTime: 0}); // Re-presenting the same image is not progress.
  assert.equal(probe.frames, 1);
  for (let i = 1; i < 8; i++) callbacks.shift()(0, {mediaTime: i / 10});
  assert.equal(probe.frames, 8); assert.equal(callbacks.length, 0);
  assert(probe.lastTime > probe.firstTime);
  assert(!probe.stopped());
  present = false; player.isConnected = false; video.paused = true;
  assert(!probe.stopped()); // Removing DOM alone is not proof of socket shutdown.
  socket.readyState = Socket.CLOSED;
  assert(probe.stopped());
  console.log("Browser media isolation, presented frames and teardown contracts passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
