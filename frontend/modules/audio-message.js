import { api, el } from "ccg/core";
import { t } from "ccg/i18n";

const TARGET_RATE = 8000;
const FRAME_SAMPLES = 160;
const MAX_SECONDS = 10;

function concatenate(chunks) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const output = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.length;
  }
  return output;
}

export function pcmForCamera(chunks, inputRate) {
  if (!Number.isFinite(inputRate) || inputRate < TARGET_RATE) {
    throw new Error(t("intercom.unsupportedRate"));
  }
  const input = concatenate(chunks);
  const ratio = inputRate / TARGET_RATE;
  const available = Math.min(Math.floor(input.length / ratio), TARGET_RATE * MAX_SECONDS);
  const complete = Math.floor(available / FRAME_SAMPLES) * FRAME_SAMPLES;
  if (!complete) throw new Error(t("intercom.tooShort"));

  const pcm = new ArrayBuffer(complete * 2);
  const view = new DataView(pcm);
  for (let index = 0; index < complete; index += 1) {
    const begin = Math.floor(index * ratio);
    const end = Math.max(begin + 1, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let cursor = begin; cursor < end && cursor < input.length; cursor += 1) {
      sum += input[cursor];
    }
    const sample = Math.max(-1, Math.min(1, sum / Math.max(1, end - begin)));
    view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return new Uint8Array(pcm);
}

class PcmRecorder {
  async start(onLimit) {
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode ||
        !(window.AudioContext || window.webkitAudioContext)) {
      throw new Error(t("intercom.unsupported"));
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    this.context = new AudioContextClass();
    this.chunks = [];
    const source = `
      class CcgPcmCapture extends AudioWorkletProcessor {
        process(inputs) {
          const channel = inputs[0] && inputs[0][0];
          if (channel) {
            const copy = channel.slice();
            this.port.postMessage(copy.buffer, [copy.buffer]);
          }
          return true;
        }
      }
      registerProcessor("ccg-pcm-capture", CcgPcmCapture);
    `;
    const moduleUrl = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
    try {
      await this.context.audioWorklet.addModule(moduleUrl);
    } finally {
      URL.revokeObjectURL(moduleUrl);
    }
    this.input = this.context.createMediaStreamSource(this.stream);
    this.processor = new AudioWorkletNode(this.context, "ccg-pcm-capture");
    this.silence = this.context.createGain();
    this.silence.gain.value = 0;
    this.processor.port.onmessage = (event) => {
      this.chunks.push(new Float32Array(event.data));
    };
    this.input.connect(this.processor).connect(this.silence).connect(this.context.destination);
    this.startedAt = performance.now();
    this.limitTimer = setTimeout(onLimit, MAX_SECONDS * 1000);
  }

  elapsedSeconds() {
    return Math.min(MAX_SECONDS, (performance.now() - this.startedAt) / 1000);
  }

  async finish() {
    clearTimeout(this.limitTimer);
    this.input?.disconnect();
    this.processor?.disconnect();
    this.silence?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    const rate = this.context.sampleRate;
    await this.context.close();
    return pcmForCamera(this.chunks, rate);
  }

  async cancel() {
    clearTimeout(this.limitTimer);
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.context && this.context.state !== "closed") await this.context.close();
  }
}

class PcmPreview {
  stop() {
    const source = this.source;
    const context = this.context;
    this.source = null;
    this.context = null;
    if (source) {
      try { source.stop(); } catch { /* It may already have ended. */ }
      source.disconnect();
    }
    if (context && context.state !== "closed") void context.close();
  }

  play(pcm) {
    this.stop();
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    const context = new AudioContextClass({ sampleRate: TARGET_RATE });
    const samples = new Int16Array(pcm.buffer, pcm.byteOffset, pcm.byteLength / 2);
    const buffer = context.createBuffer(1, samples.length, TARGET_RATE);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) {
      channel[index] = samples[index] / 0x8000;
    }
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    this.source = source;
    this.context = context;
    source.addEventListener("ended", () => {
      if (this.source !== source) return;
      this.source = null;
      this.context = null;
      source.disconnect();
      if (context.state !== "closed") void context.close();
    }, { once: true });
    source.start();
  }
}

