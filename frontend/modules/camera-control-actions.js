import { t } from "ccg/i18n";
import { api, el } from "ccg/core";

// Camera controls are grouped behind one compact menu. Nothing is read automatically when a
// tile renders: opening the dashboard must not create extra P2P sessions on resource-limited
// cameras. Each option is an explicit target state; the backend performs its own preflight and
// skips the write when the camera is already in that state.
const PROTECTION_WEEKDAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

function applying(status) {
  status.classList.remove("error");
  status.classList.add("applying");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.textContent = t("control.applying");
}

function finishApplying(status) {
  status.classList.remove("applying");
}

async function openDynamicChoice(cam, controlKey, status, trigger, strings) {
  trigger.disabled = true;
  status.classList.remove("error");
  status.textContent = strings.loading;
  try {
    const response = await api(
      `/cameras/${encodeURIComponent(cam.id)}/controls/${encodeURIComponent(controlKey)}/options`,
    );
    if (!trigger.isConnected) return;
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
      applying(modalStatus);
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
        finishApplying(modalStatus);
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
    if (!trigger.isConnected) return;
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
      applying(modalStatus);
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
        finishApplying(modalStatus);
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

export function controlWidgets(cam, status) {
  const available = cam.controls || {};
  const menu = el("div", { className: "camera-control-menu" });

  const actionSelect = (
    placeholder, options, controlKey, valueFor, confirmFor = null, success = null,
  ) => {
    const select = el("select", { className: "camera-control-select", title: placeholder });
    select.dataset.controlKey = controlKey;
    select.setAttribute("aria-label", placeholder);
    select.append(el("option", { value: "", textContent: placeholder, disabled: true, selected: true }));
    const allowed = available[controlKey]?.options || [];
    for (const [value, label] of options) {
      if (available[controlKey]?.kind !== "boolean" && !allowed.includes(value)) continue;
      select.append(el("option", { value, textContent: label }));
    }
    select.disabled = select.options.length <= 1;
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
      applying(status);
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
        finishApplying(status);
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
    schedule.dataset.controlKey = "smart_protection_schedule";
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
    alarmVoice.dataset.controlKey = "alarm_voice";
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
  return menu;
}
