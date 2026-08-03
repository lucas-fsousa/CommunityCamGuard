"use strict";
// Plain-JS dashboard. Same-origin API (cookie auth is automatic); live video is embedded
// straight from go2rtc's built-in player, so we hold no media code here.

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids) n.append(k);
  return n;
};

const state = {
  go2rtc: "", gridHdMax: 0, cameras: [], view: "grid", selected: null,
  rec: { mac: "", from: "", to: "", page: 0, pageSize: 50 },
  candidates: [], camFilter: "all",   // Cameras view: discovered (unconfigured) cams + which cards to show
};

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" }, ...opts,
  });
  if (res.status === 401) { showLogin(); throw new Error("unauthorized"); }
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.status === 204 ? null : res.json();
}

// --- auth --------------------------------------------------------------------------
function showLogin() { $("#login").classList.remove("hidden"); $("#dash").classList.add("hidden"); }
function showDash() { $("#login").classList.add("hidden"); $("#dash").classList.remove("hidden"); }

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/login", { method: "POST", body: JSON.stringify({ key: $("#login-key").value }) });
    $("#login-key").value = "";
    await boot();
  } catch { $("#login-error").textContent = t("login.invalid"); }
});

$("#btn-logout").addEventListener("click", async () => { await api("/logout", { method: "POST" }); showLogin(); });

// --- storage banner ----------------------------------------------------------------
async function loadStorage() {
  try {
    const s = await api("/storage");
    const gb = (b) => (b / 1e9).toFixed(0);
    const box = $("#storage");
    box.className = "storage " + s.status;
    box.textContent = t("storage.disk", { pct: s.used_percent, gb: gb(s.free_bytes) }) +
      (s.saving_paused ? t("storage.paused") : "");
  } catch {}
}

// --- camera tiles ------------------------------------------------------------------
// No forced mode: every stream the player is pointed at is H.264 + AAC/Opus, so go2rtc's
// mode=webrtc,mse pins the *priority*: try WebRTC first, fall back to MSE only if it truly fails.
// This matters for remote viewers — MSE is WebSocket/TCP, so a 1080p feed over the internet stalls
// to rebuffer on any jitter/loss ("travando toda hora"); WebRTC's UDP + jitter buffer rides over it.
// Diagnostics showed the HD tile landing on MSE while the substream got WebRTC, so we stop leaving
// it to chance. MSE stays as the fallback so a WebRTC-hostile network still gets a picture.
// The <cam-player> element (player.js) sets mode="webrtc,mse"; here we only hand it the signalling
// WebSocket. We point it at the app's OWN origin (/api/go2rtc/ws), which proxies to go2rtc: this
// keeps the player same-origin (so the freeze watchdog can read the real <video>) without opening
// go2rtc's unauthenticated API cross-origin. A leading "/" makes VideoRTC build ws://<app-origin>/…
function wsFor(streamId) {
  return `/api/go2rtc/ws?src=${encodeURIComponent(streamId)}`;
}

const svgIcon = (id) => `<svg class="icon"><use href="#${id}" /></svg>`;

// Small pills advertising probed capabilities (video/audio codecs, PTZ support).
function capBadges(cam) {
  const c = cam.capabilities || {};
  const wrap = el("span", { className: "badges" });
  if (c.video_codec) wrap.append(el("span", { className: "badge", textContent: c.video_codec, title: t("cap.videoCodec") }));
  if (c.has_audio) wrap.append(el("span", { className: "badge", textContent: c.audio_codec || t("cap.audio"), title: t("cap.audioPresent") }));
  if (c.ptz) wrap.append(el("span", { className: "badge ptz", textContent: "PTZ", title: t("cap.ptz") }));
  return wrap;
}

// Pan/tilt arrows — press-and-hold to keep panning. These cameras' ONVIF ContinuousMove is a
// **fixed, uninterruptible ~0.4s step** (Stop is ignored by the firmware), so a single "start"
// only nudges once and the camera's top speed is ~1 step / 0.4s. We repeat the step while held
// at that rate — sending faster doesn't pan faster, it just backlogs commands that overshoot
// after release. Matched to the step, at most one step is in flight on release (~0.4s overshoot,
// the floor since Stop can't cancel a step). Shown only for PTZ cameras.
const PTZ_REPEAT_MS = 450;   // ~= the camera's step duration; its effective max pan rate

