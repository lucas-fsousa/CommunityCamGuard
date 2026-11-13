"""Cover the tiny SOAP posters device._post / media._post (the urllib request/response handling),
which the other control tests stub out. urlopen is mocked here so no real HTTP happens.
"""
import io
import urllib.error

import pytest

from backend.app.control import device, media


class FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("mod", [device, media])
def test_post_returns_status_and_text_on_200(monkeypatch, mod):
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResp(200, b"<ok/>"))
    status, text = mod._post("10.0.0.9", "<b/>")
    assert status == 200 and text == "<ok/>"


@pytest.mark.parametrize("mod", [device, media])
def test_post_returns_httperror_code_and_body(monkeypatch, mod):
    def raise_http(req, timeout=None):
        raise urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b"<fault/>"))
    monkeypatch.setattr(mod.urllib.request, "urlopen", raise_http)
    status, text = mod._post("10.0.0.9", "<b/>")
    assert status == 500 and text == "<fault/>"


@pytest.mark.parametrize("mod", [device, media])
def test_post_returns_none_on_connection_error(monkeypatch, mod):
    def boom(req, timeout=None):
        raise OSError("refused")
    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert mod._post("10.0.0.9", "<b/>") == (None, "")


@pytest.mark.parametrize('status', [200, 500])
def test_device_response_is_bounded_closed_and_never_parses_truncated_identity(monkeypatch, status):
    calls = []
    class Body(io.BytesIO):
        def read(self, size=-1):
            calls.append(size)
            assert size == device.MAX_RESPONSE_BYTES + 1
            return super().read(size)
    body = Body(b'x' * (device.MAX_RESPONSE_BYTES + 2))
    if status == 200:
        # Use the actual context manager's closure behavior for both success/error.
        class Response:
            status = 200
            def read(self, size): return body.read(size)
            def __enter__(self): return self
            def __exit__(self, *args): body.close()
        monkeypatch.setattr(device.urllib.request, 'urlopen', lambda *args, **kwargs: Response())
    else:
        def failure(*args, **kwargs):
            raise urllib.error.HTTPError('u', status, 'error', {}, body)
        monkeypatch.setattr(device.urllib.request, 'urlopen', failure)
    assert device._post('192.0.2.1', '<b/>') == (None, '')
    assert calls == [device.MAX_RESPONSE_BYTES + 1] and body.closed
