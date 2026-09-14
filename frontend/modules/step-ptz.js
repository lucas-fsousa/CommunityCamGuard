// Finite, server-stopped gestures. Drag tracks current intent, never a movement queue.
import { api, el } from "ccg/core";
import { t } from "ccg/i18n";

export function finitePtzControls(cam) {
  const status = el("small", { className: "ptz-feedback" });
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const pad = el("span", { className: "ptz-pad", title: t("ptz.drag") });
  pad.setAttribute("role", "group");
  pad.setAttribute("aria-label", t("ptz.drag"));
  let busy = false, gesture = null, safety = null, repeat = null, suppressClick = false;
  const buttons = new Map();
  const send = async (direction) => {
    if (busy) return false;
    clearTimeout(repeat);
    repeat = null;
    const started = performance.now();
    busy = true;
    const button = buttons.get(direction);
    button.setAttribute("aria-busy", "true");
    button.classList.add("ptz-pending");
    status.classList.remove("error");
    status.textContent = "";
    let success = false;
    try {
      const result = await api(`/cameras/${encodeURIComponent(cam.id)}/ptz`, {
        method: "POST", body: JSON.stringify({ action: "step", direction }),
      });
      if (result?.ok !== true) throw new Error(t("control.unconfirmed"));
      success = true;
    } catch (error) {
      release(); // a failed gesture must not retry merely because the pointer moves
      status.classList.add("error");
      status.textContent = t("control.failed", { msg: error.message });
    } finally {
      busy = false;
      button.setAttribute("aria-busy", "false");
      button.classList.remove("ptz-pending");
    }
    // Only an actively held drag may request another step. A click never repeats.
    if (success && gesture?.dragging && gesture.direction && pad.isConnected) {
      repeat = setTimeout(() => {
        repeat = null;
        if (gesture?.dragging && gesture.direction && pad.isConnected) void send(gesture.direction);
      }, Math.max(0, 250-(performance.now()-started)));
    }
    return success;
  };
  const release = () => {
    const cancel = gesture?.dragging && busy;
    gesture = null;
    clearTimeout(safety);
    clearTimeout(repeat);
    repeat = null;
    window.removeEventListener?.("blur", release);
    document.removeEventListener?.("visibilitychange", visibility);
    if (cancel) {
      void api(`/cameras/${encodeURIComponent(cam.id)}/ptz`, {
        method: "POST", body: JSON.stringify({ action: "stop" }),
      }).catch(() => { status.classList.add("error"); status.textContent = t("ptz.stopFailed"); });
    }
    // STOP also cancels native preparation. Every committed step still has its server deadline.
  };
  const visibility = () => { if (document.hidden) release(); };
  for (const [direction, glyph] of [["left", "←"], ["up", "↑"], ["down", "↓"], ["right", "→"]]) {
    const button = el("button", { className: "icon-btn ptz-mini", type: "button",
      textContent: glyph, title: t("ptz.stepDir", { dir: t(`dir.${direction}`) }) });
    button.dataset.direction = direction;
    button.setAttribute("aria-label", button.title);
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      if (suppressClick && event.detail !== 0) { suppressClick = false; return; }
      suppressClick = false;
      if (busy) {
        status.classList.add("error");
        status.textContent = t("ptz.busy");
        return;
      }
      return send(direction);
    });
    buttons.set(direction, button);
    pad.append(button);
  }
  const handle = el("button", { className: "ptz-handle", type: "button", textContent: "✥", title: t("ptz.drag") });
  handle.setAttribute("aria-label", t("ptz.drag"));
  pad.append(handle, status);
  pad.addEventListener("pointerdown", (event) => {
    if (gesture || (event.button !== undefined && event.button !== 0)) return;
    event.preventDefault(); event.stopPropagation();
    suppressClick = true;
    const direction = event.target?.dataset?.direction || null;
    gesture = { pointer: event.pointerId, x: event.clientX, y: event.clientY,
      direction, dragging: false };
    pad.setPointerCapture?.(event.pointerId);
    safety = setTimeout(release, 8000);
    window.addEventListener?.("blur", release);
    document.addEventListener?.("visibilitychange", visibility);
    if (direction) {
      if (!busy) void send(direction);
      else { status.classList.add("error"); status.textContent = t("ptz.busy"); }
    }
  });
  pad.addEventListener("pointermove", (event) => {
    if (!gesture || gesture.pointer !== event.pointerId) return;
    event.preventDefault();
    if (!gesture.dragging && Math.hypot(event.clientX-gesture.x, event.clientY-gesture.y) < 8) return;
    gesture.dragging = true;
    const rect = pad.getBoundingClientRect();
    const x = event.clientX-(rect.left+rect.width/2), y = event.clientY-(rect.top+rect.height/2);
    const dead = Math.min(rect.width, rect.height)*0.15;
    gesture.direction = Math.hypot(x, y) < dead ? null
      : Math.abs(x) > Math.abs(y) ? (x > 0 ? "right" : "left") : (y > 0 ? "down" : "up");
    if (!busy && repeat === null && gesture.direction) void send(gesture.direction);
  });
  for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
    pad.addEventListener(name, (event) => { if (gesture?.pointer === event.pointerId) release(); });
  }
  pad.addEventListener("contextmenu", (event) => event.preventDefault());
  return pad;
}
