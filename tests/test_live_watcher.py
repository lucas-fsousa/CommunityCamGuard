"""Host-side live watcher parsing and anomaly detection; Docker and go2rtc are fully faked."""
from types import SimpleNamespace

from scripts import watch_live_streams as watcher


def test_human_bytes_and_percent_parsing():
    assert watcher._bytes("140.5MiB") == round(140.5 * 1024**2)
    assert watcher._bytes("1GB") == 1000**3
    assert watcher._percent("87.2%") == 87.2
    assert watcher._percent("unknown") is None


def test_docker_top_redacts_rtsp_credentials(monkeypatch):
    output = (
        "PID PPID S %CPU %MEM RSS ELAPSED COMMAND\n"
        "42 1 S 87.1 2.5 149472 10:21 ffmpeg -i rtsp://admin:secret@10.0.0.5/onvif1"
    )
    monkeypatch.setattr(watcher, "_run", lambda *_args, **_kwargs: (0, output))
    process = watcher.docker_processes("ccg-go2rtc")[0]
    assert "secret" not in process["command"]
    assert "rtsp://***@" in process["command"]


def test_three_stationary_packet_samples_raise_stream_anomaly(monkeypatch):
    args = SimpleNamespace(
        go2rtc_api="http://go2rtc", containers=("ccg-go2rtc", "ccg-app"), interval=2,
        cpu_warning=85, process_cpu_warning=80, memory_warning=85,
    )
    monkeypatch.setattr(watcher, "go2rtc_streams", lambda _api: {
        "cam_x_hd": {"video_packets": 100, "producers": 1, "consumers": 1},
    })
    monkeypatch.setattr(watcher, "docker_stats", lambda _names: {
        "ccg-go2rtc": {"cpu_percent": 20, "memory_percent": 10},
        "ccg-app": {"cpu_percent": 5, "memory_percent": 10},
    })
    monkeypatch.setattr(watcher, "docker_states", lambda _names: {
        name: {"running": True, "restarting": False, "oom_killed": False}
        for name in args.containers
    })
    monkeypatch.setattr(watcher, "docker_processes", lambda _name: [])
    monkeypatch.setattr(watcher, "docker_client_events", lambda _name, _since: [])
    live = watcher.Watcher(args)
    samples = [live.sample() for _ in range(4)]
    assert samples[-1]["streams"]["cam_x_hd"]["stall_strikes"] == 3
    assert "stream_packets_stalled:cam_x_hd" in samples[-1]["anomalies"]
