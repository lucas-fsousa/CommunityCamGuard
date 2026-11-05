"""One bounded output batch; defer only known-unattempted writes, never replay I/O."""

from collections import deque
from collections.abc import Callable

from .kcp_receive import ReceiveError


class PendingTcpOutput:
    def __init__(self) -> None:
        self._wires: deque[bytes] = deque()
        self.closed = False

    def offer(self, wires: tuple[bytes, ...]) -> None:
        if (self.closed or self._wires or not isinstance(wires, tuple) or len(wires) > 256
                or any(type(wire) is not bytes or not wire for wire in wires)
                or sum(map(len, wires)) > 32768):
            self.close()
            raise ReceiveError('invalid bounded TCP output batch')
        self._wires.extend(wires)

    def drain(self, send: Callable[[bytes], bool]) -> bool:
        if self.closed:
            raise ReceiveError('TCP output batch is closed')
        try:
            while self._wires:
                if not send(self._wires[0]):
                    return False  # callback certifies no write was attempted
                self._wires.popleft()
            return True
        except BaseException:
            self.close()  # an uncertain/partial send can never be replayed
            raise

    def close(self) -> None:
        self.closed = True
        self._wires.clear()
