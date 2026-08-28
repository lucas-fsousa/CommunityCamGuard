import { $, api, el, onUnauthorized, state } from "ccg/core";
import { applyI18n, getLang, I18N_LANGS, setLang, t } from "ccg/i18n";
import {
  applyLiveLayout,
  configureLiveCameraHandlers,
  ensureSelected,
  freezeWatchdog,
  reconcilePlayerSources,
  renderPlayers,
  setPlayersLive,
  STALL_POLL_MS,
  syncCameraStatusDots,
} from "ccg/live";
import {
  configureCameraManagement,
  loadProvisioningStatus,
  renderCameras,
} from "ccg/cameras";
import { renderRecordings } from "ccg/recordings";

const APP_VERSION = window.__CCG_BUILD__ || "dev";
console.log("[CCG] frontend build " + APP_VERSION);

const versionBadge = document.getElementById("app-version");
if (versionBadge) versionBadge.textContent = "build " + APP_VERSION;

let storageTimer = 0;
let watchdogTimer = 0;
let cameraStatusTimer = 0;

function stopDashboardSession() {
  if (storageTimer) {
    clearInterval(storageTimer);
    storageTimer = 0;
  }
  if (watchdogTimer) {
    clearInterval(watchdogTimer);
    watchdogTimer = 0;
  }
  if (cameraStatusTimer) {
    clearInterval(cameraStatusTimer);
    cameraStatusTimer = 0;
  }
  setPlayersLive(false);
}

function showLogin() {
  stopDashboardSession();
  $("#login").classList.remove("hidden");
  $("#dash").classList.add("hidden");
}

function showDash() {
  $("#login").classList.add("hidden");
  $("#dash").classList.remove("hidden");
}

async function loadStorage() {
  try {
    const storage = await api("/storage");
    const gigabytes = (bytes) => (bytes / 1e9).toFixed(0);
    const box = $("#storage");
    box.className = "storage " + storage.status;
    box.textContent = t("storage.disk", {
      pct: storage.used_percent,
      gb: gigabytes(storage.free_bytes),
    }) + (storage.saving_paused ? t("storage.paused") : "");
  } catch {}
}

function applyView() {
  applyLiveLayout();
  if (state.view === "cameras") renderCameras($("#cameras"));
  if (state.view === "recordings") renderRecordings($("#recordings"));
}

function render() {
  ensureSelected();
  renderPlayers();
  applyView();
}

function setView(view) {
  state.view = view;
  ensureSelected();
  reconcilePlayerSources();
  document.querySelectorAll(".views button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  applyView();
}

async function loadCameras() {
  state.cameras = await api("/cameras");
  render();
}

async function loadCameraStatuses() {
  try {
    const statuses = await api("/cameras/status");
    const byId = new Map(statuses.map((item) => [item.id, item]));
    state.cameras.forEach((camera) => Object.assign(camera, byId.get(camera.id) || {}));
    syncCameraStatusDots();
  } catch {}
}

function setupLanguageSelector() {
  const selector = $("#lang");
  const labels = { en: "EN", "pt-BR": "PT" };
  I18N_LANGS.forEach((code) => selector.append(el("option", {
    value: code,
    textContent: labels[code] || code,
    selected: code === getLang(),
  })));
  selector.addEventListener("change", () => {
    setLang(selector.value);
    applyI18n();
    if (!$("#dash").classList.contains("hidden")) {
      render();
      loadStorage();
    }
  });
}

async function boot() {
  const me = await api("/me");
  if (!me.authenticated) {
    showLogin();
    return;
  }
  showDash();
  const media = await api("/media/streams");
  state.go2rtc = (media.go2rtc_api || "").replace(/\/$/, "");
  state.gridHdMax = media.grid_hd_max_cameras ?? 0;
  await Promise.all([loadCameras(), loadStorage(), loadProvisioningStatus()]);
  if (!storageTimer) storageTimer = setInterval(loadStorage, 15000);
  if (!watchdogTimer) watchdogTimer = setInterval(freezeWatchdog, STALL_POLL_MS);
  if (!cameraStatusTimer) cameraStatusTimer = setInterval(loadCameraStatuses, 5000);
}

onUnauthorized(showLogin);
configureLiveCameraHandlers({ reloadCameras: loadCameras, refreshView: applyView });
configureCameraManagement({ loadCameras, setView });

document.querySelectorAll(".views button").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/login", {
      method: "POST",
      body: JSON.stringify({ key: $("#login-key").value }),
    });
    $("#login-key").value = "";
    await boot();
  } catch {
    $("#login-error").textContent = t("login.invalid");
  }
});

$("#btn-logout").addEventListener("click", async () => {
  await api("/logout", { method: "POST" });
  showLogin();
});

applyI18n();
setupLanguageSelector();
boot().catch(showLogin);
