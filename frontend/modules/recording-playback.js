// One archive selection owns its requests, timer and video. No live-camera state.
export function createRecordingPlayback(player, status, retry, api, t) {
  let current = null;
  let disposed = false;
  const active = (item) => !disposed && current === item;
  const message = (key) => { status.textContent = key ? t(key) : ""; };

  function stop() {
    if (current) {
      current.abort.abort();
      clearTimeout(current.timer);
      clearTimeout(current.requestTimer);
    }
    current = null;
    retry.hidden = true;
    player.pause();
    player.removeAttribute("src");
    player.load();
  }

  function play(item) {
    if (!active(item) || !item.ready || item.playPending) return;
    item.playPending = true;
    // Issue play immediately after attaching the source, not from loadedmetadata.
    // The promise waits for media readiness; native browser autoplay policy still applies.
    let promise;
    try { promise = player.play(); } catch (error) { promise = Promise.reject(error); }
    Promise.resolve(promise).catch((error) => {
      if (!active(item)) return;
      retry.hidden = false;
      message(error?.name === "NotAllowedError" ? "rec.readyPressPlay" : "rec.playbackFailed");
    }).finally(() => { item.playPending = false; });
  }

  function ready(item, cached) {
    if (!active(item)) return;
    item.ready = true;
    message(cached ? "rec.seekableReady" : "rec.startingPlayback");
    player.src = "/api/recordings/file?path=" + encodeURIComponent(item.path);
    player.load();
    play(item);
  }

  async function check(item, prepare = false) {
    const requestTimer = item.requestTimer = setTimeout(() => item.abort.abort(), 15000);
    try {
      const result = await api(
        (prepare ? "/recordings/prepare?path=" : "/recordings/playback-status?path=")
          + encodeURIComponent(item.path),
        { ...(prepare ? { method: "POST" } : {}), signal: item.abort.signal },
      );
      if (!active(item)) return;
      if (result.ready) return ready(item, result.cached);
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
      message("rec.playbackFailed");
    } finally {
      clearTimeout(requestTimer);
    }
  }

  function onPlaying() {
    if (!current?.ready || disposed) return;
    retry.hidden = true;
    message("");
  }
  function onError() {
    if (!current?.ready || disposed) return;
    message("rec.playbackFailed");
    current.failed = true;
  }
  const retryPlay = () => { if (current) play(current); };
  player.addEventListener("playing", onPlaying);
  player.addEventListener("error", onError);
  retry.addEventListener("click", retryPlay);

  return {
    select(path) {
      if (disposed) return;
      if (current?.path === path && !current.failed) {
        if (current.ready) play(current); // A second click must not reload/lose seek position.
        return;
      }
      stop();
      current = { path, abort: new AbortController(), deadline: Date.now() + 610000 };
      message("rec.startingPlayback");
      void check(current, true);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      stop();
      player.removeEventListener("playing", onPlaying);
      player.removeEventListener("error", onError);
      retry.removeEventListener("click", retryPlay);
    },
  };
}
