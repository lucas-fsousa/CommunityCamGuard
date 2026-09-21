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

## Camera-3 checkpoint after commit `6bcd0a5`

Remote CI `35140199981` succeeded for the exact commit. Image `934b2924d5de`
was built with the cached builder capped at 512 MiB / one CPU. Registry MAC and
durable enrollment matched the private camera-3 inventory before activation.
One authenticated loopback POST returned **HTTP 502 after 4.18 seconds**:

```json
{"phase":"media_meter","direct_acknowledged":false,"meter_acknowledged":true,"datagrams":2,"meter_roundtrip_confirmed":false}
```

The log reported `AvBootstrapError`, `release_attempted=True`,
`release_acknowledged=True`, `elapsed_ms=4171`. AV INIT was not sent. This is still
a bootstrap failure, not a failed codec/decoder. It does not establish whether the
two packets were requests only or unmatched replies. No immediate retry followed.

The ignored override was reset to disabled/empty before the POST. The app was
recreated after collecting the safe response/log in the same command sequence;
runtime configuration confirmed disabled/empty and `/health` returned 200. Two
post-restart samples showed exactly one producer and advancing received bytes for
each of the three base streams. This proves ingress progress, not browser playback.
Only the app was recreated (brief recorder interruption); go2rtc and unrelated WSL
containers retained their uptime. No audio, movement, light or siren was requested.

### Next diagnostic evidence (not deployed in this checkpoint)

The experimental bootstrap now collects a bounded set of fixed labels for
route-matched meters: request/reply/unknown kind, wrong channel/record length/role/
call, unsent sequence or unmatched timestamp. The HTTP failure includes these as
`meter_observations`; no payloads, IDs, endpoints or numeric sequence/timestamp
values are exposed. Only the experimental path collects them. Gate, timeouts and
legacy behavior remain unchanged. Synthetic tests assert every label and reject
foreign peers, broker traffic, wrong routes and invalid checksums.

Validation of this classification change: capped Python 3.12 container completed
1,763 tests with one Node-dependent skip; host Node contracts passed separately.
Ruff and Mypy (187 files) passed. No further live invocation was made.

Next live attempt should use these labels to distinguish one-way meter requests
from rejected roundtrip replies. Do not infer the cause from the broad old flag,
disable the gate, add blind retries or mark native video homologated.

## Classified camera-3 checkpoint — 2026-09-21

Commit `e09a9d3` had terminal CI success (`35141064090`) before deployment.
Image `e1b283b122c8` was built with the same 512 MiB / one-CPU ceiling; only
the app was recreated. One separately armed loopback attempt returned HTTP 502
after **4.85s**:

```json
{"phase":"media_meter","direct_acknowledged":false,"meter_acknowledged":true,"datagrams":2,"meter_roundtrip_confirmed":false,"meter_observations":["reply","wrong_call","wrong_role"]}
```

The safe log recorded `AvBootstrapError`, B9 release attempted/acknowledged,
and 4,743 ms elapsed. The observed route-matched replies did not produce a channel,
record-length, sequence or timestamp rejection. The remaining mismatch is in our
interpretation/validation of the role and optional call-ID fields. This does not
establish their actual values or prove whether the camera or our parser is wrong;
no payload or raw field values were collected. AV INIT was not sent.

No second attempt followed. The ignored override and running configuration were
restored to disabled/empty; health returned 200. Normal RTSP retained one producer
per base camera. App recreation briefly interrupted recording ownership; go2rtc
and unrelated projects were not restarted. No sound, light or PTZ request was made.

Next: compare the SDK's meter **reply** extension layout with its request layout
and historical PCAP. Do not assume the request extension has identical semantics
in a kind-2 reply, or remove these checks without documenting that evidence.
Native live video remains unhomologated; classification is now deployed, disabled.
