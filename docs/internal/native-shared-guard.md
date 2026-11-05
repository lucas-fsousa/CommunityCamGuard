# Shared native smart protection and weekly plan — 2026-11-05

The existing semantic `smart_protection` and `smart_protection_schedule` controls
now reuse the active native account, like volume/orientation/night mode. Driver
capability evidence and exact-device operation proofs still control visibility;
generic API permissions and the standalone path for non-native cameras are unchanged.

`shared_guard.py` supplies only a strict boolean or validated weekly domain value.
Write schemas live in `p2p/model_control_specs.py`, separated from the transaction
state machine as the typed union grows. Existing imports from model_control_job
remain compatible. All five model writes share one generation-bound inbox slot,
one socket/sequence namespace, one-send D2 and correlated D3 handling. There is
no raw thing-model path/value endpoint or independent login.

The guard switch writes only `guardParm.setVal.enable`; the schedule writes only
`guardParm.setVal.plan`. Changing a plan never enables the guard, alters siren
settings or rewrites the whole guard root. User weekday order is normalized to
the existing Sunday-first mask, so reordered equivalent days are a read-only
no-op, not an unnecessary change or a false readback mismatch. Local wall-clock
times, overnight windows and the existing equal-endpoint semantics are retained.
Recording UTC policy is unrelated and unchanged.

Shared preflight requires a complete supported state. An unchanged request only
reads. A change sends once, waits for D3 and performs bounded read-only settling
checks; owner/generation loss or unconfirmed state never causes D2 replay or
standalone fallback. Invalid inputs retain the existing public driver error type.

187 focused adapter/worker/API/legacy regressions passed under192MiB/no swap/50%
CPU (111.1MiB peak), Mypy384/Ruff passed. Tests cover neighboring-field preservation,
single-slot exclusion, ACK/application distinction, wrong peer/session/generation/
message rejection, delayed/missing readback, unavailable owner, invalid data,
weekday no-op and actual synthetic worker dispatch on its single socket.

No camera guard or schedule was changed during implementation: arming it could
trigger audible alarms. This is implementation plus simulated protocol coverage,
not new camera-backed actuation or physical acceptance.

Deployed `c29aa8c` / `b-4737e1a2c4e1` via app-only replacement. Camera3 resumed
native/active93, with both other cameras still legacy0. On the ordinary authenticated
API, GET smart_protection and GET smart_protection_schedule both returned200,
verified/authenticated. The same MSE socket advanced3→6 binary chunks during those
reads (sizes1355/197477/200551/204355/205129/204242), and source revision93 did not
change. All three cameras report online/recording; no OOM or unexpected app
restart. CI42001820514 and Image42001820592 passed. These production checks were
GET-only; they do not convert simulated guard writes into physical acceptance.
