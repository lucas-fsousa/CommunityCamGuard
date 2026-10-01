// One archive selection owns its requests, timer and video. No live-camera state.
export function createRecordingPlayback(player, status, api, t, onState = () => {}) {
  let current = null;
  let disposed = false;
  const active = (item) => !disposed && current === item;
  const message = (key, loading = false) => {
    status.textContent = key ? t(key) : "";
    onState({ loading, text: status.textContent });
  };
  // A codec-specific hint, not a guarantee for every HEVC profile/file.
  const nativeHevc = ["hvc1", "hev1"].some((codec) => {
    try { return player.canPlayType?.(`video/mp4; codecs="${codec}, mp4a.40.2"`) === "probably"; }
    catch { return false; }
  });

  function stop() {
    if (current) {
      current.abort.abort();
      clearTimeout(current.timer);
      clearTimeout(current.requestTimer);
      clearTimeout(current.nativeTimer);
      clearTimeout(current.startTimer);
      clearBuffer(current);
    }
    current = null;
    message("");
    player.pause();
    player.removeAttribute("src");
    player.load();
  }

  function watchStart(item) {
    if (item.original && !item.started) {
      clearTimeout(item.nativeTimer);
      item.nativeTimer = setTimeout(() => fallback(item), 12000);
    }
    if (!item.original && !item.started) {
      clearTimeout(item.startTimer);
      const attempt = item.attempt;
      item.startTimer = setTimeout(() => {
        if (!active(item) || item.attempt !== attempt || item.started || player.paused) return;
        item.failed = true;
        item.ready = false;
        item.attempt++; // Late play rejection/events no longer own this attempt.
        item.playPending = false;
        player.pause();
        player.removeAttribute("src");
        player.load();
        message("rec.startTimedOut");
      }, 30000);
    }
  }

  function play(item) {
    if (!active(item) || !item.ready || item.playPending) return;
    item.playPending = true;
    const attempt = item.attempt;
    watchStart(item);
    // Issue play immediately after attaching the source, not from loadedmetadata.
    // The promise waits for media readiness; native browser autoplay policy still applies.
    let promise;
    try { promise = player.play(); } catch (error) { promise = Promise.reject(error); }
    Promise.resolve(promise).catch((error) => {
      if (!active(item) || item.attempt !== attempt) return;
      if (error?.name === "NotSupportedError" && item.original) return fallback(item);
      clearTimeout(item.nativeTimer); // Autoplay denial is not a codec failure.
      clearTimeout(item.startTimer);
      // A deliberate pause can reject the pending play promise; it is not a media failure.
      if (error?.name === "AbortError" && player.paused && !player.error) return message("");
      if (error?.name === "NotAllowedError") return message("rec.readyPressPlay");
      fail(item);
    }).finally(() => { if (item.attempt === attempt) item.playPending = false; });
  }

  function clearBuffer(item) {
    clearTimeout(item.bufferTimer);
    item.bufferTimer = null;
  }

  function watchBuffer(item) {
    if (!item.started || item.bufferTimer != null) return;
    const attempt = item.attempt;
    item.bufferPosition = player.currentTime;
    const timer = item.bufferTimer = setTimeout(() => {
      if (!active(item) || item.attempt !== attempt || item.bufferTimer !== timer) return;
      item.bufferTimer = null;
      if (player.paused || player.ended) return;
      if (!player.seeking && player.currentTime > item.bufferPosition) return watchBuffer(item);
      fail(item, "rec.bufferTimedOut");
    }, 30000);
  }

  function fail(item, key = "rec.playbackFailed") {
    if (!active(item)) return;
    item.resume = player.currentTime || 0;
    item.failed = true;
    item.ready = false;
    item.attempt++; // Reject late promises/events from this failed source.
    item.playPending = false;
    clearTimeout(item.nativeTimer);
    clearTimeout(item.startTimer);
    clearBuffer(item);
    player.pause();
    player.removeAttribute("src");
    player.load();
    message(key);
  }

  function fallback(item) {
    if (!active(item) || !item.original || item.fallback) return;
    item.resume = player.currentTime || 0;
    item.fallback = true;
    item.deadline = Date.now() + 650000; // A late decode failure gets its own preparation budget.
    item.original = false;
    item.ready = false;
    item.attempt++;
    item.playPending = false;
    clearTimeout(item.nativeTimer);
    clearTimeout(item.startTimer);
    clearBuffer(item);
    player.pause();
    player.removeAttribute("src");
    player.load();
    message("rec.preparingSeekable", true);
    void check(item, true);
  }

  function ready(item, cached, original = false) {
    if (!active(item)) return;
    item.ready = true;
    item.started = false;
    item.original = original;
    item.attempt++;
    message(cached ? "rec.seekableReady" : "rec.startingPlayback", true);
    player.src = "/api/recordings/file?path=" + encodeURIComponent(item.path)
      + (original ? "&original=true" : "");
    player.load();
    play(item);
  }

  async function check(item, prepare = false) {
    const requestTimer = item.requestTimer = setTimeout(() => item.abort.abort(), 15000);
    try {
      const result = await api(
        (prepare ? "/recordings/prepare?path=" : "/recordings/playback-status?path=")
          + encodeURIComponent(item.path)
          + (prepare && nativeHevc && !item.fallback ? "&native_hevc=true" : ""),
        { ...(prepare ? { method: "POST" } : {}), signal: item.abort.signal },
      );
      if (!active(item)) return;
      if (result.ready) return ready(item, result.cached, result.original === true);
      if (!result.transcoding || Date.now() >= item.deadline) {
        item.failed = true;
        message("rec.playbackFailed");
        return;
      }
      message("rec.preparingSeekable", true);
      item.timer = setTimeout(() => check(item), 1000);
    } catch (error) {
      if (!active(item)) return;
      item.failed = true;
      message(error?.status === 429 ? "rec.playbackBusy" : "rec.playbackFailed");
    } finally {
      clearTimeout(requestTimer);
    }
  }

  function onPlaying() {
    if (!current?.ready || disposed) return;
    // Some devices can play the audio track while rejecting the video track.
    if (current.original && player.videoWidth === 0) return fallback(current);
    current.started = true;
    clearTimeout(current.nativeTimer);
    clearTimeout(current.startTimer);
    clearBuffer(current);
    message("");
  }
  function onMetadata() {
    if (!current?.ready || !current.resume || disposed) return;
    player.currentTime = Math.min(current.resume, player.duration || current.resume);
    current.resume = 0;
  }
  function onPlay() {
    if (current?.ready && !disposed) watchStart(current);
  }
  function onPause() {
    if (current) clearTimeout(current.nativeTimer);
    if (current) clearTimeout(current.startTimer);
    if (current) clearBuffer(current);
    if (current?.ready && !disposed && player.paused) message("");
  }
  function onWaiting() {
    if (current?.ready && !current.failed && !disposed && !player.paused) {
      message("rec.buffering", true);
      watchBuffer(current);
    }
  }
  function onCanPlay() {
    if (current?.ready && !disposed && player.paused) message("");
  }
  function onTimeUpdate() {
    if (!current?.ready || disposed || current.bufferTimer == null || player.seeking) return;
    if (player.currentTime > current.bufferPosition) {
      clearBuffer(current);
      if (!player.paused && !player.ended) watchBuffer(current);
    }
  }
  function onError() {
    if (!current?.ready || disposed) return;
    if (current.original && [3, 4].includes(player.error?.code)) return fallback(current);
    fail(current);
  }
  player.addEventListener("playing", onPlaying);
  player.addEventListener("error", onError);
  player.addEventListener("loadedmetadata", onMetadata);
  player.addEventListener("play", onPlay);
  player.addEventListener("pause", onPause);
  player.addEventListener("waiting", onWaiting);
  player.addEventListener("canplay", onCanPlay);
  player.addEventListener("ended", onPause);
  player.addEventListener("timeupdate", onTimeUpdate);

  return {
    select(path) {
      if (disposed) return;
      if (current?.path === path && !current.failed) {
        if (current.ready) play(current); // A second click must not reload/lose seek position.
        return;
      }
      const resume = current?.path === path && current.failed ? current.resume : 0;
      stop();
      current = { path, resume, abort: new AbortController(), deadline: Date.now() + 650000, attempt: 0 };
      message("rec.startingPlayback", true);
      void check(current, true);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      stop();
      player.removeEventListener("playing", onPlaying);
      player.removeEventListener("error", onError);
      player.removeEventListener("loadedmetadata", onMetadata);
      player.removeEventListener("play", onPlay);
      player.removeEventListener("pause", onPause);
      player.removeEventListener("waiting", onWaiting);
      player.removeEventListener("canplay", onCanPlay);
      player.removeEventListener("ended", onPause);
      player.removeEventListener("timeupdate", onTimeUpdate);
    },
  };
}
