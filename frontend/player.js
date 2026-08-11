// Same-origin live player. Extends go2rtc's VideoRTC (vendored video-rtc.js), which owns the WebRTC/MSE
// negotiation, and connects over go2rtc's WebSocket API. The <video> it builds lives in OUR DOM, so the
// dashboard can read the decoder's real progress and recover a wedged stream (app.js freezeWatchdog).
const buildVersion = window.__CCG_BUILD__ || "dev";
const { VideoRTC } = await import(`./video-rtc.js?v=${encodeURIComponent(buildVersion)}`);

// A fresh RTSP/UDP session may legitimately wait almost one camera GOP for its first complete
// VPS/SPS/PPS + IDR set (measured just under 10 s on these units), before WebRTC negotiation time.
const STARTUP_GRACE_MS = 45000;

class CamPlayer extends VideoRTC {
  constructor() {
    super();
    // Chromium's WebRTC receiver can remain ICE/DTLS "connected" and keep receiving RTP while
    // decoding no new frames. VideoRTC then permanently wins its fixed WebRTC-vs-MSE priority
    // comparison and discards the healthy MSE path. Use MSE as the sole dashboard transport: the
    // same local H.264 producer remains shared, but a wedged PeerConnection can no longer pin the
    // visible picture to an old frame. The bounded MSE queue below jumps directly to the live edge.
    this.mode = "mse";
    this.background = false;       // tear down WS/PC when removed from the DOM
    // Do NOT let go2rtc tear down + reconnect the stream when the tab is hidden. Its default
    // visibilityCheck disconnects on hide and reconnects on show — so switching to another tab and back
    // restarts every camera. A monitoring wall wants the stream live when you return; keep it connected.
    // (The freeze watchdog already skips hidden tabs and resets on unhide, so this doesn't fight it.)
    this.visibilityCheck = false;
    this._mountedAt = 0;
    this._frames = -1;             // last observed decoded-frame count (-1 = none decoded yet)
    this._framesAt = 0;            // performance.now() when that count last advanced
    this._presented = -1;          // last frame the browser compositor says it presented
    this._presentedAt = 0;
    this._mediaTime = null;        // presentation timestamp of the last genuinely new media frame
    this._mediaAt = 0;
    this._rvfcID = 0;
    this._hasRVFC = false;
    this._probeBusy = false;
    this._statsPC = null;
    this._rtcStats = {};
    this._lastStallEvent = null;
    this._lastDiagnosticAt = new Map();
    this._everPlayed = false;
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
    this._mountedAt = this._framesAt = this._presentedAt = this._mediaAt = performance.now();
    for (const name of ["waiting", "stalled"]) {
      v.addEventListener(name, () => {
        this._lastStallEvent = { name, at: performance.now() };
        this._emitDiagnostic(name);
      });
    }
    v.addEventListener("playing", () => {
      this._everPlayed = true;
      if (this._lastStallEvent) {
        this._emitDiagnostic("playing", {
          stallDurationMs: Math.round(performance.now() - this._lastStallEvent.at),
        });
        this._lastStallEvent = null;
      }
    });
    this._startMonitors();
  }

  _startMonitors() {
    if (this._disposed || !this.video) return;
    if (!this._probeTID) this._probeTID = setInterval(() => this._probe(), 2000);
    if (!this._rvfcID) this._trackPresentedFrames();
  }

  _emitDiagnostic(event, detail = {}) {
    const now = performance.now();
    const last = this._lastDiagnosticAt.get(event);
    // Browsers may emit waiting/stalled in bursts for one incident. One snapshot per five seconds
    // contains the useful state without turning telemetry itself into load.
    if (last !== undefined && now - last < 5000) return;
    this._lastDiagnosticAt.set(event, now);
    this.dispatchEvent(new CustomEvent("media-diagnostic", { detail: { event, ...detail } }));
  }

