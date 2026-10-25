import os
import subprocess

import pytest

from backend.app.media import hevc_decoder as module
from backend.app.media.fragmented_mp4 import FragmentedMp4
from tests.test_fragmented_mp4 import box


@pytest.fixture(params=[(False, False), (True, False), (True, True)], ids=["null", "h264-mp4", "live-vfr"])
def decoder(monkeypatch, request):
    as_mp4, live = request.param
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    progress_read, progress_write = os.pipe() if as_mp4 else (None, None)
    now = [0.0]
    calls = []

    class Process:
        stdin = os.fdopen(input_write, "wb", buffering=0)
        stdout = os.fdopen(output_read, "rb", buffering=0)
        stderr = os.fdopen(progress_read, "rb", buffering=0) if progress_read is not None else None
        output = FragmentedMp4() if as_mp4 else None
        media_write = output_write
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
        limit = "--as=1073741824" if as_mp4 else "--as=536870912"
        assert command[:4] == ["prlimit", limit, "--nofile=64", "--"]
        assert "-xerror" in command and "-an" in command
        assert command[command.index("-protocol_whitelist") + 1] == "pipe"
        assert kwargs["stderr"] == (subprocess.PIPE if as_mp4 else subprocess.DEVNULL)
        if live:
            assert command[command.index("-use_wallclock_as_timestamps")+1] == "1"
            assert command[command.index("-fps_mode")+1] == "vfr" and "-r" not in command
            assert command[command.index("-g")+1] == "10"
        else:
            assert "-use_wallclock_as_timestamps" not in command
        if as_mp4:
            assert command[-3:] == ["-f", "mp4", "pipe:1"]
            assert "libx264" in command and "zerolatency" in command
            assert "pipe:2" in command and "-c:a" not in command
        return process

    monkeypatch.setattr(module.shutil, "which", lambda _: "/test/tool")
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    instance = module.HevcDecoder(clock=lambda: now[0], output=process.output, live_timing=live)
    try:
        yield instance, process, now, input_read, progress_write if as_mp4 else output_write, calls
    finally:
        instance.close()
        os.close(input_read)
        os.close(output_write)
        if progress_write is not None:
            os.close(progress_write)


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


def test_archive_closes_input_before_bounded_flush_and_keeps_termination_fallback(decoder, monkeypatch):
    value, _process, _now, _read, _write, calls = decoder
    value._archive = True
    def flush():
        assert value.pending_bytes == 0
        assert not calls
        calls.append("flush")
    monkeypatch.setattr(value, "_finish_archive", flush)
    value.close()
    assert calls == ["flush", "terminate", "wait"]
    value._archive = False  # fixture's repeated close must not repeat this test callback


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


def test_media_bytes_are_separate_from_progress_and_retired_on_close(decoder):
    value, process, _, _, write, _ = decoder
    if process.output is None:
        return
    init = box(b"ftyp") + box(b"moov")
    os.write(process.media_write, init)
    os.write(write, b"frame=1\n")
    value.step()
    assert value.frames == 1 and process.output.take().data == init
    value.close()
    assert process.output.closed and process.stderr.closed


def test_used_output_cannot_cross_decoder_epochs():
    output = FragmentedMp4()
    output.feed(box(b"ftyp") + box(b"moov"))
    output.take()
    assert output.buffered_bytes == 0
    with pytest.raises(ValueError, match="fresh"):
        module.HevcDecoder(output=output)


def test_bad_media_output_retires_transcoder(decoder):
    value, process, _, _, _, _ = decoder
    if process.output is None:
        return
    os.write(process.media_write, box(b"mdat"))
    with pytest.raises(ValueError, match="order"):
        value.step()
    assert process.stopped and process.output.closed


def test_spawn_failure_retires_claimed_output(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda _: "/test/tool")

    def fail(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(module.subprocess, "Popen", fail)
    output = FragmentedMp4()
    with pytest.raises(OSError):
        module.HevcDecoder(output=output)
    assert output.closed
