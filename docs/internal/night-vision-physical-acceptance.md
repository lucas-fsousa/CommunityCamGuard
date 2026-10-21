# Camera3 dark-room night-vision acceptance

The owner confirmed darkness and existing infrared illumination before authorizing
the test. Only camera3 was targeted via the authenticated production control API;
no independent P2P process, sound, white-light command or reset was used.

The Yoosee driver now includes semantic `previous_value` for night-vision writes,
mapped from the already validated preflight state. The public API still excludes
native integers. A missing GET reader is not bypassed by opening a parallel vendor
session. No new capability or change to optical mode semantics was introduced.

Result: `daytime` returned HTTP200, previous=`automatic`, changed=true,
verified=true. After10seconds the operator restored `automatic`, HTTP200 and
verified=true. Operator runtime21.5s, peak43.4MiB/zero swap. Camera3 was left in
its original automatic mode. Visual loss/recovery of IR visibility awaits the
owner's confirmation; API verification alone does not prove an optical change.

Driver regression tests cover previous automatic/daytime/night mappings:30 passed,
111.4MiB/zero swap; Ruff passed. Build limited to512MiB/no swap/half CPU,
image f317e9b6e8af deployed by recreating only app. Before the physical cycle all
three cameras were online/recording with nonempty MP4 ages3/2/4seconds.

The local operator is `re/check_camera3_night_api_cycle.py` (ignored). Do not repeat
it automatically: if an HTTP failure leaves the previous mode unavailable, report
the uncertainty instead of guessing a restoration. This test covers daytime to
automatic IR behavior in darkness, not every model or every night-mode option.