function ptzControls(cam) {
  let held = null, timer = null, safety = null;
  const send = (action, direction) =>
    api(`/cameras/${cam.mac}/ptz`, { method: "POST", body: JSON.stringify({ action, direction }) })
      .catch((e) => console.warn(`ptz ${action} ${direction || ""}: ${e.message}`));
  const stop = () => {
    if (!held) return;
    const dir = held; held = null;
    clearInterval(timer); clearTimeout(safety);
    send("stop", dir);
  };
  const start = (dir) => {
    if (held) return;
    held = dir;
    send("start", dir);                                       // first step now
    timer = setInterval(() => send("start", dir), PTZ_REPEAT_MS);  // keep stepping while held
    safety = setTimeout(stop, 8000);   // never pan forever if a release event is missed
  };
  const b = (dir, glyph) => {
    const btn = el("button", { className: "icon-btn ptz-mini", textContent: glyph, title: t("ptz.holdDir", { dir: t("dir." + dir) }) });
    btn.addEventListener("pointerdown", (e) => {
      e.preventDefault(); e.stopPropagation();
      btn.setPointerCapture?.(e.pointerId);   // keep getting events if the finger slides off
      start(dir);
    });
    btn.addEventListener("pointerup", (e) => { e.stopPropagation(); stop(); });
    btn.addEventListener("pointercancel", stop);
    btn.addEventListener("lostpointercapture", stop);
    btn.addEventListener("contextmenu", (e) => e.preventDefault());  // no long-press menu on touch
    return btn;
  };
  // Compact inline arrows (← ↑ ↓ →). A cross D-pad reads nicely but is 3 rows tall, which forces a
  // tall footer; the footer stays one line this way. (A hover-overlay D-pad is the way to get both.)
  return el("span", { className: "ptz-inline", title: t("ptz.hold") },
    b("left", "←"), b("up", "↑"), b("down", "↓"), b("right", "→"));
}


// Remove/probe buttons are shared by the live camera bar and the Cameras-view manage card.
function removeBtn(cam) {
  const del = el("button", { className: "icon-btn danger", title: t("cam.remove"), innerHTML: svgIcon("i-trash") });
  del.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (confirm(t("cam.removeConfirm", { name: cam.name || cam.mac }))) { await api("/cameras/" + cam.mac, { method: "DELETE" }); loadCameras(); }
  });
  return del;
}

function probeBtn(cam) {
  const probe = el("button", { className: "icon-btn", title: t("cam.probe"), innerHTML: svgIcon("i-probe") });
  probe.addEventListener("click", async (e) => {
    e.stopPropagation();
    probe.disabled = true; probe.classList.add("spin");
    try { await api(`/cameras/${cam.mac}/probe`, { method: "POST" }); await loadCameras(); }
    catch (err) { alert(t("cam.probeFailed", { msg: err.message })); }
    finally { probe.disabled = false; probe.classList.remove("spin"); }
  });
  return probe;
}

function camBar(cam) {
  const rec = el("span", { className: "rec" + (cam.recording ? " on" : ""), title: cam.recording ? t("cam.recording") : t("cam.idle") });
  const del = removeBtn(cam);
  const probe = probeBtn(cam);
  const reload = el("button", { className: "icon-btn", title: t("cam.restart"), innerHTML: svgIcon("i-refresh") });
  reload.addEventListener("click", (e) => { e.stopPropagation(); refreshPlayer(cam.mac, reload); });
  const caps = cam.capabilities || {};

  // A single compact row: identity on the left, controls on the right. Keeping it one line is what
  // keeps the footer small (the go2rtc player already adds its own control strip above us).
  const actions = el("span", { className: "bar-actions" });
  if (caps.ptz) actions.append(ptzControls(cam));
  if (cam.has_substream) actions.append(qualityControls(cam));
  actions.append(zoomControls(cam), reload, probe, del);
  return el("div", { className: "bar" },
    rec,
    el("span", { className: "name", textContent: cam.name || t("cam.unnamed") }),
    el("span", { className: "ip", textContent: cam.last_ip || "" }),
    capBadges(cam),
    el("span", { className: "bar-spacer" }),
    actions,
  );
}

