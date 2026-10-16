// Test-only isolation, installed before the production dashboard loads.
// Keeps the actual selected camera response; does not mock media or authorization.
(() => {
  const camera = __CAMERA_ID__;
  const probe = window.__ccgMediaProbe = { sockets: [], blocked: 0, frames: 0 };
  const originalFetch = window.fetch;
  window.fetch = async function(input, options) {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    const method = (options?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    if (method !== "GET" && !["/api/login", "/api/logout"].includes(url.pathname)) {
      probe.blocked++;
      throw new Error("Media acceptance forbids control writes");
    }
    const response = await originalFetch.call(this, input, options);
    if (url.origin !== location.origin || url.pathname !== "/api/cameras" || !response.ok) return response;
    const selected = (await response.json()).filter(item => item.id === camera);
    if (selected.length !== 1) throw new Error("Expected one registered test camera");
    localStorage.setItem("ccg.quality." + selected[0].mac, "smooth");
    return new Response(JSON.stringify(selected), {status: response.status,
      headers: {"Content-Type": "application/json"}});
  };
  const NativeSocket = window.WebSocket;
  window.WebSocket = class extends NativeSocket {
    constructor(address, protocols) {
      const url = new URL(address, location.href);
      if (url.host !== location.host || url.pathname !== "/api/go2rtc/ws" ||
          url.searchParams.get("src") !== camera + "_web" || probe.sockets.length >= 8) {
        probe.blocked++;
        throw new Error("Media acceptance forbids other streams");
      }
      super(address, protocols);
      probe.sockets.push(this);
    }
  };
  probe.observe = () => {
    const player = document.querySelector("cam-player");
    if (!player?.video || !player.video.requestVideoFrameCallback) return false;
    probe.player = player;
    probe.video = player.video;
    const frame = (_, metadata) => {
      probe.callbacks = (probe.callbacks || 0) + 1;
      if (Number.isFinite(metadata.mediaTime) &&
          (probe.lastTime === undefined || metadata.mediaTime > probe.lastTime)) {
        probe.frames++;
        probe.firstTime ??= metadata.mediaTime;
        probe.lastTime = metadata.mediaTime;
      }
      if (probe.frames < 8 && probe.callbacks < 64) probe.video.requestVideoFrameCallback(frame);
    };
    probe.video.requestVideoFrameCallback(frame);
    return true;
  };
  probe.stopped = () => !!probe.video && !probe.player.isConnected &&
    probe.video.paused && !probe.video.getAttribute("src") && !probe.video.srcObject &&
    probe.sockets.length > 0 && probe.sockets.every(socket => socket.readyState === NativeSocket.CLOSED) &&
    document.querySelectorAll("cam-player").length === 0;
})();
