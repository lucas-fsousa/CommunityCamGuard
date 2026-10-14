"""Single-owner sequence namespace for one authenticated account generation."""


class BrokerSequences:
    """Constant-memory allocation; no wrap, recycling, sockets or synchronization.

    The account owner serializes all callers and retires this namespace before
    replacing credentials. A consumer closing its own waiters must not retire it.
    Generation is a local boundary, not protection against wire-session reuse.
    """

    def __init__(self, session_id: int, generation: int, next_sequence: int):
        if (type(session_id) is not int or not 0 < session_id < 1 << 64
                or type(generation) is not int or generation <= 0
                or type(next_sequence) is not int or not 0 <= next_sequence < 1 << 32):
            raise ValueError("invalid broker sequence identity")
        self._session, self._generation, self._next = session_id, generation, next_sequence
        self.closed = False

    def matches(self, session_id: int, generation: int) -> bool:
        return (not self.closed and type(session_id) is int and type(generation) is int
                and (session_id, generation) == (self._session, self._generation))

    def allocate(self) -> int:
        return self.reserve(1)[0]

    def reserve(self, count: int) -> tuple[int, ...]:
        """Atomically reserve a small command group before any member is sent.

        Single account-owner caller, as for allocate(); not a cross-thread lock.
        Exhaustion consumes nothing, and reserved IDs are never returned/recycled.
        """
        if type(count) is not int or not 1 <= count <= 64:
            raise ValueError("sequence reservation must contain 1..64 IDs")
        if self.closed:
            raise ValueError("broker sequence namespace closed")
        if self._next + count > 1 << 32:
            raise ValueError("broker sequence exhausted; new generation required")
        first = self._next
        self._next += count
        return tuple(range(first, first + count))

    def close(self) -> None:
        self.closed = True
        self._session = 0