// Which go2rtc stream a tile should pull, weighing picture against CPU.
//
// Both variants are re-encoded to H.264 (the browser cannot take the cameras' HEVC); they
// differ in source. `_hd` is the main 1080p feed at ~30% of a core per viewer, `_web` the
// substream at ~8%. Single view is always one camera, so it takes `_hd`. The grid takes it
// too while it is small enough (`grid_hd_max_cameras`) — on these units the substream is a
// 640x360 / 37 kbps feed, so the CPU buys a visible improvement — and falls back to `_web`
// once there are enough tiles that the full-resolution cost would starve the host.
// Per-camera quality preference, chosen by the user and kept client-side (no server round-trip,
// no go2rtc restart — the variants already exist as separate streams, so switching is instant).
// "auto" = the sharpest the host can sustain (the default, matching the "max quality" intent);
// "sharp" = force the main 1080p feed; "smooth" = force the cheap substream.
const QUALITY_PREFS = ["auto", "sharp", "smooth"];
function qualityPref(mac) {
  try { return localStorage.getItem("ccg.quality." + mac) || "auto"; } catch { return "auto"; }
}
function setQualityPref(mac, val) {
  try { localStorage.setItem("ccg.quality." + mac, val); } catch { /* private mode: session-only */ }
}

function streamFor(cam) {
  if (!cam.has_substream) return cam.web_stream_id;   // only one source; nothing to choose
  const pref = qualityPref(cam.mac);
  if (pref === "sharp") return cam.hd_stream_id;
  if (pref === "smooth") return cam.web_stream_id;
  // auto: single view is always sharp; the grid is sharp only while small enough not to starve
  // the host (grid_hd_max_cameras), else the substream. See docs/DECISIONS.md §34.
  const hd = state.view === "single" || state.cameras.length <= state.gridHdMax;
  return hd ? cam.hd_stream_id : cam.web_stream_id;
}

function camFrame(cam) {
  const sid = streamFor(cam);
  const frame = el("cam-player", { className: "frame" });
  frame.dataset.src = sid;   // so a view switch can tell if it must reconnect
  frame.src = wsFor(sid);    // VideoRTC .src setter kicks off the WebSocket/WebRTC connect
  applyZoom(cam.mac, frame);   // a rebuilt frame (restart / resume) keeps its zoom
  return frame;
}

// A black stand-in that holds the tile's firstChild slot while the live player is torn down (under
// Recordings, so live audio/CPU stops). Resuming replaces it with a fresh camFrame.
function suspendedFrame() {
  return el("div", { className: "frame" });
}


// --- digital zoom -------------------------------------------------------------------
// These cameras have **no optical zoom**: the ONVIF Zoom verb answers 200 but never actuates
// (measured — a still scene and a full-velocity zoom give the same frame-diff PSNR, while a pan
// collapses it). The vendor app's zoom is client-side too (its ZoomView/_OnGesture are renderer
// calls). So zoom here is a CSS transform on the player. The go2rtc player is a cross-origin
// iframe we cannot script, but transforming the iframe *element* needs no access to its content.
const ZOOM_MIN = 1, ZOOM_MAX = 6, ZOOM_STEP = 0.5;
const zooms = new Map();   // mac -> { scale, x, y }   (x/y = pan offset in px, pre-scale)

function zoomOf(mac) {
  if (!zooms.has(mac)) zooms.set(mac, { scale: 1, x: 0, y: 0 });
  return zooms.get(mac);
}

// Keep the panned image covering the tile: at scale s the overflow is (s-1)/2 per side, so the
// offset can never exceed that or the user would drag empty space into view.
function clampPan(z, frame) {
  const maxX = Math.max(0, (frame.offsetWidth * (z.scale - 1)) / 2);
  const maxY = Math.max(0, (frame.offsetHeight * (z.scale - 1)) / 2);
  z.x = Math.min(maxX, Math.max(-maxX, z.x));
  z.y = Math.min(maxY, Math.max(-maxY, z.y));
}

function applyZoom(mac, frame) {
  const tile = tiles.get(mac);
  frame = frame || (tile && tile.el.firstChild);
  if (!frame || frame.tagName !== "CAM-PLAYER") return;
  const z = zoomOf(mac);
  clampPan(z, frame);
  frame.style.transform = `translate(${z.x}px, ${z.y}px) scale(${z.scale})`;
  if (tile) {
    // The overlay only takes the pointer while zoomed; at 1x it stays transparent to clicks so
    // the go2rtc player underneath keeps its own controls (unmute to listen).
    tile.el.classList.toggle("zoomed", z.scale > 1);
    const lvl = tile.el.querySelector(".zoom-level");
    if (lvl) lvl.textContent = `${z.scale.toFixed(1)}×`;
  }
}

