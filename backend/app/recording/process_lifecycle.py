"""Bounded receipts for directly owned recording FFmpeg processes.

Normal return confirms that Popen reaped this child. It does not close the
upstream go2rtc producer, stop its other consumers, or certify grandchildren.
"""

import subprocess


class RecorderCleanupUnconfirmed(RuntimeError):
    pass


def retire_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
