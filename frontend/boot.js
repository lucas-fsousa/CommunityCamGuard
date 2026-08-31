"use strict";

// Stable bootstrap: the server derives one cache key from the actual backend/frontend contents.
// This file itself is served with `no-cache`; everything it loads gets the automatic content hash.
(async () => {
  const response = await fetch("/api/build", { cache: "no-store" });
  if (!response.ok) throw new Error(`build identity unavailable (${response.status})`);
  const { version } = await response.json();
  window.__CCG_BUILD__ = version;

  const style = document.createElement("link");
  style.rel = "stylesheet";
  style.href = `/style.css?v=${encodeURIComponent(version)}`;
  document.head.append(style);

  // Every semantic module receives the same content-derived cache key. An import map keeps those
  // versioned URLs out of source imports and guarantees a contributor cannot serve a stale child
  // module after changing only one frontend file.
  const moduleUrl = (path) => `${path}?v=${encodeURIComponent(version)}`;
  const importMap = document.createElement("script");
  importMap.type = "importmap";
  importMap.textContent = JSON.stringify({ imports: {
    "ccg/core": moduleUrl("/modules/core.js"),
    "ccg/i18n": moduleUrl("/i18n.js"),
    "ccg/live": moduleUrl("/modules/live-cameras.js"),
    "ccg/cameras": moduleUrl("/modules/camera-management.js"),
    "ccg/audio-message": moduleUrl("/modules/audio-message.js"),
    "ccg/push-to-talk": moduleUrl("/modules/push-to-talk.js"),
    "ccg/provisioning-ble": moduleUrl("/modules/camera-provisioning-ble.js"),
    "ccg/recordings": moduleUrl("/modules/recordings.js"),
  } });
  document.head.append(importMap);

  // Register <cam-player> before app.js creates tiles, then start the orchestration module.
  await import(`/player.js?v=${encodeURIComponent(version)}`);
  await import(`/app.js?v=${encodeURIComponent(version)}`);
})().catch((error) => {
  console.error("[CCG] dashboard bootstrap failed", error);
  const badge = document.getElementById("app-version");
  if (badge) badge.textContent = "build error";
});
