# Remote audio and native MP4 correction — 2026-10-29

User report: voice-message send failed, hold-to-speak failed, camera3 unavailable.
The observed garage message POST was403: intercom incorrectly reused provisioning's
LAN-only HTTP/WebSocket guard despite allowing the configured HTTPS dashboard
origin. Normal session authentication, origin policy and delegated `intercom`
permission now authorize these routes. Provisioning guards are unchanged. Mocked
ASGI tests cover allowed/denied/unauthenticated/cross-origin HTTP and WebSocket
requests, plus denied remote provisioning. No real camera receives their PCM.

Camera3 separately blocked at revision42 after local FFmpeg output was rejected
by `FragmentedMp4.feed` (invalid/oversized box length). The prior log did not retain
the numeric length, so it cannot prove whether that specific box exceeded2MiB or
had an invalid header. A new typed local `Mp4SizeError` records only numeric size
and allowlisted box type. Following confirmed child/socket/outer-route/motor
retirement, it can use existing sequential recovery instead of blocking forever.
Wire protocol/authentication errors and unknown cleanup are not waived.

The browser encoder now forces time-based keyframes (at most1s apart at available
frame times) and uses8Mbit/s maximum rate with a2Mbit VBV buffer. Resolution and
frame timing are unchanged. The original HEVC/AAC archive remains stream-copy,
without those browser-only settings. Parser/fanout limits stay2MiB; no buffers were
enlarged. Size-based fragmentation was intentionally avoided because fanout joins
must begin at complete keyframe fragments.

A six-frame1920×1080 noisy MPEG-TS fixture reproduces a rejected oversized fragment
with the prior command. The new command preserves all six frames/resolution and
fits the existing parser. This fixture suppresses only its finite EOF mfra trailer;
it does not relax production framing. Two real-FFmpeg tests passed under384MiB/no
swap/50% CPU (182.7MiB peak). 194 targeted tests passed under192MiB (118.6MiB peak),
and Mypy376/Ruff passed.

Deployed image `a74e902`, dashboard build `b-f7845836ef1f`. CI40978266990 and
Image40978267040 both passed. The first trusted recovery attempt rejected an MTP
TCP checksum before media readiness (`feed:47/_fail:31`) and rolled back. No wire
validation was relaxed. A second operator recovery completed normally (147.2MiB
host peak), publishing camera3 native revision53. All three cameras report online
and recording; current original MP4 files were nonempty and updated5/1.2/1.4s ago.
The app reports no OOM and zero restarts. A real authenticated MSE connection
received H264/AAC metadata plus initialization and three media fragments
(1355,250562,251213,248093bytes), within the unchanged2MiB bound.

On `https://ls-ccg.bytec.cloud`, login returned200 and authenticated empty PCM
returned422 (body validation, before driver dispatch), rather than the former403.
Cross-origin empty PCM returned403 and unauthenticated empty PCM401. Public and
local build IDs match. These production checks emitted no audio and do not claim
a new physical microphone/speaker confirmation. The short MSE check demonstrates
current recovery, not indefinite stability or the exact historical bad box size.
