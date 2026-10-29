"""One bounded local FFmpeg command; optional AAC and original-codec UTC archive."""

from pathlib import Path

from .archive_spec import validate_segment_seconds


def decoder_command(*, output: bool, live_timing: bool, frame_rate: int | None,
                    transport: str = "hevc", audio: bool = False, archive: Path | None = None,
                    archive_segment_seconds: int = 60) -> list[str]:
    validate_segment_seconds(archive_segment_seconds)
    if transport not in {"hevc", "mpegts"} or (audio and (transport != "mpegts" or not output)):
        raise ValueError("supported local media transport required")
    if archive is not None and (not audio or ":" in str(archive)):
        raise ValueError("local native A/V archive required")
    address_space = 1073741824 if output else 536870912
    cmd = ["prlimit", f"--as={address_space}", "--nofile=64", "--", "ffmpeg", "-v", "error",
           "-nostdin", "-xerror", "-err_detect", "explode", "-filter_threads", "1",
           "-protocol_whitelist", "pipe", "-threads", "1", "-f", transport,
           "-probesize", "1048576" if audio else "32", "-analyzeduration", "1000000" if audio else "0"]
    if transport == "hevc":
        if live_timing:
            cmd += ["-use_wallclock_as_timestamps", "1"]
        elif frame_rate is not None:
            cmd += ["-r", str(frame_rate)]
    cmd += ["-i", "pipe:0", "-map", "0:v:0"]
    cmd += ["-map", "0:a:0"] if audio else ["-an"]
    cmd += ["-threads", "1"]
    if not output:
        return [*cmd, "-progress", "pipe:1", "-stats_period", "0.1", "-f", "null", "-"]
    cmd += ["-v", "quiet", "-progress", "pipe:2", "-stats_period", "0.1", "-c:v", "libx264",
            "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
            "-profile:v", "baseline", "-g", "10" if live_timing or audio else "25",
            "-keyint_min", "10" if live_timing or audio else "25", "-sc_threshold", "0"]
    if live_timing or audio:
        cmd += ["-fps_mode", "vfr"]
    if audio:
        cmd += ["-c:a", "copy", "-bsf:a", "aac_adtstoasc", "-max_muxing_queue_size", "128",
                "-muxing_queue_data_threshold", "1048576", "-max_interleave_delta", "1000000"]
    flags = "+frag_keyframe+empty_moov+default_base_moof" + ("+delay_moov" if audio else "")
    cmd += ["-flush_packets", "1", "-movflags", flags,
            "-f", "mp4", "pipe:1"]
    if archive is not None:
        cmd += ["-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-bsf:a", "aac_adtstoasc",
                "-max_muxing_queue_size", "128", "-muxing_queue_data_threshold", "1048576",
                "-max_interleave_delta", "1000000", "-avoid_negative_ts", "make_zero",
                "-f", "segment", "-segment_time", str(archive_segment_seconds), "-segment_format", "mp4",
                "-reset_timestamps", "1", "-strftime", "1", "-segment_format_options",
                "movflags=+frag_keyframe+empty_moov+delay_moov+default_base_moof", str(archive)]
    return cmd
