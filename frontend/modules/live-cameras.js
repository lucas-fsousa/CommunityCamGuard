import { t } from "ccg/i18n";
import { $, api, el, state, svgIcon } from "ccg/core";

let reloadCameras = async () => {};
let refreshView = () => {};

export function configureLiveCameraHandlers(handlers) {
  reloadCameras = handlers.reloadCameras;
  refreshView = handlers.refreshView;
}

export function cameraStatusDot(cam) {
  const dot = el("span", {
    className: "camera-status" + (cam.online ? " online" : " offline"),
    title: t(cam.online ? "cam.online" : "cam.offline"),
  });
  dot.dataset.mac = cam.mac;
  return dot;
}

export function syncCameraStatusDots() {
  const byMac = new Map(state.cameras.map((camera) => [camera.mac, camera]));
  document.querySelectorAll(".camera-status[data-mac]").forEach((dot) => {
    const camera = byMac.get(dot.dataset.mac);
    if (!camera) return;
    dot.classList.toggle("online", Boolean(camera.online));
    dot.classList.toggle("offline", !camera.online);
    dot.title = t(camera.online ? "cam.online" : "cam.offline");
  });
}

// --- camera tiles ------------------------------------------------------------------
// Every stream the player is pointed at is H.264 + AAC/Opus. VideoRTC negotiates WebRTC and MSE
// together, then its codec weights prefer WebRTC for these tracks; MSE remains the transport fallback.
// This matters for remote viewers — MSE is WebSocket/TCP, so a 1080p feed over the internet stalls
// to rebuffer on any jitter/loss ("travando toda hora"); WebRTC's UDP + jitter buffer rides over it.
// Diagnostics previously showed the HD tile landing on MSE while the substream got WebRTC; keeping
// H.264+Opus available makes WebRTC win that selection on normal browsers.
// The <cam-player> element (player.js) sets mode="webrtc,mse"; here we only hand it the signalling
// WebSocket. We point it at the app's OWN origin (/api/go2rtc/ws), which proxies to go2rtc: this
// keeps the player same-origin (so the freeze watchdog can read the real <video>) without opening
// go2rtc's unauthenticated API cross-origin. A leading "/" makes VideoRTC build ws://<app-origin>/…
function wsFor(streamId) {
  return `/api/go2rtc/ws?src=${encodeURIComponent(streamId)}`;
}

// Small pills advertising probed capabilities (video/audio codecs, PTZ support).
export function capBadges(cam) {
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
    api(`/cameras/${encodeURIComponent(cam.id)}/ptz`, {
      method: "POST", body: JSON.stringify({ action, direction }),
    })
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
export function removeBtn(cam) {
  const del = el("button", { className: "icon-btn danger", title: t("cam.remove"), innerHTML: svgIcon("i-trash") });
  del.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (confirm(t("cam.removeConfirm", { name: cam.name || cam.mac }))) {
      await api("/cameras/" + encodeURIComponent(cam.id), { method: "DELETE" });
      reloadCameras();
    }
  });
  return del;
}

export function probeBtn(cam) {
  const probe = el("button", { className: "icon-btn", title: t("cam.probe"), innerHTML: svgIcon("i-probe") });
  probe.addEventListener("click", async (e) => {
    e.stopPropagation();
    probe.disabled = true; probe.classList.add("spin");
    try {
      await api(`/cameras/${encodeURIComponent(cam.id)}/probe`, { method: "POST" });
      await reloadCameras();
    }
    catch (err) { alert(t("cam.probeFailed", { msg: err.message })); }
    finally { probe.disabled = false; probe.classList.remove("spin"); }
  });
  return probe;
}

