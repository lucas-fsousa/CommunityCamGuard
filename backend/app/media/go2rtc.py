"""Manage the go2rtc media engine: build its config from the camera registry and run it.

go2rtc is the media hub (see docs/DECISIONS.md). It pulls each camera's RTSP stream —
absorbing the transport quirks of cheap cameras that trip up raw ffmpeg — and re-exposes
clean **WebRTC** (low latency, for the dashboard) and a clean local **RTSP restream** (for
the segment recorder). Streams are keyed by the registry's opaque public camera ID, so their
identity survives DHCP and native-identifier changes and carries no driver-specific assumption.
We regenerate the config and restart go2rtc whenever the registry changes.

The generated config is written as JSON, which is valid YAML — go2rtc parses it fine and we
avoid a YAML dependency plus any hand-escaping of RTSP URLs (which contain ``:@?&``).
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from ..camera_identity import valid_camera_id
from ..config import get_settings
from ..db import registry
from ..db.registry import Camera
from . import quality


def stream_id(camera_id: str) -> str:
    """Return the validated opaque ID used as the go2rtc base-stream name."""
    if not valid_camera_id(camera_id):
        raise ValueError("a valid opaque camera_id is required for media streams")
    return camera_id


def web_stream_id(camera_id: str) -> str:
    """Locally downscaled live variant, derived from :func:`hd_stream_id`.

    It deliberately does **not** open the camera's secondary RTSP feed. Every browser/consumer
    reads from the server-side hub, keeping the camera at one RTSP session regardless of how many
    clients are open.
    """
    return stream_id(camera_id) + "_web"


def sub_stream_id(camera_id: str) -> str:
    """The camera's secondary feed, pulled by go2rtc natively.

    It has to be its own stream rather than an ffmpeg input: these cameras **reject RTSP over
    interleaved TCP** ("Nonmatching transport in server reply"), which is what go2rtc's bundled
    ffmpeg asks for. go2rtc's own RTSP client negotiates a transport they accept, so ffmpeg
    reads the substream *from go2rtc* instead of from the camera.
    """
    return stream_id(camera_id) + "_sub"


def hd_stream_id(camera_id: str) -> str:
    """The full-resolution live variant: the **main** 1080p feed re-encoded to H.264.

    The counterpart to :func:`web_stream_id` — same treatment, bigger source, so ~30% of a CPU
    core per camera. It is preloaded once on the server and shared by all browser consumers, so a
    reload never starts another camera session or another HD transcode.
    """
    return stream_id(camera_id) + "_hd"


def _api_listen() -> str:
    """``host:port`` for go2rtc's API, taken from the configured ``go2rtc_api`` URL."""
    parts = urlsplit(get_settings().go2rtc_api)
    return f"{parts.hostname or '127.0.0.1'}:{parts.port or 3201}"


def restream_rtsp_url(camera_id: str) -> str:
    """Clean local RTSP restream the recorder can ffmpeg ``-c copy`` from."""
    s = get_settings()
    return f"rtsp://{s.go2rtc_host}:{s.go2rtc_rtsp_port}/{stream_id(camera_id)}"


def webrtc_page_url(camera_id: str) -> str:
    """go2rtc's built-in low-latency viewer page for this camera (for the dashboard)."""
    return f"{get_settings().go2rtc_api.rstrip('/')}/webrtc.html?src={stream_id(camera_id)}"


