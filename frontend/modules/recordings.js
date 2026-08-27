import { t } from "ccg/i18n";
import { api, el, state, svgIcon } from "ccg/core";

// --- recordings browser (view) -----------------------------------------------------
function field(label, input) {
  return el("label", { className: "field" }, el("span", { textContent: label }), input);
}

function cameraKey(mac) {
  return String(mac || "").replace(/[:-]/g, "").toLowerCase();
}

export function renderRecordings(stage) {
  stage.innerHTML = "";   // rebuilt each time the view is entered (no live camera frames here)
  const r = state.rec;
  const today = new Date().toISOString().slice(0, 10);
  if (!r.from) r.from = today;
  if (!r.to) r.to = today;
  const nameOf = Object.fromEntries(
    state.cameras.map((c) => [cameraKey(c.mac), c.name || c.mac]));

  const camSel = el("select");
  camSel.append(el("option", { value: "", textContent: t("rec.allCameras") }));
  state.cameras.forEach((c) => camSel.append(
    el("option", { value: c.mac, textContent: c.name || c.mac, selected: c.mac === r.mac })));
  const fromI = el("input", { type: "date", value: r.from });
  const toI = el("input", { type: "date", value: r.to });
  const search = el("button", { className: "btn-primary", innerHTML: svgIcon("i-scan") + `<span>${t("rec.search")}</span>` });
  const retention = el("span", { className: "muted retention", title: t("rec.retentionHint") });

  const player = el("video", { className: "rec-player", controls: true, preload: "auto" });
  const playbackState = el("small", { className: "muted rec-playback-state" });
  const info = el("span", { className: "muted" });
  const prev = el("button", { textContent: t("rec.prev") });
  const next = el("button", { textContent: t("rec.next") });
  const list = el("div", { className: "rec-list" });
  let playbackSelection = 0;

  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function waitForSeekable(path, fileUrl, selection) {
    let idleChecks = 0;
    while (selection === playbackSelection) {
      await wait(1000);
      if (selection !== playbackSelection) return;
      let status;
      try {
        status = await api("/recordings/playback-status?path=" + encodeURIComponent(path));
      } catch (_) {
        return;
      }
      if (selection !== playbackSelection) return;
      if (status.cached) {
        const resumeAt = Number.isFinite(player.currentTime) ? player.currentTime : 0;
        const shouldResume = !player.paused;
        playbackState.textContent = t("rec.seekableReady");
        player.addEventListener("loadedmetadata", () => {
          if (selection !== playbackSelection) return;
          if (resumeAt > 0 && Number.isFinite(player.duration)) {
            player.currentTime = Math.min(resumeAt, Math.max(0, player.duration - 0.1));
          }
          if (shouldResume) player.play().catch(() => {});
          playbackState.textContent = "";
        }, { once: true });
        // Same endpoint, cache-busted so the browser cannot reuse the earlier chunked response.
        player.src = fileUrl + "&ready=" + Date.now();
        player.load();
        return;
      }
      if (status.ready) {
        playbackState.textContent = "";       // source codec was browser-native
        return;
      }
      if (status.transcoding) {
        idleChecks = 0;
        playbackState.textContent = t("rec.preparingSeekable");
        continue;
      }
      // The video request and its FFmpeg job start independently from this status poll. Allow a
      // short race window, but don't poll forever after a failed encoder.
      idleChecks += 1;
      if (idleChecks >= 5) {
        playbackState.textContent = t("rec.playbackFailed");
        return;
      }
    }
  }

  async function load() {
    r.mac = camSel.value; r.from = fromI.value; r.to = toI.value;
    list.innerHTML = `<p class='muted'>${t("rec.loading")}</p>`;
    const qs = new URLSearchParams({
      mac: r.mac, day_from: r.from, day_to: r.to,
      limit: r.pageSize, offset: r.page * r.pageSize,
    });
    const res = await api("/recordings?" + qs.toString());
    retention.textContent = res.retention_days
      ? t("rec.retention", { days: res.retention_days })
      : t("rec.retentionOff");
    const first = res.total ? r.page * r.pageSize + 1 : 0;
    const last = Math.min((r.page + 1) * r.pageSize, res.total);
    info.textContent = t("rec.range", { first, last, total: res.total });
    prev.disabled = r.page === 0;
    next.disabled = (r.page + 1) * r.pageSize >= res.total;
    list.innerHTML = "";
    if (!res.items.length) {
      list.append(el("p", { className: "muted", textContent: t("rec.none") }));
      return;
    }
    res.items.forEach((s) => {
      const cameraName = s.camera_name || nameOf[cameraKey(s.mac)] || s.mac;
      const download = el("a", {
        className: "rec-download",
        href: "/api/recordings/download?path=" + encodeURIComponent(s.path),
        title: t("rec.download"),
        ariaLabel: t("rec.downloadRecording", { camera: cameraName }),
        innerHTML: svgIcon("i-download"),
      });
      download.addEventListener("click", (event) => event.stopPropagation());
      const row = el("div", { className: "rec-row" },
        el("div", { className: "rec-row-copy" },
          el("span", { textContent: `${s.day} ${s.started_at.slice(11, 19)}` }),
          el("small", { textContent: `${cameraName} · ${(s.size_bytes / 1e6).toFixed(1)} MB` })),
        download,
      );
      row.addEventListener("click", () => {
        list.querySelectorAll(".rec-row.active").forEach((n) => n.classList.remove("active"));
        row.classList.add("active");
        const selection = ++playbackSelection;
        const fileUrl = "/api/recordings/file?path=" + encodeURIComponent(s.path);
        playbackState.textContent = t("rec.startingPlayback");
        player.src = fileUrl;
        player.play().catch(() => {});   // first HEVC view starts progressively while cache warms
        waitForSeekable(s.path, fileUrl, selection);
      });
      list.append(row);
    });
  }

  search.addEventListener("click", () => { r.page = 0; load(); });
  prev.addEventListener("click", () => { if (r.page > 0) { r.page--; load(); } });
  next.addEventListener("click", () => { r.page++; load(); });

  stage.append(el("div", { className: "rec-screen" },
    el("div", { className: "rec-filter" }, field(t("rec.camera"), camSel), field(t("rec.from"), fromI), field(t("rec.to"), toI), search, retention),
    el("div", { className: "rec-body" },
      el("div", { className: "rec-side" }, list, el("div", { className: "rec-pager" }, prev, info, next)),
      el("div", { className: "rec-main" }, player, playbackState),
    ),
  ));
  load();
}
