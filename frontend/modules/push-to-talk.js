import { el } from "ccg/core";
import { t } from "ccg/i18n";

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 320;
const MAX_SECONDS = 10;

class PcmFramer {
  constructor(inputRate) {
    if (!Number.isFinite(inputRate) || inputRate < TARGET_RATE) {
      throw new Error(t("intercom.unsupportedRate"));
    }
    this.ratio = inputRate / TARGET_RATE;
    this.input = [];
    this.position = 0;
    this.output = [];
  }

  feed(chunk) {
    this.input.push(...chunk);
    const frames = [];
    while (this.position + this.ratio <= this.input.length) {
      const begin = Math.floor(this.position);
      const end = Math.max(begin + 1, Math.floor(this.position + this.ratio));
      let sum = 0;
      for (let index = begin; index < end; index += 1) sum += this.input[index];
      const sample = Math.max(-1, Math.min(1, sum / (end - begin)));
      this.output.push(sample < 0 ? Math.round(sample * 0x8000) : Math.round(sample * 0x7fff));
      this.position += this.ratio;
      if (this.output.length === FRAME_SAMPLES) {
        const wire = new Uint8Array(FRAME_SAMPLES * 2);
        const view = new DataView(wire.buffer);
        this.output.forEach((value, index) => view.setInt16(index * 2, value, true));
        frames.push(wire);
        this.output = [];
      }
    }
    const consumed = Math.floor(this.position);
    if (consumed) {
      this.input.splice(0, consumed);
      this.position -= consumed;
    }
    return frames;
  }
}

function websocketUrl(cameraId) {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/api/cameras/${encodeURIComponent(cameraId)}/intercom/stream`;
}

class PushToTalkSession {
  constructor(cameraId, onStatus, onLimit) {
    this.cameraId = cameraId;
    this.onStatus = onStatus;
    this.onLimit = onLimit;
    this.finished = false;
  }

  async start() {
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode ||
        !(window.AudioContext || window.webkitAudioContext)) {
      throw new Error(t("intercom.unsupported"));
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    this.context = new AudioContextClass();
    this.framer = new PcmFramer(this.context.sampleRate);
    const source = `
      class CcgLivePcmCapture extends AudioWorkletProcessor {
        process(inputs) {
          const channel = inputs[0] && inputs[0][0];
          if (channel) {
            const copy = channel.slice();
            this.port.postMessage(copy.buffer, [copy.buffer]);
          }
          return true;
        }
      }
      registerProcessor("ccg-live-pcm-capture", CcgLivePcmCapture);
    `;
    const moduleUrl = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
    try {
      await this.context.audioWorklet.addModule(moduleUrl);
    } finally {
      URL.revokeObjectURL(moduleUrl);
    }
    this.input = this.context.createMediaStreamSource(this.stream);
    this.processor = new AudioWorkletNode(this.context, "ccg-live-pcm-capture");
    this.silence = this.context.createGain();
    this.silence.gain.value = 0;
    await this.openSocket();
    this.processor.port.onmessage = (event) => {
      if (this.socket?.readyState !== WebSocket.OPEN) return;
      for (const frame of this.framer.feed(new Float32Array(event.data))) {
        this.socket.send(frame);
      }
    };
    this.input.connect(this.processor).connect(this.silence).connect(this.context.destination);
    this.limitTimer = setTimeout(this.onLimit, MAX_SECONDS * 1000);
  }

  openSocket() {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(websocketUrl(this.cameraId));
      this.socket = socket;
      this.completion = new Promise((complete, fail) => {
        this.completeSession = complete;
        this.failSession = fail;
      });
      this.completion.catch(() => {});
      const timeout = setTimeout(() => reject(new Error(t("talk.timeout"))), 15000);
      socket.addEventListener("message", (event) => {
        let message;
        try { message = JSON.parse(event.data); } catch { return; }
        if (message.type === "ready") {
          clearTimeout(timeout);
          this.ready = true;
          this.onStatus("talk.active");
          resolve();
        } else if (message.type === "error") {
          clearTimeout(timeout);
          const error = new Error(message.detail || t("talk.failed"));
          this.failSession(error);
          reject(error);
        } else if (message.type === "complete") {
          this.completeSession(message);
        }
      });
      socket.addEventListener("close", () => {
        clearTimeout(timeout);
        const error = new Error(t("talk.closed"));
        this.failSession(error);
        if (!this.ready) reject(error);
      });
      socket.addEventListener("error", () => {
        const error = new Error(t("talk.failed"));
        this.failSession(error);
        reject(error);
      });
    });
  }

  async stop() {
    if (this.finished) return;
    this.finished = true;
    clearTimeout(this.limitTimer);
    this.input?.disconnect();
    this.processor?.disconnect();
    this.silence?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.context && this.context.state !== "closed") await this.context.close();
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send("stop");
      try {
        await Promise.race([
          this.completion,
          new Promise((_, reject) => setTimeout(() => reject(new Error(t("talk.timeout"))), 12000)),
        ]);
      } finally {
        this.socket.close();
      }
    } else {
      this.socket?.close();
    }
  }
}

export function pushToTalkButton(cam) {
  const trigger = el("button", {
    className: "icon-btn", textContent: "🗣", title: t("talk.open"), type: "button",
  });
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    let session = null;
    let held = false;
    let starting = false;
    const status = el("small", { className: "camera-control-status", textContent: t("talk.idle") });
    const talk = el("button", {
      className: "btn-primary push-talk-button", textContent: t("talk.hold"), type: "button",
    });
    const close = el("button", {
      className: "icon-btn", textContent: "×", title: t("scan.close"), type: "button",
    });
    const setStatus = (key, error = false) => {
      status.classList.toggle("error", error);
      status.textContent = t(key);
    };
    const stop = async () => {
      held = false;
      talk.classList.remove("active");
      if (!session) return;
      const current = session;
      session = null;
      try {
        await current.stop();
        setStatus("talk.done");
      } catch (error) {
        status.classList.add("error");
        status.textContent = t("talk.failedDetail", { msg: error.message });
      } finally {
        talk.disabled = false;
      }
    };
    const start = async () => {
      if (starting || session) return;
      starting = true;
      talk.disabled = true;
      setStatus("talk.connecting");
      const current = new PushToTalkSession(cam.id, setStatus, () => void stop());
      try {
        await current.start();
        session = current;
        if (!held) await stop();
      } catch (error) {
        try { await current.stop(); } catch { /* Preserve the original startup error. */ }
        setStatus("talk.failedDetail", true);
        status.textContent = t("talk.failedDetail", { msg: error.message });
        talk.disabled = false;
      } finally {
        starting = false;
      }
    };
    talk.addEventListener("pointerdown", (pointerEvent) => {
      pointerEvent.preventDefault();
      held = true;
      talk.classList.add("active");
      talk.setPointerCapture?.(pointerEvent.pointerId);
      void start();
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
      talk.addEventListener(name, () => void stop());
    }
    close.addEventListener("click", () => {
      if (starting || session) return;
      overlay.remove();
    });
    const card = el("div", { className: "card modal-card audio-message-modal" },
      el("div", { className: "modal-head" },
        el("h2", { textContent: t("talk.title", { name: cam.name || cam.mac }) }), close),
      el("p", { className: "muted compact", textContent: t("talk.hint") }),
      el("div", { className: "audio-message-actions" }, talk), status);
    const overlay = el("div", { className: "modal" }, card);
    document.body.append(overlay);
  });
  return trigger;
}