function setZoom(mac, scale, origin) {
  const z = zoomOf(mac);
  const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(scale * 100) / 100));
  if (origin && z.scale > 0) {
    // Zoom about the pointer instead of the tile centre, so what is under the cursor stays put.
    const k = next / z.scale;
    z.x = origin.x - k * (origin.x - z.x);
    z.y = origin.y - k * (origin.y - z.y);
  }
  z.scale = next;
  if (next === ZOOM_MIN) { z.x = 0; z.y = 0; }   // fully zoomed out is always centred
  applyZoom(mac);
}

// Drag-to-pan + wheel-to-zoom, mounted on the overlay (active only while zoomed).
function zoomOverlay(mac) {
  const ov = el("div", { className: "zoom-overlay" });
  let dragging = false, lastX = 0, lastY = 0;
  ov.addEventListener("pointerdown", (e) => {
    dragging = true; lastX = e.clientX; lastY = e.clientY;
    ov.setPointerCapture(e.pointerId); ov.classList.add("grabbing");
  });
  ov.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const z = zoomOf(mac);
    z.x += e.clientX - lastX; z.y += e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    applyZoom(mac);
  });
  const end = (e) => {
    dragging = false; ov.classList.remove("grabbing");
    if (e && e.pointerId !== undefined && ov.hasPointerCapture(e.pointerId)) ov.releasePointerCapture(e.pointerId);
  };
  ov.addEventListener("pointerup", end);
  ov.addEventListener("pointercancel", end);
  ov.addEventListener("dblclick", () => setZoom(mac, ZOOM_MIN));
  ov.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = ov.getBoundingClientRect();
    const origin = { x: e.clientX - r.left - r.width / 2, y: e.clientY - r.top - r.height / 2 };
    setZoom(mac, zoomOf(mac).scale * (e.deltaY < 0 ? 1.15 : 1 / 1.15), origin);
  }, { passive: false });
  return ov;
}

// Quick-action zoom controls for the camera bar. Works on every camera (it is pure rendering),
// so unlike PTZ it is not gated on a capability.
function zoomControls(cam) {
  const btn = (label, title, fn) => {
    const b = el("button", { className: "icon-btn", title, textContent: label });
    b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
    return b;
  };
  return el("span", { className: "zoom-inline" },
    btn("−", t("zoom.out"), () => setZoom(cam.mac, zoomOf(cam.mac).scale - ZOOM_STEP)),
    el("span", { className: "zoom-level", textContent: `${zoomOf(cam.mac).scale.toFixed(1)}×` }),
    btn("+", t("zoom.in"), () => setZoom(cam.mac, zoomOf(cam.mac).scale + ZOOM_STEP)),
    btn("⤢", t("zoom.reset"), () => setZoom(cam.mac, ZOOM_MIN)),
  );
}

// Per-camera quality selector for the bar: a dropdown so the user picks the mode directly
// (Auto / HD / SD) instead of cycling blindly. Only meaningful when the camera has a substream
// (two sources to choose between); for a single-source camera streamFor has no choice, so the
// caller omits it. Changing it reconnects just this one player.
function qualityControls(cam) {
  const sel = el("select", { className: "quality-select", title: t("quality.label") });
  for (const val of QUALITY_PREFS) {
    const o = el("option", { value: val, textContent: t("quality." + val) });
    if (val === qualityPref(cam.mac)) o.selected = true;
    sel.append(o);
  }
  sel.addEventListener("click", (e) => e.stopPropagation());   // don't trigger the tile's own click
  sel.addEventListener("change", (e) => {
    e.stopPropagation();
    setQualityPref(cam.mac, sel.value);
    refreshPlayer(cam.mac);   // rebuild this tile's iframe against the new source
  });
  return sel;
}

// Restart a single camera's player — swap its iframe for a fresh one so one stuck stream
// recovers on its own, instead of an F5 that re-buffers every camera. The tile's first child
// is the iframe (built in renderPlayers); replacing it forces a clean reconnect for just this cam.
function refreshPlayer(mac, btn) {
  const t = tiles.get(mac);
  const cam = state.cameras.find((c) => c.mac === mac);
  if (!t || !cam) return;
  if (btn) { btn.classList.add("spin"); setTimeout(() => btn.classList.remove("spin"), 600); }
  t.el.firstChild.replaceWith(camFrame(cam));
}

