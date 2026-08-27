"""Browser-friendly playback of recordings.

Segments are recorded **HEVC** (``-c:v copy`` — zero-CPU 24/7, the whole point), but browsers
can't decode HEVC in a ``<video>`` tag (black screen, and the failed video track takes the audio
down with it). So an HEVC segment is transcoded to **H.264 on demand** the first time it is
opened, and the result is cached, so later views are instant.

The first viewer starts one background preparation job and waits for a complete **faststart** MP4
(``moov`` at the front, real duration, seekable) before attaching it to the browser player. A
fragmented preview was previously streamed while encoding, but that exposed only a few seconds at
a time and made arbitrary seeking impossible -- the opposite of what a recordings reviewer needs.
Audio is ``-c:a copy`` (already AAC). ffprobe/ffmpeg come from the image. Segments already H.264
are served as-is.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from ..config import get_settings

# Codecs a browser plays natively in a <video> tag → serve the original, don't transcode.
_BROWSER_VIDEO = {"h264", "avc1", "vp8", "vp9", "av1"}
_JOBS_LOCK = threading.Lock()
_JOBS: dict[Path, _TranscodeJob] = {}
log = logging.getLogger(__name__)


def _cache_root() -> Path:
    root = Path(get_settings().db_path).parent / "playback_cache"   # data/ (persisted, gitignored)
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_path(segment: Path) -> Path:
    """Deterministic cache location for a segment's transcoded H.264 copy."""
    key = hashlib.sha1(str(segment.resolve()).encode()).hexdigest()[:20]
    return _cache_root() / f"{key}.mp4"


def _cache_size() -> int:
    """Total bytes of the cached transcodes (best-effort)."""
    total = 0
    for f in _cache_root().glob("*.mp4"):
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def _evict(keep: Path | None = None) -> None:
    """Enforce the cache size cap by deleting least-recently-used transcodes.

    LRU by mtime (refreshed on every cache hit, see :func:`transcoded_path`). ``keep`` is the
    file we're about to serve — never evicted, even if it alone exceeds the cap. Cap of 0 (or
    less) disables eviction. Best-effort: races/permission errors just skip a file. The cache
    holds only derived transcodes, always reproducible from the source segment on next view.
    """
    cap = get_settings().playback_cache_mb * 1024 * 1024
    if cap <= 0:
        return
    files = []
    for f in _cache_root().glob("*.mp4"):
        try:
            st = f.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, f))
    total = sum(size for _, size, _ in files)
    if total <= cap:
        return
    keep = keep.resolve() if keep else None
    for _, size, f in sorted(files):                 # oldest first
        if total <= cap:
            break
        if keep and f.resolve() == keep:
            continue
        try:
            f.unlink()
            total -= size
        except OSError:
            pass


