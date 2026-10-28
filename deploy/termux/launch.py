"""Keep the actual app PID visible to runit outside PRoot for graceful shutdown."""

import os
import runpy
import sys
from pathlib import Path

os.umask(0o077)
pidfile = Path("/run/ccg/app.pid")
pidfile.write_text(str(os.getpid()))
# The source release, not the launcher's shared directory, supplies the backend.
sys.path.insert(0, str(Path.cwd()))
try:
    runpy.run_module("backend.app.main", run_name="__main__")
finally:
    if pidfile.read_text() == str(os.getpid()):
        pidfile.unlink()
