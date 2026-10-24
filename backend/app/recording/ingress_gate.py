"""Per-camera admission gate for the recorder's existing RTSP consumer.

The source coordinator owns these holds. A successful suspend certifies only the
recording child retired, never the upstream go2rtc/camera connection. No API,
source login, fallback or persistent source selection is provided here.
"""

from _thread import LockType
from collections.abc import Callable

from ..camera_identity import valid_camera_id
from .process_lifecycle import RecorderCleanupUnconfirmed

MAX_HOLDS = 32


class RecordingIngressGate:
    def __init__(self, *, lock: LockType, retire: Callable[[str], None],
                 has_process: Callable[[str], bool]):
        self._lock = lock
        self._retire = retire
        self._has_process = has_process
        self._held: dict[str, bool] = {}

    @staticmethod
    def _validate(camera_id: str) -> None:
        if type(camera_id) is not str or not valid_camera_id(camera_id):
            raise ValueError("public camera ID required")

    def held_locked(self, camera_id: str) -> bool:
        """Recorder admission only: caller must already hold the shared lock."""
        return camera_id in self._held

    def is_suspended(self, camera_id: str) -> bool:
        """Admission is held; this alone does not certify successful retirement."""
        self._validate(camera_id)
        with self._lock:
            return camera_id in self._held

    def is_retired(self, camera_id: str) -> bool:
        self._validate(camera_id)
        with self._lock:
            return self._held.get(camera_id) is True and not self._has_process(camera_id)

    def suspend(self, camera_id: str) -> None:
        self._validate(camera_id)
        with self._lock:
            if camera_id not in self._held and len(self._held) >= MAX_HOLDS:
                raise RuntimeError("recording suspension capacity reached")
            self._held[camera_id] = False  # gate before I/O; failures retain it
            self._retire(camera_id)
            self._held[camera_id] = True

    def resume(self, camera_id: str) -> None:
        """Release a confirmed hold; later maintenance may start a new consumer.

        Caller must separately confirm the replacement source has stopped before
        releasing this hold. This object cannot certify a native source's cleanup.
        """
        self._validate(camera_id)
        with self._lock:
            if camera_id not in self._held:
                return
            if not self._held[camera_id] or self._has_process(camera_id):
                raise RecorderCleanupUnconfirmed("recording suspension cleanup unconfirmed")
            del self._held[camera_id]
