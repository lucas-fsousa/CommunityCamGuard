import { el, state } from "ccg/core";
import { t } from "ccg/i18n";
import { renderPreferences } from "ccg/settings-preferences";
import { renderAccessKeys } from "ccg/access-keys";

let dispose = null;
export function stopSettings() { dispose?.(); dispose = null; }

// Add sections here; their renderers own their forms, requests and cleanup.
export function renderSettings(container) {
  stopSettings(); container.replaceChildren();
  if (!state.canManage) {
    container.append(el("section", { className: "settings-card card" },
      el("p", { textContent: t("settings.primaryRequired") })));
    return;
  }
  let alive = true, active = null;
  const sections = [
    { id: "preferences", label: "nav.settings", render: renderPreferences, preserve: true },
    { id: "access", label: "settings.accessTab", render: renderAccessKeys },
  ];
  const nav = el("nav", { className: "settings-nav" });
  nav.setAttribute("role", "tablist"); nav.setAttribute("aria-label", t("settings.sections"));
  const content = el("div", { className: "settings-content" });
  const narrow = window.matchMedia("(max-width: 700px)");
  const orient = () => nav.setAttribute("aria-orientation", narrow.matches ? "horizontal" : "vertical");
  orient(); narrow.addEventListener("change", orient);
  function select(section) {
    if (!alive || section === active) return;
    if (active && !active.preserve) {
      active.cleanup?.(); active.cleanup = null; active.mounted = false;
      active.panel.replaceChildren();
    }
    active = section;
    for (const entry of sections) {
      const selected = entry === section;
      entry.button.setAttribute("aria-selected", String(selected));
      entry.button.tabIndex = selected ? 0 : -1;
      entry.panel.hidden = !selected;
    }
    if (!section.mounted) {
      section.mounted = true; section.cleanup = section.render(section.panel);
    }
  }
  sections.forEach((section, index) => {
    const button = section.button = el("button", { type: "button", id: `settings-tab-${section.id}`,
      textContent: t(section.label), className: "settings-tab" });
    const panel = section.panel = el("div", { id: `settings-panel-${section.id}`, className: "settings-tab-panel" });
    button.setAttribute("role", "tab"); button.setAttribute("aria-controls", panel.id);
    panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", button.id);
    button.addEventListener("click", () => select(section));
    button.addEventListener("keydown", event => {
      let target;
      if (["ArrowRight", "ArrowDown"].includes(event.key)) target = (index + 1) % sections.length;
      if (["ArrowLeft", "ArrowUp"].includes(event.key)) target = (index + sections.length - 1) % sections.length;
      if (event.key === "Home") target = 0;
      if (event.key === "End") target = sections.length - 1;
      if (target === undefined) return;
      event.preventDefault(); select(sections[target]); sections[target].button.focus();
    });
    nav.append(button); content.append(panel);
  });
  container.append(el("div", { className: "settings-layout" }, nav, content));
  dispose = () => {
    alive = false; narrow.removeEventListener("change", orient);
    sections.forEach(section => section.cleanup?.());
  };
  select(sections[0]);
}
