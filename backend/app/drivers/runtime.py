"""Optional driver resources owned by the server, independent of camera brands."""

from typing import Protocol


class DriverRuntimeService(Protocol):
    def stop(self) -> None: ...
