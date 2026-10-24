"""ASGI adapter for the server-owned browser admission/drain gate."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

from starlette.requests import HTTPConnection
from starlette.websockets import WebSocket

from ..media.source_registry import SourceUnavailable
from ..media.viewer_gate import ViewerGate
from ..session_channels import close_channel, run_guarded


def viewer_gate(connection: HTTPConnection) -> ViewerGate | None:
    state = getattr(getattr(connection, "app", None), "state", None)
    gate = getattr(state, "viewer_sessions", None)
    return gate if isinstance(gate, ViewerGate) else None


@contextmanager
def media_operation(connection: HTTPConnection, camera_id: str) -> Iterator[None]:
    """Track a synchronous recovery until its actual hub request has returned."""
    gate = viewer_gate(connection)
    lease = gate.admit(camera_id) if gate is not None else None
    try:
        yield
    finally:
        if gate is not None and lease is not None:
            gate.release(lease)


async def run_viewer(socket: WebSocket, camera_id: str, token: str,
                     operation: Callable[[], Awaitable[None]], verify: Callable[[str], bool]) -> None:
    gate = viewer_gate(socket)
    if gate is None:  # standalone router/test composition, no implicit global owner
        await run_guarded(socket, token, operation, verify)
        return
    try:
        lease = gate.admit(camera_id)
    except SourceUnavailable:
        await close_channel(socket, 1013)
        return
    try:
        await run_guarded(socket, token, operation,
                          lambda value: not lease.cancelled.is_set() and verify(value))
    finally:
        gate.release(lease)  # run_guarded has already awaited work and guard cleanup
