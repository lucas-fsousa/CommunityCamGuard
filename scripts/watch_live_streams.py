#!/usr/bin/env python3
"""Low-overhead live-view watcher for the Docker deployment.

Samples the two CCG containers, go2rtc/FFmpeg processes and go2rtc stream counters into a rotating
JSONL file. It is intentionally a host-side tool: ``ccg-app`` and ``ccg-go2rtc`` are separate PID
namespaces, so neither container can truthfully inspect the other's FFmpeg CPU/RSS without mounting
the Docker socket (which would grant the web app root-equivalent host access).

Run while reproducing a stall:

    .venv/bin/python scripts/watch_live_streams.py

The default output is ``tmp/live-diagnostics.jsonl`` (gitignored), capped at 20 MiB plus two old
files. Ctrl+C stops it cleanly. No camera credentials or media payloads are recorded.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# Keep this host tool runnable with stdlib Python; the shared contract has no
# framework imports or runtime side effects.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.media.diagnostic_fields import public_metrics

DEFAULT_CONTAINERS = ("ccg-go2rtc", "ccg-app")
_STREAM_ID = re.compile(r"^cam_[0-9a-f]{24}(?:_hd|_web)?$")
_EVENTS = {"waiting", "stalled", "playing", "catchup_start", "catchup_end",
           "live_edge_jump", "mse_failure", "watchdog_recovery"}


def _run(args: list[str], timeout: float = 4.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args, check=False, capture_output=True, text=True, timeout=timeout,
        )
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        return result.returncode, output
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 127, "diagnostic command unavailable or timed out"


def _percent(value: str | None) -> float | None:
    try:
        return float((value or "").strip().rstrip("%"))
    except ValueError:
        return None


def _bytes(value: str | None) -> int | None:
    """Parse the first human-size value in Docker's ``used / limit`` fields."""
    match = re.match(r"\s*([\d.]+)\s*([kmgt]?i?b)", value or "", re.IGNORECASE)
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2).lower()
    factors = {
        "b": 1, "kb": 1000, "kib": 1024, "mb": 1000**2, "mib": 1024**2,
        "gb": 1000**3, "gib": 1024**3, "tb": 1000**4, "tib": 1024**4,
    }
    return round(number * factors[unit])


