// Presentation only: the driver catalogue remains the authority for every active control.
import { api, el, onSessionEnd, state, svgIcon } from "ccg/core";
import { notify } from "ccg/notifications";
import { t } from "ccg/i18n";
import { controlWidgets } from "ccg/control-actions";
import { allowed, permittedCamera } from "ccg/session-access";

const GROUPS = [
  ["image", [["orientation", "control.orientation"], ["night_vision", "control.nightVision"],
    ["white_light", "control.whiteLight"]]],
  ["audio", [["speaker_volume", "control.speakerVolume"]]],
  ["security", [["smart_protection", "control.smartProtection"],
    ["smart_protection_schedule", "control.scheduleOpen"], ["alarm_voice", "control.alarmVoiceOpen"],
    ["siren_pulse", "control.siren"]]],
];

function row(label, widget, reason = "panel.unavailable") {
  const enabled = Boolean(widget && !widget.disabled);
  const content = widget || el("button", {
    type: "button", disabled: true, textContent: t("panel.disabled"),
  });
  return el("div", { className: "control-row" + (enabled ? "" : " unavailable") },
    el("div", { className: "control-row-label" },
      el("strong", { textContent: t(label) }),
      ...(!enabled ? [el("small", { textContent: t(reason) })] : [])), content);
}

export function cameraControls(camera, extras = {}) {
  const trigger = el("button", {
    className: "camera-panel-trigger icon-btn", type: "button", innerHTML: svgIcon("i-settings"),
    title: t("control.menu"),
  });
  trigger.setAttribute("aria-haspopup", "dialog");
  trigger.setAttribute("aria-label", t("control.menu"));
  trigger.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (trigger.disabled) return;
    trigger.disabled = true;
    trigger.classList.add("spin");
    trigger.title = t("panel.loading");
    trigger.setAttribute("aria-busy", "true");
    const controller = new AbortController();
    let active = true;
    const cancel = onSessionEnd(() => { active = false; controller.abort(); });
    const timeout = setTimeout(() => controller.abort(), 10000);
    let cam;
    try {
      // One explicit catalogue read. Never rebuild tiles/players or replay a write.
      const cameras = await api("/cameras", { signal: controller.signal });
      if (!active || controller.signal.aborted || !trigger.isConnected) return;
      const current = Array.isArray(cameras) && cameras.find((item) => item.id === camera.id);
      if (!current) throw new Error("Camera unavailable");
      cam = permittedCamera(current);
    } catch (_error) {
      if (active && trigger.isConnected) notify(t("panel.loadFailed"), { anchor: trigger, error: true });
      return;
    } finally {
      clearTimeout(timeout);
      cancel();
      trigger.disabled = false;
      trigger.classList.remove("spin");
      trigger.title = t("control.menu");
      trigger.setAttribute("aria-busy", "false");
    }
    const status = el("small", { className: "camera-control-status" });
    status.setAttribute("role", "status");
    const widgets = new Map([...controlWidgets(cam, status).children]
      .map((widget) => [widget.dataset.controlKey, widget]));

    const close = el("button", { className: "icon-btn", type: "button", textContent: "×", title: t("scan.close") });
    close.setAttribute("aria-label", t("scan.close"));
    const body = el("div", { className: "camera-panel-body" });
    for (const [group, entries] of GROUPS) {
      body.append(el("section", { className: "control-section" },
        el("h3", { textContent: t(`panel.${group}`) }),
        ...entries.map(([key, label]) => row(label, widgets.get(key),
          allowed(key.startsWith("audio_") ? "intercom" : key) ? "panel.unavailable" : "panel.accessDenied"))));
    }
    const recordings = el("button", { type: "button", textContent: t("panel.openRecordings") });
    recordings.disabled = !allowed("recordings");
    body.append(el("section", { className: "control-section" },
      el("h3", { textContent: t("panel.storage") }),
      row("panel.serverRecordings", recordings, "panel.accessDenied"),
      row("panel.sdRecordings", null, "panel.sdPending")));
    body.append(el("section", { className: "control-section maintenance-section" },
      el("h3", { textContent: t("panel.maintenance") }),
      el("p", { className: "muted compact", textContent: t("panel.maintenanceHint") }),
      el("div", { className: "maintenance-actions" }, ...(extras.maintenance || []).map((button) => {
        button.textContent = button.title;
        return button;
      }))));
    const panel = el("section", { className: "camera-panel" },
      el("header", { className: "camera-panel-head" },
        el("div", {}, el("small", { textContent: t("control.menu") }),
          el("h2", { textContent: cam.name || t("cam.unnamed"), title: cam.name || t("cam.unnamed") })), close),
      el("p", { className: "camera-panel-hint", textContent: t("panel.hint") }), body,
      el("footer", { className: "camera-panel-status" }, status));
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", `${t("control.menu")} · ${cam.name || t("cam.unnamed")}`);
    const overlay = el("div", { className: "camera-panel-shell" }, panel);
    const previousOverflow = document.body.style.overflow;
    const siblings = [...document.body.children].map((node) => [node, node.inert]);
    siblings.forEach(([node]) => { node.inert = true; });
    document.body.style.overflow = "hidden";
    let nested = null, returnFocus = null;
    const dialogs = new MutationObserver(() => {
      const next = [...document.body.children].find((node) => node.classList.contains("modal")) || null;
      if (next === nested) return;
      if (next) {
        returnFocus = document.activeElement;
        panel.inert = true;
        next.querySelector("button:not(:disabled), input, select")?.focus();
      } else {
        panel.inert = false;
        if (returnFocus?.isConnected) returnFocus.focus();
      }
      nested = next;
    });
    dialogs.observe(document.body, { childList: true });
    const dismiss = () => {
      unregister();
      dialogs.disconnect();
      overlay.remove();
      siblings.forEach(([node, inert]) => { node.inert = inert; });
      document.body.style.overflow = previousOverflow;
      if (trigger.isConnected) trigger.focus();
    };
    const unregister = onSessionEnd(dismiss);
    close.addEventListener("click", dismiss);
    recordings.addEventListener("click", () => {
      state.rec.cameraId = cam.id;
      state.rec.page = 0;
      dismiss();
      document.querySelector('[data-view="recordings"]')?.click();
    });
    // No backdrop dismissal. Nested audio/schedule dialogs remain above this panel.
    panel.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const items = [...panel.querySelectorAll("button:not(:disabled), select:not(:disabled), a[href]")];
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    });
    document.body.append(overlay);
    close.focus();
  });
  return trigger;
}
