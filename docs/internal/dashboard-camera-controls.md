# Camera control panel — 2026-09-12

The live tile keeps PTZ (only when supported), quality, digital zoom and player recovery shortcuts
plus one **Camera controls** button. PTZ stays outside the overlay so the operator can position
the camera while seeing the video. Audio, security and maintenance stay in the panel.
The responsive side panel groups image, audio/communication, security/alerts,
recordings/storage and maintenance. It uses the camera's friendly name and a scrollable body;
status feedback remains outside that scroll area.

## Capability semantics

The current server catalogue is read from dashboard state when opening the panel. Only advertised
writable controls and explicit audio/PTZ capabilities produce active widgets. Choice/action values
are intersected with the descriptor's allowed options, including volume, night mode and pulse
duration. The server remains the final authority if a capability expires while the panel is open.
No camera query, microphone capture, option enumeration or action happens merely by opening it.

Known functions without an active widget appear disabled with an explicit explanation: the driver
has not released them, and support may be absent or unverified. The API does not currently expose
enough per-feature reasons to distinguish those cases reliably; the UI must not invent a reason.
The SD row explicitly says dashboard integration is pending and does not assert a card/slot exists.
Server recordings are a separate active entry, navigating to the existing view with this camera's
opaque ID selected. No SD endpoint or native control was added.

## Lifecycle and modules

- `camera-controls.js`: grouping, placeholders, navigation and panel lifecycle.
- `camera-control-actions.js`: existing semantic writes, dynamic options and weekly schedule forms.
- `audio-message.js` / `push-to-talk.js`: unchanged recording and streaming implementations.
- `live-cameras.js`: player and live shortcuts; almost 290 control-layout lines moved out.

Opening locks background interaction/scroll and focuses the close button. Backdrop clicks do not
dismiss. Explicit close restores previous inert/scroll state and focus. Nested audio/schedule
dialogs stay above the panel, which becomes inert until they close; its observer is disconnected
on dismissal. Late option/schedule responses cannot open a dialog after its panel was closed.
The recordings navigation intentionally closes the panel before changing view. Existing siren
and camera-removal confirmations remain in force; there is no automatic write retry.

## Validation and rollout

Dependency-free Node DOM tests (64 MiB JS heap) cover five sections, disabled placeholders,
zero opening requests, latest-catalogue use, allowed-value filtering, semantic write payload,
backdrop/close/focus/scroll restoration, nested-dialog inertness and filtered recordings navigation.
1,320 existing tests passed under a 512 MiB address-space bound; the additional Node-backed test
was run separately because V8 reserves more virtual address space than that bound (not physical
heap memory). JavaScript syntax checks and Ruff passed. No browser/media process was launched;
pixel layout on desktop/mobile still needs visual review.

The frontend is bind-mounted, so these files are already served without a container restart.
The automatic build endpoint reported `b-2e3e39c2cb92`; the new module returned HTTP 200.
App and media container start times remained unchanged. No camera command was sent.

Follow-ups: visual/mobile review, richer backend capability-unavailability reasons, SD UI when the
driver listing is homologated, and additional semantic widgets as drivers implement them.

## Applying feedback and failed-light diagnostics — 2026-09-12

Writes now display a prominent spinner with localized text (“Aplicando modificação na câmera…” /
“Applying changes to the camera…”). Static selectors, weekly schedules and alarm-sound selection
share that feedback. The active input remains disabled during its request; the spinner is removed
in cleanup on success or error. Existing success/error text remains; there is no automatic retry.
Reduced-motion preference disables spinner rotation without hiding the status message.

Read-only audit of retained app-container access logs found white-light PUT failures (502) at
04:35:23Z, 04:35:36Z and 04:35:52Z, interleaved with a 200 at 04:35:40Z on September 12.
Orientation/night-vision/volume requests in the same window returned 200. This is HTTP evidence,
not independent physical confirmation. The old logs have no response bodies or protocol-stage
diagnostic, so they cannot establish whether these failures were preflight, write or readback.
The host clock was already beyond that window; a short `--since` filter missed the events.
The old `data/app.log` is not the active container log.

The floodlight writer now logs only the opaque camera ID, a fixed phase label and integer native
error (or unknown): session, preflight, write exchange, missing reply, rejection or unconfirmed
readback. It never logs credentials, IPs, payloads or requested values. Missing write replies are
reported as unknown outcome, not falsely as camera rejection. This improves future diagnosis;
it does not claim to fix or retrospectively identify the recorded failures. No camera writes
were repeated during investigation. Tests verify phases, redaction and single-shot writes.

Deployed as `b-ca458ed9dafe` with a 512 MiB / one-CPU build and app-only recreation; go2rtc kept
its original start time and neither container reported OOM. The image passed an isolated
network-disabled import/OpenAPI smoke check before deployment. Validation: 1,384 tests under
the Python address-space bound plus the separate 64 MiB Node DOM harness, Ruff and mypy
(159 source files). Native PTZ ownership/route modules are included but remain unregistered.
