// Same-origin live player. Replaces the old cross-origin go2rtc <iframe> (stream.html) so the
// dashboard can read the *actual* decoded-frame progress of the <video> and detect a real freeze
// — the kind that is invisible to go2rtc's producer/consumer packet counters (a wedged WebRTC
// PeerConnection keeps "receiving"/"sending" packets while the picture is stuck). We extend go2rtc's
// own VideoRTC (vendored video-rtc.js), which owns the WebRTC/MSE negotiation and reconnect logic,
// and connects to go2rtc over its WebSocket API (cross-origin WS connect is allowed; the <video> it
// builds lives in OUR DOM, so it is same-origin and inspectable).
import { VideoRTC } from "./video-rtc.js";

class CamPlayer extends VideoRTC {
  constructor() {
    super();
    this.mode = "webrtc,mse";     // WebRTC first, MSE fallback — matches the old iframe URL
    this.background = false;      // tear down WS/PC when removed from the DOM
    this._lastFrameAt = 0;        // performance.now() of the last *presented* video frame
    this._mountedAt = 0;
  }

  oninit() {
    super.oninit();
    const v = this.video;
    // Native controls preserve the "unmute to listen" affordance the old player had; muted autoplay
    // satisfies the autoplay policy. playsInline avoids iOS fullscreen hijack.
    v.controls = true;
    v.muted = true;
    v.playsInline = true;
    v.disablePictureInPicture = true;
    v.style.objectFit = "contain";
    v.style.width = "100%";
    v.style.height = "100%";
    this.style.display = "block";
    this._mountedAt = performance.now();
    this._trackFrames();
  }

  // Count only frames actually *presented* to the screen. requestVideoFrameCallback fires once per
  // painted frame (Chromium/Opera support it); when the picture freezes it stops firing even though
  // go2rtc keeps streaming — which is exactly the signal the packet-counter watchdog could not see.
  _trackFrames() {
    const v = this.video;
    if (typeof v.requestVideoFrameCallback === "function") {
      const cb = () => {
        this._lastFrameAt = performance.now();
        if (this.isConnected && this.video) this.video.requestVideoFrameCallback(cb);
      };
      v.requestVideoFrameCallback(cb);
    } else {
      // Fallback: poll the decoded-frame total (Safari/older engines).
      let last = -1;
      this._pollTID = setInterval(() => {
        if (!this.video) return;
        const q = this.video.getVideoPlaybackQuality && this.video.getVideoPlaybackQuality();
        const n = q ? q.totalVideoFrames : Math.floor((this.video.currentTime || 0) * 30);
        if (n !== last) { last = n; this._lastFrameAt = performance.now(); }
      }, 1000);
    }
  }

  // Milliseconds since the last presented frame — the freeze signal. Returns 0 (i.e. "healthy")
  // while intentionally paused, or during the startup grace before the first frame arrives.
  frozenMs() {
    const v = this.video;
    if (!v || v.paused) return 0;
    const ref = this._lastFrameAt || this._mountedAt || 0;
    if (!ref) return 0;
    if (!this._lastFrameAt && performance.now() - this._mountedAt < STARTUP_GRACE_MS) return 0;
    return performance.now() - ref;
  }

  // Reset the freeze clock — used when the tab returns to foreground, so a rebuild isn't triggered
  // just because rVFC was paused while hidden.
  markSeen() { this._lastFrameAt = performance.now(); }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._pollTID) { clearInterval(this._pollTID); this._pollTID = 0; }
  }
}

const STARTUP_GRACE_MS = 12000;   // don't flag a player as frozen before it has had time to connect
customElements.define("cam-player", CamPlayer);
