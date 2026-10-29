# Driver-owned native recording admission —2026-10-29

The production driver boundary now accepts a server-owned recording request:
`CameraDriver.native_media_offer(camera, archive=NativeArchiveSpec(...))`.
The generic recorder supplies the opaque camera ID, local root and configured
segment duration with `Recorder.native_archive(camera_id)`. Building this plan has
no filesystem, process or network effects. UTC layout and camera ownership are
derived from its immutable fields, not supplied by browser paths or vendor IDs.

The base driver declines unsupported offers. Yoosee still requires the existing
exact-unit native-video review, current identity and matching enrollment. An
archive request additionally needs explicit rollout of a read-only `native_av`
proof covering `hevc`, `aac_lc_16k_mono` and `original_archive`. Enrollment, a
video-only proof or a property bit cannot supply this permission. Missing/revoked
proof returns no offer, never a video-only source with silently lost recording.
The existing separately reviewed motion scope and clean-recovery lease remain.

An admitted factory creates native A/V and an original-codec archive using one
camera capture and one FFmpeg child. Segment duration now travels from the server
plan through the source/decoder to FFmpeg, rather than always using60s. The lower
level experimental API keeps its60s default for compatibility. Epoch isolation,
UTC folder preparation and confirmed retirement before replacement are unchanged.

128 focused tests passed under256MiB/no swap/50% CPU (155.1MiB peak). They cover
driver refusal, incomplete/writable/revoked A/V proofs, cross-camera destinations,
construction without IO, forwarding60/300s through the pipeline and original
HEVC/AAC output from real bounded local FFmpeg runs. The short file fixtures do
not certify a300s rollover or continuous recording. Mypy360 files passed under
256MiB (91.1MiB peak); Ruff passed.

This closes the normal-driver A/V/recording-offer gap. It does not activate native
boot by itself. Durable bootstrap/retirement, identity validity maintenance and
native storage pause/resume integration must be connected before permanent use;
normal production remains on the established sources in the meantime.

## Driver-offer operator path

The host trial can now take `--driver-av-evidence docs/...md` with `--native-av`,
and no motion scope. This is an explicit operator attestation that the referenced
bounded repository document covers native A/V for this exact target. The private
clone collects fresh correlated identity properties, records the document digest
and the two read-only media proofs in that clone, then invokes the ordinary
driver offer with `Recorder.native_archive(camera_id)`. It does not construct a
special A/V source in place of the driver. No grants are copied to the primary
database or another camera; the resulting source retains normal driver validation.

Source planning/observation moved into small diagnostic modules rather than
growing the operator script.46 focused tests passed under192MiB/no swap/50% CPU
(135.5MiB peak), including document scope, fresh identity refusal, per-camera
recording policy and trial argument guards. Mypy362 files passed. The live
driver-offer recording acceptance is still pending at this checkpoint.

CI on the initial archive change exposed a pre-existing ASGI test teardown race
(`CancelledError` after a received frame). The fixture now sends disconnect and
waits for actual handler completion and reader release before TestClient teardown.
60 lifecycle/socket tests and eight isolated repetitions passed under192MiB,
zero swap and50% CPU. No retry/ignore rule or production error suppression was added.
