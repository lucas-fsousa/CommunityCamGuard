"""Small loopback RTSP metadata fixture. Never emits audio/video frames."""

import socket
import socketserver
import threading

SDP = (b"v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=synthetic\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
       b"m=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
       b"a=fmtp:96 packetization-mode=1;profile-level-id=42C01E\r\na=control:trackID=0\r\n")


class CameraFixture:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = set()
        self.accepted = 0
        self.peak = 0
        self.connected = threading.Event()
        self.closed = threading.Event()
        self.closed.set()
        self.stop = threading.Event()
        fixture = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                with fixture.lock:
                    fixture.clients.add(self.request)
                    fixture.accepted += 1
                    fixture.peak = max(fixture.peak, len(fixture.clients))
                    fixture.connected.set()
                    fixture.closed.clear()
                self.request.settimeout(0.1)
                pending = b""
                try:
                    while not fixture.stop.is_set():
                        try:
                            data = self.request.recv(4096)
                        except TimeoutError:
                            continue
                        if not data:
                            return
                        pending += data
                        if len(pending) > 16384:
                            return
                        while b"\r\n\r\n" in pending:
                            head, pending = pending.split(b"\r\n\r\n", 1)
                            method = head.split(b" ", 1)[0]
                            headers = dict(line.split(b":", 1) for line in head.split(b"\r\n")[1:])
                            body = SDP if method == b"DESCRIBE" else b""
                            response = (b"RTSP/1.0 200 OK\r\nCSeq:" + headers[b"CSeq"]
                                        + b"\r\nSession: local-fixture\r\n")
                            if method == b"DESCRIBE":
                                response += b"Content-Type: application/sdp\r\n"
                            if method == b"SETUP":
                                response += b"Transport:" + headers[b"Transport"] + b"\r\n"
                            response += f"Content-Length: {len(body)}\r\n\r\n".encode() + body
                            self.request.sendall(response)
                except (OSError, KeyError, ValueError):
                    return
                finally:
                    with fixture.lock:
                        fixture.clients.discard(self.request)
                        if not fixture.clients:
                            fixture.closed.set()

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)

    @property
    def url(self):
        return f"rtsp://127.0.0.1:{self.server.server_address[1]}/synthetic"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.server.shutdown()
        with self.lock:
            clients = tuple(self.clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.server.server_close()
        self.thread.join(timeout=1)
        assert self.closed.wait(1)


def describe(url):
    port = int(url.split(":")[2].split("/")[0])
    consumer = socket.create_connection(("127.0.0.1", port), timeout=3)
    try:
        consumer.sendall(f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n\r\n".encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = consumer.recv(4096)
            if not chunk or len(data) + len(chunk) > 16384:
                raise AssertionError("bounded RTSP response required")
            data += chunk
        assert data.startswith(b"RTSP/1.0 200")
        return consumer
    except BaseException:
        consumer.close()
        raise
