# 0008 — Reboot and two-way audio outside standard ONVIF

**Status:** accepted · **Date:** 2026-07-27

## Context

Two wanted controls — **software reboot** and **two-way audio (talk)** — looked reachable over ONVIF:
the device service answers `GetDeviceInformation`, and a service sweep showed the camera advertises
`media_service` and `deviceio_service` with `AudioOutputs`/`AudioSources` tokens (it has a speaker and
mic). Both turned out to be dead ends on this firmware — **proven by testing, not assumed:**

- **`SystemReboot`** — plain, with WS-Security, and with WS-Addressing — all just close the connection;
  the camera never reboots (monitored ports 5000 and 554 stayed up 45–120 s each time). The ONVIF
  stack is partial: `GetScopes`, `GetSystemDateAndTime`, `SystemReboot`, and the audio-output ops all
  "close without response" (= not implemented).
- **Two-way audio** — `GetAudioOutputs*` / `GetAudioDecoderConfigurations` close without response, and
  the standard RTSP **backchannel** (`Require: …/backchannel`) is ignored (SDP byte-identical, no
  `sendonly` audio track).
- **Port 50000** accepts the TCP connection but stays completely silent (no banner, no reply to a GET
  or a zero-frame hello) — it's the vendor **Gwell P2P** channel, waiting for a specific binary
  handshake it only speaks with the vendor app.

## Decision

**Reboot and two-way audio live only in the Gwell P2P channel (port 50000)** and are **shelved to the
roadmap** — cracking them needs a packet capture of the vendor app to reverse the handshake, not more
ONVIF probing. We keep the compliant-camera path as the foundation: `control/device.py` (`reboot()` =
standard ONVIF `SystemReboot`, correct for compliant cameras; `info()` = model/firmware, which works
and is stored). No reboot button in the UI on these units — it would be a dead button, and
`GetDeviceInformation` responding ≠ `SystemReboot` honoured, so that false capability signal was
removed.

The service sweep's **concrete win**: `media_service` `GetStreamUri` returns the camera's **real** RTSP
paths, so the driver probe asks the camera instead of trusting guessed paths (falls back to the
hard-coded list when absent).

## Consequences

- The team stops rabbit-holing ONVIF for reboot/talk — the boundary is evidence-based.
- Controls degrade honestly: unsupported → HTTP 501, no dead UI (see ADR 0001, 0007).
- Also found in the sweep and deliberately **not wired**: `Get/SetVideoEncoderConfigurations` — a
  decoy that reports H.264 720p while the real stream is HEVC 1080p; the ONVIF encoder metadata is
  disconnected from the real encoder, so a quality feature built on it would be fiction (see ADR 0005).

## 2026-09-01 amendment — proprietary RTSP talkback recovered

The original conclusion remains correct for standard ONVIF and its advertised SDP, but is no longer
correct for the firmware's complete RTSP surface. Static analysis of the matching
`RtspServer_0.0.0.2` executable recovered an undocumented `USER_CMD_SET` method. Its
`AudioCtlCmd: OPEN|CLOSE` control enables the speaker path fed through RTP-over-TCP interleaved
channel 2 (320-byte payloads). Although the RTP header retains payload type 8, the proprietary
handler bypasses the advertised capture codec: its mixer reads each payload directly as 160 signed
16-bit PCM samples. The archived firmware revision configures 8 kHz, but camera 3 firmware 40.1.14
advertises and consumes 16 kHz on the live path. The driver therefore splits each canonical 640-byte
browser frame into two 320-byte/10 ms RTP packets. It completes the session with explicit cleanup.

The dashboard now prefers this LAN-only RTSP backchannel and retains Gwell P2P only as a fallback
for enrolled cameras without usable local RTSP material. Standard `Require: .../backchannel` is
still ignored, so this is deliberately isolated in the Yoosee driver rather than presented as a
generic ONVIF capability. The firmware also accepts `USER_CMD_SET` without Digest authentication;
the app's existing local-only intercom boundary is therefore a security requirement, not merely a
product choice.

Both production dashboard paths were then exercised against camera 3 through the rebuilt app:

- the recorded-message HTTP endpoint delivered 250/250 frames (5.0 seconds), completed the direct
  LAN session and released the route;
- the push-to-talk WebSocket delivered 100/100 frames (2.0 seconds) with the same clean teardown.

The application diagnostics measured non-silent PCM in both cases and physical listening confirmed
speaker output, but encoding those blocks as G.711 A-law produced distorted, unintelligible audio.
Static inspection of the firmware's 320-byte PCM mixer exposed that mismatch. Controlled live tests
then isolated all remaining variables: little-endian raw PCM at 16 kHz was recognizable, while
big-endian was severe noise and 8 kHz was degraded. A 100 ms/ten-packet preload plus absolute-clock
pacing absorbed WSL scheduler delays and made music clearly recognizable with almost no cuts. CLOSE
waits for the queued audio to drain. Neither path uses the vendor app, cloud transport or the
experimental P2P fallback.

Final dashboard homologation used build `b-e8c8be95de38`. The operator physically tested the audio
on all three current units, covering camera firmware 40.1.14 and 40.1.22; recorded-message and
hold-to-speak playback were intelligible and passed. This closes codec, byte order, sample rate,
packet cadence and browser integration as a delivered feature. Remaining work is endurance across
repeated maximum-duration sessions, simultaneous listen/talk characterization and adapters for
other camera families—not basic Yoosee speech delivery.
