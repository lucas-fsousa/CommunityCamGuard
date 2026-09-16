# Native AV bootstrap: SDK evidence — 2026-09-16

## Scope and reproducibility

Offline symbol-scoped ARM64 inspection of `re/extracted/libiotvideomulti.so`,
SHA-256 `b4d6f72168b666f80b08ebdd2eb210f57b5fd8199d8b375963f2568c5e96900a`.
The existing `re/p2pdis.py` was used with `P2PDIS_SO` selecting that binary,
256 MiB address-space / 20 CPU-second limits. No emulator, whole-APK decompile,
camera connection or production container restart was needed.

## Observed control flow

| Function / address | Evidence |
| --- | --- |
| `iv_process_calling`, `0x20293c` | Creates LAN/NAT MTP channel before direct receipt; sends direct calling at `0x202da4` with GOT callback `0x240920`. Broker callback comes from `0x240930`; transfer timer from `0x240888`. ELF relocations resolve these to the three functions below. |
| `gutes_on_ackfrm_Lan_Calling`, `0x2028d0` | 108-byte callback logs and returns. Its result comparison has no state-changing body. No transfer flag is set by this callback. |
| `iv_on_ackfrm_Calling`, `0x20300c` | Separate broker callback handles timeout and optional network-address metadata. Do not confuse it with the LAN receipt callback. |
| `iv_on_time_out_check_into_transfer`, `0x203288` | At `0x203450` reads selected channel `session+0x1b0`; if present and signed score at `channel+0x24` exceeds 5, calls `iv_start_process_transfer` (`0x2034cc`). This branch does not test a direct A4 ACK flag. |
| `iv_rcv_meter_ack`, `0x20e828` | Finds channel by address/type, selects meter item using sequence (`0x20e8f8`), marks item state 2, computes elapsed time from echoed timestamp, and invokes optimization while session state is 1. |
| `iv_mtp_chnnel_eval_quality`, `0x2082dc` | Counts state-2 meter items, calculates loss/RTT-based score and stores it at channel offset `0x24`. With no acknowledged items it does not initialize a positive score. |
| `iv_mtpSession_optimize_proc`, `0x20a518` | Evaluates/sorts channels and populates selected-channel slots beginning at `session+0x1b0`. A single positive candidate can be selected; LAN/NAT preference branches also exist. |

This disproves our experimental guard's assumption that a **direct LAN A4 receipt
is independently mandatory** before entering AV. It does not prove the two peer
datagrams from the last live attempt included a meter ACK: our historical
`meter_acknowledged` boolean also accepts inbound requests.

## Driver correction

Experimental AV now requires `meter_roundtrip_confirmed`: a checksum-valid kind-2
response from the pinned peer, matching link/source/destination, channel 4, actual
record length, recognized role, matching call ID when present, and sequence plus
full echoed timestamp from one of the two measurements sent on this fresh socket.
An inbound request can receive its bounded reply but cannot satisfy this gate.
Neither an A4 receipt nor the old broad meter-observation flag is sufficient.

This is a conservative bounded bootstrap criterion, **not** a complete port of
the SDK's quality scoring, channel optimization or cryptographic authentication.
Identity/access preparation and subsequent AV INIT/ACCEPT/START/cookie validation
remain required. The SDK evidence supports separating transport readiness from
the LAN A4 receipt; live compatibility of this criterion is still unproven.

Existing intercom/SD callers keep their bootstrap completion behavior. The AV route
opts into the stronger roundtrip requirement; retry counts/timeouts, exclusive
ownership, traffic budgets, AV CLOSE and B9 cleanup are unchanged. The safe HTTP
bootstrap error now includes the new boolean, without payloads or identifiers.

## Next live checkpoint

Local validation: the full Python 3.12 suite in a disposable container capped at
512 MiB / one CPU passed 1,762 tests with one Node-dependent skip. The additional
socket-level no-A4 negotiation regression passed with the 23-test AV probe group
after that suite's collection. Ruff, Mypy (186 files) and host Node contracts
passed. Remote CI must validate the final committed tree before any live attempt.

After full validation and green CI, separately arm a single camera-3 diagnostic.
If roundtrip confirmation fails, inspect that phase rather than relaxing it to
the old meter flag. If it passes, record AV negotiation/record counts and cleanup;
do not enable a production capability from a bootstrap success alone. Keep normal
RTSP, disabled-by-default diagnostics, and no automatic retries.
