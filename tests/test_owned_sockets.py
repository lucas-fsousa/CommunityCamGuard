"""Synthetic procfs snapshots; never open public listeners or inspect other PIDs."""

import io

import pytest

from backend.app.media import owned_sockets


def row(*, inode="123", address=None):
    address = owned_sockets.LOOPBACK_HEX if address is None else address
    return f"0: {address}:1234 00000000:0000 0A 0 0 0 0 0 {inode}\n"


def snapshot(monkeypatch, data, *, descriptors=1):
    class Path:
        def __init__(self, value):
            self.value = value

        def iterdir(self):
            return (str(123 + index) for index in range(descriptors))

        def open(self):
            return io.StringIO("header\n" + (data if self.value.endswith("/tcp") else ""))

    monkeypatch.setattr(owned_sockets, "Path", Path)
    monkeypatch.setattr(owned_sockets.os, "readlink", lambda fd: f"socket:[{fd}]")


def test_other_process_listener_does_not_supply_worker_readiness(monkeypatch):
    snapshot(monkeypatch, row(inode="999", address="00000000") + row())
    assert owned_sockets.loopback_listener(12345) == 0x1234


@pytest.mark.parametrize("case", ["public-bind", "extra-listener", "descriptor-budget", "row-budget"])
def test_unexpected_exposure_or_unbounded_inspection_fails_closed(monkeypatch, case):
    if case == "public-bind":
        snapshot(monkeypatch, row(address="00000000"))
    elif case == "extra-listener":
        snapshot(monkeypatch, row() + row(inode="124"), descriptors=2)
    elif case == "descriptor-budget":
        snapshot(monkeypatch, row(), descriptors=owned_sockets.MAX_FDS + 1)
    else:
        snapshot(monkeypatch, row(inode="999") * (owned_sockets.MAX_ROWS + 1))
    with pytest.raises(ValueError):
        owned_sockets.loopback_listener(12345)
