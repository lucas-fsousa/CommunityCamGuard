import { api, el, state } from "ccg/core";
import { t } from "ccg/i18n";
import { openAccessKeyDialog } from "ccg/access-key-dialog";

// Primary-only management. Public login remains staged until all activation gates pass.
export function renderAccessKeys(container) {
  if (!state.canManage) return () => {};
  let alive = true, busy = false, blocked = true, offset = 0, count = 0, request, timer;
  const limit = 20;
  let closeDialog = null;
  const list = el("div", { className: "access-key-list" });
  const status = el("p", { className: "settings-status" });
  status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  const reload = el("button", { type: "button", textContent: t("keys.refresh") });
  const create = el("button", { type: "button", textContent: t("keys.create") });
  const previous = el("button", { type: "button", textContent: t("keys.previous") });
  const next = el("button", { type: "button", textContent: t("keys.next") });
  const panel = el("section", { className: "settings-card card access-keys" },
    el("h2", { textContent: t("keys.title") }),
    el("p", { className: "muted", textContent: t("keys.staged") }), list, status,
    el("div", { className: "settings-actions" }, previous, next, reload, create));
  container.append(panel);
  let rows = [];
  function refresh() {
    reload.disabled = busy;
    create.disabled = busy;
    previous.disabled = busy || blocked || offset === 0;
    next.disabled = busy || blocked || count < limit;
    rows.forEach(row => { row.button.disabled = busy || blocked || row.revoked; });
    panel.setAttribute("aria-busy", String(busy));
  }
  async function call(url, options = {}) {
    request = new AbortController();
    const controller = request;
    timer = setTimeout(() => controller.abort(), 15000);
    try { return await api(url, { ...options, signal: controller.signal }); }
    finally { clearTimeout(timer); }
  }
  function date(value) {
    const parsed = new Date(value);
    return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString(document.documentElement.lang) : "—";
  }
  function build(items) {
    rows = []; list.replaceChildren();
    if (!items.length) list.append(el("p", { className: "muted", textContent: t("keys.empty") }));
    for (const item of items) {
      const button = el("button", { type: "button", textContent: t("keys.revoke") });
      const row = { button, revoked: item.status === "revoked" };
      rows.push(row);
      list.append(el("div", { className: "access-key-row" },
        el("div", {}, el("strong", { textContent: item.label }),
          el("p", { className: "muted", textContent: t("keys." + item.status) }),
          el("p", { className: "muted", textContent: item.expires_at === null ? t("keys.never") : t("keys.expires", { date: date(item.expires_at) }) }),
          el("p", { className: "muted", textContent: (item.permissions || []).map(p => t("keys.grant." + p)).join(", ") })), button));
      button.addEventListener("click", async () => {
        if (!alive || busy || blocked || row.revoked) return;
        if (!window.confirm(t("keys.confirm", { label: item.label }))) return;
        busy = true; refresh(); status.textContent = t("keys.working");
        try {
          await call("/access-keys/" + encodeURIComponent(item.id) + "/revoke",
            { method: "POST", body: "{}" });
          if (!alive) return;
          // Reconcile through an explicit reload; do not trust stale metadata.
          blocked = true; status.textContent = t("keys.revokedReload");
        } catch (error) {
          if (!alive) return;
          blocked = true;
          status.textContent = t(error.status === 403 ? "settings.primaryRequired" : "keys.revokeFailed");
        } finally { if (alive) { busy = false; refresh(); } }
      });
    }
  }
  async function load(target = offset) {
    if (!alive || busy) return;
    busy = true; refresh(); status.textContent = t("keys.working");
    try {
      const page = await call(`/access-keys?limit=${limit}&offset=${target}`);
      if (!alive) return;
      offset = target; count = page.items.length; blocked = false;
      build(page.items); status.textContent = "";
    } catch (error) {
      if (!alive) return;
      blocked = true;
      status.textContent = t(error.status === 403 ? "settings.primaryRequired" : "keys.loadFailed");
    } finally { if (alive) { busy = false; refresh(); } }
  }
  reload.addEventListener("click", () => void load());
  create.addEventListener("click", () => {
    if (!alive || busy) return;
    closeDialog?.(); closeDialog = openAccessKeyDialog(() => { if (alive) void load(0); });
  });
  previous.addEventListener("click", () => { if (!previous.disabled) void load(Math.max(0, offset - limit)); });
  next.addEventListener("click", () => { if (!next.disabled) void load(offset + limit); });
  refresh(); // Load on demand, not on every visit to Settings.
  return () => { alive = false; request?.abort(); clearTimeout(timer); closeDialog?.(); };
}
