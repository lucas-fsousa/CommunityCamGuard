import { api, el, state } from "ccg/core";
import { t } from "ccg/i18n";

let dispose = null;
export function stopSettings() { dispose?.(); dispose = null; }

export function renderSettings(container) {
  stopSettings();
  container.replaceChildren();
  const panel = el("section", { className: "settings-card card" },
    el("h2", { textContent: t("nav.settings") }),
    el("p", { className: "muted", textContent: t("settings.scope") }));
  container.append(panel);
  if (!state.canManage) {
    panel.append(el("p", { textContent: t("settings.primaryRequired") }));
    return;
  }
  let alive = true, busy = false, blocked = false, snapshot = null, request = null;
  const fields = [];
  const status = el("p", { className: "settings-status" });
  status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  const form = el("form", { className: "settings-form" });
  const save = el("button", { type: "submit", className: "btn-primary", textContent: t("settings.save") });
  const reload = el("button", { type: "button", textContent: t("settings.reload") });
  const actions = el("div", { className: "settings-actions" }, save, reload);
  panel.append(form, status);
  dispose = () => { alive = false; request?.abort(); };

  function changes() {
    const result = {};
    for (const field of fields) {
      const value = field.baseline.checked ? null : Number(field.input.value);
      if (value !== (snapshot.overrides[field.name] ?? null)) result[field.name] = value;
    }
    return result;
  }
  function refresh() {
    fields.forEach(field => {
      field.baseline.disabled = busy || blocked;
      field.input.disabled = busy || blocked || field.baseline.checked;
    });
    save.disabled = busy || blocked || !snapshot || !Object.keys(changes()).length;
    reload.disabled = busy;
    save.textContent = t(busy ? "settings.working" : "settings.save");
    form.setAttribute("aria-busy", String(busy));
  }
  function build(value) {
    snapshot = value;
    state.gridHdMax = value.values.grid_hd_max_cameras;
    fields.length = 0;
    form.replaceChildren();
    for (const [name, key, max] of [
      ["grid_hd_max_cameras", "grid", 64], ["playback_cache_mb", "cache", 65536],
    ]) {
      const id = "setting-" + name;
      const input = el("input", { id, type: "number", min: "0", max: String(max), step: "1", required: true });
      const baseline = el("input", { type: "checkbox", checked: !(name in value.overrides) });
      input.value = String(value.values[name]);
      const help = el("p", { id: id + "-help", className: "muted settings-help", textContent: t("settings." + key + "Help") });
      input.setAttribute("aria-describedby", help.id);
      form.append(el("div", { className: "settings-field" },
        el("label", { htmlFor: id, textContent: t("settings." + key) }), help, input,
        el("label", { className: "settings-baseline" }, baseline, el("span", { textContent: t("settings.baseline") })),
        el("p", { className: "muted settings-effective", textContent: t("settings.effective", { value: value.values[name] }) })));
      fields.push({ name, input, baseline });
      input.addEventListener("input", refresh);
      baseline.addEventListener("change", () => {
        input.value = String(snapshot.values[name]);
        refresh();
      });
    }
    form.append(actions);
    refresh();
  }
  async function call(options) {
    request = new AbortController();
    const timer = setTimeout(() => request?.abort(), 15000);
    try { return await api("/settings", { ...options, signal: request.signal }); }
    finally { clearTimeout(timer); }
  }
  async function load() {
    if (!alive || busy) return;
    busy = true; refresh(); status.textContent = t("settings.working");
    try {
      const result = await call();
      if (!alive) return;
      blocked = false; build(result); status.textContent = "";
    } catch (error) {
      if (!alive) return;
      blocked = true;
      status.textContent = t(error.status === 403 ? "settings.primaryRequired" : "settings.loadFailed");
    } finally { if (alive) { busy = false; refresh(); } }
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!alive || busy || blocked || !snapshot) return;
    if (!form.reportValidity() || fields.some(f => !f.baseline.checked && !/^\d+$/.test(f.input.value))) return;
    const patch = changes();
    if (!Object.keys(patch).length) return;
    busy = true; refresh(); status.textContent = t("settings.working");
    try {
      const result = await call({ method: "PATCH", body: JSON.stringify({ revision: snapshot.revision, changes: patch }) });
      if (!alive) return;
      build(result);
      // Settings view suspends live consumers. Next live-view activation uses this limit.
      status.textContent = t("settings.saved");
    } catch (error) {
      if (!alive) return;
      blocked = true; // Uncertain completion must be reconciled, never automatically retried.
      status.textContent = t(error.status === 409 ? "settings.conflict"
        : error.status === 403 ? "settings.primaryRequired" : "settings.saveFailed");
    } finally { if (alive) { busy = false; refresh(); } }
  });
  reload.addEventListener("click", load);
  form.append(actions); refresh(); void load();
}