// Freeze watchdog. The failure we actually see: a WebRTC PeerConnection wedges (lost keyframe, stuck
// decoder) — the picture freezes while go2rtc keeps "sending" packets and the player's timer keeps
// advancing, so no server-side counter (producer OR consumer) reflects it. The only truthful signal
// is the real decoded-frame progress of the <video>, which we can now read because the player is
// same-origin (player.js / <cam-player>, tracked via requestVideoFrameCallback). A watched tile whose
// presented frames stop advancing past FREEZE_MS is rebuilt — a fresh PeerConnection gets a new
// keyframe and recovers. Purely client-side: the freeze is client-side, and recording is independent.
const STALL_POLL_MS = 3000;       // how often we check
const FREEZE_MS = 10000;          // no newly-presented frame for this long (while playing) = frozen
function freezeWatchdog() {
  if (_playersSuspended || document.hidden) return;
  if (state.view !== "grid" && state.view !== "single") return;
  tiles.forEach((t, mac) => {
    const frame = t.el.firstChild;
    if (!frame || frame.tagName !== "CAM-PLAYER" || typeof frame.frozenMs !== "function") return;
    if (frame.frozenMs() > FREEZE_MS) {
      console.warn(`freeze watchdog: ${frame.dataset.src} frozen ${Math.round(frame.frozenMs())}ms — rebuilding`);
      refreshPlayer(mac);   // rebuild only this player → fresh WebRTC session + keyframe
    }
  });
}
// When the tab returns to the foreground, rVFC was paused while hidden — reset each player's freeze
// clock so we don't rebuild a perfectly healthy stream on the first check after unhide.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  tiles.forEach((t) => {
    const frame = t.el.firstChild;
    if (frame && frame.tagName === "CAM-PLAYER" && typeof frame.markSeen === "function") frame.markSeen();
  });
});

// Suspend/resume the live players. To stop their audio + CPU (e.g. when viewing Recordings, so live
// sound doesn't overlap the recording) we tear down the <cam-player> and drop in a black placeholder;
// resuming rebuilds it to reconnect. Grid<->Single stay live (no-op) so those switches never rebuffer.
let _playersSuspended = false;
function setPlayersLive(live) {
  if (live === !_playersSuspended) return;   // already in the desired state
  _playersSuspended = !live;
  tiles.forEach((t, mac) => {
    const frame = t.el.firstChild;
    if (live) {
      const cam = state.cameras.find((c) => c.mac === mac);
      if (cam) frame.replaceWith(camFrame(cam));   // reconnect the stream
    } else if (frame && frame.tagName === "CAM-PLAYER") {
      frame.replaceWith(suspendedFrame());         // tear down the stream + its audio
    }
  });
}

// --- render: persistent players, CSS-only view switching ---------------------------
// Camera tiles (iframe + bar) are built once and kept mounted; switching grid<->single
// only toggles CSS, so a running stream is never torn down and re-created (no reload).
const tiles = new Map();   // mac -> { el }

// Reconcile #players with the camera list without recreating existing tiles. New cameras
// get a tile appended; removed ones are dropped; existing tiles keep their live frame and
// only have their bar refreshed. (Never re-append an existing tile — that moves the iframe
// in the DOM, which reloads it.)
function renderPlayers() {
  const players = $("#players");
  const macs = new Set(state.cameras.map((c) => c.mac));
  for (const [mac, t] of tiles) if (!macs.has(mac)) { t.el.remove(); tiles.delete(mac); }
  state.cameras.forEach((cam) => {
    const t = tiles.get(cam.mac);
    if (!t) {
      // Order matters: the frame stays firstChild and the bar lastChild (refreshPlayer,
      // setPlayersLive and the bar refresh below all address the tile by those positions).
      const elx = el("div", { className: "cam" }, camFrame(cam), zoomOverlay(cam.mac), camBar(cam));
      elx.dataset.mac = cam.mac;
      if (_playersSuspended) elx.firstChild.replaceWith(suspendedFrame());  // don't start audio under Recordings
      tiles.set(cam.mac, { el: elx });
      players.append(elx);
    } else {
      t.el.lastChild.replaceWith(camBar(cam));   // refresh bar in place; frame untouched
      // Grid<->Single changes which variant this tile should pull (substream vs re-encoded
      // full res). Only reconnect when the source actually differs — tiles are deliberately
      // kept mounted so an ordinary re-render never reloads a stream.
      const frame = t.el.firstChild;
      if (!_playersSuspended && frame.dataset.src && frame.dataset.src !== streamFor(cam)) {
        frame.replaceWith(camFrame(cam));
      }
    }
  });
}