  // Track compositor submissions for diagnostics, but use mediaTime (not presentedFrames) as the
  // progress signal: a browser may submit the last picture again while the media timeline is stuck.
  _trackPresentedFrames() {
    const v = this.video;
    if (!v || typeof v.requestVideoFrameCallback !== "function") return;
    if (this._rvfcID) return;
    this._hasRVFC = true;
    const onFrame = (_now, meta) => {
      if (this._disposed || !this.video) return;
      const n = meta && meta.presentedFrames;
      if (Number.isFinite(n) && n > this._presented) {
        this._presented = n;
        this._presentedAt = performance.now();
      }
      // `presentedFrames` counts compositor submissions and can advance while the last picture is
      // submitted again. mediaTime is the timestamp on the media timeline; only a new timestamp is
      // evidence that the video itself advanced.
      const mt = meta && meta.mediaTime;
      if (Number.isFinite(mt) && mt !== this._mediaTime) {
        this._mediaTime = mt;
        this._mediaAt = performance.now();
      }
      this._rvfcID = this.video.requestVideoFrameCallback(onFrame);
    };
    this._rvfcID = v.requestVideoFrameCallback(onFrame);
  }

  // Freeze detection via the DECODER's own frame counter — the one signal a wedge cannot fake.
  // The previous signal (requestVideoFrameCallback firing) was fooled: a stuck WebRTC pipeline keeps
  // *re-presenting* the last frame at display rate, so rVFC kept firing and the freeze clock never
  // advanced — which is why a frozen tile was never rebuilt. The truthful number is
  // `inbound-rtp.framesDecoded` from `RTCPeerConnection.getStats()`: it stops the instant the picture
  // stops, even though the connection stays "connected" and packets keep arriving. For the MSE fallback
  // (no PeerConnection) we use the <video>'s own decoded-frame total, which behaves the same way.
  async _probe() {
    if (this._disposed || this._probeBusy) return;
    this._probeBusy = true;
    let n = null;
    const pc = this.pc;
    try {
      if (pc && pc !== this._statsPC) {
        // A reconnect creates a fresh stats counter starting at zero.
        this._statsPC = pc;
        this._frames = -1;
        this._framesAt = performance.now();
      }
      if (pc && pc.connectionState === "connected") {
        (await pc.getStats()).forEach((r) => {
          if (r.type === "inbound-rtp" && r.kind === "video" && Number.isFinite(r.framesDecoded)) {
            n = r.framesDecoded;
            this._rtcStats = {
              rtcPacketsReceived: r.packetsReceived,
              rtcPacketsLost: r.packetsLost,
              rtcJitter: r.jitter,
              rtcFramesReceived: r.framesReceived,
              rtcFramesDropped: r.framesDropped,
              rtcKeyFramesDecoded: r.keyFramesDecoded,
              rtcFreezeCount: r.freezeCount,
              rtcTotalFreezesDuration: r.totalFreezesDuration,
              rtcJitterBufferDelay: r.jitterBufferDelay,
              rtcJitterBufferEmittedCount: r.jitterBufferEmittedCount,
            };
          }
        });
      }
      if (n == null) {
        const q = this.video && this.video.getVideoPlaybackQuality && this.video.getVideoPlaybackQuality();
        n = q ? q.totalVideoFrames : null;
      }
      if (n == null) return;
      if (this._frames >= 0 && n < this._frames) this._framesAt = performance.now();
      if (n !== this._frames) { this._frames = n; this._framesAt = performance.now(); }
    } catch (e) { /* the startup/watchdog clock handles an unavailable stats call */ }
    finally { this._probeBusy = false; }
  }

