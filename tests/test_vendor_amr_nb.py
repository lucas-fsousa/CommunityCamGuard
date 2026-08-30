from __future__ import annotations

import pytest

from backend.app.drivers.yoosee.p2p import amr_nb


class FakeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class FakeCodec:
    def __init__(self):
        self.exits = 0
        self.encodes = 0
        self.Encoder_Interface_init = FakeFunction(lambda _dtx: 1)
        self.Encoder_Interface_Encode = FakeFunction(self._encode)
        self.Encoder_Interface_exit = FakeFunction(self._exit)

    def _encode(self, _state, mode, samples, output, _force_speech):
        assert mode == 7 and len(samples) == 160
        self.encodes += 1
        output[0] = 7 << 3
        for index in range(1, 32):
            output[index] = index
        return 32

    def _exit(self, _state):
        self.exits += 1


def test_streaming_encoder_preserves_partial_pcm_between_chunks(monkeypatch) -> None:
    codec = FakeCodec()
    monkeypatch.setattr(amr_nb.ctypes.util, "find_library", lambda _name: "fake-amr")
    monkeypatch.setattr(amr_nb.ctypes, "CDLL", lambda _name: codec)

    with amr_nb.AmrNbEncoder(max_seconds=1) as encoder:
        assert encoder.feed(bytes(100)) == ()
        frames = encoder.feed(bytes(220))
        assert len(frames) == 1 and len(frames[0]) == 32
        padded = encoder.feed(bytes(2), final=True)
        assert len(padded) == 1

    assert codec.encodes == 2
    assert codec.exits == 1


def test_encoder_rejects_odd_or_excess_pcm_and_closed_use(monkeypatch) -> None:
    codec = FakeCodec()
    monkeypatch.setattr(amr_nb.ctypes.util, "find_library", lambda _name: "fake-amr")
    monkeypatch.setattr(amr_nb.ctypes, "CDLL", lambda _name: codec)
    encoder = amr_nb.AmrNbEncoder(max_seconds=0.1)

    with pytest.raises(ValueError, match="complete"):
        encoder.feed(b"x")
    with pytest.raises(ValueError, match="session limit"):
        encoder.feed(bytes(1602))
    encoder.close()
    encoder.close()
    with pytest.raises(RuntimeError, match="closed"):
        encoder.feed(bytes(2))
    assert codec.exits == 1


def test_encoder_fails_closed_on_wrong_native_mode(monkeypatch) -> None:
    codec = FakeCodec()
    codec.Encoder_Interface_Encode = FakeFunction(lambda *_args: 31)
    monkeypatch.setattr(amr_nb.ctypes.util, "find_library", lambda _name: "fake-amr")
    monkeypatch.setattr(amr_nb.ctypes, "CDLL", lambda _name: codec)

    with amr_nb.AmrNbEncoder(max_seconds=1) as encoder:
        with pytest.raises(RuntimeError, match="mode-7"):
            encoder.feed(bytes(320))


def test_missing_native_library_is_explicit(monkeypatch) -> None:
    monkeypatch.setattr(amr_nb.ctypes.util, "find_library", lambda _name: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        amr_nb.AmrNbEncoder()
