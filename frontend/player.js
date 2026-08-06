// Same-origin live player. Extends go2rtc's VideoRTC (vendored video-rtc.js), which owns the WebRTC/MSE
// negotiation, and connects over go2rtc's WebSocket API. The <video> it builds lives in OUR DOM, so the
// dashboard can read the decoder's real progress and recover a wedged stream (app.js freezeWatchdog).
import { VideoRTC } from "./video-rtc.js";

const STARTUP_GRACE_MS = 12000;   // give a fresh player time to connect before it can be called "frozen"

class CamPlayer extends VideoRTC {
  constructor() {
    super();
    this.mode = "webrtc,mse";     // WebRTC first, MSE fallback
    this.background = false;       // tear down WS/PC when removed from the DOM
    // Do NOT let go2rtc tear down + reconnect the stream when the tab is hidden. Its default
    // visibilityCheck disconnects on hide and reconnects on show — so switching to another tab and back
    // restarts every camera. A monitoring wall wants the stream live when you return; keep it connected.
    // (The freeze watchdog already skips hidden tabs and resets on unhide, so this doesn't fight it.)
    this.visibilityCheck = false;
    this._mountedAt = 0;
    this._frames = -1;             // last observed decoded-frame count (-1 = none decoded yet)
    this._framesAt = 0;            // performance.now() when that count last advanced
    this._disposed = false;        // a replaced tile must never reconnect in the background
  }

  oninit() {
    super.oninit();
    const v = this.video;
    // Native controls keep the "unmute to listen" affordance; muted autoplay satisfies the autoplay
    // policy; playsInline avoids iOS fullscreen hijack.
    v.controls = true;
    v.muted = true;
    v.playsInline = true;
    v.disablePictureInPicture = true;
    v.style.objectFit = "contain";
    v.style.width = "100%";
    v.style.height = "100%";
    this.style.display = "block";
    this._mountedAt = this._framesAt = performance.now();
    this._probeTID = setInterval(() => this._probe(), 2000);
  }

  // Freeze detection via the DECODER's own frame counter — the one signal a wedge cannot fake.
  // The previous signal (requestVideoFrameCallback firing) was fooled: a stuck WebRTC pipeline keeps
  // *re-presenting* the last frame at display rate, so rVFC kept firing and the freeze clock never
  // advanced — which is why a frozen tile was never rebuilt. The truthful number is
  // `inbound-rtp.framesDecoded` from `RTCPeerConnection.getStats()`: it stops the instant the picture
  // stops, even though the connection stays "connected" and packets keep arriving. For the MSE fallback
  // (no PeerConnection) we use the <video>'s own decoded-frame total, which behaves the same way.
  async _probe() {
    if (this._disposed) return;
    let n = null;
    const pc = this.pc;
    if (pc && pc.connectionState === "connected") {
      try {
        (await pc.getStats()).forEach((r) => {
          if (r.type === "inbound-rtp" && r.kind === "video" && Number.isFinite(r.framesDecoded)) {
            n = r.framesDecoded;
          }
        });
      } catch (e) { /* fall through to the <video> counter */ }
    }
    if (n == null) {
      const q = this.video && this.video.getVideoPlaybackQuality && this.video.getVideoPlaybackQuality();
      n = q ? q.totalVideoFrames : null;
    }
    if (n == null) return;
    if (n > this._frames) { this._frames = n; this._framesAt = performance.now(); }
  }

  // Milliseconds the decoded picture has been stuck (0 = healthy, or still within the startup grace).
  frozenMs() {
    // Native controls allow the user to pause a camera. A paused video is intentional, not stuck.
    if (this._disposed || !this.video || this.video.paused || this.video.ended) return 0;
    if (this._frames < 0) {                       // nothing decoded yet
      const dt = performance.now() - this._mountedAt;
      return dt < STARTUP_GRACE_MS ? 0 : dt;      // a stream that never starts is frozen too
    }
    return performance.now() - this._framesAt;
  }

  // Reset the freeze clock — used when the tab returns to the foreground, since framesDecoded can be
  // throttled while the tab is hidden (so we don't rebuild a perfectly healthy stream on the first check).
  markSeen() { this._framesAt = performance.now(); }

  // VideoRTC normally waits five seconds after removal before tearing down. That is useful for a
  // temporary DOM move, but harmful when we deliberately rebuild a frozen tile: the old WebRTC
  // session remains a consumer and can race to reconnect. Dispose it synchronously instead.
  dispose() {
    if (this._disposed) return;
    this._disposed = true;
    if (this._probeTID) { clearInterval(this._probeTID); this._probeTID = 0; }
    if (this.reconnectTID) { clearTimeout(this.reconnectTID); this.reconnectTID = 0; }
    if (this.disconnectTID) { clearTimeout(this.disconnectTID); this.disconnectTID = 0; }
    super.ondisconnect();
  }

  onconnect() {
    return this._disposed ? false : super.onconnect();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._probeTID) { clearInterval(this._probeTID); this._probeTID = 0; }
  }
}

customElements.define("cam-player", CamPlayer);
