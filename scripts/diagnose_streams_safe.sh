#!/usr/bin/env bash
# One read-only, bounded snapshot: no camera probes, raw config or raw process arguments.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
umask 077
diagnostic_python="python3"
if [ -x .venv/bin/python ]; then
  diagnostic_python=".venv/bin/python"
fi
diagnostic_output="data/diag/diag-$(date -u +%Y%m%d-%H%M%S).jsonl"
exec "$diagnostic_python" scripts/watch_live_streams.py --once --verbose \
  --output "$diagnostic_output" \
  --go2rtc-api "${GO2RTC_API:-http://127.0.0.1:3201}" \
  --containers "${CCG_GO2RTC:-ccg-go2rtc}" "${CCG_APP:-ccg-app}"
