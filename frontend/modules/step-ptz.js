// Driver-advertised finite steps. No hold timer, retry or browser-selected transport.
import { api, el } from "ccg/core";
import { t } from "ccg/i18n";

export function finitePtzControls(cam) {
  const status = el("small", { className: "camera-control-status" });
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  let busy = false;
  const buttons = ["left", "up", "down", "right"].map((direction, i) => {
    const button = el("button", { className: "icon-btn ptz-mini", type: "button",
      textContent: ["←", "↑", "↓", "→"][i], title: t("ptz.stepDir", { dir: t(`dir.${direction}`) }) });
    button.setAttribute("aria-label", button.title);
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (busy) return;
      busy = true;
      buttons.forEach((item) => { item.disabled = true; });
      status.classList.remove("error");
      status.textContent = t("ptz.moving");
      try {
        const result = await api(`/cameras/${encodeURIComponent(cam.id)}/ptz`, {
          method: "POST", body: JSON.stringify({ action: "step", direction }),
        });
        if (result?.ok !== true) throw new Error(t("control.unconfirmed"));
        status.textContent = "";
      } catch (error) {
        status.classList.add("error");
        status.textContent = t("control.failed", { msg: error.message });
      } finally {
        busy = false;
        buttons.forEach((item) => { item.disabled = false; });
      }
    });
    return button;
  });
  return el("span", { className: "ptz-inline", title: t("ptz.step") }, ...buttons, status);
}