function camBar(cam) {
  const status = cameraStatusDot(cam);
  const del = removeBtn(cam);
  const probe = probeBtn(cam);
  const reload = el("button", { className: "icon-btn", title: t("cam.restart"), innerHTML: svgIcon("i-refresh") });
  reload.addEventListener("click", (e) => {
    e.stopPropagation();
    // A browser-only replacement reconnects to the same hot FFmpeg producer and therefore keeps
    // any delay already accumulated there. The explicit user action means "back to live": detach
    // this consumer, cycle only this camera's local H.264 producer, then create a clean player.
    void refreshPlayer(cam.id, reload, true);
  });
  const caps = cam.capabilities || {};

  // A single compact row: identity on the left, controls on the right. Keeping it one line is what
  // keeps the footer small (the go2rtc player already adds its own control strip above us).
  const actions = el("span", { className: "bar-actions" });
  if (caps.ptz) actions.append(ptzControls(cam));
  if (cam.has_quality_variants) actions.append(qualityControls(cam));
  if (Object.keys(cam.controls || {}).length) {
    actions.append(cameraControls(cam));
  }
  actions.append(zoomControls(cam), reload, probe, del);
  return el("div", { className: "bar" },
    status,
    el("span", { className: "name", textContent: cam.name || t("cam.unnamed") }),
    el("span", { className: "ip", textContent: cam.last_ip || "" }),
    capBadges(cam),
    el("span", { className: "bar-spacer" }),
    actions,
  );
}

// Camera controls are grouped behind one compact menu. Nothing is read automatically when a
// tile renders: opening the dashboard must not create extra P2P sessions on resource-limited
// cameras. Each option is an explicit target state; the backend performs its own preflight and
// skips the write when the camera is already in that state.
const PROTECTION_WEEKDAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

async function openDynamicChoice(cam, controlKey, status, trigger, strings) {
  trigger.disabled = true;
  status.classList.remove("error");
  status.textContent = strings.loading;
  try {
    const response = await api(
      `/cameras/${encodeURIComponent(cam.id)}/controls/${encodeURIComponent(controlKey)}/options`,
    );
    const options = Array.isArray(response.options) ? response.options : [];
    if (!options.length) throw new Error(strings.empty);
    const select = el("select", { className: "camera-control-select", required: true });
    select.append(el("option", {
      value: "", textContent: strings.placeholder, disabled: true, selected: true,
    }));
    const groups = new Map();
    for (const option of options) {
      const parent = option.group
        ? groups.get(option.group) || (() => {
          const group = el("optgroup", { label: t(`control.optionGroup.${option.group}`) });
          groups.set(option.group, group);
          select.append(group);
          return group;
        })()
        : select;
      parent.append(el("option", {
        value: option.value,
        textContent: option.detail ? `${option.label} · ${option.detail}` : option.label,
      }));
    }
    const modalStatus = el("small", { className: "camera-control-status" });
    const apply = el("button", { className: "btn-primary", textContent: strings.apply });
    const close = el("button", {
      className: "icon-btn", textContent: "×", title: t("scan.close"), type: "button",
    });
    const card = el("div", { className: "card modal-card dynamic-choice-modal" },
      el("div", { className: "modal-head" },
        el("h2", { textContent: strings.title }), close),
      el("p", { className: "muted compact", textContent: strings.hint }),
      select,
      el("div", { className: "schedule-actions" }, modalStatus, apply));
    const overlay = el("div", { className: "modal" }, card);
    close.addEventListener("click", () => overlay.remove());
    apply.addEventListener("click", async () => {
      if (!select.value) {
        modalStatus.classList.add("error");
        modalStatus.textContent = strings.required;
        return;
      }
      apply.disabled = true;
      modalStatus.classList.remove("error");
      modalStatus.textContent = t("control.applying");
      try {
        await api(
          `/cameras/${encodeURIComponent(cam.id)}/controls/${encodeURIComponent(controlKey)}`,
          { method: "PUT", body: JSON.stringify({ value: select.value }) },
        );
        status.textContent = t("control.applied");
        overlay.remove();
      } catch (error) {
        modalStatus.classList.add("error");
        modalStatus.textContent = t("control.failed", { msg: error.message });
      } finally {
        apply.disabled = false;
      }
    });
    document.body.append(overlay);
    status.textContent = "";
  } catch (error) {
    status.classList.add("error");
    status.textContent = t("control.failed", { msg: error.message });
  } finally {
    trigger.disabled = false;
  }
}