  // Milliseconds the visible picture has been stuck (0 = healthy, or still within startup grace).
  frozenMs() {
    if (this._disposed || !this.video) return 0;
    // Native controls allow an intentional pause after playback started. Do not exempt the initial
    // paused state (or an ended live stream), otherwise a player that never negotiates is invisible
    // to the startup watchdog forever.
    const activeTransport = this.pcState === WebSocket.OPEN ||
      (this.wsState === WebSocket.OPEN && Boolean(this.mseCodecs));
    if (this.video.paused && !this.video.ended && this._everPlayed && activeTransport) return 0;
    const now = performance.now();
    if (this._frames < 0) {                       // nothing decoded yet
      const dt = now - this._mountedAt;
      return dt < STARTUP_GRACE_MS ? 0 : dt;      // a stream that never starts is frozen too
    }
    const decoderStall = now - this._framesAt;
    if (!this._hasRVFC) return decoderStall;
    const mediaStall = now - (this._mediaTime == null ? this._mountedAt : this._mediaAt);
    // Either stage being stuck is enough to rebuild: decoded frames without media-time progress
    // are duplicates/stale output; media callbacks without decoded progress are not a healthy feed.
    return Math.max(decoderStall, mediaStall);
  }

  // Reset the freeze clock — used when the tab returns to the foreground, since framesDecoded can be
  // throttled while the tab is hidden (so we don't rebuild a perfectly healthy stream on the first check).
  markSeen() {
    this._framesAt = this._presentedAt = this._mediaAt = performance.now();
  }

  diagnostics() {
    const q = this.video && this.video.getVideoPlaybackQuality && this.video.getVideoPlaybackQuality();
    const v = this.video;
    let bufferedStart = null, bufferedEnd = null;
    if (v && v.buffered && v.buffered.length) {
      bufferedStart = v.buffered.start(0);
      bufferedEnd = v.buffered.end(v.buffered.length - 1);
    }
    return {
      transport: this.pcState === WebSocket.OPEN ? "webrtc" :
        (this.wsState === WebSocket.OPEN && this.mseCodecs ? "mse" : "connecting"),
      connectionState: this.pc && this.pc.connectionState,
      iceConnectionState: this.pc && this.pc.iceConnectionState,
      readyState: v && v.readyState,
      networkState: v && v.networkState,
      paused: v && v.paused,
      currentTime: v && v.currentTime,
      playbackRate: v && v.playbackRate,
      bufferedStart,
      bufferedEnd,
      bufferedGap: bufferedEnd == null || !v ? null : bufferedEnd - v.currentTime,
      mseQueueBytes: this.mseQueueBytes,
      mseQueueLimit: this.mseQueueLimit,
      framesDecoded: this._frames,
      totalVideoFrames: q && q.totalVideoFrames,
      droppedVideoFrames: q && q.droppedVideoFrames,
      mediaTime: this._mediaTime,
      presentedFrames: this._presented,
      ...this._rtcStats,
    };
  }

  // VideoRTC normally waits five seconds after removal before tearing down. That is useful for a
  // temporary DOM move, but harmful when we deliberately rebuild a frozen tile: the old WebRTC
  // session remains a consumer and can race to reconnect. Dispose it synchronously instead.
  dispose() {
    if (this._disposed) return;
    this._disposed = true;
    if (this._probeTID) { clearInterval(this._probeTID); this._probeTID = 0; }
    if (this._rvfcID && this.video && typeof this.video.cancelVideoFrameCallback === "function") {
      this.video.cancelVideoFrameCallback(this._rvfcID);
      this._rvfcID = 0;
    }
    if (this.reconnectTID) { clearTimeout(this.reconnectTID); this.reconnectTID = 0; }
    if (this.disconnectTID) { clearTimeout(this.disconnectTID); this.disconnectTID = 0; }
    super.ondisconnect();
  }

  onconnect() {
    return this._disposed ? false : super.onconnect();
  }

  connectedCallback() {
    const alreadyInitialized = Boolean(this.video);
    super.connectedCallback();
    if (alreadyInitialized) this._startMonitors();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._probeTID) { clearInterval(this._probeTID); this._probeTID = 0; }
    if (this._rvfcID && this.video && typeof this.video.cancelVideoFrameCallback === "function") {
      this.video.cancelVideoFrameCallback(this._rvfcID);
      this._rvfcID = 0;
    }
  }
}

customElements.define("cam-player", CamPlayer);
