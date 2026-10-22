import os
import subprocess

import pytest

from backend.app.media import hevc_decoder as module


@pytest.fixture
def decoder(monkeypatch):
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    now = [0.0]
    calls = []

    class Process:
        stdin = os.fdopen(input_write, "wb", buffering=0)
        stdout = os.fdopen(output_read, "rb", buffering=0)
        stopped = False
        stubborn = False
        killed = False

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            self.killed = True
            calls.append("kill")

        def wait(self, timeout):
            assert timeout == 0.5
            if self.stubborn and not self.killed:
                raise subprocess.TimeoutExpired("decoder", timeout)
            self.stopped = True
            calls.append("wait")

    process = Process()

    def popen(command, **kwargs):
        assert command[:4] == ["prlimit", "--as=536870912", "--nofile=64", "--"]
        assert "-xerror" in command and "-an" in command
        assert command[command.index("-protocol_whitelist") + 1] == "pipe"
        assert kwargs["stderr"] == subprocess.DEVNULL
        return process

    monkeypatch.setattr(module.shutil, "which", lambda _: "/test/tool")
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    instance = module.HevcDecoder(clock=lambda: now[0])
    try:
        yield instance, process, now, input_read, output_write, calls
    finally:
        instance.close()
        os.close(input_read)
        os.close(output_write)


def test_input_and_incremental_progress(decoder):
    value, _, now, read, write, calls = decoder
    assert value.offer(b"access-unit")
    os.write(write, b"fra")
    value.step()
    assert os.read(read, 4096) == b"access-unit" and value.frames == 0
    now[0] = 14
    os.write(write, b"me=1\nprogress=continue\n")
    value.step()
    assert value.frames == 1
    now[0] = 18
    value.step()
    now[0] = 19
    with pytest.raises(TimeoutError):
        value.step()
    assert calls == ["terminate", "wait"]


@pytest.mark.parametrize("progress", [b"frame=bad\n", b"frame=" + b"1" * 21 + b"\n"])
def test_invalid_progress_closes_process(decoder, progress):
    value, process, _, _, write, _ = decoder
    os.write(write, progress)
    with pytest.raises(ValueError):
        value.step()
    assert process.stopped


def test_no_progress_and_stubborn_child_are_bounded(decoder):
    value, process, now, _, _, calls = decoder
    process.stubborn = True
    now[0] = 15
    with pytest.raises(TimeoutError):
        value.step()
    assert process.stopped and calls == ["terminate", "kill", "wait"]


def test_unexpected_exit_does_not_restart(decoder):
    value, process, _, _, _, calls = decoder
    process.stopped = True
    with pytest.raises(RuntimeError):
        value.step()
    assert calls == []


def test_duplicate_progress_does_not_refresh_idle_deadline(decoder):
    value, _, now, _, write, _ = decoder
    os.write(write, b"frame=1\n")
    value.step()
    now[0] = 5
    os.write(write, b"frame=1\n")
    with pytest.raises(TimeoutError):
        value.step()


def test_partial_progress_line_cannot_grow_unbounded(decoder):
    value, process, _, _, write, _ = decoder
    for _ in range(2):
        os.write(write, b"x" * 4096)
        value.step()
    os.write(write, b"x")
    with pytest.raises(ValueError, match="overflow"):
        value.step()
    assert process.stopped


@pytest.mark.parametrize("instant", [-1.0, float("nan"), float("inf")])
def test_invalid_clock_retires_process(decoder, instant):
    value, process, now, _, _, _ = decoder
    now[0] = instant
    with pytest.raises(ValueError):
        value.step()
    assert process.stopped


def test_progress_regression_retires_process(decoder):
    value, process, _, _, write, _ = decoder
    os.write(write, b"frame=2\n")
    value.step()
    os.write(write, b"frame=1\n")
    with pytest.raises(ValueError, match="regressed"):
        value.step()
    assert process.stopped


def test_cancel_reaps_child_once_and_closes_both_pipes(decoder):
    value, process, _, _, _, calls = decoder
    value.offer(b"private-frame")
    value.close()
    value.close()
    assert value.pending_bytes == 0
    assert process.stdin.closed and process.stdout.closed
    assert calls == ["terminate", "wait"]