async function openProtectionSchedule(cam, status, trigger) {
  trigger.disabled = true;
  status.classList.remove("error");
  status.textContent = t("control.scheduleLoading");
  try {
    const current = await api(
      `/cameras/${encodeURIComponent(cam.id)}/controls/smart_protection_schedule`,
    );
    const value = current.value || {};
    const start = el("input", { type: "time", required: true, value: value.start || "00:00" });
    const end = el("input", { type: "time", required: true, value: value.end || "00:00" });
    const selected = new Set(value.weekdays || []);
    const dayInputs = PROTECTION_WEEKDAYS.map((day) => {
      const input = el("input", { type: "checkbox", checked: selected.has(day) });
      return { day, input, label: el("label", { className: "schedule-day" }, input,
        el("span", { textContent: t(`weekday.${day}`) })) };
    });
    const modalStatus = el("small", { className: "camera-control-status" });
    const save = el("button", { className: "btn-primary", textContent: t("control.scheduleSave") });
    const close = el("button", {
      className: "icon-btn", textContent: "×", title: t("scan.close"), type: "button",
    });
    const card = el("div", { className: "card modal-card protection-schedule-modal" },
      el("div", { className: "modal-head" },
        el("h2", { textContent: t("control.scheduleTitle", { name: cam.name || cam.mac }) }), close),
      el("p", { className: "muted compact", textContent: t("control.scheduleHint") }),
      el("div", { className: "schedule-times" },
        el("label", {}, el("span", { textContent: t("control.scheduleStart") }), start),
        el("label", {}, el("span", { textContent: t("control.scheduleEnd") }), end)),
      el("strong", { className: "schedule-days-title", textContent: t("control.scheduleDays") }),
      el("div", { className: "schedule-days" }, ...dayInputs.map((item) => item.label)),
      el("div", { className: "schedule-actions" }, modalStatus, save));
    const overlay = el("div", { className: "modal" }, card);
    close.addEventListener("click", () => overlay.remove());
    save.addEventListener("click", async () => {
      const weekdays = dayInputs.filter((item) => item.input.checked).map((item) => item.day);
      if (!start.value || !end.value || !weekdays.length) {
        modalStatus.classList.add("error");
        modalStatus.textContent = t("control.scheduleInvalid");
        return;
      }
      save.disabled = true;
      modalStatus.classList.remove("error");
      modalStatus.textContent = t("control.applying");
      try {
        await api(
          `/cameras/${encodeURIComponent(cam.id)}/controls/smart_protection_schedule`,
          { method: "PUT", body: JSON.stringify({ value: {
            start: start.value, end: end.value, weekdays,
          } }) },
        );
        status.textContent = t("control.applied");
        overlay.remove();
      } catch (error) {
        modalStatus.classList.add("error");
        modalStatus.textContent = t("control.failed", { msg: error.message });
      } finally {
        save.disabled = false;
      }
    });
    document.body.append(overlay);
    status.textContent = "";
  } catch (error) {
    status.classList.add("error");
    status.textContent = t("control.failed", { msg: error.message });
  } finally {
    trigger.disabled = false;
  }
}

