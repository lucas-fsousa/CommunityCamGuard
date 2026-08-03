"""Manage the go2rtc media engine: build its config from the camera registry and run it.

go2rtc is the media hub (see docs/DECISIONS.md). It pulls each camera's RTSP stream —
absorbing the transport quirks of cheap cameras that trip up raw ffmpeg — and re-exposes
clean **WebRTC** (low latency, for the dashboard) and a clean local **RTSP restream** (for
the segment recorder). Cameras are keyed by MAC, so a stream keeps its identity across
DHCP changes; we regenerate the config and restart go2rtc whenever the registry changes.

The generated config is written as JSON, which is valid YAML — go2rtc parses it fine and we
avoid a YAML dependency plus any hand-escaping of RTSP URLs (which contain ``:@?&``).
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from ..config import get_settings
from ..db import registry
from ..db.registry import Camera
from . import quality


def stream_id(mac: str) -> str:
    """A URL/path-safe go2rtc stream name derived from the stable MAC identity."""
    return "cam_" + mac.replace(":", "").lower()


def web_stream_id(mac: str) -> str:
    """The cheap live variant: the camera's **substream**, re-encoded to H.264 + AAC/Opus.

    The re-encode is unavoidable (the browser cannot take these cameras' HEVC — see
    ``build_config``), so it is done on the small feed: ~8% of a CPU core per viewer instead of
    ~30% at full resolution. This is what lets a wall of cameras share one host.

    The catch is the source: on these units the substream is **640x360 at ~37 kbps** — measured,
    and genuinely poor. No encoder setting recovers detail the camera never sent, so
    ``grid_hd_max_cameras`` exists to prefer :func:`hd_stream_id` on a small wall instead.
    """
    return stream_id(mac) + "_web"


def sub_stream_id(mac: str) -> str:
    """The camera's secondary feed, pulled by go2rtc natively.

    It has to be its own stream rather than an ffmpeg input: these cameras **reject RTSP over
    interleaved TCP** ("Nonmatching transport in server reply"), which is what go2rtc's bundled
    ffmpeg asks for. go2rtc's own RTSP client negotiates a transport they accept, so ffmpeg
    reads the substream *from go2rtc* instead of from the camera.
    """
    return stream_id(mac) + "_sub"


def hd_stream_id(mac: str) -> str:
    """The full-resolution live variant: the **main** 1080p feed re-encoded to H.264.

    The counterpart to :func:`web_stream_id` — same treatment, bigger source, so ~30% of a CPU
    core per viewer against ~8%. Worth it where the picture matters: single-camera view always,
    and the grid too while it holds at most ``grid_hd_max_cameras`` tiles (the substream is a
    37 kbps feed, so on a small wall the CPU buys a real improvement).

    go2rtc keeps an ``ffmpeg:`` source idle until something consumes it, so the variants that
    are not on screen cost nothing. See ``build_config`` and docs/DECISIONS.md §34.
    """
    return stream_id(mac) + "_hd"


def _api_listen() -> str:
    """``host:port`` for go2rtc's API, taken from the configured ``go2rtc_api`` URL."""
    parts = urlsplit(get_settings().go2rtc_api)
    return f"{parts.hostname or '127.0.0.1'}:{parts.port or 3201}"


def restream_rtsp_url(mac: str) -> str:
    """Clean local RTSP restream the recorder can ffmpeg ``-c copy`` from."""
    s = get_settings()
    return f"rtsp://{s.go2rtc_host}:{s.go2rtc_rtsp_port}/{stream_id(mac)}"


def webrtc_page_url(mac: str) -> str:
    """go2rtc's built-in low-latency viewer page for this camera (for the dashboard)."""
    return f"{get_settings().go2rtc_api.rstrip('/')}/webrtc.html?src={stream_id(mac)}"