def build_config(cameras: list[Camera] | None = None) -> dict:
    """Build the go2rtc config dict from the registry (or a supplied camera list).

    All listeners bind the loopback host (``go2rtc_host``) on the 32xx range — nothing on
    0.0.0.0. Under WSL mirrored the Windows browser still reaches 127.0.0.1.
    """
    s = get_settings()
    if cameras is None:
        cameras = registry.list_cameras()
    streams: dict[str, str | list[str]] = {}
    preload: dict[str, str] = {}
    for cam in cameras:
        url = cam.rtsp_url
        if not url:
            continue
        sid = stream_id(cam.camera_id)
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
        # So: re-encode the main stream once on the server and share that H.264 producer. This is
        # intentionally not sourced from the camera substream: opening main + sub made each camera
        # serve two RTSP sessions before a single browser was counted. SD is downscaled locally.
        # Both audio codecs: AAC is what MP4/MSE can carry, Opus is what WebRTC needs. Encoding
        # both costs nothing at 16 kHz mono and lets the player negotiate **WebRTC** — lowest
        # latency, and universally supported now that the video is H.264, so the dashboard no
        # longer depends on the browser having an HEVC decoder.
        audio_part = "#audio=aac#audio=opus" if cam.capabilities.get("has_audio") else ""
        # Per-stream bitrate comes from the quality policy. Frame pacing and the effective GOP
        # are in the generated H.264 codec template below; go2rtc expands #raw *before* its
        # built-in template, whose later `-g 50` used to override our requested `-g 20`.
        # A proprietary talk session can leave this firmware's RTSP audio clock jumping
        # backwards. RTSP output interleaves tracks, so that bad audio timeline used to hold back
        # perfectly current video: the base/recorder stayed live while `_hd` fell almost a minute
        # behind. Normalize audio before both AAC and Opus encoders. The recorder already applies
        # the same repair independently; this keeps live view equally resilient.
        repair_audio_clock = bool(audio_part)
        sub_raw = quality.encode_raw_args(
            s.live_quality, s.live_fps, hd=False,
            repair_audio_clock=repair_audio_clock,
        )
        main_raw = quality.encode_raw_args(
            s.live_quality, s.live_fps, hd=True,
            repair_audio_clock=repair_audio_clock,
        )
        # The video codec directive, optionally hardware-accelerated (see live_hwaccel). Empty
        # hwaccel -> plain `#video=h264` (software), which is the default and current behaviour.
        vid = quality.video_h264_directive(s.live_hwaccel)
        hd_sid = hd_stream_id(cam.camera_id)
        # `#async` makes go2rtc prepend FFmpeg's wall-clock timestamp input options. These cameras
        # have no trustworthy PTS; one unit was empirically running its declared 10 fps timeline at
        # ~3x wall speed (13.1 Mbps and >100% CPU despite a 4.5 Mbps cap). Server wall time makes
        # frame pacing and VBV rate control mean what they say.
        streams[hd_sid] = f"ffmpeg:{sid}#async{vid}{audio_part}{main_raw}"

        # Hold one local H.264 producer open. New WebRTC consumers attach to an already-running
        # encoder with current codec parameters/keyframes instead of repeatedly constructing the
        # camera -> HEVC decoder -> H.264 encoder chain. `video&audio` is go2rtc's native preload
        # query; for silent cameras the absent audio match is optional.
        preload[hd_sid] = "video&audio" if audio_part else "video"

        # SD/Auto is a *local* derivative of the hot HD stream. This spends server CPU only when
        # requested, but never opens the camera's `/onvif2` feed and therefore never adds a second
        # RTSP session to constrained camera hardware.
        sd_vid = quality.video_h264_directive(s.live_hwaccel, codec="h264_sd")
        streams[web_stream_id(cam.camera_id)] = (
            f"ffmpeg:{hd_sid}{sd_vid}{audio_part}{sub_raw}")
    return {
        "api": {"listen": _api_listen()},
        # go2rtc's streams producer warnings include the complete source URL, including RTSP
        # userinfo. The config/API stay loopback-only, but Docker logs are routinely collected
        # for support and must never turn into a second plaintext credential store. Disable only
        # that module while retaining startup/API/WebRTC diagnostics from the rest of go2rtc.
        # The exec module stays at error: its official debug mode prints full FFmpeg arguments.
        "log": {"level": "info", "streams": "disabled", "exec": "error"},
        "rtsp": {"listen": f"{s.go2rtc_host}:{s.go2rtc_rtsp_port}"},
        "webrtc": {"listen": f"{s.go2rtc_host}:{s.go2rtc_webrtc_port}"},
        # Override the built-in software preset so frame pacing/GOP are the *last* video options.
        # Hardware presets remain go2rtc's own when LIVE_HWACCEL is explicitly selected.
        "ffmpeg": {
            # go2rtc's default RTSP input forces ``-fflags nobuffer -flags low_delay``. That is a
            # poor fit for these cameras' HEVC restream: a local FFmpeg consumer normally joins in
            # the middle of a long GOP and needs to retain packets until the next complete
            # VPS/SPS/PPS + IDR set. With the low-latency flags it repeatedly lost references
            # (PPS/POC/RPS decoder errors) and could remain alive/consume packets while producing
            # no H.264 pictures. Keep the wall-clock repair supplied by ``#async``, but use the
            # normal demuxer buffer. This is still a loopback RTSP hop; the few seconds of MSE
            # startup buffer dominate latency and reliability matters more than shaving packets.
            "rtsp": (
                "-timeout {timeout} -user_agent go2rtc/ffmpeg "
                "-rtsp_flags prefer_tcp -i {input}"
            ),
            "h264": quality.h264_encoder_template(s.live_fps),
            "h264_sd": quality.h264_encoder_template(s.live_fps, width=640),
            # ffmpeg sources are redirected to go2rtc's exec producer. Keep its startup window
            # above the measured first-IDR delay; the fragment is consumed by exec, not FFmpeg.
            "output": (
                "-user_agent ffmpeg/go2rtc -rtsp_transport tcp -f rtsp "
                "{output}#starttimeout=45"
            ),
        },
        "streams": streams,
        "preload": preload,
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
        self._activity_lock = threading.Lock()
        self._packet_progress: dict[str, tuple[int, float]] = {}

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

    def stream_activity(self) -> dict[str, dict]:
        """Per-stream liveness for the freeze watchdog: ``{stream_id: {video_packets, consumers}}``.

        go2rtc leaves a stalled WebRTC/MSE consumer attached to a **dead producer** — the player's
        timer keeps running while the picture is frozen (visible via the camera's burnt-in
        timestamp). ``video_packets`` is the producers' received **video** packet total; a stream
        whose video packets stop advancing *while it still has consumers* is frozen upstream, and
        the dashboard rebuilds that player. Best-effort: returns ``{}`` if go2rtc can't be read.
        """
        try:
            with urllib.request.urlopen(f"{self.api}/api/streams", timeout=2) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError):
            return {}
        out: dict[str, dict] = {}
        for sid, s in (data or {}).items():
            producers = s.get("producers") or []
            consumers = s.get("consumers") or []
            video_packets = sum(
                rc.get("packets", 0)
                for p in producers
                for rc in (p.get("receivers") or [])
                if (rc.get("codec") or {}).get("codec_type") == "video"
            )
            out[sid] = {"video_packets": video_packets, "consumers": len(consumers)}
        return out

    def stream_online(self, *, stale_after: float = 15.0) -> dict[str, bool]:
        """Return producer liveness based on video packets advancing in wall-clock time.

        Merely having a go2rtc producer object is insufficient: disconnected sources can leave a
        placeholder behind, and a historical non-zero packet counter says nothing about the
        camera's current state. The first non-zero sample is accepted immediately; subsequent
        samples must advance within ``stale_after`` seconds. Counter resets are treated as a new
        producer and therefore as progress.
        """

        activity = self.stream_activity()
        if not activity:
            return {}
        now = time.monotonic()
        max_age = max(3.0, float(stale_after))
        online: dict[str, bool] = {}
        with self._activity_lock:
            for sid, sample in activity.items():
                packets = max(0, int(sample.get("video_packets") or 0))
                previous = self._packet_progress.get(sid)
                if packets > 0 and (
                    previous is None or packets != previous[0]
                ):
                    last_progress = now
                else:
                    last_progress = previous[1] if previous is not None else 0.0
                self._packet_progress[sid] = (packets, last_progress)
                online[sid] = packets > 0 and now - last_progress <= max_age
            for sid in set(self._packet_progress) - set(activity):
                self._packet_progress.pop(sid, None)
        return online

    def restart_preload(self, sid: str, *, disconnect_grace: float = 2.0) -> bool:
        """Restart one preloaded *local* producer without touching the base camera/recording feed.

        The caller first disposes the browser player. A grace period lets the WebSocket relay detach
        its consumer; removing the preload then leaves the H.264 producer with no consumers, so
        go2rtc terminates that FFmpeg process. Re-adding it creates a clean decoder/encoder chain
        while the camera's shared base RTSP producer and recorder remain uninterrupted.

        Do not shorten this to the old 0.5 s: with the MSE media relay still unwinding, DELETE only
        removed the preload consumer and PUT immediately pinned the same wedged FFmpeg process
        again. Two seconds is bounded and comfortably covers the observed proxy close latency.

        go2rtc 1.9.x can return HTTP 500 after successfully adding a preload. Verify the resulting
        registry instead of treating that response alone as failure.
        """
        if disconnect_grace > 0:
            time.sleep(min(disconnect_grace, 2.0))
        query = urlencode({"src": sid})
        endpoint = f"{self.api}/api/preload?{query}"
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(endpoint, method="DELETE"), timeout=3):
                pass
        except (urllib.error.URLError, OSError):
            # Absence is fine: PUT below is also the repair for a preload that disappeared.
            pass
        time.sleep(0.25)
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(endpoint, data=b"", method="PUT"), timeout=5) as r:
                if r.status in (200, 201, 204):
                    return True
        except (urllib.error.URLError, OSError):
            pass
        try:
            with urllib.request.urlopen(f"{self.api}/api/preload", timeout=2) as r:
                current = json.loads(r.read())
            return sid in (current or {})
        except (urllib.error.URLError, OSError, ValueError):
            return False

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
    def restream_rtsp_url(self, camera_id: str) -> str:
        return restream_rtsp_url(camera_id)

    def webrtc_page_url(self, camera_id: str) -> str:
        return webrtc_page_url(camera_id)