function cameraControls(cam) {
  const available = cam.controls || {};
  const status = el("small", { className: "camera-control-status" });
  const menu = el("div", { className: "camera-control-menu" });

  const actionSelect = (
    placeholder, options, controlKey, valueFor, confirmFor = null, success = null,
  ) => {
    const select = el("select", { className: "camera-control-select", title: placeholder });
    select.append(el("option", { value: "", textContent: placeholder, disabled: true, selected: true }));
    for (const [value, label] of options) {
      select.append(el("option", { value, textContent: label }));
    }
    select.addEventListener("click", (event) => event.stopPropagation());
    select.addEventListener("change", async (event) => {
      event.stopPropagation();
      if (!select.value) return;
      const selected = select.value;
      if (confirmFor && !confirmFor(selected)) {
        select.selectedIndex = 0;
        return;
      }
      select.disabled = true;
      status.classList.remove("error");
      status.textContent = t("control.applying");
      try {
        await api(`/cameras/${encodeURIComponent(cam.id)}/controls/${encodeURIComponent(controlKey)}`, {
          method: "PUT",
          body: JSON.stringify({ value: valueFor(selected) }),
        });
        status.textContent = success || t("control.applied");
      } catch (error) {
        status.classList.add("error");
        status.textContent = t("control.failed", { msg: error.message });
      } finally {
        select.selectedIndex = 0;
        select.disabled = false;
      }
    });
    return select;
  };

  if (available.white_light?.writable) {
    menu.append(actionSelect(t("control.whiteLight"), [
      ["on", t("control.lightOn")],
      ["off", t("control.lightOff")],
    ], "white_light", (value) => value === "on"));
  }
  if (available.orientation?.writable) {
    menu.append(actionSelect(t("control.orientation"), [
      ["normal", t("control.orientationNormal")],
      ["inverted", t("control.orientationInverted")],
    ], "orientation", (orientation) => orientation));
  }
  if (available.smart_protection?.writable) {
    menu.append(actionSelect(t("control.smartProtection"), [
      ["on", t("control.smartProtectionOn")],
      ["off", t("control.smartProtectionOff")],
    ], "smart_protection", (value) => value === "on"));
  }
  if (available.smart_protection_schedule?.writable) {
    const schedule = el("button", {
      className: "camera-control-schedule-btn",
      textContent: t("control.scheduleOpen"),
      type: "button",
    });
    schedule.addEventListener("click", (event) => {
      event.stopPropagation();
      void openProtectionSchedule(cam, status, schedule);
    });
    menu.append(schedule);
  }
  if (available.siren_pulse?.writable) {
    menu.append(actionSelect(t("control.siren"), [
      ["2", t("control.sirenSeconds", { seconds: 2 })],
      ["5", t("control.sirenSeconds", { seconds: 5 })],
      ["10", t("control.sirenSeconds", { seconds: 10 })],
    ], "siren_pulse", (seconds) => Number(seconds),
      (seconds) => window.confirm(t("control.sirenConfirm", { seconds })),
      t("control.sirenComplete"),
    ));
  }
  if (available.alarm_voice?.writable && available.alarm_voice?.dynamic_options) {
    const alarmVoice = el("button", {
      className: "camera-control-schedule-btn",
      textContent: t("control.alarmVoiceOpen"),
      type: "button",
    });
    alarmVoice.addEventListener("click", (event) => {
      event.stopPropagation();
      void openDynamicChoice(cam, "alarm_voice", status, alarmVoice, {
        loading: t("control.alarmVoiceLoading"),
        empty: t("control.alarmVoiceEmpty"),
        placeholder: t("control.alarmVoicePlaceholder"),
        title: t("control.alarmVoiceTitle", { name: cam.name || cam.mac }),
        hint: t("control.alarmVoiceHint"),
        apply: t("control.alarmVoiceApply"),
        required: t("control.alarmVoiceRequired"),
      });
    });
    menu.append(alarmVoice);
  }
  if (available.speaker_volume?.writable) {
    menu.append(actionSelect(t("control.speakerVolume"), [
      ["0", t("control.volumePercent", { percent: 0 })],
      ["25", t("control.volumePercent", { percent: 25 })],
      ["50", t("control.volumePercent", { percent: 50 })],
      ["75", t("control.volumePercent", { percent: 75 })],
      ["100", t("control.volumePercent", { percent: 100 })],
    ], "speaker_volume", (percent) => Number(percent)));
  }
  if (available.night_vision?.writable) {
    menu.append(actionSelect(t("control.nightVision"), [
      ["automatic", t("control.nightVisionAutomatic")],
      ["daytime", t("control.nightVisionDaytime")],
      ["night", t("control.nightVisionNight")],
    ], "night_vision", (mode) => mode));
  }
  menu.append(status);

  const details = el("details", { className: "camera-controls" },
    el("summary", { className: "icon-btn", textContent: "⚙", title: t("control.menu") }),
    menu,
  );
  details.addEventListener("click", (event) => event.stopPropagation());
  return details;
}

