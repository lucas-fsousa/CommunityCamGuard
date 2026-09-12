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