function buildRail() {
  const rail = $("#rail");
  rail.innerHTML = "";
  state.cameras.forEach((c) => {
    const item = el("div", { className: "rail-item" + (c.mac === state.selected ? " active" : "") },
      el("span", { className: "rec" + (c.recording ? " on" : "") }),
      el("span", { className: "rail-name", textContent: c.name || c.mac }),
    );
    item.addEventListener("click", () => { state.selected = c.mac; applyView(); });
    rail.append(item);
  });
}

// Switch layout without touching the players' DOM (pure CSS via the #stage view class).
function applyView() {
  const stage = $("#stage");
  stage.className = state.view;

  // Live players are live only in grid/single; unload them elsewhere (Recordings, Cameras) so
  // their audio doesn't play under other screens (cross-origin iframes can't be muted from here).
  const liveView = state.view === "grid" || state.view === "single";
  setPlayersLive(liveView);
  // The "no cameras yet" hint belongs to the live views only.
  $("#empty").classList.toggle("hidden", !(liveView && state.cameras.length === 0));

  // Non-live views render regardless of how many cameras exist (Cameras must show even with none).
  if (state.view === "cameras") { renderCameras($("#cameras")); return; }
  if (state.view === "recordings") { renderRecordings($("#recordings")); return; }

  if (!state.cameras.length) return;
  if (!state.cameras.some((c) => c.mac === state.selected)) state.selected = state.cameras[0].mac;
  tiles.forEach((t, mac) => t.el.classList.toggle("selected", mac === state.selected));

  if (state.view === "grid") {
    const n = state.cameras.length, cols = n === 1 ? 1 : 2;
    const fillRows = Math.max(1, Math.ceil(Math.min(n, 4) / cols));
    const players = $("#players");
    players.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    players.style.gridAutoRows = n <= 4 ? `calc(100% / ${fillRows})` : `calc(100% / 2)`;
  } else if (state.view === "single") {
    buildRail();
  }
}

function render() {
  renderPlayers();
  applyView();
}

function setView(view) {
  state.view = view;
  document.querySelectorAll(".views button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  applyView();   // layout-only: keep the players mounted so streams don't reload
}
document.querySelectorAll(".views button").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));

async function loadCameras() {
  state.cameras = await api("/cameras");
  render();
}

// --- Cameras view: scan + configuration --------------------------------------------
// A dedicated tab (not a modal): discovered cameras render as config cards alongside the
// configured cameras' manage cards, with an All / Available / Configured filter. The live
// Grid/Single stays purely for watching streams.

// Centred spinner + message, shown while the scan is in flight.
function loadingBlock(text) {
  return el("div", { className: "loading" },
    el("div", { className: "spinner" }),
    el("p", { className: "muted", textContent: text }));
}

let _scanning = false;
let _scanError = "";
async function runScan() {
  if (_scanning) return;                                  // ignore re-clicks while a scan runs
  _scanning = true; _scanError = "";
  setView("cameras");                                     // switch to the tab; spinner shows (via _scanning)
  try {
    const res = await api("/discovery/scan", { method: "POST" });
    state.candidates = res.candidates || [];
  } catch (e) { _scanError = e.message; }
  finally {
    _scanning = false;
    await loadCameras();                                  // refresh configured list + re-render the tab
  }
}

// Config card for a discovered (unconfigured) camera: the no-auth identity + credential inputs.
function candidateForm(c) {
  const name = el("input", { placeholder: t("add.namePlaceholder") });
  const user = el("input", { placeholder: t("add.username"), value: c.suggested_username || "admin" });
  const pass = el("input", { placeholder: t("add.password"), type: "password" });
  const path = el("input", { placeholder: t("add.pathPlaceholder"), value: c.suggested_path || "/onvif1" });
  const add = el("button", { className: "btn-primary block", textContent: t("add.add") });
  const err = el("p", { className: "error" });   // credential/other add errors, shown in the card
  const vendor = [c.vendor, c.model].filter(Boolean).join(" ").trim();
  add.addEventListener("click", async () => {
    add.disabled = true; add.textContent = t("add.adding"); err.textContent = "";   // add also validates + probes
    try {
      await api("/cameras", { method: "POST", body: JSON.stringify({
        mac: c.mac, name: name.value, username: user.value, password: pass.value,
        stream_path: path.value, last_ip: c.ip, vendor: vendor || null,
      }) });
      state.candidates = state.candidates.filter((x) => x.mac !== c.mac);   // now configured
      await loadCameras();                                                  // re-renders the tab
    } catch (e) { err.textContent = e.message; add.disabled = false; add.textContent = t("add.add"); }
  });
  // Identity we could read WITHOUT a login (no-auth ONVIF).
  const bits = [];
  if (vendor) bits.push(vendor);
  if (c.firmware) bits.push(t("add.fw", { v: c.firmware }));
  if (c.driver && c.driver !== "generic") bits.push(t("add.driver", { d: c.driver }));
  const ident = bits.length
    ? el("small", { className: "ident", textContent: t("add.identified", { bits: bits.join(" · ") }) })
    : el("small", { className: "ident muted", textContent: t("add.unknown") });
  return el("div", { className: "cam-card available" },
    el("div", { className: "cam-card-head" },
      el("span", { className: "tag" }, "NEW"),
      el("strong", { textContent: c.mac }),
    ),
    el("small", { className: "muted", textContent: t("add.portsLine", { ip: c.ip, ports: c.open_ports.join(", ") }) }),
    ident,
    name, user, pass, path, add, err,
  );
}