def build_config(cameras: list[Camera] | None = None) -> dict:
    """Build the go2rtc config dict from the registry (or a supplied camera list).

    All listeners bind the loopback host (``go2rtc_host``) on the 32xx range — nothing on
    0.0.0.0. Under WSL mirrored the Windows browser still reaches 127.0.0.1.
    """
    s = get_settings()
    if cameras is None:
        cameras = registry.list_cameras()
    streams: dict[str, str | list[str]] = {}
    for cam in cameras:
        url = cam.rtsp_url
        if not url:
            continue
        sid = stream_id(cam.mac)
        streams[sid] = url

        # --- browser-facing variants ----------------------------------------------------
        # **Video has to become H.264 for the browser.** This is the same conclusion every
        # working NVR reaches (Frigate's camera-setup docs open by telling you to configure the
        # camera for "H.264 video and AAC audio"), except those setups get H.264 straight from
        # the camera. Ours cannot: it emits HEVC and its ONVIF
        # `SetVideoEncoderConfiguration` is a decoy that answers 200 and changes nothing (§34).
        #
        # Handing the browser HEVC instead was tried and does not work here. go2rtc's fMP4 for
        # these HEVC feeds carries a **wrong track header** (declares 2560x1440 for 640x360
        # frames — its SPS parse), and because the cameras send no PTS the sample durations
        # jitter 90-120 ms. A decoder configured for the wrong size and fed irregular timing
        # stalls: that is the freezing. Re-encoding fixes both at once — correct header, and a
        # flat 0.0667 s per sample (verified).
        #
        # So: re-encode, but from the **substream**, which is what makes it affordable —
        # ~5% of a core per viewer at 640x360 vs ~26% at 1080p. Frigate's guidance is the same
        # ("use the substream for live viewing, keep the main stream for recording").
        # Both audio codecs: AAC is what MP4/MSE can carry, Opus is what WebRTC needs. Encoding
        # both costs nothing at 16 kHz mono and lets the player negotiate **WebRTC** — lowest
        # latency, and universally supported now that the video is H.264, so the dashboard no
        # longer depends on the browser having an HEVC decoder.
        audio_part = "#audio=aac#audio=opus" if cam.capabilities.get("has_audio") else ""
        # Encoder params come from the quality policy (media/quality.py): a fixed frame rate
        # (the cameras send no PTS, so preserving their timing hands the browser jittering
        # sample durations and it stalls — see `live_fps`) plus a level-driven target bitrate,
        # the lever that actually buys picture quality. `hd=` tracks the *source* resolution:
        # the substream needs few bits, the 1080p main feed benefits from more.
        sub_raw = quality.encode_raw_args(s.live_quality, s.live_fps, hd=False)
        main_raw = quality.encode_raw_args(s.live_quality, s.live_fps, hd=True)
        # The video codec directive, optionally hardware-accelerated (see live_hwaccel). Empty
        # hwaccel -> plain `#video=h264` (software), which is the default and current behaviour.
        vid = quality.video_h264_directive(s.live_hwaccel)
        sub = cam.substream_url
        if sub:
            sub_id = sub_stream_id(cam.mac)
            streams[sub_id] = sub
            # Audio lives only on the main feed, so it is merged in as a second source.
            video = f"ffmpeg:{sub_id}{vid}{sub_raw}"
            streams[web_stream_id(cam.mac)] = (
                [video, f"ffmpeg:{sid}{audio_part}"] if audio_part else video)
        else:
            # No substream advertised: the web variant re-encodes the main feed, so it earns the
            # main-feed bitrate (it is this camera's only live source).
            streams[web_stream_id(cam.mac)] = f"ffmpeg:{sid}{vid}{audio_part}{main_raw}"

        # Full-resolution variant for single-camera view: the main feed re-encoded. Only ever
        # consumed by one tile at a time, and go2rtc keeps an `ffmpeg:` source idle until
        # something consumes it, so grid view pays nothing for it.
        if sub:
            streams[hd_stream_id(cam.mac)] = f"ffmpeg:{sid}{vid}{audio_part}{main_raw}"
    return {
        "api": {"listen": _api_listen()},
        "rtsp": {"listen": f"{s.go2rtc_host}:{s.go2rtc_rtsp_port}"},
        "webrtc": {"listen": f"{s.go2rtc_host}:{s.go2rtc_webrtc_port}"},
        "streams": streams,
    }


class Go2rtc:
    """Owns the go2rtc process lifecycle and config file."""

    def __init__(self, bin_path: Path | None = None, config_path: Path | None = None,
                 manage: bool | None = None) -> None:
        settings = get_settings()
        self.bin = Path(bin_path or settings.go2rtc_bin)
        self.config_path = Path(config_path or settings.go2rtc_config)
        self.api = settings.go2rtc_api.rstrip("/")
        # Whether we own the go2rtc process. False when go2rtc runs as its own container
        # (compose): we only regenerate its config and ask it to reload — never spawn a binary.
        self.manage = settings.manage_go2rtc if manage is None else manage
        self._proc: subprocess.Popen | None = None

    # --- config -------------------------------------------------------------------
    def write_config(self, cameras: list[Camera] | None = None) -> Path:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(build_config(cameras), indent=2))
        return self.config_path

    # --- process ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, cameras: list[Camera] | None = None) -> None:
        if self.is_running():
            return
        if not self.bin.exists():
            raise FileNotFoundError(f"go2rtc binary not found at {self.bin}")
        self.write_config(cameras)
        self._proc = subprocess.Popen(
            [str(self.bin), "-config", str(self.config_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    def reload_external(self) -> bool:
        """Ask an externally-managed go2rtc to restart so it re-reads the fresh config file.

        go2rtc has no file-watch hot-reload, but its API restarts the process in place
        (re-reading the mounted config). Best-effort: returns False on any transport error so a
        resync never crashes the caller (the registry change has already been persisted).
        """
        req = urllib.request.Request(f"{self.api}/api/restart", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status in (200, 204)
        except (urllib.error.URLError, OSError):
            return False

    def restart(self, cameras: list[Camera] | None = None) -> None:
        """Apply registry changes. go2rtc has no clean hot-reload of a changed config file.

        Managed mode (we own the binary): re-exec it. External mode (go2rtc is its own
        container): rewrite the config and ask the running go2rtc to reload it via its API —
        **never** spawn a binary that isn't there (that used to raise FileNotFoundError and 500
        every add/delete under compose).
        """
        if self.manage:
            self.stop()
            self.start(cameras)
        else:
            self.write_config(cameras)
            self.reload_external()

    def wait_healthy(self, timeout: float = 10.0) -> bool:
        """Poll the API until it answers, so callers know go2rtc is ready."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.api}/api/streams", timeout=1) as r:
                    if r.status == 200:
                        return True
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        return False

    # --- stream surfaces for consumers -------------------------------------------
    def restream_rtsp_url(self, mac: str) -> str:
        return restream_rtsp_url(mac)

    def webrtc_page_url(self, mac: str) -> str:
        return webrtc_page_url(mac)
