"""Bounded passive encoded-audio sample from the SAME native media receiver."""

import hashlib
import json
import os
import threading
from dataclasses import asdict
from pathlib import Path

from ..drivers.yoosee.p2p.v1_receive import V1Record
from ..drivers.yoosee.p2p.video_delivery import VideoDelivery
from ..media.config_file import write_private_config

MAX_FRAMES = 128
MAX_BYTES = 256 * 1024


class NativeAudioSample(VideoDelivery):
    def __init__(self):
        super().__init__()
        self._audio_lock = threading.Lock()
        self._header = None
        self._audio: list[bytes] = []
        self._audio_bytes = 0
        self._timestamps: list[int] = []
        self._video_timestamps: list[int] = []
        self._saved = False

    def consume(self, record: V1Record) -> None:
        super().consume(record)
        with self._audio_lock:
            if self._saved:
                return
            if record.encoding is not None:
                self._header = record.encoding
            if record.video and len(self._video_timestamps) < MAX_FRAMES:
                self._video_timestamps.append(record.video_timestamp)
            for frame in record.audio:
                if len(self._audio) >= MAX_FRAMES or self._audio_bytes + len(frame) > MAX_BYTES:
                    break
                self._audio.append(frame)
                self._audio_bytes += len(frame)
                self._timestamps.append(record.audio_timestamp)

    def save(self, directory: Path) -> dict:
        with self._audio_lock:
            metadata: dict[str, object] = {"encoding": asdict(self._header) if self._header else None,
                        "frames": len(self._audio), "bytes": self._audio_bytes,
                        "frame_lengths": [len(item) for item in self._audio],
                        "timestamps_raw": list(self._timestamps),
                        "video_timestamps_raw": list(self._video_timestamps),
                        "adts_sync": bool(self._audio) and all(len(item) >= 7 and item[0] == 255
                            and item[1] & 0xF6 == 0xF0 for item in self._audio)}
            data = b"".join(self._audio)
            metadata["sha256"] = hashlib.sha256(data).hexdigest()
            self._audio.clear()
            self._timestamps.clear()
            self._video_timestamps.clear()
            self._audio_bytes = 0
            self._saved = True
        # Never hold the media-reader lock across filesystem I/O.
        fd = os.open(directory / "native-audio.raw", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        write_private_config(directory / "native-audio.json", json.dumps(metadata))
        return {key: metadata[key] for key in ("encoding", "frames", "bytes", "adts_sync")}