// Manage card for a configured camera: identity + status + probe/remove.
function configuredCard(cam) {
  const rec = el("span", { className: "rec" + (cam.recording ? " on" : ""),
    title: cam.recording ? t("cam.recording") : t("cam.idle") });
  const meta = [cam.vendor, (cam.capabilities || {}).driver].filter(Boolean).join(" · ");
  return el("div", { className: "cam-card configured" },
    el("div", { className: "cam-card-head" },
      rec,
      el("strong", { className: "name", textContent: cam.name || t("cam.unnamed") }),
    ),
    el("small", { className: "muted", textContent: [cam.last_ip, meta].filter(Boolean).join(" · ") }),
    capBadges(cam),
    el("span", { style: "flex:1" }),
    el("div", { className: "cam-card-actions" }, probeBtn(cam), removeBtn(cam)),
  );
}

function renderCameras(stage) {
  stage.innerHTML = "";
  const seg = el("div", { className: "seg cam-filter" });
  ["all", "available", "configured"].forEach((f) => {
    const b = el("button", { className: state.camFilter === f ? "active" : "", textContent: t("cams.filter_" + f) });
    b.addEventListener("click", () => { state.camFilter = f; renderCameras(stage); });
    seg.append(b);
  });
  const scan = el("button", { className: "btn-primary" + (_scanning ? " spin" : ""),
    disabled: _scanning, innerHTML: svgIcon("i-scan") + `<span>${t("nav.scan")}</span>` });
  scan.addEventListener("click", runScan);
  stage.append(el("div", { className: "cam-toolbar" }, seg, el("span", { style: "flex:1" }), scan));

  if (_scanError) stage.append(el("p", { className: "error", textContent: t("cams.scanFailed", { msg: _scanError }) }));
  if (_scanning) { stage.append(loadingBlock(t("scan.scanning"))); return; }

  // Configured and available cameras live in separate sections, each its own grid, so a short
  // manage card never stretches to match a tall config card. The filter shows/hides whole sections.
  if (state.camFilter !== "available") {
    stage.append(camSection(t("cams.headingConfigured"),
      state.cameras.map(configuredCard), t("cams.noneConfigured")));
  }
  if (state.camFilter !== "configured") {
    stage.append(camSection(t("cams.headingAvailable"),
      state.candidates.map(candidateForm), t("cams.noneAvailable")));
  }
}

// A titled section (heading + count) wrapping its own grid of cards, or an empty-state hint.
function camSection(title, cards, emptyMsg) {
  const body = cards.length
    ? el("div", { className: "cam-grid" })
    : el("p", { className: "muted empty-hint", textContent: emptyMsg });
  cards.forEach((c) => body.append(c));
  return el("section", { className: "cam-section" },
    el("h3", { className: "cam-section-title" },
      el("span", { textContent: title }),
      el("span", { className: "count", textContent: String(cards.length) })),
    body);
}

// --- recordings browser (view) -----------------------------------------------------
function field(label, input) {
  return el("label", { className: "field" }, el("span", { textContent: label }), input);
}

