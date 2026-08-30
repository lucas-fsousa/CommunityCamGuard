from __future__ import annotations

from backend.app.drivers.yoosee.p2p import access_session
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    LoginMaterial,
    OnlineDevice,
    P2PProbeError,
)


def test_access_session_skips_certified_node_that_cannot_initialize(monkeypatch):
    endpoints = [("192.0.2.10", 19800), ("192.0.2.11", 19800)]
    material = LoginMaterial(123, bytes(range(64)))
    device = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    attempted: list[tuple[str, int]] = []

    def fake_certify(_sock, _material, remaining, _timeout, *, deadline):
        attempted.append(remaining[0])
        return CertifiedNode(remaining[0], 9, bytes(32), 17)

    def fake_initialize(_sock, node, _timeout, *, deadline):
        if node.address == endpoints[0]:
            raise P2PProbeError("incomplete")
        return node, (device,)

    monkeypatch.setattr(access_session, "certify_node", fake_certify)
    monkeypatch.setattr(access_session, "initialize_node", fake_initialize)

    node, devices, skipped = access_session.establish_initialized_node(
        object(),  # type: ignore[arg-type]
        material,
        endpoints,
        0.1,
        deadline=10**20,
    )

    assert node.address == endpoints[1]
    assert devices == (device,)
    assert skipped == 1
    assert attempted == endpoints


def test_list_query_retransmits_boundedly_before_accepting_reply(monkeypatch):
    sent = []
    receives = [[], [], [(b"\x7f\x16" + bytes(62), ("192.0.2.1", 51701))]]

    class FakeSocket:
        def sendto(self, payload, peer):
            sent.append((payload, peer))

    monkeypatch.setattr(
        access_session.socket,
        "getaddrinfo",
        lambda *_args: [(2, 2, 17, "", ("192.0.2.1", 51701))],
    )
    monkeypatch.setattr(
        access_session,
        "receive_datagrams",
        lambda _sock, _until: receives.pop(0),
    )
    monkeypatch.setattr(
        access_session,
        "parse_list_reply",
        lambda _wire: [("192.0.2.10", 19800)],
    )

    result = access_session.obtain_list(
        FakeSocket(), 123, 0.1, retries=3, deadline=10**20  # type: ignore[arg-type]
    )

    assert result == [("192.0.2.10", 19800)]
    assert [peer for _payload, peer in sent] == [("192.0.2.1", 51701)] * 3


def test_list_query_rejects_unbounded_retry_configuration():
    try:
        access_session.obtain_list(object(), 123, 0.1, retries=0)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("zero retries were accepted")
