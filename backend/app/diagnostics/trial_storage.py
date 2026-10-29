"""Target-only recording-policy exercise; never pause the other camera recorders."""

from ..recording.recorder import Recorder


class TrialStorage:
    def __init__(self, production: Recorder):
        # No start(), thread or legacy capture. The normal archive plan/pause API
        # is real, but its pause flag is isolated from the two other recorders.
        self.recorder = Recorder(segment_seconds=production.segment_seconds)
        self.recorder.root = production.root
        self._paused = False
        self._resumed = False

    def advance(self, elapsed: float) -> str | None:
        if elapsed >= 20 and not self._paused:
            self.recorder.pause()
            self._paused = True
            return "paused"
        if elapsed >= 50 and not self._resumed:
            self.recorder.resume()
            self._resumed = True
            return "resumed"
        return None

    @property
    def paused(self) -> bool:
        return self.recorder.paused
