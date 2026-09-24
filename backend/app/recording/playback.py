"""Browser-friendly playback of recordings.

Segments preserve the camera codec (``-c:v copy``, no continuous video encoding).
HEVC decoding support varies by browser/device. Capable clients can request original
delivery through the API; other clients use this **H.264 on-demand** compatibility
cache. Preparing a complete cache entry avoids repeated conversion on later views.

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
import time
import uuid
from pathlib import Path

from ..config import get_settings
from ..runtime_settings import playback_cache_limit_mb
from . import codec_cache
from .playback_budget import (
    ENCODE_TIMEOUT_SECONDS,
    MAX_JOBS,
    QUEUE_WAIT_SECONDS,
    PlaybackBusy,
    encoder_slot,
)

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
    cap = playback_cache_limit_mb() * 1024 * 1024
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
    """Reuse codec metadata only while file identity/size/timestamps are unchanged."""
    return codec_cache.lookup(segment, _probe_video_codec)


def _probe_video_codec(segment: Path) -> str:
    """The segment's video codec (e.g. ``hevc`` / ``h264``); ``""`` if it can't be read."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "default=nk=1:nw=1", str(segment)],
            capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ""
        out = result.stdout.strip()
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
    return [*_ffmpeg_prefix(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1", "-i", str(src),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-c:a", "copy",
            "-threads", "1",
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
    hit = cached_path(segment)
    if hit is not None:
        return hit
    try:
        job = _prepare_job(segment, background=True)
    except PlaybackBusy:
        return None
    if job is None:
        return cached_path(segment)
    job.done.wait(ENCODE_TIMEOUT_SECONDS + QUEUE_WAIT_SECONDS + 5)
    return cached_path(segment)


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
        started = time.monotonic()
        acquired = None
        reason = "none"
        try:
            with encoder_slot():
                acquired = time.monotonic()
                if self.cache.is_file():
                    reason = "cache_hit"
                    return
                encoded = subprocess.run(
                    _ffmpeg_cmd(self.segment, self.part),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=ENCODE_TIMEOUT_SECONDS,
                )
                if encoded.returncode != 0 or not self.part.is_file():
                    self.failed = True
                    reason = "encoder_failed" if encoded.returncode != 0 else "output_missing"
                    return
                if not self.cache.is_file():
                    os.replace(self.part, self.cache)
                _evict(keep=self.cache)
        except PlaybackBusy:
            self.failed = True
            reason = "queue_timeout"
        except subprocess.TimeoutExpired:
            self.failed = True
            reason = "encode_timeout"
        except (OSError, subprocess.SubprocessError):
            self.failed = True
            reason = "io_or_process_error"
        finally:
            try:
                self.part.unlink(missing_ok=True)
            except OSError:
                self.failed = True
                if reason in ("none", "cache_hit"):
                    reason = "cleanup_failed"
            finally:
                finished = time.monotonic()
                log.info("playback_prepare outcome=%s reason=%s queue_ms=%d encode_ms=%d",
                         "failed" if self.failed else "ready", reason,
                         int(((acquired if acquired is not None else finished) - started) * 1000),
                         int((finished - acquired) * 1000) if acquired is not None else 0)
                with _JOBS_LOCK:
                    if _JOBS.get(self.segment) is self:
                        _JOBS.pop(self.segment, None)
                    self.done.set()


def prepare_transcode(segment: Path) -> bool:
    """Ensure a shared background job is preparing ``segment``; return whether it is running."""
    return _prepare_job(segment) is not None


def _prepare_job(segment: Path, *, background: bool = False) -> _TranscodeJob | None:
    key = segment.resolve()
    with _JOBS_LOCK:
        existing = _JOBS.get(key)
        if existing is not None:
            return existing
        if len(_JOBS) >= MAX_JOBS or (background and _JOBS):
            raise PlaybackBusy("playback preparation is busy")
    if cached_path(segment) is not None or not needs_transcode(segment):
        return None
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job is None:
            if cached_path(segment) is not None:
                return None
            if len(_JOBS) >= MAX_JOBS or (background and _JOBS):
                raise PlaybackBusy("playback preparation is busy")
            job = _TranscodeJob(key)
            _JOBS[key] = job
            try:
                job.start()
            except RuntimeError:
                _JOBS.pop(key, None)
                raise PlaybackBusy("playback worker unavailable") from None
    return job


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
        with _JOBS_LOCK:
            if _JOBS:
                return False  # Do not scan/probe archives while a viewer's job owns the budget.
        cap = playback_cache_limit_mb() * 1024 * 1024
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