// Which go2rtc stream a tile should pull, weighing picture against CPU.
//
// Both variants are served by the local media hub (the browser cannot take the cameras' HEVC).
// `_hd` is one shared, preheated 1080p transcode per camera; `_web` is a local 640px derivative.
// Neither browser choice opens another RTSP connection to the camera. Single view takes `_hd`.
// The grid takes it
// too by default: maximum resolution is the product policy. A user on a weaker host can select
// Auto (respect `grid_hd_max_cameras`) or SD (always `_web`) per camera.
// Per-camera quality preference, chosen by the user and kept client-side (no server round-trip,
// no go2rtc restart — the variants already exist as separate streams, so switching is instant).
// "sharp" = force the main 1080p feed (default); "auto" = respect the host budget;
// "smooth" = force the cheap substream.
const QUALITY_PREFS = ["auto", "sharp", "smooth"];
function qualityPref(mac) {
  // Max resolution is the product default. Auto and SD are explicit user choices for hosts that
  // cannot sustain one full-resolution transcode per visible camera.
  try { return localStorage.getItem("ccg.quality." + mac) || "sharp"; } catch { return "sharp"; }
}
function setQualityPref(mac, val) {
  try { localStorage.setItem("ccg.quality." + mac, val); } catch { /* private mode: session-only */ }
}

function streamFor(cam) {
  const pref = qualityPref(cam.mac);
  if (pref === "sharp") return cam.hd_stream_id;
  if (pref === "smooth") return cam.web_stream_id;
  // auto: only the selected single-view camera is HD; the grid follows the configured host budget.
  // This policy is opt-in. The default above remains HD for every camera.
  const hd = state.view === "single"
    ? cam.mac === state.selected
    : state.cameras.length <= state.gridHdMax;
  return hd ? cam.hd_stream_id : cam.web_stream_id;
}

function camFrame(cam) {
  const sid = streamFor(cam);
  const frame = el("cam-player", { className: "frame" });
  frame.dataset.src = sid;   // so a view switch can tell if it must reconnect
  frame.addEventListener("media-diagnostic", (e) => reportMediaEvent(cam, frame, e.detail || {}));
  frame.src = wsFor(sid);    // VideoRTC .src setter kicks off the WebSocket/WebRTC connect
  applyZoom(cam.mac, frame);   // a rebuilt frame (restart / resume) keeps its zoom
  return frame;
}

function reportMediaEvent(cam, frame, detail) {
  if (!cam || !detail.event || document.hidden) return;
  const d = typeof frame.diagnostics === "function" ? frame.diagnostics() : {};
  // The API schema deliberately accepts only scalars. Flatten the player/RTC snapshot and omit
  // undefined/NaN values, which keeps every event compact enough to inspect as one log line.
  const metrics = {};
  for (const [key, value] of Object.entries({ ...d, ...detail })) {
    if (key === "event" || value === undefined || value === null) continue;
    if (["boolean", "string"].includes(typeof value) ||
        (typeof value === "number" && Number.isFinite(value))) metrics[key] = value;
  }
  void api("/media/client-event", {
    method: "POST",
    body: JSON.stringify({ event: detail.event, camera_id: cam.id, stream: frame.dataset.src, metrics }),
  }).catch((err) => console.debug("media diagnostics unavailable", err));
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
// (Auto / HD / SD) instead of cycling blindly. Both choices are local server streams; changing it
// reconnects just this player and never creates another connection to the camera.
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
    refreshPlayer(cam.id);   // rebuild this tile's iframe against the new source
  });
  return sel;
}

// Restart a single camera's player. A lightweight replacement is enough for view/quality changes;
// an explicit recovery also cycles the server's local H.264 producer so an upstream backlog cannot
// survive this action the way it survives F5. Neither path reconnects the base camera/recorder feed.
async function refreshPlayer(cameraId, btn, restartProducer = false) {
  const cam = state.cameras.find((candidate) => candidate.id === cameraId);
  const t = cam ? tiles.get(cam.mac) : null;
  if (!t || !cam) return;
  if (btn) { btn.disabled = true; btn.classList.add("spin"); }
  try {
    if (restartProducer) {
      const old = t.el.firstChild;
      // Detach the browser before asking the server to cycle its preload; otherwise this consumer
      // would keep the old FFmpeg producer alive and the recovery would be a no-op.
      if (old && old.tagName === "CAM-PLAYER" && typeof old.dispose === "function") old.dispose();
      const placeholder = suspendedFrame();
      if (old) old.replaceWith(placeholder); else t.el.prepend(placeholder);
      producerProgress.delete(cam.mac);
      try { await api(`/media/recover/${encodeURIComponent(cameraId)}`, { method: "POST" }); }
      catch (err) {
        console.warn("[stream recovery] local producer recovery failed", cameraId, err);
      }
      if (placeholder.isConnected) placeholder.replaceWith(camFrame(cam));
      return;
    }
    replacePlayerFrame(t.el, cam);
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove("spin"); }
  }
}