def docker_stats(containers: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    code, output = _run([
        "docker", "stats", "--no-stream", "--format", "{{json .}}", *containers,
    ], timeout=8)
    if code != 0:
        return {"_error": {"message": "docker stats unavailable"}}
    result: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        name = row.get("Name") or row.get("Container") or "unknown"
        used, _, limit = (row.get("MemUsage") or "").partition("/")
        mem_used, mem_limit = _bytes(used), _bytes(limit)
        result[name] = {
            "cpu_percent": _percent(row.get("CPUPerc")),
            "memory_percent": _percent(row.get("MemPerc")),
            "memory_used_bytes": mem_used,
            "memory_limit_bytes": mem_limit,
            "network_io": row.get("NetIO"),
            "block_io": row.get("BlockIO"),
            "pids": int(row["PIDs"]) if str(row.get("PIDs", "")).isdigit() else None,
        }
    return result


def docker_states(containers: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    fmt = '{{json .Name}} {{.RestartCount}} {{json .State}}'
    code, output = _run(["docker", "inspect", "--format", fmt, *containers])
    if code != 0:
        return {"_error": {"message": "docker state unavailable"}}
    result: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        try:
            name_json, restarts, state_json = line.split(" ", 2)
            state = json.loads(state_json)
            result[json.loads(name_json).lstrip("/")] = {
                "status": state.get("Status"),
                "running": state.get("Running"),
                "restarting": state.get("Restarting"),
                "oom_killed": state.get("OOMKilled"),
                "exit_code": state.get("ExitCode"),
                "restart_count": int(restarts),
                "started_at": state.get("StartedAt"),
            }
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return result


def docker_processes(container: str) -> list[dict[str, Any]]:
    code, output = _run([
        "docker", "top", container, "-eo", "pid,ppid,state,pcpu,pmem,rss,etime,comm",
    ])
    if code != 0:
        return [{"error": "docker process sample unavailable"}]
    rows = []
    for line in output.splitlines()[1:]:
        columns = line.split(None, 7)
        if len(columns) != 8:
            continue
        pid, ppid, state, cpu, memory, rss, elapsed, command = columns
        # No argv collection: credentials can occur in URLs, query strings or flags.
        # Keep only known executable labels; even process names can be user-controlled.
        command = command.split()[0].rsplit("/", 1)[-1]
        command = command if command in {"ffmpeg", "go2rtc", "python", "python3", "uvicorn"} else "other"
        rows.append({
            "pid": int(pid), "ppid": int(ppid), "state": state,
            "cpu_percent": _percent(cpu), "memory_percent": _percent(memory),
            "rss_kib": int(rss) if rss.isdigit() else None,
            "elapsed": elapsed, "command": command[:1000],
        })
    return rows


def docker_client_events(container: str, since: str) -> list[dict[str, Any]]:
    """Extract structured browser events already emitted by the app logger."""
    code, output = _run(["docker", "logs", "--tail", "200", "--since", since, container], timeout=5)
    if code != 0:
        return []
    events = []
    marker = "live_view_event "
    for line in output.splitlines():
        if marker not in line:
            continue
        try:
            event = json.loads(line.split(marker, 1)[1])
            if not isinstance(event, dict) or event.get("event") not in _EVENTS:
                continue
            camera = event.get("camera_id")
            stream = event.get("stream")
            if not isinstance(camera, str) or not re.fullmatch(r"cam_[0-9a-f]{24}", camera):
                continue
            if stream not in (camera, camera + "_hd", camera + "_web"):
                continue
            metrics = event.get("metrics")
            safe = {"event": event["event"], "camera_id": camera, "stream": stream,
                    "metrics": public_metrics(metrics) if isinstance(metrics, dict) else {}}
            if isinstance(event.get("at"), str):
                safe["at"] = datetime.fromisoformat(event["at"]).isoformat()
            server = event.get("server")
            if isinstance(server, dict):
                safe["server"] = {key: server[key] for key in ("video_packets", "consumers")
                                  if type(server.get(key)) is int and 0 <= server[key] <= 10**15}
            events.append(safe)
        except (ValueError, TypeError):
            continue
    return events


def go2rtc_streams(api: str) -> dict[str, dict[str, Any]]:
    try:
        with urllib.request.urlopen(api.rstrip("/") + "/api/streams", timeout=3) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("stream counter response too large")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("invalid stream counter response")
    except (urllib.error.URLError, OSError, ValueError):
        return {"_error": {"message": "go2rtc stream counters unavailable"}}
    result = {}
    for stream_id, stream in (data or {}).items():
        if not _STREAM_ID.fullmatch(stream_id) or not isinstance(stream, dict):
            continue
        producers = stream.get("producers") or []
        consumers = stream.get("consumers") or []
        if not isinstance(producers, list) or not isinstance(consumers, list):
            continue
        video_packets = sum(
            receiver.get("packets", 0)
            for producer in producers
            if isinstance(producer, dict) and isinstance(producer.get("receivers"), list)
            for receiver in (producer.get("receivers") or [])
            if isinstance(receiver, dict) and isinstance(receiver.get("codec"), dict)
            and receiver["codec"].get("codec_type") == "video"
            and type(receiver.get("packets")) is int and 0 <= receiver["packets"] <= 10**15
        )
        result[stream_id] = {
            "video_packets": video_packets,
            "producers": len(producers),
            "consumers": len(consumers),
        }
    return result


class Watcher:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.previous_packets: dict[str, int] = {}
        self.stall_strikes: dict[str, int] = {}
        self.last_log_poll = datetime.now(UTC).timestamp() - args.interval

    def sample(self) -> dict[str, Any]:
        # Query from a small overlap so timestamp rounding cannot lose an event. De-duplication is
        # unnecessary for diagnosis and a repeated transition is safer than a missing one.
        since = datetime.fromtimestamp(self.last_log_poll - 0.25, UTC).isoformat()
        self.last_log_poll = datetime.now(UTC).timestamp()
        client_events = docker_client_events("ccg-app", since)
        streams = go2rtc_streams(self.args.go2rtc_api)
        anomalies: list[str] = []
        for event in client_events:
            name = event.get("event")
            if name not in {"playing", "catchup_end"}:
                anomalies.append(f"browser_{name or 'event'}:{event.get('stream', 'unknown')}")
        for stream_id, stream in streams.items():
            if stream_id == "_error":
                anomalies.append("go2rtc_api_unavailable")
                continue
            packets = int(stream.get("video_packets") or 0)
            previous = self.previous_packets.get(stream_id)
            if previous is not None and packets <= previous and stream.get("consumers", 0) > 0:
                self.stall_strikes[stream_id] = self.stall_strikes.get(stream_id, 0) + 1
            else:
                self.stall_strikes[stream_id] = 0
            self.previous_packets[stream_id] = packets
            stream["video_packet_delta"] = None if previous is None else packets - previous
            stream["stall_strikes"] = self.stall_strikes[stream_id]
            if self.stall_strikes[stream_id] >= 3:
                anomalies.append(f"stream_packets_stalled:{stream_id}")

        stats = docker_stats(self.args.containers)
        states = docker_states(self.args.containers)
        processes = {name: docker_processes(name) for name in self.args.containers}
        for name, stat in stats.items():
            if name == "_error":
                anomalies.append("docker_stats_unavailable")
                continue
            if (stat.get("cpu_percent") or 0) >= self.args.cpu_warning:
                anomalies.append(f"container_cpu_high:{name}")
            if (stat.get("memory_percent") or 0) >= self.args.memory_warning:
                anomalies.append(f"container_memory_high:{name}")
        for name, state in states.items():
            if name == "_error":
                anomalies.append("docker_inspect_unavailable")
                continue
            if state.get("oom_killed"):
                anomalies.append(f"container_oom:{name}")
            if state.get("restarting") or not state.get("running"):
                anomalies.append(f"container_not_running:{name}")
        for name, rows in processes.items():
            for process in rows:
                if (process.get("cpu_percent") or 0) >= self.args.process_cpu_warning:
                    anomalies.append(f"process_cpu_high:{name}:{process.get('pid')}")
                if process.get("state") in {"D", "Z"}:
                    anomalies.append(f"process_state_{process['state']}:{name}:{process.get('pid')}")

        return {
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "interval_seconds": self.args.interval,
            "anomalies": sorted(set(anomalies)),
            "client_events": client_events,
            "containers": stats,
            "states": states,
            "processes": processes,
            "streams": streams,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=5.0, help="sample interval in seconds")
    parser.add_argument("--output", type=Path, default=Path("tmp/live-diagnostics.jsonl"))
    parser.add_argument("--go2rtc-api", default="http://127.0.0.1:3201")
    parser.add_argument("--containers", nargs="+", default=list(DEFAULT_CONTAINERS))
    parser.add_argument("--cpu-warning", type=float, default=85.0)
    parser.add_argument("--process-cpu-warning", type=float, default=80.0)
    parser.add_argument("--memory-warning", type=float, default=85.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="print every sample, not anomalies only")
    args = parser.parse_args(argv)
    args.interval = max(2.0, args.interval)
    args.containers = tuple(args.containers)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.umask(0o077)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    file_log = logging.getLogger("live_watcher_jsonl")
    file_log.setLevel(logging.INFO)
    handler = RotatingFileHandler(args.output, maxBytes=20 * 1024 * 1024, backupCount=2)
    handler.setFormatter(logging.Formatter("%(message)s"))
    file_log.addHandler(handler)
    file_log.propagate = False

    stop = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    watcher = Watcher(args)
    print(f"live watcher -> {args.output} (interval {args.interval:.1f}s; Ctrl+C to stop)")
    while not stop:
        started = time.monotonic()
        sample = watcher.sample()
        encoded = json.dumps(sample, separators=(",", ":"), sort_keys=True)
        file_log.info(encoded)
        if args.verbose or sample["anomalies"]:
            print(f"{sample['at']} anomalies={sample['anomalies'] or 'none'}", flush=True)
        if args.once:
            break
        remaining = max(0.0, args.interval - (time.monotonic() - started))
        stop_until = time.monotonic() + remaining
        while not stop and time.monotonic() < stop_until:
            time.sleep(min(0.25, stop_until - time.monotonic()))
    handler.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