export function audioMessageButton(cam) {
  const trigger = el("button", {
    className: "icon-btn", textContent: "🎙", title: t("intercom.open"), type: "button",
  });
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    let recorder = null;
    let pcm = null;
    let elapsedTimer = null;
    let sending = false;
    const pcmPreview = new PcmPreview();
    const status = el("small", { className: "camera-control-status" });
    const record = el("button", { className: "btn-primary", textContent: t("intercom.record") });
    const stop = el("button", { textContent: t("intercom.stop"), disabled: true });
    const preview = el("button", { textContent: t("intercom.preview"), disabled: true });
    const send = el("button", { className: "btn-primary", textContent: t("intercom.send"), disabled: true });
    const close = el("button", {
      className: "icon-btn", textContent: "×", title: t("scan.close"), type: "button",
    });

    const setStatus = (key, values = {}, error = false) => {
      status.classList.toggle("error", error);
      status.textContent = t(key, values);
    };
    const finishRecording = async () => {
      if (!recorder) return;
      const activeRecorder = recorder;
      recorder = null;
      clearInterval(elapsedTimer);
      stop.disabled = true;
      try {
        pcm = await activeRecorder.finish();
        const seconds = pcm.byteLength / (TARGET_RATE * 2);
        setStatus("intercom.ready", { seconds: seconds.toFixed(1) });
        preview.disabled = false;
        send.disabled = false;
      } catch (error) {
        setStatus("intercom.failed", { msg: error.message }, true);
      } finally {
        record.disabled = false;
      }
    };
    const dismiss = async () => {
      if (sending) return;
      clearInterval(elapsedTimer);
      await recorder?.cancel();
      pcmPreview.stop();
      overlay.remove();
    };

    record.addEventListener("click", async () => {
      pcmPreview.stop();
      pcm = null;
      record.disabled = true;
      preview.disabled = true;
      send.disabled = true;
      setStatus("intercom.requesting");
      recorder = new PcmRecorder();
      try {
        await recorder.start(() => void finishRecording());
        stop.disabled = false;
        setStatus("intercom.recording", { seconds: "0.0" });
        elapsedTimer = setInterval(() => {
          setStatus("intercom.recording", { seconds: recorder.elapsedSeconds().toFixed(1) });
        }, 100);
      } catch (error) {
        await recorder.cancel();
        recorder = null;
        record.disabled = false;
        setStatus("intercom.failed", { msg: error.message }, true);
      }
    });
    stop.addEventListener("click", () => void finishRecording());
    preview.addEventListener("click", () => pcm && pcmPreview.play(pcm));
    send.addEventListener("click", async () => {
      if (!pcm || !window.confirm(t("intercom.confirm", { name: cam.name || cam.mac }))) return;
      pcmPreview.stop();
      sending = true;
      close.disabled = true;
      send.disabled = true;
      record.disabled = true;
      preview.disabled = true;
      setStatus("intercom.sending");
      try {
        await api(`/cameras/${encodeURIComponent(cam.id)}/intercom/messages`, {
          method: "POST", headers: { "Content-Type": "audio/pcm" }, body: pcm,
        });
        setStatus("intercom.sent");
        pcm = null;
      } catch (error) {
        setStatus("intercom.failed", { msg: error.message }, true);
        send.disabled = false;
        preview.disabled = false;
      } finally {
        sending = false;
        close.disabled = false;
        record.disabled = false;
      }
    });
    close.addEventListener("click", () => void dismiss());

    const card = el("div", { className: "card modal-card audio-message-modal" },
      el("div", { className: "modal-head" },
        el("h2", { textContent: t("intercom.title", { name: cam.name || cam.mac }) }), close),
      el("p", { className: "muted compact", textContent: t("intercom.hint") }),
      el("div", { className: "audio-message-actions" }, record, stop, preview, send),
      status);
    const overlay = el("div", { className: "modal" }, card);
    document.body.append(overlay);
  });
  return trigger;
}
