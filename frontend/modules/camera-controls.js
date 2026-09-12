// Presentation only: the driver catalogue remains the authority for every active control.
import { el, state } from "ccg/core";
import { t } from "ccg/i18n";
import { controlWidgets } from "ccg/control-actions";
import { audioMessageButton } from "ccg/audio-message";
import { pushToTalkButton } from "ccg/push-to-talk";

const GROUPS = [
  ["image", [["orientation", "control.orientation"], ["night_vision", "control.nightVision"],
    ["white_light", "control.whiteLight"], ["movement", "panel.movement"]]],
  ["audio", [["audio_messages", "intercom.open"], ["audio_streams", "talk.open"],
    ["speaker_volume", "control.speakerVolume"]]],
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

function labeledButton(button, label) {
  button.textContent = t(label);
  button.className = "camera-control-schedule-btn";
  return button;
}

export function cameraControls(camera, extras = {}) {
  const trigger = el("button", {
    className: "camera-panel-trigger", type: "button", textContent: t("control.menu"),
    title: t("control.menu"),
  });
  trigger.setAttribute("aria-haspopup", "dialog");
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    // Use the latest server catalogue when opening, not the tile's original snapshot.
    const cam = state.cameras.find((item) => item.id === camera.id) || camera;
    const status = el("small", { className: "camera-control-status" });
    status.setAttribute("role", "status");
    const widgets = new Map([...controlWidgets(cam, status).children]
      .map((widget) => [widget.dataset.controlKey, widget]));
    if (cam.audio_messages) widgets.set("audio_messages", labeledButton(audioMessageButton(cam), "intercom.open"));
    if (cam.audio_streams) widgets.set("audio_streams", labeledButton(pushToTalkButton(cam), "talk.open"));
    if (cam.capabilities?.ptz && extras.movement) widgets.set("movement", extras.movement);

    const close = el("button", { className: "icon-btn", type: "button", textContent: "×", title: t("scan.close") });
    close.setAttribute("aria-label", t("scan.close"));
    const body = el("div", { className: "camera-panel-body" });
    for (const [group, entries] of GROUPS) {
      body.append(el("section", { className: "control-section" },
        el("h3", { textContent: t(`panel.${group}`) }),
        ...entries.map(([key, label]) => row(label, widgets.get(key)))));
    }
    const recordings = el("button", { type: "button", textContent: t("panel.openRecordings") });
    body.append(el("section", { className: "control-section" },
      el("h3", { textContent: t("panel.storage") }),
      row("panel.serverRecordings", recordings),
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
          el("h2", { textContent: cam.name || t("cam.unnamed") })), close),
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
      dialogs.disconnect();
      overlay.remove();
      siblings.forEach(([node, inert]) => { node.inert = inert; });
      document.body.style.overflow = previousOverflow;
      if (trigger.isConnected) trigger.focus();
    };
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
