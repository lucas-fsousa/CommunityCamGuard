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

  const classic = (src) => new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `${src}?v=${encodeURIComponent(version)}`;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`failed to load ${src}`));
    document.body.append(script);
  });

  // Register <cam-player> before app.js creates tiles. i18n then installs the globals app.js uses.
  await import(`/player.js?v=${encodeURIComponent(version)}`);
  await classic("/i18n.js");
  await classic("/app.js");
})().catch((error) => {
  console.error("[CCG] dashboard bootstrap failed", error);
  const badge = document.getElementById("app-version");
  if (badge) badge.textContent = "build error";
});
