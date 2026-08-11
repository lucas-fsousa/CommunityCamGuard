import { t } from "ccg/i18n";
import { api, el, state, svgIcon } from "ccg/core";
import { capBadges, probeBtn, removeBtn } from "ccg/live";

let loadCameras = async () => {};
let setView = () => {};

export function configureCameraManagement(handlers) {
  loadCameras = handlers.loadCameras;
  setView = handlers.setView;
}

function browserIsLoopback() {
  const host = (location.hostname || "").toLowerCase().replace(/\.$/, "");
  return host === "localhost" || host === "::1" || /^127(?:\.\d{1,3}){3}$/.test(host);
}

export async function loadProvisioningStatus() {
  if (!browserIsLoopback()) {
    state.provisioning = { local_only: true, blocked: true };
    return;
  }
  try { state.provisioning = await api("/provisioning/status"); }
  catch (err) { state.provisioning = { local_only: true, blocked: true, error: err.message }; }
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

// Factory-new onboarding lives in a compact on-demand modal. A label photo is decoded in this
// browser and never uploaded. Wi-Fi names are read-only options returned by the local server.
function openProvisioningModal() {
  if (!browserIsLoopback() || (state.provisioning && state.provisioning.blocked)) return;

  const label = el("input", { placeholder: t("provision.label"), autocomplete: "off" });
  const deviceId = el("input", { placeholder: t("provision.deviceId"), inputMode: "numeric", autocomplete: "off" });
  const capability = el("input", { placeholder: t("provision.capability"), autocomplete: "off" });
  const firmware = el("input", { placeholder: t("provision.firmware"), autocomplete: "off" });
  const mac = el("input", { placeholder: t("provision.mac"), autocomplete: "off" });
  const name = el("input", { placeholder: t("add.namePlaceholder"), autocomplete: "off" });
  const network = el("select", { disabled: true });
  const password = el("input", { placeholder: t("provision.wifiPassword"), type: "password",
    autocomplete: "new-password", disabled: true });
  const photo = el("input", { type: "file", accept: "image/*", capture: "environment", className: "label-photo" });
  const result = el("p", { className: "provision-result muted" });
  const error = el("p", { className: "error" });
  const networkError = el("small", { className: "muted" });
  const inspect = el("button", { textContent: t("provision.inspect") });
  const refreshNetworks = el("button", { textContent: t("provision.refreshNetworks") });
  const transportReady = Boolean(state.provisioning && state.provisioning.transport_ready);
  const start = el("button", { className: "btn-primary", textContent: t("provision.start"), disabled: true });

  const identityPayload = () => ({
    label: label.value.trim(), device_id: deviceId.value.trim(),
    capability_code: capability.value.trim(), firmware_version: firmware.value.trim(), mac: mac.value.trim(),
  });
  const updateStart = () => {
    const networkReady = Boolean(network.value);
    password.disabled = !networkReady;
    start.disabled = !transportReady || !networkReady;
    start.title = transportReady ? "" : t("provision.transportPending");
  };

  async function scanNetworks() {
    refreshNetworks.disabled = true; network.disabled = true; networkError.textContent = "";
    network.replaceChildren(el("option", { value: "", textContent: t("provision.scanningNetworks") }));
    try {
      const response = await api("/provisioning/networks");
      network.replaceChildren();
      if (!response.networks.length) {
        network.append(el("option", { value: "", textContent: t("provision.noNetworks") }));
        networkError.textContent = t("provision.noWifiRadio");
      } else {
        network.append(el("option", { value: "", textContent: t("provision.chooseNetwork") }));
        response.networks.forEach((item) => network.append(el("option", {
          value: item.id,
          textContent: `${item.ssid} · ${item.signal}%${item.security ? ` · ${item.security}` : ""}`,
        })));
        network.disabled = false;
      }
    } catch (err) {
      network.replaceChildren(el("option", { value: "", textContent: t("provision.noNetworks") }));
      networkError.textContent = err.message;
    } finally { refreshNetworks.disabled = false; updateStart(); }
  }

  async function inspectIdentity() {
    error.textContent = ""; result.textContent = t("provision.inspecting"); inspect.disabled = true;
    try {
      const info = await api("/provisioning/inspect", { method: "POST", body: JSON.stringify(identityPayload()) });
      deviceId.value = info.device_id; capability.value = info.capability_code;
      if (info.firmware_version) firmware.value = info.firmware_version;
      if (info.mac) mac.value = info.mac;
      result.textContent = t("provision.valid", { id: info.device_id, modes: info.setup_modes.join(", ") });
      return info;
    } catch (err) { result.textContent = ""; error.textContent = err.message; return null; }
    finally { inspect.disabled = false; }
  }

  photo.addEventListener("change", async () => {
    const file = photo.files && photo.files[0];
    if (!file) return;
    error.textContent = ""; result.textContent = t("provision.decoding");
    try {
      if (!("BarcodeDetector" in window)) throw new Error(t("provision.noDecoder"));
      const bitmap = await createImageBitmap(file);
      const codes = await new BarcodeDetector({ formats: ["qr_code"] }).detect(bitmap);
      bitmap.close?.();
      const qr = codes.find((code) => code.rawValue)?.rawValue;
      if (!qr) throw new Error(t("provision.noQr"));
      label.value = qr; await inspectIdentity();
    } catch (err) { result.textContent = ""; error.textContent = err.message; }
    finally { photo.value = ""; }
  });
  inspect.addEventListener("click", inspectIdentity);
  refreshNetworks.addEventListener("click", scanNetworks);
  network.addEventListener("change", updateStart);
  start.addEventListener("click", async () => {
    error.textContent = ""; result.textContent = ""; start.disabled = true;
    try {
      await api("/provisioning/start", { method: "POST", body: JSON.stringify({
        ...identityPayload(), wifi_network_id: network.value, wifi_password: password.value, name: name.value,
      }) });
      password.value = ""; result.textContent = t("provision.started");
    } catch (err) { error.textContent = err.message; }
    finally { updateStart(); }
  });

  const close = el("button", { className: "icon-btn", textContent: "×", title: t("scan.close") });
  const card = el("div", { className: "card modal-card provisioning-modal" },
    el("div", { className: "modal-head" }, el("h2", { textContent: t("provision.title") }), close),
    el("p", { className: "muted compact", textContent: t("provision.description") }),
    label,
    el("div", { className: "provision-inline" }, photo, inspect),
    el("details", {}, el("summary", { textContent: t("provision.manualDetails") }),
      el("div", { className: "provision-grid" }, deviceId, capability, firmware, mac)),
    el("hr"),
    el("div", { className: "provision-network-row" }, network, refreshNetworks), networkError,
    name, password,
    el("small", { className: "muted", textContent: t("provision.wifiHint") }),
    !transportReady ? el("small", { className: "muted", textContent: t("provision.transportPending") }) : "",
    el("div", { className: "provision-actions" }, start), result, error);
  const overlay = el("div", { className: "modal" }, card);
  const dismiss = () => { password.value = ""; overlay.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (event) => { if (event.key === "Escape") dismiss(); };
  close.addEventListener("click", dismiss);
  overlay.addEventListener("click", (event) => { if (event.target === overlay) dismiss(); });
  document.addEventListener("keydown", onKey);
  document.body.append(overlay);
  void scanNetworks();
}

export function renderCameras(stage) {
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
  const setup = el("button", {
    disabled: !browserIsLoopback() || Boolean(state.provisioning && state.provisioning.blocked),
    title: browserIsLoopback() ? t("provision.open") : t("provision.localOnly"),
    innerHTML: svgIcon("i-cam") + `<span>${t("provision.open")}</span>`,
  });
  setup.addEventListener("click", openProvisioningModal);
  stage.append(el("div", { className: "cam-toolbar" }, seg, el("span", { style: "flex:1" }), setup, scan));

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
