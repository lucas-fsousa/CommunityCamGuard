"""Revalidate open dashboard sockets without blocking the event loop.

This closes the socket and cancels its owned operation, not independent WebRTC
peers or already-authorized HTTP downloads. Temporary login must remain disabled
until those separate paths are covered as well.
"""

import asyncio
from collections.abc import Awaitable, Callable

from starlette.websockets import WebSocket, WebSocketState

CHECK_INTERVAL = 1.0
CHECK_TIMEOUT = 5.0


async def _until_invalid(token: str, verify: Callable[[str], bool]) -> None:
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            valid = await asyncio.wait_for(asyncio.to_thread(verify, token), CHECK_TIMEOUT)
        except Exception:
            # Storage/verification failures never extend an open channel's authority.
            return
        if not valid:
            return


async def run_guarded(
    websocket: WebSocket, token: str, operation: Callable[[], Awaitable[None]],
    verify: Callable[[str], bool],
    on_cancel: Callable[[], None] | None = None,
) -> None:
    """Caller must authenticate before accepting/starting. Own and reap both tasks."""
    async def perform() -> None:
        await operation()
    work = asyncio.create_task(perform())
    guard = asyncio.create_task(_until_invalid(token, verify))
    try:
        done, _ = await asyncio.wait((work, guard), return_when=asyncio.FIRST_COMPLETED)
        if guard in done:
            # Cancel before close so producer cleanup starts even if close fails.
            if on_cancel:
                on_cancel()
            work.cancel()
            if websocket.application_state != WebSocketState.DISCONNECTED:
                try:
                    await websocket.close(code=1008)
                except Exception:
                    pass
        else:
            await work  # Preserve operation errors for the caller's normal handling.
    finally:
        guard.cancel()
        if not work.done() and on_cancel:
            on_cancel()
        if not work.done() and not work.cancelling():
            work.cancel()
        await asyncio.gather(work, guard, return_exceptions=True)
