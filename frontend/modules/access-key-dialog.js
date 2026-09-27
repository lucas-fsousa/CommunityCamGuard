import { api, el } from "ccg/core";
import { t } from "ccg/i18n";

const PERMISSIONS = ["live", "recordings", "ptz", "intercom", "white_light", "orientation",
  "siren_pulse", "speaker_volume", "night_vision", "smart_protection", "smart_protection_schedule", "alarm_voice", "reboot"];

export function openAccessKeyDialog(onChanged = () => {}) {
  let alive = true, busy = false, uncertain = false, request, timer, secretTimer, secretInput;
  const dialog = el("dialog", { className: "access-key-dialog" });
  const close = el("button", { type: "button", textContent: "×", className: "access-key-close" });
  close.setAttribute("aria-label", t("keys.close"));
  const title = el("h2", { id: "access-key-title", textContent: t("keys.create") });
  dialog.setAttribute("aria-labelledby", title.id);
  const name = el("input", { id: "access-key-name", type: "text", required: true, maxLength: 80, autocomplete: "off" });
  const expiry = el("input", { id: "access-key-expiry", type: "datetime-local", required: true });
  const never = el("input", { type: "checkbox" });
  never.addEventListener("change", () => { expiry.disabled = never.checked; expiry.required = !never.checked; });
  const grants = PERMISSIONS.map(permission => ({ permission, input: el("input", { type: "checkbox" }) }));
  const fieldset = el("fieldset", { className: "access-key-grants" },
    el("legend", { textContent: t("keys.permissions") }),
    ...grants.map(({ permission, input }) => el("label", {}, input, el("span", { textContent: t("keys.grant." + permission) }))));
  const save = el("button", { type: "submit", className: "btn-primary", textContent: t("keys.create") });
  const form = el("form", {}, el("label", { htmlFor: name.id, textContent: t("keys.name") }), name,
    el("label", { htmlFor: expiry.id, textContent: t("keys.expiration") }), expiry,
    el("label", { className: "settings-baseline" }, never, el("span", { textContent: t("keys.never") })), fieldset, save);
  const status = el("p", {}); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  const result = el("div");
  dialog.append(close, title, el("p", { className: "muted", textContent: t("keys.staged") }), form, status, result);
  document.body.append(dialog);
  function dispose() {
    if (!alive) return;
    alive = false; request?.abort(); clearTimeout(timer); clearTimeout(secretTimer);
    if (secretInput) secretInput.value = "";
    result.replaceChildren(); dialog.close(); dialog.remove();
  }
  close.addEventListener("click", dispose);
  // No backdrop dismissal; close explicitly. Avoid accidentally losing the one-time key.
  dialog.addEventListener("cancel", event => event.preventDefault());
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!alive || busy || uncertain || !form.reportValidity()) return;
    const permissions = grants.filter(grant => grant.input.checked).map(grant => grant.permission);
    const expires = never.checked ? null : new Date(expiry.value);
    if (!name.value.trim() || !permissions.length || (expires !== null &&
      (!Number.isFinite(expires.getTime()) || expires.getTime() <= Date.now()))) {
      status.textContent = t("keys.invalid"); return;
    }
    busy = true; save.disabled = true; fieldset.disabled = true;
    name.disabled = true; never.disabled = true; expiry.disabled = true;
    status.textContent = t("keys.working");
    request = new AbortController(); timer = setTimeout(() => request.abort(), 15000);
    try {
      const created = await api("/access-keys", { method: "POST", signal: request.signal,
        body: JSON.stringify({ label: name.value.trim(), expires_at: expires?.toISOString() ?? null, permissions }) });
      if (!alive) return;
      form.hidden = true;
      status.textContent = t(created.login_enabled === true ? "keys.copyOnce" : "keys.stagedCreated");
      const secret = el("input", { type: "text", readOnly: true, value: created.secret, autocomplete: "off" });
      secretInput = secret;
      secret.setAttribute("aria-label", t("keys.secret"));
      result.append(secret, el("p", { className: "muted", textContent: t("keys.copyOnce") }));
      // Never persist or auto-copy credentials; bound their visible lifetime.
      secretTimer = setTimeout(() => { secret.value = ""; result.replaceChildren(); }, 60000);
      onChanged();
    } catch {
      if (!alive) return;
      uncertain = true; status.textContent = t("keys.createFailed"); onChanged();
    } finally { clearTimeout(timer); busy = false; }
  });
  dialog.showModal(); name.focus();
  return dispose;
}
