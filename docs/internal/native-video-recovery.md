# Native video restart boundaries — offline validation, 2026-09-14

## Scope and result

The offline keyframe/recovery milestone is complete for the observed historical
single-layer HEVC profile. This is **not a live native reconnect implementation**:
no camera session, production media-source change or container rebuild occurred.
The existing RTSP/intercom pipeline remains unchanged.

`media/hevc_access.py` inspects bounded complete Annex-B access units; the
transport-neutral `media/hevc_recovery.py` decides whether to emit a picture and
whether a fresh decoder epoch is required. Codec logic is not coupled to a camera
brand. Drivers still own capability and transport selection.

NAL type numbering follows the primary
[FFmpeg HEVC definitions](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/hevc/hevc.h).
The historical replay observed:

| Flow | VPS/SPS/PPS + IDR (types 32/33/34/19) | Dependent TRAIL_R (type 1) |
|---|---|---|
| flow5 | 19 | 1,118 |
| flow9, recovered prefix | 2 | 1 |

## Conservative rules

- Require a complete picture with one first-slice flag, single layer, valid NAL
  header and Annex-B boundaries. Cap an access unit at 1 MiB and 128 NALs.
- Initial connection/discontinuity requires fresh VPS, SPS and PPS, in order and
  before an IDR in the **same access unit**. IDR types 19/20 are supported by this
  gate; the capture proves type 19 only. CRA and bare IDR are not restart points.
- Do not cache parameter payloads from an old transport and prepend them to guessed
  later frames. Only three SHA-256 parameter fingerprints and scalar state persist;
  no frame queue or socket is owned by the gate.
- Parameter changes paired with a complete IDR request a fresh decoder epoch;
  repeated identical parameters do not reset a running decoder. Partial/unpaired
  configuration, malformed units and timestamp regression force reacquisition.
- Timestamps remain raw values. This gate does not translate timezones, synchronize
  audio or infer wall-clock timestamps. A live owner must set a finite no-keyframe
  deadline, close/reopen reliable transport on fatal loss, and honor decoder resets.

Header inspection is not full HEVC bitstream validation: it does not parse parameter
IDs/references or validate every slice syntax. Therefore a gate decision is not
itself proof of decodability on arbitrary hardware/profiles. Independent decoder
tests below establish the observed profile, not all models or codecs.

## Independent recovery experiments

`scripts/validate_native_recovery.py` replays the clean captured `flow5` and simulates
a discontinuity by omitting a bounded range of complete video records. A fresh
FFprobe/strict FFmpeg decoder receives only the retained recovery sample. No broken
KCP sequence is skipped: this models output after a separately re-established
transport; the known flow9 transport gap remains terminal and is rejected here.

```sh
prlimit --as=536870912 --cpu=60 -- .venv/bin/python -m scripts.validate_native_recovery re/pcapdroid/pcap.pcap --flow flow5 --drop-at 10 --drop-count 5
```

Scenarios, using one-based original video-record indexes:

| Simulated interruption | Dependent records discarded | Resumed at | New decoder output |
|---|---|---|---|
| Lose records 10–14 | 46 | 61 | 120/120, strict decode passed |
| Lose records 60–61 (including IDR) | 59 | 121 | 120/120, strict decode passed |
| Lose first IDR, record 1 | 59 | 61 | 120/120, strict decode passed |

Independent output was HEVC 640×360, matching the captured encoding header in all
three scenarios. Raw timestamp deltas from first lost picture to restart were
3,349,000 / 3,999,000 / 3,949,000 ticks respectively. This demonstrates that waiting
for the next periodic IDR can add seconds; it does not promise instantaneous recovery.

Negative control: send original records 15–134 directly to a fresh decoder without
the gate. The independent probe decoded only **74 of 120** supplied pictures and
strict FFmpeg failed. The gated sample beginning at record 61 decoded all 120.
No decoded media or raw payloads were saved, played or logged.

The existing decoder validator retains at most 8 MiB input, runs processes
sequentially with one decoder/filter thread, 512 MiB address-space / 30 CPU-s /
45-second wall limits per process, and a null output sink. This is an offline
correctness experiment, not a live latency or resource comparison with RTSP.

## Validation and remaining integration

Twenty-one new tests cover decoder-epoch transitions, unchanged/changed parameters,
gap resets, missing/unpaired configuration, CRA/bare-IDR refusal, timestamp rollback,
malformed/oversized/multilayer NALs, multiple pictures, chunk prefixes and bounded
scenario collection. No hardware or FFmpeg dependency is needed for these unit tests.

Regression checks passed: the Python suite (excluding the Node subprocess wrapper,
run separately), frontend camera-panel/PTZ contracts, Ruff and Mypy (171 source
files). The replay command rejects truncated V1 tails and samples shorter than the
requested 120 pictures, rather than presenting a partial sample as full recovery.

Next work is a bounded native receive lifecycle that preserves acknowledged KCP
state from initialization, clears all state when changing sessions, handles timeouts
and provides pacing/audio synchronization. Only then can a camera-3-only source
handoff be homologated, keeping one selected producer and preserving recordings.
Do not apply this gate to current RTSP or migrate the validated intercom implicitly.