function replacePlayerFrame(tile, cam) {
  const old = tile.firstChild;
  // A deliberate replacement is final, unlike a transient DOM move. Dispose synchronously so
  // the old peer cannot reconnect and consume another ffmpeg transcode behind the new tile.
  if (old && old.tagName === "CAM-PLAYER" && typeof old.dispose === "function") old.dispose();
  producerProgress.delete(cam.mac);
  old.replaceWith(camFrame(cam));
}

// Freeze watchdog. The failure we actually see: a WebRTC PeerConnection wedges (lost keyframe, stuck
// decoder) — the picture freezes while go2rtc keeps "sending" packets and the player's timer keeps
// advancing, so no server-side counter (producer OR consumer) reflects it. The only truthful signal
// is the real decoded-frame progress of the <video>, which we can now read because the player is
// same-origin (player.js / <cam-player>, tracked via decoded-frame counters). A watched tile whose
// presented frames stop advancing past FREEZE_MS is rebuilt — a fresh PeerConnection gets a new
// keyframe and recovers. Purely client-side: the freeze is client-side, and recording is independent.
export const STALL_POLL_MS = 3000;       // how often we check
const FREEZE_MS = 10000;          // no newly-presented frame for this long (while playing) = frozen
const RECOVERY_COOLDOWN_MS = 30000; // never flap a persistently bad stream every watchdog tick
const PRODUCER_STALL_STRIKES = 3;    // three unchanged server samples (~9s) confirms a dead transcode
const PRODUCER_STARTUP_GRACE_MS = 45000; // match go2rtc's slow-source window; steady state stays strict
const lastAutoRecovery = new Map();
const producerProgress = new Map();
let _watchdogBusy = false;

function tileIsVisible(tile) {
  // In Single view every unselected tile has display:none. Chromium legitimately stops decoding
  // those streams, which used to make the watchdog "freeze" and rebuild healthy cameras forever.
  // Off-screen grid tiles can be throttled for the same reason, so only repair what the user can see.
  if (!tile || tile.offsetParent === null) return false;
  const r = tile.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 &&
    r.top < window.innerHeight && r.left < window.innerWidth;
}
export async function freezeWatchdog() {
  if (_watchdogBusy || _playersSuspended || document.hidden) return;
  if (state.view !== "grid" && state.view !== "single") return;
  _watchdogBusy = true;
  try {
    let activity = null;
    try { activity = await api("/media/activity"); } catch { /* client counters still work */ }
    tiles.forEach((t, mac) => {
      const frame = t.el.firstChild;
      if (!frame || frame.tagName !== "CAM-PLAYER" || typeof frame.frozenMs !== "function") return;
      if (!tileIsVisible(t.el)) return;
      const sid = frame.dataset.src;
      const server = activity && activity[sid];
      let producerFrozen = false;
      if (server && server.consumers > 0) {
        const prev = producerProgress.get(mac);
        const reset = !prev || prev.sid !== sid;
        const progressed = reset || server.video_packets !== prev.packets;
        const strikes = progressed ? 0 : prev.strikes + 1;
        const firstSeen = reset ? Date.now() : prev.firstSeen;
        producerProgress.set(mac, { sid, packets: server.video_packets, strikes, firstSeen });
        producerFrozen = Date.now() - firstSeen >= PRODUCER_STARTUP_GRACE_MS &&
          strikes >= PRODUCER_STALL_STRIKES;
      } else {
        producerProgress.delete(mac);
      }

      const frozenMs = frame.frozenMs();
      if (!producerFrozen && frozenMs <= FREEZE_MS) return;
      const last = lastAutoRecovery.get(mac) || 0;
      if (Date.now() - last < RECOVERY_COOLDOWN_MS) return;
      lastAutoRecovery.set(mac, Date.now());
      console.warn("[freeze watchdog] rebuilding stalled stream", {
        stream: sid,
        clientFrozenMs: Math.round(frozenMs),
        producerFrozen,
        server,
        player: typeof frame.diagnostics === "function" ? frame.diagnostics() : null,
      });
      reportMediaEvent(state.cameras.find((c) => c.mac === mac), frame, {
        event: "watchdog_recovery",
        clientFrozenMs: Math.round(frozenMs),
        producerFrozen,
        serverVideoPackets: server && server.video_packets,
        serverConsumers: server && server.consumers,
      });
      // Packet counters can advance while an H.264 producer emits an undecodable stream (observed:
      // the browser and an independent FFmpeg both got no frame although video_packets increased).
      // Therefore a confirmed visible freeze must recycle the local HD transcode as well as the
      // consumer. The base RTSP producer and recorder remain shared and untouched.
      const camera = state.cameras.find((candidate) => candidate.mac === mac);
      if (camera) void refreshPlayer(camera.id, null, true);
    });
  } finally {
    _watchdogBusy = false;
  }
}
// When the tab returns to the foreground, browser frame accounting may have been paused while hidden — reset each player's freeze
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
export function setPlayersLive(live) {
  if (live === !_playersSuspended) return;   // already in the desired state
  _playersSuspended = !live;
  tiles.forEach((t, mac) => {
    const frame = t.el.firstChild;
    if (live) {
      const cam = state.cameras.find((c) => c.mac === mac);
      if (cam) replacePlayerFrame(t.el, cam);      // reconnect the stream
    } else if (frame && frame.tagName === "CAM-PLAYER") {
      frame.dispose?.();
      frame.replaceWith(suspendedFrame());         // tear down the stream + its audio
    }
  });
}