function renderRecordings(stage) {
  stage.innerHTML = "";   // rebuilt each time the view is entered (no live camera frames here)
  const r = state.rec;
  const today = new Date().toISOString().slice(0, 10);
  if (!r.from) r.from = today;
  if (!r.to) r.to = today;
  const nameOf = Object.fromEntries(state.cameras.map((c) => [c.mac, c.name || c.mac]));

  const camSel = el("select");
  camSel.append(el("option", { value: "", textContent: t("rec.allCameras") }));
  state.cameras.forEach((c) => camSel.append(
    el("option", { value: c.mac, textContent: c.name || c.mac, selected: c.mac === r.mac })));
  const fromI = el("input", { type: "date", value: r.from });
  const toI = el("input", { type: "date", value: r.to });
  const search = el("button", { className: "btn-primary", innerHTML: svgIcon("i-scan") + `<span>${t("rec.search")}</span>` });
  const retention = el("span", { className: "muted retention", title: t("rec.retentionHint") });

  const player = el("video", { className: "rec-player", controls: true });
  const info = el("span", { className: "muted" });
  const prev = el("button", { textContent: t("rec.prev") });
  const next = el("button", { textContent: t("rec.next") });
  const list = el("div", { className: "rec-list" });

  async function load() {
    r.mac = camSel.value; r.from = fromI.value; r.to = toI.value;
    list.innerHTML = `<p class='muted'>${t("rec.loading")}</p>`;
    const qs = new URLSearchParams({
      mac: r.mac, day_from: r.from, day_to: r.to,
      limit: r.pageSize, offset: r.page * r.pageSize,
    });
    const res = await api("/recordings?" + qs.toString());
    retention.textContent = res.retention_days
      ? t("rec.retention", { days: res.retention_days })
      : t("rec.retentionOff");
    const first = res.total ? r.page * r.pageSize + 1 : 0;
    const last = Math.min((r.page + 1) * r.pageSize, res.total);
    info.textContent = t("rec.range", { first, last, total: res.total });
    prev.disabled = r.page === 0;
    next.disabled = (r.page + 1) * r.pageSize >= res.total;
    list.innerHTML = "";
    if (!res.items.length) {
      list.append(el("p", { className: "muted", textContent: t("rec.none") }));
      return;
    }
    res.items.forEach((s) => {
      const row = el("div", { className: "rec-row" },
        el("span", { textContent: `${s.day} ${s.started_at.slice(11, 19)}` }),
        el("small", { textContent: `${nameOf[s.mac] || s.mac} · ${(s.size_bytes / 1e6).toFixed(1)} MB` }),
      );
      row.addEventListener("click", () => {
        list.querySelectorAll(".rec-row.active").forEach((n) => n.classList.remove("active"));
        row.classList.add("active");
        player.src = "/api/recordings/file?path=" + encodeURIComponent(s.path);
        player.play().catch(() => {});   // seekable now; user drives the scrubber
      });
      list.append(row);
    });
  }

  search.addEventListener("click", () => { r.page = 0; load(); });
  prev.addEventListener("click", () => { if (r.page > 0) { r.page--; load(); } });
  next.addEventListener("click", () => { r.page++; load(); });

  stage.append(el("div", { className: "rec-screen" },
    el("div", { className: "rec-filter" }, field(t("rec.camera"), camSel), field(t("rec.from"), fromI), field(t("rec.to"), toI), search, retention),
    el("div", { className: "rec-body" },
      el("div", { className: "rec-side" }, list, el("div", { className: "rec-pager" }, prev, info, next)),
      el("div", { className: "rec-main" }, player),
    ),
  ));
  load();
}

// --- language selector -------------------------------------------------------------
// Populate the header <select> and re-render on change. Static labels are filled by applyI18n;
// dynamic strings (camera bars, recordings) are rebuilt by render()/loadStorage() when the
// dashboard is visible so a language switch updates the whole UI without a reload.
function setupLang() {
  const sel = $("#lang");
  const labels = { en: "EN", "pt-BR": "PT" };
  I18N_LANGS.forEach((code) =>
    sel.append(el("option", { value: code, textContent: labels[code] || code, selected: code === getLang() })));
  sel.addEventListener("change", () => {
    setLang(sel.value);
    applyI18n();
    if (!$("#dash").classList.contains("hidden")) { render(); loadStorage(); }
  });
}

// --- boot --------------------------------------------------------------------------
async function boot() {
  const me = await api("/me");
  if (!me.authenticated) { showLogin(); return; }
  showDash();
  const media = await api("/media/streams");
  state.go2rtc = (media.go2rtc_api || "").replace(/\/$/, "");
  state.gridHdMax = media.grid_hd_max_cameras ?? 0;
  await Promise.all([loadCameras(), loadStorage()]);
  setInterval(loadStorage, 15000);
  setInterval(freezeWatchdog, STALL_POLL_MS);   // auto-reload a player whose video has frozen
}

applyI18n();     // fill static labels for the initial language (the login screen shows first)
setupLang();
boot().catch(showLogin);
