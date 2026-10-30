"""HTTPS intercom admission with synthetic drivers only; no camera/audio I/O."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import access_keys, auth, config
from backend.app.api import intercom
from backend.app.api.local_only import require_local_request
from backend.app.drivers.contracts import AudioMessageResult

CID = 'cam_'+'a'*24
ORIGIN = 'https://cameras.example.org'


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv('DASHBOARD_PUBLIC_ORIGIN', ORIGIN)
    config.get_settings.cache_clear()
    app = FastAPI();app.include_router(intercom.router);app.include_router(intercom.stream_router)
    @app.post('/provisioning', dependencies=[Depends(auth.require_auth),
                                           Depends(require_local_request)])
    def provisioning():pytest.fail('remote provisioning must remain forbidden')
    return TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 1234))


def token(permission):
    if permission == 'primary':return auth.issue_token()
    key = access_keys.create(access_keys.CreateKey(label='Synthetic', expires_at=None,
        permissions=('intercom',) if permission == 'intercom' else ('live',)))
    return auth.issue_temporary_token(key.secret)


@pytest.mark.parametrize('permission', ['primary', 'intercom', 'denied', 'missing'])
def test_remote_pcm_requires_auth_permission_and_preserves_provisioning_guard(remote, monkeypatch, permission):
    calls = []
    def send(cid, pcm):
        calls.append((cid, len(pcm)))
        return AudioMessageResult(20,1,1,1,True,True,True)
    monkeypatch.setattr(intercom, 'send_audio_message', send)
    headers = {'Origin':ORIGIN, 'Content-Type':'audio/pcm', 'X-Forwarded-For':'203.0.113.9'}
    if permission != 'missing':headers['Cookie'] = f'{auth.COOKIE_NAME}={token(permission)}'
    response = remote.post(f'/api/cameras/{CID}/intercom/messages', content=bytes(640), headers=headers)
    assert response.status_code == (401 if permission == 'missing' else 403 if permission == 'denied' else 200)
    assert bool(calls) is (permission in {'primary','intercom'})
    if permission == 'primary':
        assert remote.post('/provisioning', headers=headers).status_code == 403
        assert remote.post(f'/api/cameras/{CID}/intercom/messages', content=bytes(640),
            headers={**headers,'Origin':'https://attacker.example'}).status_code == 403
        assert len(calls) == 1


@pytest.mark.parametrize('permission,origin,allowed', [
    ('primary',ORIGIN,True), ('intercom',ORIGIN,True), ('denied',ORIGIN,False),
    ('missing',ORIGIN,False), ('primary','https://attacker.example',False)])
def test_remote_talk_websocket_keeps_origin_and_permission_checks(remote, monkeypatch, permission, origin, allowed):
    calls = []
    async def stream(socket, cid, stop):
        calls.append(cid);await socket.accept();await socket.send_json({'type':'synthetic'});await socket.close()
    monkeypatch.setattr(intercom, '_stream_audio', stream)
    headers = {'Origin':origin,'X-Forwarded-For':'203.0.113.9'}
    if permission != 'missing':headers['Cookie'] = f'{auth.COOKIE_NAME}={token(permission)}'
    if allowed:
        with remote.websocket_connect(f'wss://cameras.example.org/api/cameras/{CID}/intercom/stream', headers=headers) as socket:
            assert socket.receive_json() == {'type':'synthetic'}
    else:
        with pytest.raises(WebSocketDisconnect):
            with remote.websocket_connect(f'wss://cameras.example.org/api/cameras/{CID}/intercom/stream', headers=headers):
                pytest.fail('unauthorized audio socket opened')
    assert bool(calls) is allowed
