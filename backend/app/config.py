"""Application configuration loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Never cut recording chunks shorter than this (tiny chunks are wasteful and fragile).
MIN_SEGMENT_SECONDS = 60


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Dashboard access
    dashboard_secret_key: str = "change-me"
    session_signing_key: str = ""

    # Dashboard/API — exposed to the LAN and always protected by DASHBOARD_SECRET_KEY. Internal
    # go2rtc API/RTSP ports remain loopback-only behind the authenticated app proxy.
    host: str = "0.0.0.0"
    port: int = 3200
    # Auto-start go2rtc + recorder + storage monitor with the API (disable for API-only /
    # hardware-less runs and tests).
    autostart_services: bool = True
    # Whether this process spawns/owns the go2rtc binary. True on host; set False when
    # go2rtc runs as its own container — then we only generate its config and connect.
    manage_go2rtc: bool = True

    # Discovery
    discovery_timeout: float = 4.0
    discovery_scan_subnets: str = ""

    # Persistence (camera registry + recording index)
    db_path: Path = Path("./data/ccg.db")

    # Optional, short-lived reverse-engineering bridge for the recovered BLE handshake. The file
    # lives in git-ignored temp storage, must be owner-only and is never returned to the browser.
    provisioning_ble_material_file: Path | None = None
    # TanKey/randNumber are a single short-lived handshake pair. The cloud issues a different
    # pair on every request and the camera silently falls back to an unusable session when an old
    # pair is presented, so reject material before that can look like a successful provisioning.
    provisioning_ble_material_max_age_seconds: int = 300
    # Explicit, temporary escape hatch for Web Bluetooth on a phone reached through an HTTPS
    # reverse tunnel. Authentication remains mandatory and only the BLE subset is opened.
    provisioning_remote_ble_enabled: bool = False

    # Recording
    recordings_dir: Path = Path("./recordings")
    # Length of each recording chunk, in seconds (user-configurable via .env). Clamped to a
    # minimum of MIN_SEGMENT_SECONDS so chunks never get too small.
    segment_seconds: int = 300

    @field_validator("segment_seconds")
    @classmethod
    def _min_segment(cls, v: int) -> int:
        return max(MIN_SEGMENT_SECONDS, int(v))

    # Recording retention: a sporadic cleanup job deletes segments older than this many days.
    # Floor is 0 = keep forever (job disabled); there is no upper bound — footage grows until
    # the storage monitor pauses recording at the disk-full mark.
    recording_retention_days: int = 7

    @field_validator("recording_retention_days")
    @classmethod
    def _min_retention(cls, v: int) -> int:
        return max(0, int(v))

    # Live view: frame rate the browser-facing transcodes are pinned to.
    #
    # It must be a *fixed* rate, not the source's own timing: these cameras send no PTS, so
    # letting ffmpeg pass their timing through (`-fps_mode passthrough`) reproduces the 67/133 ms
    # jitter that makes the player stall — measured, and it is the freezing from docs/DECISIONS
    # §34. A fixed `-r` gives the flat sample durations the browser needs.
    #
    # Set it to what the camera *actually delivers* (measure; the advertised rate lies — ours
    # claim 15 fps and deliver ~10). Going higher just re-encodes duplicated frames: dropping
    # 20 -> 10 fps halved CPU for two 1080p streams (121% -> 60%) at an identical bitrate.
    live_fps: int = 10

    @field_validator("live_fps")
    @classmethod
    def _sane_fps(cls, v: int) -> int:
        return max(1, min(int(v), 60))

    # Live view: quality level for the browser-facing H.264 transcodes — "low" | "medium" |
    # "high" | "max". Controls the target bitrate of the re-encode (see media/quality.py).
    # Defaults to "max": the sharpest the stream/camera can give. This is independent of the
    # *host* budget, which is governed by `grid_hd_max_cameras` (whether the grid uses the main
    # feed at all). Invalid values fall back to "max".
    live_quality: str = "max"

    @field_validator("live_quality")
    @classmethod
    def _known_quality(cls, v: str) -> str:
        from .media.quality import normalize_level
        return normalize_level(v)

    # Live view: hardware transcoder for the H.264 re-encodes — "" (software libx264, default) or
    # a go2rtc hardware value ("vaapi", "cuda", "v4l2m2m", ...). Hardware encoding moves the cost
    # off the CPU, which is what makes a *sharp* (main-feed) grid affordable on a weak host — but
    # it is device-specific and must be validated on the box. Unknown values fall back to "".
    live_hwaccel: str = ""

    @field_validator("live_hwaccel")
    @classmethod
    def _known_hwaccel(cls, v: str) -> str:
        from .media.quality import normalize_hwaccel
        return normalize_hwaccel(v)

    # In the UI's opt-in Auto quality mode, how many cameras may share the grid before it drops
    # from the full-resolution stream to the cheap substream. Raising it buys picture quality with
    # CPU — the substream on these cameras is a 640x360 / 37 kbps feed, which is genuinely poor.
    #
    # **Auto defaults to 0 (always substream) because on a 2-vCPU host HD is not safe.** The normal
    # per-camera default remains explicit HD; selecting Auto/SD is the user's performance choice.
    # Measured with
    # the recorder and both audio tracks running, two cameras on the full-resolution feed put the
    # go2rtc cgroup at `cpu.pressure full avg10 = 29.7` — the same starvation that made players
    # freeze (docs/DECISIONS.md §34); on the substream the same load sits at ~2. The knob is here
    # because the limit is the *host*, not the design: give the box more cores and raising this
    # is the single change that improves the grid.
    grid_hd_max_cameras: int = 0

    # Playback transcode cache (HEVC -> H.264 for the browser). Size cap in MB for the LRU
    # cache under data/playback_cache/; 0 disables eviction (unbounded). Never touches the
    # source recordings — only the derived, always-reproducible transcodes.
    playback_cache_mb: int = 2048
    # Opt-in background pre-transcode: warm the cache for recent HEVC segments so the first
    # Recordings view is instant instead of waiting for an on-demand transcode. OFF by default —
    # it spends CPU continuously, which contradicts the zero-CPU (-c:v copy) recorder ethos, so
    # only enable it on a box with spare CPU. Bounded by playback_cache_mb (leaves headroom, so
    # it never fights eviction).
    playback_pretranscode: bool = False

    # Storage policy (never auto-deletes). Alert at N%, stop saving (keep streaming) when
    # nearly full, resume automatically once usage drops back below the resume mark.
    storage_alert_percent: int = 80
    storage_full_percent: int = 98
    storage_resume_percent: int = 95
    storage_check_seconds: int = 30

    # Storage backend
    storage_backend: str = "local"  # "local" | "s3"

    # S3
    s3_bucket: str = ""
    s3_prefix: str = "cameras/"
    aws_region: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    # Frontend (static, no build step)
    frontend_dir: Path = Path("./frontend")

    # Media engine (go2rtc). Loopback + 32xx range. ``go2rtc_host`` is where the app/recorder
    # reach go2rtc (127.0.0.1 on host; the service name when containerised).
    go2rtc_api: str = "http://127.0.0.1:3201"
    go2rtc_host: str = "127.0.0.1"
    go2rtc_rtsp_port: int = 3203
    go2rtc_webrtc_port: int = 3202
    go2rtc_bin: Path = Path("./bin/go2rtc")
    go2rtc_config: Path = Path("./data/go2rtc.yaml")

    @property
    def effective_signing_key(self) -> str:
        return self.session_signing_key or self.dashboard_secret_key

    @property
    def scan_subnets(self) -> list[str]:
        return [s.strip() for s in self.discovery_scan_subnets.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
