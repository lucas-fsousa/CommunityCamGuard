import { $, state } from "ccg/core";

export function applySessionAccess(me) {
  state.canManage = me.can_manage === true;
  state.authentication = me.authentication;
  state.permissions = Array.isArray(me.permissions) ? me.permissions : [];
  if (state.authentication === "temporary" && !state.permissions.length) state.cameras = [];
  if (!viewAllowed(state.view)) state.view = "grid";
  document.querySelectorAll(".views button").forEach(button => {
    button.classList.toggle("hidden", !viewAllowed(button.dataset.view));
  });
  $("#storage").classList.toggle("hidden", !allowed("recordings"));
}

// Presentation restriction only. Every operation is authorized again by the server.
export function allowed(permission) {
  return state.authentication !== "temporary" || state.permissions.includes(permission);
}

export function viewAllowed(view) {
  if (state.authentication !== "temporary") return true;
  if (view === "recordings") return allowed("recordings");
  // Grid/single also contain camera controls, including for control-only guests.
  return view === "grid" || view === "single";
}

export function permittedCamera(camera) {
  if (state.authentication !== "temporary") return camera;
  return { ...camera,
    controls: Object.fromEntries(Object.entries(camera.controls || {}).filter(([key]) => allowed(key))),
    audio_messages: camera.audio_messages && allowed("intercom"),
    audio_streams: camera.audio_streams && allowed("intercom"),
  };
}
