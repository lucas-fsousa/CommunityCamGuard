import { el } from "ccg/core";
import { t } from "ccg/i18n";

const regions = new WeakMap();

// Keep notifications in the active dialog's focus/inert scope when applicable.
export function notify(message, { anchor, error = false } = {}) {
  const host = anchor?.closest('[role="dialog"], .modal') || document.body;
  let region = regions.get(host);
  if (!region?.isConnected) {
    region = el("div", { className: "toast-region" });
    host.append(region);
    regions.set(host, region);
  }
  const close = el("button", { type: "button", className: "toast-close", textContent: "×" });
  close.setAttribute("aria-label", t("notification.dismiss"));
  const text = el("span", { textContent: String(message) });
  text.setAttribute("role", "status");
  text.setAttribute("aria-live", "polite");
  const toast = el("div", { className: `toast${error ? " toast-error" : ""}` }, text, close);
  let timer;
  const dismiss = () => {
    clearTimeout(timer);
    toast.remove();
    if (!region.children.length) region.remove();
  };
  toast.dismiss = dismiss;
  close.addEventListener("click", (event) => { event.stopPropagation(); dismiss(); });
  while (region.children.length >= 3) region.children[0].dismiss();
  region.append(toast);
  timer = setTimeout(dismiss, 5000);
  return dismiss;
}
