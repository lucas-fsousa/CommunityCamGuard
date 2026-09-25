// UI invalidation aid, never an authorization substitute. Server owns validity.
export function createSessionWatch(check, invalid, events = window) {
  let generation = 0, timer = null, request = null, running = false;
  const stop = () => {
    running = false; generation++;
    clearTimeout(timer); request?.abort(); request = null;
    events.removeEventListener("focus", poll);
    events.removeEventListener("pageshow", poll);
  };
  async function poll() {
    if (!running || request) return;
    clearTimeout(timer);
    const epoch = generation;
    const controller = request = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const result = await check(controller.signal);
      if (running && epoch === generation && result.authenticated === false) {
        stop(); invalid();
      }
    } catch { /* Transient outage is not proof of invalidity. Server still denies access. */ }
    finally {
      clearTimeout(timeout);
      if (epoch === generation) {
        request = null;
        if (running) timer = setTimeout(poll, 1000);
      }
    }
  }
  return {
    start() {
      stop(); running = true;
      events.addEventListener("focus", poll);
      events.addEventListener("pageshow", poll);
      void poll();
    },
    stop,
  };
}
