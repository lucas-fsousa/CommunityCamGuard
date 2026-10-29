"""Real loopback negotiation with the bundled launcher's flags; no cameras or FFmpeg."""

import ast
import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
import websockets


@pytest.mark.parametrize("launcher,call_name", [
    ("backend/app/main.py", "run"), ("backend/app/diagnostics/trial_server.py", "Config"),
])
def test_launcher_flags_disable_deflate_and_deliver_identical_binary_bytes(launcher, call_name):
    tree = ast.parse((Path(__file__).parents[1] / launcher).read_text())
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "uvicorn" and node.func.attr == call_name)
    options = {item.arg: ast.literal_eval(item.value) for item in call.keywords
               if item.arg in {"proxy_headers", "ws_per_message_deflate"}}
    assert options == {"proxy_headers": False, "ws_per_message_deflate": False}

    async def application(scope, receive, send):
        assert scope["type"] == "websocket"
        assert (await receive())["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})
        message = await receive()
        await send({"type": "websocket.send", "bytes": message["bytes"]})
        await send({"type": "websocket.close", "code": 1000})

    async def exchange(port):
        payload = bytes(range(256)) * 64
        async with websockets.connect(f"ws://127.0.0.1:{port}", compression="deflate",
                                      open_timeout=3, close_timeout=1) as client:
            assert client.protocol.extensions == []
            await client.send(payload)
            assert await asyncio.wait_for(client.recv(), 3) == payload

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        server = uvicorn.Server(uvicorn.Config(application, lifespan="off", log_level="error",
                                               access_log=False, **options))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 4
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            assert server.started
            asyncio.run(exchange(listener.getsockname()[1]))
        finally:
            server.should_exit = True
            thread.join(4)
            assert not thread.is_alive()
