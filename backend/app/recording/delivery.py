"""Range-compatible archive delivery with revocable temporary-session ownership.

Delegate all file/range parsing to Starlette. Never load the whole recording or
hand temporary transfers to a pathsend extension that bypasses task ownership.
"""

import asyncio

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.types import Receive, Scope, Send

from .. import auth
from ..session_channels import CHECK_TIMEOUT, wait_until_invalid


class SessionFileResponse(FileResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = Request(scope).cookies.get(auth.COOKIE_NAME) or ""
        try:
            principal = await asyncio.wait_for(asyncio.to_thread(auth.token_principal, token), CHECK_TIMEOUT)
        except Exception:
            principal = None
        if principal is None:
            await JSONResponse({"detail": "Not authenticated"}, status_code=401,
                               headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        if principal.authentication != "temporary":
            await super().__call__(scope, receive, send)
            return

        # Do not mutate server scope or allow zero-copy sendfile to escape cancellation.
        scope = dict(scope, extensions={key: value for key, value in scope.get("extensions", {}).items()
                                        if key != "http.response.pathsend"})
        started = False

        async def guarded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                message = dict(message, headers=[(key, value) for key, value in message["headers"]
                                                if key.lower() != b"cache-control"]
                               + [(b"cache-control", b"private, no-store")])
                started = True
            await send(message)

        work = asyncio.create_task(super().__call__(scope, receive, guarded_send))
        guard = asyncio.create_task(wait_until_invalid(token, auth.verify_token))
        try:
            done, _ = await asyncio.wait((work, guard), return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                await work
                return
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            if not started:
                await JSONResponse({"detail": "Not authenticated"}, status_code=401,
                                   headers={"Cache-Control": "no-store"})(scope, receive, send)
                return
            # Cannot replace an already-sent 200/206 with 401 or pretend a partial
            # download completed. Let the ASGI server abort the incomplete response.
            raise ConnectionAbortedError("Session ended during recording delivery")
        finally:
            guard.cancel()
            if not work.done():
                work.cancel()
            await asyncio.gather(work, guard, return_exceptions=True)