// --- render: persistent players, CSS-only view switching ---------------------------
// Camera tiles (iframe + bar) are built once and kept mounted; switching grid<->single
// only toggles CSS, so a running stream is never torn down and re-created (no reload).
const tiles = new Map();   // mac -> { el }

export function ensureSelected() {
  if (state.cameras.length && !state.cameras.some((c) => c.mac === state.selected)) {
    state.selected = state.cameras[0].mac;
  }
}

export function reconcilePlayerSources() {
  if (_playersSuspended) return;
  state.cameras.forEach((cam) => {
    const t = tiles.get(cam.mac);
    const frame = t && t.el.firstChild;
    if (frame && frame.tagName === "CAM-PLAYER" && frame.dataset.src !== streamFor(cam)) {
      replacePlayerFrame(t.el, cam);
    }
  });
}

// Reconcile #players with the camera list without recreating existing tiles. New cameras
// get a tile appended; removed ones are dropped; existing tiles keep their live frame and
// only have their bar refreshed. (Never re-append an existing tile — that moves the iframe
// in the DOM, which reloads it.)
export function renderPlayers() {
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
        replacePlayerFrame(t.el, cam);
      }
    }
  });
}

function buildRail() {
  const rail = $("#rail");
  rail.innerHTML = "";
  state.cameras.forEach((c) => {
    const item = el("div", { className: "rail-item" + (c.mac === state.selected ? " active" : "") },
      cameraStatusDot(c),
      el("span", { className: "rail-name", textContent: c.name || c.mac }),
    );
    item.addEventListener("click", () => {
      state.selected = c.mac;
      reconcilePlayerSources();
      refreshView();
    });
    rail.append(item);
  });
}

// Switch layout without touching the players' DOM (pure CSS via the #stage view class).
export function applyLiveLayout() {
  const stage = $("#stage");
  stage.className = state.view;

  // Live players are live only in grid/single; unload them elsewhere (Recordings, Cameras) so
  // their audio doesn't play under other screens (cross-origin iframes can't be muted from here).
  const liveView = state.view === "grid" || state.view === "single";
  setPlayersLive(liveView);
  // The "no cameras yet" hint belongs to the live views only.
  $("#empty").classList.toggle("hidden", !(liveView && state.cameras.length === 0));

  if (!liveView || !state.cameras.length) return;
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