def video_codec(segment: Path) -> str:
    """The segment's video codec (e.g. ``hevc`` / ``h264``); ``""`` if it can't be read."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "default=nk=1:nw=1", str(segment)],
            capture_output=True, text=True, timeout=10).stdout.strip()
        return out.splitlines()[0].lower() if out else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def needs_transcode(segment: Path) -> bool:
    """True if the segment's video codec isn't browser-playable (so we must transcode)."""
    codec = video_codec(segment)
    return bool(codec) and codec not in _BROWSER_VIDEO


def _ffmpeg_prefix() -> list[str]:
    """Keep review work below the always-on live/recording pipeline on POSIX hosts."""
    return ["nice", "-n", "10", "ffmpeg"] if shutil.which("nice") else ["ffmpeg"]


def _ffmpeg_cmd(src: Path, dst: Path) -> list[str]:
    return [*_ffmpeg_prefix(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-c:a", "copy",
            # faststart: moov (with the real duration) up front → the browser can seek immediately
            "-movflags", "+faststart", "-f", "mp4", str(dst)]


def cached_path(segment: Path) -> Path | None:
    """Return and touch an existing derived cache file, without starting any work."""
    cache = cache_path(segment)
    if not cache.is_file():
        return None
    try:
        os.utime(cache, None)                # mark recently used -> survives LRU eviction
    except OSError:
        pass
    return cache


def transcoded_path(segment: Path) -> Path | None:
    """A browser-playable H.264 copy of ``segment`` — transcoded + cached on first use.

    Returns the cache path (seekable H.264 MP4), or ``None`` when the segment is already
    browser-playable (caller should serve the original) or the transcode fails.
    """
    if not needs_transcode(segment):
        return None
    cache = cache_path(segment)
    hit = cached_path(segment)
    if hit is not None:
        return hit
    part = cache.with_name(f"{cache.stem}.{uuid.uuid4().hex}.part")
    try:
        proc = subprocess.run(_ffmpeg_cmd(segment, part),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        if proc.returncode == 0 and part.exists() and not cache.is_file():
            os.replace(part, cache)          # atomic promote to the shared cache
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        part.unlink(missing_ok=True)
    if cache.is_file():
        _evict(keep=cache)                   # keep the cache under its size cap
        return cache
    return None


class _TranscodeJob:
    """One shared seekable-cache preparation for every viewer requesting a segment.

    The work is independent of the HTTP request, so leaving the view does not waste the encode.
    Concurrent viewers poll the same job instead of starting duplicate FFmpeg processes.
    """

    def __init__(self, segment: Path) -> None:
        self.segment = segment.resolve()
        self.cache = cache_path(self.segment)
        nonce = uuid.uuid4().hex
        self.part = self.cache.with_name(f"{self.cache.stem}.{nonce}.part.mp4")
        self.done = threading.Event()
        self.failed = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            encoded = subprocess.run(
                _ffmpeg_cmd(self.segment, self.part),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=600,
            )
            if encoded.returncode != 0 or not self.part.is_file():
                self.failed = True
                log.warning("seekable playback preparation failed for %s", self.segment)
                return
            if not self.cache.is_file():
                os.replace(self.part, self.cache)
            _evict(keep=self.cache)
        except (OSError, subprocess.SubprocessError) as exc:
            self.failed = True
            log.warning("playback preparation failed for %s: %s", self.segment, exc)
        finally:
            self.part.unlink(missing_ok=True)
            self.done.set()
            with _JOBS_LOCK:
                if _JOBS.get(self.segment) is self:
                    _JOBS.pop(self.segment, None)


def prepare_transcode(segment: Path) -> bool:
    """Ensure a shared background job is preparing ``segment``; return whether it is running."""
    if cached_path(segment) is not None or not needs_transcode(segment):
        return False
    key = segment.resolve()
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job is None or job.done.is_set():
            job = _TranscodeJob(key)
            _JOBS[key] = job
            job.start()
    return True


def transcode_in_progress(segment: Path) -> bool:
    """Whether a seekable-cache preparation job currently owns this segment."""
    with _JOBS_LOCK:
        job = _JOBS.get(segment.resolve())
        return bool(job and not job.done.is_set())


class Warmer:
    """Opt-in background pre-transcode: keep the cache warm for the most recent HEVC segments.

    On-demand transcoding (``transcoded_path``) makes the *first* view of an HEVC clip wait for
    ffmpeg. When enabled (``playback_pretranscode``), this thread transcodes recent segments ahead
    of time so the Recordings reviewer gets instant playback. It is deliberately gentle: **one
    segment per tick**, newest-first (what a reviewer most likely opens), and it **stops before the
    cache cap** so it never fights LRU eviction (which would cause transcode↔evict churn). Disabled
    by default — it spends CPU continuously, unlike the zero-CPU (`-c:v copy`) recorder.
    """

    HEADROOM = 0.9   # only warm while the cache is below 90% of the cap (leave room for eviction)

    def __init__(self, *, enabled: bool | None = None, interval: float = 60.0,
                 window: int = 200) -> None:
        s = get_settings()
        self.enabled = s.playback_pretranscode if enabled is None else enabled
        self.interval = interval
        self.window = window                 # only consider the N newest segments (bounded work)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _next_segment(self) -> Path | None:
        """The newest recorded HEVC segment that isn't cached yet, or None."""
        from . import recorder  # local import: recorder doesn't import playback (no cycle)
        for it in recorder.query_segments(limit=self.window, offset=0)["items"]:
            seg = Path(it["path"])
            if seg.is_file() and not cache_path(seg).is_file() and needs_transcode(seg):
                return seg
        return None

    def warm_once(self) -> bool:
        """Transcode at most one pending segment. Returns True if it did work."""
        if not self.enabled:
            return False
        cap = get_settings().playback_cache_mb * 1024 * 1024
        if cap > 0 and _cache_size() >= cap * self.HEADROOM:
            return False                     # near the cap — stop, don't churn against eviction
        seg = self._next_segment()
        if seg is None:
            return False
        transcoded_path(seg)                 # transcodes + caches + evicts
        return True

    # --- background loop ----------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            return                           # disabled → don't spin a thread
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                did = self.warm_once()
            except Exception:                # never let a transcode error kill the loop
                did = False
            # If we warmed one, come back promptly for the next; otherwise idle a full interval.
            self._stop.wait(2.0 if did else self.interval)
