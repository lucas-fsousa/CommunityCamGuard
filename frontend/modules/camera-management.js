import { t } from "ccg/i18n";
import { api, el, state, svgIcon } from "ccg/core";
import { cameraStatusDot, capBadges, probeBtn, removeBtn } from "ccg/live";
import { connectProvisioningCamera, supportsWebBluetooth } from "ccg/provisioning-ble";

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
  try { state.provisioning = await api("/provisioning/status"); }
  catch (err) { state.provisioning = { local_only: true, blocked: true, error: err.message }; }
}

function browserCanProvision() {
  return Boolean(state.provisioning?.transport_ready) && !state.provisioning?.blocked;
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
  const status = cameraStatusDot(cam);
  const meta = [cam.vendor, (cam.capabilities || {}).driver].filter(Boolean).join(" · ");
  return el("div", { className: "cam-card configured" },
    el("div", { className: "cam-card-head" },
      status,
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
  if (!browserCanProvision() || (state.provisioning && state.provisioning.blocked)) return;

  const label = el("input", { placeholder: t("provision.label"), autocomplete: "off" });
  const deviceId = el("input", { placeholder: t("provision.deviceId"), inputMode: "numeric", autocomplete: "off" });
  const capability = el("input", { placeholder: t("provision.capability"), autocomplete: "off" });
  const firmware = el("input", { placeholder: t("provision.firmware"), autocomplete: "off" });
  const mac = el("input", { placeholder: t("provision.mac"), autocomplete: "off" });
  const network = el("select", { disabled: true });
  const manualSsid = el("input", { placeholder: t("provision.manualSsid"), autocomplete: "off" });
  const manualSecurity = el("select", {},
    el("option", { value: "wpa", textContent: "WPA/WPA2" }),
    el("option", { value: "open", textContent: t("provision.openNetwork") }),
    el("option", { value: "wep", textContent: "WEP" }));
  const password = el("input", { placeholder: t("provision.wifiPassword"), type: "password",
    autocomplete: "new-password" });
  const photo = el("input", { type: "file", accept: "image/*", capture: "environment",
    className: "label-photo", title: t("provision.photo"), ariaLabel: t("provision.photo") });
  const result = el("p", { className: "provision-result muted" });
  const error = el("p", { className: "error" });
  const networkError = el("small", { className: "muted" });
  const manualHint = el("small", { className: "muted" });
  const readiness = el("small", { className: "provision-readiness muted" });
  const qrBox = el("div", { className: "provision-qr-box hidden" });
  const refreshNetworks = el("button", { textContent: t("provision.refreshNetworks") });
  const transportReady = Boolean(state.provisioning && state.provisioning.transport_ready);
  let bleHandshakeReady = state.provisioning?.transports?.bluetooth === "handshake-ready";
  const remoteBle = !browserIsLoopback() && Boolean(state.provisioning?.remote_ble_enabled);
  const start = el("button", { className: "btn-primary", textContent: t("provision.start"), disabled: true });
  const findBluetooth = el("button", { textContent: t("provision.findBluetooth"), disabled: true });
  const manualFields = [deviceId, capability, firmware, mac];
  const manualNetworkPanel = el("section", { className: "provision-manual-network hidden" },
    el("strong", { textContent: t("provision.manualWifiTitle") }),
    el("small", { className: "muted", textContent: t("provision.manualWifiDescription") }),
    el("div", { className: "provision-network-manual-grid" },
      el("label", {}, el("span", { textContent: t("provision.manualSsidLabel") }), manualSsid),
      el("label", {}, el("span", { textContent: t("provision.securityLabel") }), manualSecurity)),
    el("small", { className: "muted", textContent: t("provision.twoGhzHint") }));
  const vendorAccountType = el("select", {},
    el("option", { value: "email", textContent: t("provision.vendorAccountEmail") }),
    el("option", { value: "mobile", textContent: t("provision.vendorAccountMobile") }),
    el("option", { value: "userId", textContent: t("provision.vendorAccountId") }));
  const vendorAccount = el("input", {
    placeholder: t("provision.vendorAccountIdentity"), autocomplete: "username",
  });
  const vendorMobileArea = el("input", {
    placeholder: t("provision.vendorMobileArea"), inputMode: "numeric", autocomplete: "tel-country-code",
    hidden: true,
  });
  const vendorPassword = el("input", {
    placeholder: t("provision.vendorAccountPassword"), type: "password", autocomplete: "current-password",
  });
  const saveVendorAccount = el("button", {
    className: "btn-primary", textContent: t("provision.vendorAccountSave"),
  });
  const vendorAccountStatus = el("small", { className: "muted" });
  const vendorAccountPanel = el("details", {
    className: "provision-vendor-account",
    open: !state.provisioning?.vendor_account_configured,
    hidden: Boolean(state.provisioning?.vendor_account_configured),
  },
  el("summary", { textContent: t("provision.vendorAccountTitle") }),
  el("small", { className: "muted", textContent: t("provision.vendorAccountDescription") }),
  el("div", { className: "provision-vendor-account-grid" },
    vendorAccountType, vendorAccount, vendorMobileArea, vendorPassword, saveVendorAccount),
  vendorAccountStatus);
  let qrUrl = "";
  let manualNetworkMode = false;
  let identityValid = false;
  let validatingIdentity = false;
  let validationTimer = 0;
  let validationVersion = 0;
  let bleConnecting = false;
  let bleSession = null;
  let bleWaiter = null;
  const bleInbox = new Map();
  let bleProvisioningPending = false;
  let bleAttemptId = "";
  let bleStage = "";
  let bleFinishFrames = [];
  let wifiConfigured = false;
  let privilegedBound = false;

  const clearQr = () => {
    if (qrUrl) URL.revokeObjectURL(qrUrl);
    qrUrl = "";
    qrBox.replaceChildren();
    qrBox.classList.add("hidden");
  };

  const privilegedStatus = el("small", { className: "muted" });
  const cameraName = el("input", {
    placeholder: t("provision.cameraName"), maxLength: 80,
  });
  const finishWifiOnly = el("button", { textContent: t("provision.finishWifiOnly"), disabled: true });
  const bindPrivileged = el("button", {
    className: "btn-primary", textContent: t("provision.bindPrivileged"), disabled: true,
  });
  privilegedStatus.textContent = t("provision.privilegedPending");
  const privilegedPanel = el("section", { className: "provision-privileged" },
    el("strong", { textContent: t("provision.privilegedTitle") }),
    el("small", { className: "muted", textContent: t("provision.privilegedDescription") }),
    cameraName,
    el("small", { className: "muted", textContent: t("provision.cameraNameHint") }),
    privilegedStatus,
    el("div", { className: "provision-actions" }, finishWifiOnly, bindPrivileged));

  const finishBluetooth = async () => {
    if (bleSession && bleFinishFrames.length) {
      await bleSession.writeFrames(encodedFrames(bleFinishFrames));
    }
    bleSession?.disconnect();
    bleSession = null;
    bleFinishFrames = [];
    privilegedPanel.classList.add("hidden");
  };

  const identityPayload = () => ({
    label: label.value.trim(), device_id: deviceId.value.trim(),
    capability_code: capability.value.trim(), firmware_version: firmware.value.trim(), mac: mac.value.trim(),
  });
  const identityHasEnoughInput = () => Boolean(
    label.value.trim() || (deviceId.value.trim().length >= 6 && capability.value.trim()),
  );
  const selectedNetworkNeedsPassword = () => {
    if (manualNetworkMode) return manualSecurity.value !== "open";
    const security = (network.selectedOptions[0]?.dataset.security || "").trim().toLowerCase();
    return !["--", "open", "none"].includes(security);
  };
  const updateManualState = () => {
    const locked = Boolean(label.value.trim() || (photo.files && photo.files.length));
    manualFields.forEach((field) => {
      field.readOnly = locked;
      field.classList.toggle("provision-locked", locked);
    });
    manualHint.textContent = t(locked ? "provision.manualLocked" : "provision.manualAvailable");
  };
  const updateStart = () => {
    start.textContent = bleSession && bleHandshakeReady
      ? t("provision.startBluetooth") : t("provision.start");
    findBluetooth.disabled = !supportsWebBluetooth() || !bleHandshakeReady
      || validatingIdentity || !identityValid || bleConnecting;
    findBluetooth.title = !supportsWebBluetooth()
      ? t("provision.bluetoothUnavailable")
      : !bleHandshakeReady ? t("provision.vendorAccountRequired") : "";
    if (wifiConfigured) {
      start.disabled = true;
      start.title = "";
      readiness.textContent = t("provision.wifiStageComplete");
      readiness.classList.add("ready");
      return;
    }
    if (qrUrl) {
      start.disabled = true;
      start.title = "";
      readiness.textContent = t("provision.qrReady");
      readiness.classList.add("ready");
      return;
    }
    const networkReady = manualNetworkMode ? Boolean(manualSsid.value.trim()) : Boolean(network.value);
    let reason = "";
    if (!transportReady) reason = t("provision.transportPending");
    else if (bleProvisioningPending) reason = t("provision.bluetoothWaitingLan");
    else if (remoteBle && !bleSession) reason = t("provision.connectBluetoothFirst");
    else if (validatingIdentity) reason = t("provision.inspecting");
    else if (!identityValid) reason = t("provision.needIdentity");
    else if (!networkReady) reason = t("provision.needNetwork");
    else if (selectedNetworkNeedsPassword() && !password.value) reason = t("provision.needPassword");
    start.disabled = Boolean(reason);
    start.title = reason;
    readiness.textContent = reason || t("provision.ready");
    readiness.classList.toggle("ready", !reason);
  };

  saveVendorAccount.addEventListener("click", async () => {
    const identity = vendorAccount.value.trim();
    const secret = vendorPassword.value;
    const mobileArea = vendorMobileArea.value.trim();
    if (!identity || !secret || (vendorAccountType.value === "mobile" && !mobileArea)) {
      vendorAccountStatus.textContent = t("provision.vendorAccountMissing");
      return;
    }
    saveVendorAccount.disabled = true;
    vendorAccountStatus.textContent = t("provision.vendorAccountSaving");
    try {
      await api("/provisioning/vendor-account/login", {
        method: "POST",
        body: JSON.stringify({
          account_type: vendorAccountType.value, account: identity, password: secret,
          mobile_area: vendorAccountType.value === "mobile" ? mobileArea : "0",
        }),
      });
      vendorPassword.value = "";
      vendorAccount.value = "";
      bleHandshakeReady = true;
      state.provisioning.vendor_account_configured = true;
      state.provisioning.ble_material_source = "native-account";
      state.provisioning.transports.bluetooth = "handshake-ready";
      vendorAccountStatus.textContent = "";
      vendorAccountPanel.hidden = true;
    } catch (err) {
      vendorAccountStatus.textContent = err.message;
    } finally {
      saveVendorAccount.disabled = false;
      updateStart();
    }
  });
  vendorAccountType.addEventListener("change", () => {
    vendorMobileArea.hidden = vendorAccountType.value !== "mobile";
  });

  async function scanNetworks() {
    clearQr(); refreshNetworks.disabled = true; network.disabled = true; networkError.textContent = "";
    manualNetworkMode = false; manualNetworkPanel.classList.add("hidden"); network.hidden = false;
    network.replaceChildren(el("option", { value: "", textContent: t("provision.scanningNetworks") }));
    try {
      const response = await api("/provisioning/networks");
      network.replaceChildren();
      if (!response.networks.length) {
        network.append(el("option", { value: "", textContent: t("provision.noNetworks") }));
        manualNetworkMode = Boolean(response.manual_entry_allowed);
        manualNetworkPanel.classList.toggle("hidden", !manualNetworkMode);
        network.hidden = manualNetworkMode;
        networkError.textContent = t(manualNetworkMode ? "provision.manualWifiEnabled" : "provision.noWifiRadio");
      } else {
        network.append(el("option", { value: "", textContent: t("provision.chooseNetwork") }));
        response.networks.forEach((item) => {
          const option = el("option", {
            value: item.id,
            textContent: `${item.ssid} · ${item.signal}%${item.security ? ` · ${item.security}` : ""}`,
          });
          option.dataset.security = item.security || "";
          option.dataset.ssid = item.ssid || "";
          network.append(option);
        });
        network.disabled = false;
      }
    } catch (err) {
      network.replaceChildren(el("option", { value: "", textContent: t("provision.noNetworks") }));
      networkError.textContent = err.message;
    } finally { refreshNetworks.disabled = false; updateStart(); }
  }

  async function inspectIdentity(version) {
    validatingIdentity = true; error.textContent = ""; result.textContent = t("provision.inspecting"); updateStart();
    try {
      const info = await api("/provisioning/inspect", { method: "POST", body: JSON.stringify(identityPayload()) });
      if (version !== validationVersion) return null;
      deviceId.value = info.device_id; capability.value = info.capability_code;
      if (info.firmware_version) firmware.value = info.firmware_version;
      if (info.mac) mac.value = info.mac;
      else if (!mac.value.trim()) {
        // The vendor QR encodes device ID + capability bits, but not the MAC printed elsewhere
        // on the same label. Keep only this field editable so final LAN matching can be exact.
        mac.readOnly = false;
        mac.classList.remove("provision-locked");
      }
      identityValid = true;
      result.textContent = t("provision.valid", { id: info.device_id, modes: info.setup_modes.join(", ") });
      try {
        const status = await api("/provisioning/privileged/status", {
          method: "POST", body: JSON.stringify(identityPayload()),
        });
        if (version !== validationVersion) return null;
        if (status?.bound && status?.p2p_access_ready) {
          privilegedBound = true;
          wifiConfigured = true;
          start.hidden = true; findBluetooth.hidden = true;
          finishWifiOnly.disabled = true;
          bindPrivileged.disabled = !info.mac;
          bindPrivileged.textContent = t("provision.completeRetry");
          privilegedPanel.classList.remove("hidden");
          privilegedStatus.textContent = t("provision.boundResume");
        }
      } catch { /* a fresh, not-yet-bound label has no resumable privileged state */ }
      return info;
    } catch (err) {
      if (version === validationVersion) {
        identityValid = false; result.textContent = ""; error.textContent = err.message;
      }
      return null;
    } finally {
      if (version === validationVersion) { validatingIdentity = false; updateStart(); }
    }
  }

  function scheduleIdentityInspection(immediate = false) {
    window.clearTimeout(validationTimer);
    const version = ++validationVersion;
    identityValid = false;
    validatingIdentity = false;
    error.textContent = "";
    clearQr();
    if (!identityHasEnoughInput()) {
      result.textContent = "";
      updateStart();
      return;
    }
    validationTimer = window.setTimeout(() => void inspectIdentity(version), immediate ? 0 : 450);
    updateStart();
  }

  photo.addEventListener("change", async () => {
    const file = photo.files && photo.files[0];
    if (!file) return;
    window.clearTimeout(validationTimer); ++validationVersion;
    updateManualState(); identityValid = false; clearQr(); updateStart();
    error.textContent = ""; result.textContent = t("provision.decoding");
    try {
      if (!("BarcodeDetector" in window)) throw new Error(t("provision.noDecoder"));
      const bitmap = await createImageBitmap(file);
      const codes = await new BarcodeDetector({ formats: ["qr_code"] }).detect(bitmap);
      bitmap.close?.();
      const qr = codes.find((code) => code.rawValue)?.rawValue;
      if (!qr) throw new Error(t("provision.noQr"));
      label.value = qr; updateManualState(); scheduleIdentityInspection(true);
    } catch (err) { result.textContent = ""; error.textContent = err.message; }
    finally { photo.value = ""; updateManualState(); }
  });
  label.addEventListener("input", () => { updateManualState(); scheduleIdentityInspection(); });
  manualFields.forEach((field) => field.addEventListener("input", () => scheduleIdentityInspection()));
  refreshNetworks.addEventListener("click", scanNetworks);
  network.addEventListener("change", () => { clearQr(); updateStart(); });
  manualSsid.addEventListener("input", () => { clearQr(); updateStart(); });
  manualSecurity.addEventListener("change", () => { clearQr(); updateStart(); });
  password.addEventListener("input", () => { clearQr(); updateStart(); });

  const encodedFrames = (frames) => (frames || []).map((encoded) => {
    const raw = atob(encoded);
    return Uint8Array.from(raw, (char) => char.charCodeAt(0));
  });
  const encodeBytes = (bytes) => {
    let binary = "";
    for (const byte of bytes || []) binary += String.fromCharCode(byte);
    return btoa(binary);
  };
  const decodeBleResponse = (message) => api("/provisioning/ble/decode-response", {
    method: "POST",
    body: JSON.stringify({
      ...identityPayload(), attempt_id: bleAttemptId,
      command: message.command, encrypted: message.encrypted,
      time_area: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      time_zone: -new Date().getTimezoneOffset() * 60,
      data_base64: encodeBytes(message.data),
    }),
  });
  const collectSsids = (value, found = []) => {
    if (Array.isArray(value)) value.forEach((item) => collectSsids(item, found));
    else if (value && typeof value === "object") {
      Object.entries(value).forEach(([key, item]) => {
        if (key.toLowerCase() === "ssid" && typeof item === "string") found.push(item);
        else collectSsids(item, found);
      });
    }
    return found;
  };
  const onBleNotification = (_frame, message, notificationError) => {
    if (notificationError) {
      if (!bleWaiter) return;
      const waiter = bleWaiter; bleWaiter = null; window.clearTimeout(waiter.timer);
      waiter.reject(notificationError); return;
    }
    if (!message) return;
    if (bleWaiter && message.command === bleWaiter.command) {
      const waiter = bleWaiter; bleWaiter = null; window.clearTimeout(waiter.timer);
      waiter.resolve(message);
      return;
    }
    const queued = bleInbox.get(message.command) || [];
    queued.push(message);
    bleInbox.set(message.command, queued.slice(-4));
  };
  const waitForBleStage = (expectedCommand, timeout = 12000) => new Promise((resolve, reject) => {
    const queued = bleInbox.get(expectedCommand) || [];
    if (queued.length) {
      const message = queued.shift();
      if (queued.length) bleInbox.set(expectedCommand, queued);
      else bleInbox.delete(expectedCommand);
      resolve(message);
      return;
    }
    const timer = window.setTimeout(() => {
      if (bleWaiter?.command !== expectedCommand) return;
      bleWaiter = null;
      reject(new Error(t("provision.bluetoothTimeout")));
    }, timeout);
    bleWaiter = { command: expectedCommand, resolve, reject, timer };
  });
  const takeQueuedBleStage = (expectedCommand) => {
    const queued = bleInbox.get(expectedCommand) || [];
    if (!queued.length) return null;
    const message = queued.shift();
    if (queued.length) bleInbox.set(expectedCommand, queued);
    else bleInbox.delete(expectedCommand);
    return message;
  };
  const exchangeBleStage = (frames, expectedCommand) => new Promise((resolve, reject) => {
    if (!bleSession) { reject(new Error(t("provision.bluetoothDisconnected"))); return; }
    const timer = window.setTimeout(() => {
      if (bleWaiter?.command !== expectedCommand) return;
      bleWaiter = null;
      reject(new Error(t("provision.bluetoothTimeout")));
    }, 12000);
    bleWaiter = { command: expectedCommand, resolve, reject, timer };
    bleSession.writeFrames(encodedFrames(frames)).catch((writeError) => {
      if (bleWaiter?.command === expectedCommand) bleWaiter = null;
      window.clearTimeout(timer); reject(writeError);
    });
  });

  findBluetooth.addEventListener("click", async () => {
    clearQr(); bleConnecting = true; error.textContent = "";
    result.textContent = t("provision.bluetoothSearching"); updateStart();
    try {
      if (!supportsWebBluetooth()) throw new Error(t("provision.bluetoothUnavailable"));
      bleSession?.disconnect();
      bleSession = null;
      bleSession = await connectProvisioningCamera(deviceId.value, onBleNotification);
      result.textContent = t("provision.bluetoothConnected", { name: bleSession.device.name || deviceId.value });
      privilegedPanel.classList.remove("hidden");
      privilegedStatus.textContent = t("provision.privilegedPending");
    } catch (err) {
      result.textContent = "";
      if (err.name !== "NotFoundError") error.textContent = err.message;
    } finally { bleConnecting = false; updateStart(); }
  });
  finishWifiOnly.addEventListener("click", async () => {
    finishWifiOnly.disabled = true; bindPrivileged.disabled = true; error.textContent = "";
    try {
      await finishBluetooth();
      result.textContent = t("provision.bluetoothConfigured");
    } catch (err) {
      error.textContent = err.message;
      finishWifiOnly.disabled = false; bindPrivileged.disabled = false;
    }
  });
  bindPrivileged.addEventListener("click", async () => {
    finishWifiOnly.disabled = true; bindPrivileged.disabled = true; error.textContent = "";
    privilegedStatus.textContent = t(privilegedBound
      ? "provision.confirmingPrivileged" : "provision.bindingPrivileged");
    try {
      if (!privilegedBound) {
        const response = await api("/provisioning/privileged/bind", {
          method: "POST",
          body: JSON.stringify({
            ...identityPayload(),
            time_area: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
            time_zone: -new Date().getTimezoneOffset() * 60,
          }),
        });
        if (response.p2p_binding !== "bound") throw new Error(t("provision.privilegedBindInvalid"));
        privilegedBound = true;
        bindPrivileged.textContent = t("provision.completeRetry");
      }
      let p2pProbe = null;
      let p2pRouteProbe = null;
      let p2pProbeError = null;
      try {
        p2pProbe = await api("/provisioning/privileged/p2p-probe", {
          method: "POST", body: JSON.stringify(identityPayload()),
        });
        if (p2pProbe?.target_online) {
          p2pRouteProbe = await api("/provisioning/privileged/p2p-route-probe", {
            method: "POST", body: JSON.stringify(identityPayload()),
          });
        }
      } catch (probeError) {
        // Binding is already durable. A transient access-node failure must never re-enable the
        // bind button or encourage the user to submit the one-time enrollment twice.
        p2pProbeError = probeError;
      }
      if (p2pRouteProbe?.direct_handshake) {
        privilegedStatus.textContent = t("provision.completingCamera");
        const completed = await api("/provisioning/privileged/complete", {
          method: "POST",
          body: JSON.stringify({ ...identityPayload(), name: cameraName.value.trim() }),
        });
        if (completed?.status !== "configured") {
          throw new Error(t("provision.completionInvalid"));
        }
        try {
          await finishBluetooth();
        } catch {
          bleSession?.disconnect(); bleSession = null; bleFinishFrames = [];
          privilegedPanel.classList.add("hidden");
        }
        result.textContent = t(completed.already_configured
          ? "provision.cameraAlreadyConfigured" : "provision.cameraConfigured", {
          name: completed.camera?.name || cameraName.value || t("provision.cameraDefaultName"),
        });
        await loadCameras();
      } else if (p2pProbe) {
        result.textContent = t("provision.privilegedBoundP2pPending");
        privilegedStatus.textContent = t("provision.retryP2p");
        bindPrivileged.disabled = false;
      } else {
        result.textContent = t("provision.privilegedBoundP2pProbeFailed", {
          message: p2pProbeError?.message || t("provision.privilegedProbeUnknown"),
        });
        privilegedStatus.textContent = t("provision.retryP2p");
        bindPrivileged.disabled = false;
      }
    } catch (err) {
      privilegedStatus.textContent = privilegedBound
        ? t("provision.completionRetryHint") : "";
      error.textContent = err.message;
      finishWifiOnly.disabled = privilegedBound;
      bindPrivileged.disabled = false;
    }
  });
  start.addEventListener("click", async () => {
    error.textContent = ""; result.textContent = ""; start.disabled = true; clearQr(); bleStage = "";
    try {
      let networkId = network.value;
      if (manualNetworkMode) {
        const signed = await api("/provisioning/networks/manual", { method: "POST", body: JSON.stringify({
          ssid: manualSsid.value, security: manualSecurity.value,
        }) });
        networkId = signed.network.id;
      }
      if (bleSession && bleHandshakeReady) {
        bleProvisioningPending = true;
        bleInbox.clear();
        result.textContent = t("provision.bluetoothSending");
        const prepared = await api("/provisioning/ble/prepare", { method: "POST", body: JSON.stringify({
          ...identityPayload(), wifi_network_id: networkId, wifi_password: password.value,
        }) });
        if (!prepared.attempt_id) throw new Error(t("provision.bluetoothAttemptMissing"));
        bleAttemptId = prepared.attempt_id;
        bleStage = t("provision.bluetoothStageChallenge");
        result.textContent = bleStage;
        const challengeMessage = await exchangeBleStage(
          prepared.frames.challenge, prepared.expected_responses.challenge,
        );
        const challengeReply = await decodeBleResponse(challengeMessage);
        if (challengeReply.valid !== true) {
          throw new Error(t("provision.bluetoothHandshakeInvalid"));
        }
        // The vendor client changes activities after 0x71. Besides UI navigation, that gives the
        // low-power firmware time to install the newly negotiated TanKey before encrypted 0x80.
        await new Promise((resolve) => window.setTimeout(resolve, 750));
        bleStage = t("provision.bluetoothStageLinkType");
        result.textContent = bleStage;
        // ChooseLinkTypeActivity sends this command and navigates immediately; its write callback
        // reports only queue success and never waits for 0x73. Some firmware does not emit 0x73
        // here at all. Blocking on it prevents the later SSID/password command from ever running.
        await bleSession.writeFrames(encodedFrames(prepared.frames.link_type));
        await new Promise((resolve) => window.setTimeout(resolve, 150));
        bleStage = t("provision.bluetoothStageWifiScan");
        result.textContent = bleStage;
        const wifiMessage = await exchangeBleStage(
          prepared.frames.wifi_list, prepared.expected_responses.wifi_list,
        );
        const wifiReply = await decodeBleResponse(wifiMessage);
        if (!wifiReply.json) {
          throw new Error(t("provision.bluetoothWifiListInvalid"));
        }
        const visibleSsids = [...new Set(collectSsids(wifiReply.json))];
        const requestedSsid = manualNetworkMode
          ? manualSsid.value.trim() : network.selectedOptions[0]?.dataset.ssid || "";
        if (requestedSsid && !visibleSsids.includes(requestedSsid)) {
          throw new Error(t("provision.bluetoothSsidNotVisible", { ssid: requestedSsid }));
        }
        bleStage = t("provision.bluetoothStageWifiConfig");
        result.textContent = bleStage;
        const configMessage = await exchangeBleStage(
          prepared.frames.wifi_config, prepared.expected_responses.wifi_config_ack,
        );
        const configReply = await decodeBleResponse(configMessage);
        if (configReply.configuration_acknowledged !== true) {
          throw new Error(t("provision.bluetoothConfigNotAcknowledged"));
        }
        // This mirrors WaitDeviceOnlineActivity exactly. 0x83 is only an ACK/echo. The APK then
        // races asynchronous 0x85 against a read-only devresult lookup of the same configToken.
        // Firmware is allowed to complete through either path; account binding remains explicit.
        let connectionReply = null;
        for (let attempt = 1; !connectionReply && attempt <= 36; attempt += 1) {
          bleStage = t("provision.bluetoothStageWifiConfirmation");
          result.textContent = t("provision.bluetoothWaitingWifi", { attempt, total: 36 });
          const bleResult = takeQueuedBleStage(prepared.expected_responses.wifi_connection);
          if (bleResult) {
            const decoded = await decodeBleResponse(bleResult);
            if (decoded.wifi_connection?.connected) connectionReply = decoded;
            else if (decoded.wifi_connection) {
              throw new Error(t("provision.bluetoothWifiRejected", {
                status: decoded.wifi_connection.status,
              }));
            }
          }
          if (!connectionReply) {
            let online = null;
            try {
              online = await api("/provisioning/privileged/online-status", {
                method: "POST",
                body: JSON.stringify({ ...identityPayload(), attempt_id: bleAttemptId }),
              });
            } catch (statusError) {
              // ConfigNetOnlineStatusProxy ignores transient HTTP failures and asks again on its
              // next five-second tick. Preserve that behavior while the BLE 0x85 path stays live.
              console.warn("[CCG BLE] post-Wi-Fi status query failed", statusError);
            }
            if (online?.online && online.privileged_handoff_ready) {
              connectionReply = { wifi_connection: {
                connected: true,
                status: 0,
                privileged_handoff_ready: true,
              } };
            } else if (online?.terminal_failure) {
              throw new Error(t("provision.bluetoothWifiRejected", { status: 0 }));
            }
          }
          if (!connectionReply && attempt < 36) {
            await new Promise((resolve) => window.setTimeout(resolve, 5000));
          }
        }
        if (!connectionReply?.wifi_connection?.connected) {
          throw new Error(t("provision.bluetoothFinalResponseMissing"));
        }
        password.value = "";
        wifiConfigured = true;
        bleFinishFrames = prepared.frames.finish || [];
        finishWifiOnly.disabled = false;
        start.hidden = true;
        findBluetooth.hidden = true;
        privilegedPanel.classList.remove("hidden");
        const handoffReady = connectionReply.wifi_connection.privileged_handoff_ready === true;
        bindPrivileged.disabled = !handoffReady;
        privilegedStatus.textContent = t(handoffReady
          ? "provision.privilegedReady" : "provision.privilegedUnavailable");
        result.textContent = t("provision.wifiConnected");
        updateStart();
        return;
      }
      const response = await api("/provisioning/start", { method: "POST", body: JSON.stringify({
        ...identityPayload(), wifi_network_id: networkId, wifi_password: password.value,
      }) });
      const encoded = response.qr && response.qr.data_base64;
      if (!encoded) throw new Error(t("provision.qrMissing"));
      const raw = atob(encoded);
      const bytes = Uint8Array.from(raw, (char) => char.charCodeAt(0));
      qrUrl = URL.createObjectURL(new Blob([bytes], { type: response.qr.mime_type || "image/svg+xml" }));
      qrBox.replaceChildren(
        el("img", { className: "provision-qr", src: qrUrl, alt: t("provision.qrAlt") }),
        el("strong", { textContent: t("provision.qrInstruction") }),
        el("small", { className: "muted", textContent: t("provision.experimental") }),
      );
      qrBox.classList.remove("hidden");
      password.value = ""; result.textContent = "";
    } catch (err) {
      error.textContent = bleStage
        ? t("provision.bluetoothStageFailed", { stage: bleStage, message: err.message })
        : err.message;
      if (bleProvisioningPending) {
        bleSession?.disconnect(); bleSession = null; bleAttemptId = ""; bleInbox.clear();
        finishWifiOnly.disabled = true; bindPrivileged.disabled = true;
        privilegedPanel.classList.remove("hidden");
        privilegedStatus.textContent = t("provision.privilegedPending");
      }
    }
    finally { bleProvisioningPending = false; updateStart(); }
  });

  const close = el("button", { className: "icon-btn", textContent: "×", title: t("scan.close") });
  const card = el("div", { className: "card modal-card provisioning-modal" },
    el("div", { className: "modal-head" }, el("h2", { textContent: t("provision.title") }), close),
    el("p", { className: "muted compact", textContent: t("provision.description") }),
    remoteBle ? el("small", { className: "provision-remote-warning", textContent: t("provision.remoteBle") }) : "",
    vendorAccountPanel,
    label,
    el("div", { className: "provision-inline" }, photo),
    el("details", {}, el("summary", { textContent: t("provision.manualDetails") }),
      manualHint, el("div", { className: "provision-grid" }, deviceId, capability, firmware, mac)),
    el("hr"),
    el("div", { className: "provision-network-row" }, network, refreshNetworks), networkError,
    manualNetworkPanel,
    password,
    el("small", { className: "muted", textContent: t("provision.wifiHint") }),
    !transportReady ? el("small", { className: "muted", textContent: t("provision.transportPending") }) : "",
    el("small", { className: "muted", textContent: t("provision.bluetoothPurpose") }),
    readiness, el("div", { className: "provision-actions" }, findBluetooth, start),
    privilegedPanel, qrBox, result, error);
  const overlay = el("div", { className: "modal" }, card);
  const dismiss = () => {
    password.value = ""; vendorPassword.value = ""; clearQr(); window.clearTimeout(validationTimer);
    if (bleWaiter) {
      window.clearTimeout(bleWaiter.timer);
      bleWaiter.reject(new Error(t("provision.bluetoothDisconnected")));
      bleWaiter = null;
    }
    bleSession?.disconnect(); overlay.remove();
    bleInbox.clear();
  };
  close.addEventListener("click", dismiss);
  document.body.append(overlay);
  updateManualState();
  updateStart();
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
    disabled: !browserCanProvision() || Boolean(state.provisioning && state.provisioning.blocked),
    title: browserCanProvision() ? t("provision.open") : t("provision.localOnly"),
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
