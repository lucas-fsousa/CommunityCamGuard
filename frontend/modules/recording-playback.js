// One archive selection owns its requests, timer and video. No live-camera state.
export function createRecordingPlayback(player, status, api, t) {
  let current = null;
  let disposed = false;
  const active = (item) => !disposed && current === item;
  const message = (key) => { status.textContent = key ? t(key) : ""; };
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
    }
    current = null;
    message("");
    player.pause();
    player.removeAttribute("src");
    player.load();
  }

  function watchNativeStart(item) {
    if (item.original && !item.started) {
      clearTimeout(item.nativeTimer);
      item.nativeTimer = setTimeout(() => fallback(item), 12000);
    }
  }

  function play(item) {
    if (!active(item) || !item.ready || item.playPending) return;
    item.playPending = true;
    const attempt = item.attempt;
    watchNativeStart(item);
    // Issue play immediately after attaching the source, not from loadedmetadata.
    // The promise waits for media readiness; native browser autoplay policy still applies.
    let promise;
    try { promise = player.play(); } catch (error) { promise = Promise.reject(error); }
    Promise.resolve(promise).catch((error) => {
      if (!active(item) || item.attempt !== attempt) return;
      if (error?.name === "NotSupportedError" && item.original) return fallback(item);
      clearTimeout(item.nativeTimer); // Autoplay denial is not a codec failure.
      message(error?.name === "NotAllowedError" ? "rec.readyPressPlay" : "rec.playbackFailed");
    }).finally(() => { if (item.attempt === attempt) item.playPending = false; });
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
    player.pause();
    player.removeAttribute("src");
    player.load();
    message("rec.preparingSeekable");
    void check(item, true);
  }

  function ready(item, cached, original = false) {
    if (!active(item)) return;
    item.ready = true;
    item.original = original;
    item.attempt++;
    message(cached ? "rec.seekableReady" : "rec.startingPlayback");
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
      message("rec.preparingSeekable");
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
    message("");
  }
  function onMetadata() {
    if (!current?.ready || !current.resume || disposed) return;
    player.currentTime = Math.min(current.resume, player.duration || current.resume);
    current.resume = 0;
  }
  function onPlay() {
    if (current?.ready && !disposed) watchNativeStart(current);
  }
  function onPause() {
    if (current) clearTimeout(current.nativeTimer);
  }
  function onError() {
    if (!current?.ready || disposed) return;
    if (current.original && [3, 4].includes(player.error?.code)) return fallback(current);
    clearTimeout(current.nativeTimer);
    message("rec.playbackFailed");
    current.failed = true;
  }
  player.addEventListener("playing", onPlaying);
  player.addEventListener("error", onError);
  player.addEventListener("loadedmetadata", onMetadata);
  player.addEventListener("play", onPlay);
  player.addEventListener("pause", onPause);

  return {
    select(path) {
      if (disposed) return;
      if (current?.path === path && !current.failed) {
        if (current.ready) play(current); // A second click must not reload/lose seek position.
        return;
      }
      stop();
      current = { path, abort: new AbortController(), deadline: Date.now() + 650000, attempt: 0 };
      message("rec.startingPlayback");
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
    },
  };
}
