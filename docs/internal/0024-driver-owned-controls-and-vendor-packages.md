# 0024 — Driver-owned controls and vendor packages

**Status:** accepted, P2P verticalization implemented · **Date:** 2026-08-28

## Context

ADR 0001 introduced a useful RTSP/ONVIF driver registry, but proprietary Yoosee work later grew in
top-level `provisioning` and `vendor_p2p` packages. The HTTP router imported those implementations
directly and advertised controls from the presence of P2P material rather than from the selected
driver. A second proprietary family would therefore require changes across API, frontend and core.

The persisted public `camera_id` already lets the application select a camera without exposing a
native identifier. The same indirection must select behavior: an enrollment, open port or vendor
string alone must never grant another driver's controls.

## Decision

- Every controllable operation is represented by a semantic, vendor-neutral descriptor and result.
  HTTP accepts values such as `white_light=true`, never an opcode, thing-model path or raw payload.
- An application service resolves `camera_id -> camera -> driver`; only that driver may describe,
  read or write controls for the camera.
- Camera-family code is organized as a package under `drivers/<family>/`. The Yoosee package owns
  the adapter from semantic controls to its encrypted P2P enrollment and protocol operations.
- The recovered GAT/IoTVideo transport, crypto, authentication, RTSP setup and typed feature
  operations live under `drivers/yoosee/p2p`; there is no application-global `vendor_p2p` package.
  Generic services may depend on driver contracts, while Yoosee-specific onboarding adapters import
  this vertical implementation explicitly.
- P2P errors, allowlists and typed results live in `p2p/contracts.py`, independent from UDP session
  orchestration. `p2p/client.py` retains compatibility reexports while feature adapters migrate to
  the stable contracts module.
- Common IoTVideo frame construction, randomized flags, checksums and mode-1/mode-2 finalization
  live in `p2p/wire.py`, giving protocol codecs a socket-free dependency base.
- The allowlisted GDM property-read codec (`B7`, `B8` and brokered `AA`) lives in
  `p2p/model_protocol.py`; JSON/path parsing no longer shares a module with UDP rendezvous loops.
- Direct rendezvous packet construction (`A4`, `CA`, `CB`) and `A3` peer parsing live in
  `p2p/rendezvous_protocol.py`, separate from the code that owns sockets and retry budgets.
- Access-node discovery, certification, initialization, TermDNS and heartbeat frame codecs live in
  `p2p/access_protocol.py`; their UDP lifecycle, timeout and retry orchestration live in
  `p2p/access_session.py`.
- Shared UDP deadline handling, access-node frame decryption and reliable acknowledgements live in
  `p2p/session_io.py`. Feature modules consume that boundary directly instead of private client
  helpers.
- Direct camera NAT rendezvous and handshake retry orchestration live in
  `p2p/rendezvous_session.py`; packet construction remains isolated in
  `p2p/rendezvous_protocol.py`. A completed route is closed with the SDK's distinct brokered B9
  P2P-inner hangup; AV STOP/CLOSE and closing a local UDP socket are not route teardown.
- Allowlisted thing-model read retries, correlation and report handling live in
  `p2p/model_session.py`; JSON and frame parsing remain socket-free in `p2p/model_protocol.py`.
- Scalar thing-model write framing/correlation and bounded UDP exchange live in the internal
  `p2p/model_write_protocol.py` and `p2p/model_write_session.py` layers. Feature modules still own
  every fixed path, semantic allowlist, preflight and readback; this helper is not a public command
  tunnel and rejects object/array payloads.
- Durable enrollment selection and authenticated brokered control-session initialization live in
  `p2p/camera_session.py`. Feature modules call this explicit boundary instead of a private helper
  on the compatibility client. It deliberately does not open an A4/CA/CB direct-media route.
- `p2p/client.py` is retained as a compatibility facade for the three read/probe operations and
  historical protocol reexports. Executable architecture tests prevent protocol/session layers
  and feature modules from importing that facade or accumulating new operation implementations in
  it.
- Encrypted Yoosee account/session persistence lives in `drivers/yoosee/account_store.py`. It keeps
  the existing `vendor_accounts` table for an in-place upgrade, but no longer presents a
  manufacturer-specific repository as a generic `db` module.
- The driver-generated control catalog is authoritative for API/UI gating. The old
  `vendor_controls` response is a temporary compatibility projection of that catalog.
- The canonical HTTP boundary is `/api/cameras/{camera_id}/controls/{control_key}`. It accepts only
  a scalar semantic value and the application service rejects keys or operations absent from that
  camera driver's catalog before transport dispatch. The older `/api/vendor-controls/...` routes
  remain temporarily for client compatibility.
- The bundled dashboard consumes only the authoritative `controls` descriptors and canonical
  camera-control route. Compatibility fields/routes are no longer dependencies of current UI code.
- The application service admits at most one control operation per opaque camera ID. Reads,
  dynamic-option queries and writes share the same non-blocking lock; overlap returns HTTP 409
  instead of opening competing P2P sessions or racing two read-before-write transactions. Different
  cameras remain independent. The Yoosee driver additionally serializes all entry points by native
  device ID so independent compatibility routes cannot race one read/modify/write transaction.
  Its stateless UDP list-service query is retransmitted at most three times within the operation's
  existing deadline. This retry happens before a camera route or action exists, so it cannot replay
  a device write; it only tolerates a lost discovery datagram.
- Thing-model (`B7`, `D2`, `AC`), passthrough (`B9`) and resource-service (`C0`) frames include the
  destination camera ID and are routed by the certified access node. Production originally also
  opened A4/A3 plus CA/CB before every control. The SDK's direct-link teardown is separate from
  closing the host UDP socket, so those disposable media rendezvous accumulated until camera 3
  commonly rejected its fourth fresh route. Static SDK analysis and a live read-only run proved the
  direct link unnecessary: six distinct roots returned error zero in one brokered session at
  60–110 ms each without A4/CA/CB. Controls now stay brokered; direct rendezvous remains isolated to
  explicit reachability probes and future media sessions. The obsolete five-second pacing delay was
  removed. After rebuilding the production container, eight fresh canonical HTTP reads completed
  consecutively with HTTP 200, verified state and `direct_connection=false` in 1.8–3.4 seconds each;
  the former fourth-route failure did not recur.
- Native static analysis recovered the direct-link teardown exactly: B9 mode 2/proc 3, inner type
  zero, the 24-bit MTP link ID in both route fields, and reason `0x4e22`. The receiving SDK locates
  the MTP channel by that first route field and resets it; the sender uses the same ID stored in its
  MTP session. Production route probes now emit this idempotent teardown once and wait only for its
  bounded transport ACK. Four consecutive read-only camera-3 route cycles completed A4/A3/CA/CB
  and each ACKed the hangup in 4.7–5.4 seconds, including the previously unreliable fourth route.
- The rebuilt production endpoint independently completed an authenticated camera-3 direct-route
  probe with broker acknowledgement, advertised route, six direct datagrams and a completed
  handshake while reporting that neither media nor a command was opened. The laboratory media
  harness can now load one exact device from the app's encrypted enrollment without copying a
  token or using Frida. Its first silent camera-3 intercom run completed the MTP meter, KCP AV
  ACCEPT/START, the reported 16 kHz audio/640x360 video profile, legacy microphone
  START/STOP/CLOSE, and the final B9 teardown with ACKs throughout. It sent zero audio frames.
- The first production-integration slice keeps media details below the driver boundary: pure,
  socket-free `media_protocol` and `stream_protocol` modules own checksummed MTP framing,
  coalesced KCP segments, meter/AV control records, cookie-keyed StreamPipe TLVs, the negotiated
  v1 codec header, legacy talk state and encoded-audio records. Historical golden datagrams and
  malformed-input tests protect these codecs before any long-lived media session or public talk
  API is introduced.
- Direct-route results retain the randomized calling attempt only inside the driver, so the media
  layer can reuse its link ID, call ID and cookie without exposing them through the sanitized route
  result. A bounded `media_session` now owns the camera-facing mode-1 A4 and MTP meter exchange;
  it validates the exact peer and route identities, acknowledges camera meter requests and does
  not yet send AV or microphone frames.
- AV initialization is a separate bounded session layer. It sends the four native INIT attempts,
  acknowledges every validated camera KCP PUSH, records ACCEPT/START proposals and decodes the v1
  codec header through the pure StreamPipe layer. This stage still does not echo AV START or emit
  talk/audio records; its result carries only typed state needed by the later intercom lifecycle.
- The legacy intercom-control layer accepts only an acknowledged v1 AV session. It sends AV START,
  the recovered capture header and talk ON, but has no audio-frame input at this stage. Talk OFF
  and AV CLOSE run through nested cleanup blocks even when negotiation or an acknowledgement fails;
  every camera KCP PUSH is itself acknowledged. This makes the zero-audio lifecycle testable before
  browser capture and AMR encoding are attached.
- A device-scoped intercom orchestrator now composes brokered authentication, direct rendezvous,
  meter, AV and the zero-audio legacy lifecycle behind the same serialized stale-session renewal
  boundary as other Yoosee operations. The exact route is closed in `finally` on success, a rejected
  stage or an exception. This is still an internal proof operation: it is not advertised as a
  camera control and has no HTTP/UI surface.
- After rebuilding the container, that production orchestrator completed live against camera 3 in
  11.6 seconds: direct handshake, MTP meter, AV acceptance, capture header, AV START, talk ON,
  talk OFF, AV CLOSE and B9 release all reported acknowledgement, with stream version 1. The
  operation cannot accept audio frames, so this validation was silent by construction. The base
  RTSP producer, H.264 restream and recorder remained active and a subsequent recording segment
  was observed.
- AMR-NB encoding is driver-owned rather than delegated to the browser or an ignored laboratory
  binary. The Docker image installs the reproducible OpenCORE runtime, while a bounded streaming
  wrapper keeps one encoder state per utterance, accepts only signed 8 kHz mono PCM16, emits the
  APK's 20 ms mode-7 frames, caps session duration and rejects unexpected native output. This
  encoder is not connected to the camera transport until the audio send lifecycle has equivalent
  cleanup/backpressure coverage. The deployed container encoded 0.2 seconds of silence into ten
  exact 32-byte mode-7 frames through the installed OpenCORE runtime.
- The internal legacy audio sender now enforces that ten-second/raw-mode-7 boundary again at the
  transport edge, emits one v1 record per 20 ms, waits for each KCP acknowledgement before advancing
  the queue, acknowledges reverse camera PUSH traffic and aborts the remainder on bounded ACK loss.
  Its core is now an incremental sender that accepts one complete frame at a time, exposes progress
  snapshots, rejects input after close/abort and paces from the previous actual send so a stalled
  producer cannot cause a catch-up burst. The proven batch API delegates to this core without changing
  its result or deadline semantics. The enclosing lifecycle still executes talk OFF and AV CLOSE after
  an audio failure; a continuous browser session still needs its own WebSocket/lifecycle boundary.
- The legacy control lifecycle is incremental as well: its explicit start phase performs AV START,
  capture header and talk ON; each subsequent call hands exactly one AMR frame to the bounded sender;
  and idempotent close performs talk OFF and AV CLOSE once. The recorded-message wrapper delegates to
  this stateful path, preserving its sequence numbers, cleanup behavior and public result. Opening the
  direct route and owning it across browser messages remains a separate high-level session concern.
- That high-level concern now has an internal module with no public route: it owns the UDP socket,
  direct route, media/AV negotiation, incremental OpenCORE encoder and control lifecycle until close.
  PCM chunks remain capped at ten seconds; each emitted AMR frame must be acknowledged; deadline or
  ACK failure aborts the stream; and nested cleanup closes codec, talk state, AV, B9 route and socket.
  The whole trusted chunk iterator runs inside the existing stale-access renewal/device mutex. This
  is infrastructure for a bounded WebSocket worker, not authorization to expose vendor details or
  hold a P2P route directly from the event loop.
- Incremental audio is also a distinct driver-neutral capability. The application service resolves
  one camera/driver, acquires that camera's nonblocking operation lock for the full iterator lifetime,
  and validates nonempty complete 320-byte PCM-frame multiples under the same ten-second ceiling.
  The Yoosee adapter alone maps that iterator to its internal route orchestrator. Other brands remain
  unsupported unless their own driver opts in; camera JSON advertises recorded messages and streaming
  separately. No WebSocket route consumes this contract yet.
- Encoder and sender are now joined only through an internal, device-serialized orchestrator. It
  encodes bounded PCM before allocating a route, retains one-shot stale-access renewal, and releases
  the exact B9 route in `finally`; the silent probe remains a separate entry point that cannot accept
  audio. A production-container run against camera 3 sent 0.2 seconds of silence as ten AMR frames:
  direct handshake, meter, AV v1, header, talk ON, all 10 frame ACKs, talk OFF, AV CLOSE and B9 release
  completed. All three RTSP transcoders and recorders continued without replacement or interruption.
- A second camera-3 run sent 30 acknowledged frames containing a low-level 740 Hz/0.6-second tone
  while FFmpeg consumed the already-preloaded local restream (not another camera connection). The
  camera microphone returned a continuous approximately 0.65-second 740 Hz band at the matching
  instant, peaking 27.6 dB above the capture baseline. This closes server-to-speaker playback with
  acoustic loopback evidence; human speech intelligibility and browser capture remain separate work.
- The first public audio boundary is driver-neutral recorded-message delivery, not a vendor command
  endpoint. It accepts only complete 8 kHz/mono/s16le 20 ms frames, caps bodies at ten seconds,
  requires the same authenticated trusted-LAN checks as camera controls, dispatches through the
  selected driver's explicit support method and shares the per-camera operation lock. Blocking P2P
  work runs outside the FastAPI event loop. Unsupported drivers fail closed and public results omit
  enrollment, route, codec and peer details. Continuous push-to-talk remains a distinct future
  session contract rather than overloading this bounded request.
- The dashboard's recorded-message client is an isolated semantic module. It creates an
  AudioWorklet only after an explicit click, keeps Float32 capture and converted PCM in memory,
  averages the native sample windows down to 8 kHz, trims to complete 160-sample frames and stops at
  ten seconds. Stopping does not transmit: local preview and confirmed send are separate actions,
  and opening/closing the modal never contacts the camera. Browser microphone policy still requires
  `localhost` or trusted HTTPS; the API retains its independent trusted-LAN/authentication checks.
- The siren is exposed only as a bounded semantic pulse (2, 5 or 10 seconds). Its typed Yoosee
  adapter requires a confirmed OFF preflight, never retries ON, sends OFF unconditionally with a
  dedicated cleanup budget, and reports success only after the AD response and final OFF readback.
- Speaker volume is exposed as the APK's semantic 0/25/50/75/100% positions. The driver keeps the
  raw 0..10 representation private, normalizes reads using the APK buckets and requires exact raw
  readback after a change. Camera 3 completed a canonical-API 75→50→75 cycle with transport ACK,
  error zero and exact readback; a final independent read confirmed 75% without playing audio.
- White-light passthrough keeps distinct request/response envelopes. Requests always use the proven
  `01 ff 00 00` prefix; responses accept only that legacy echo or camera 3 firmware 40.1.14's
  observed `01 00 00 00` response-direction prefix. The parser still requires the fixed type and a
  binary state and does not expose a generic passthrough surface.
- Night vision is exposed as the semantic automatic/daytime/night choice. The current Yoosee
  profile maps only to its physically proven legacy 0/1/2 scalar with preflight and exact readback;
  the unadvertised V2 support/selection bitfield is not accepted through the production boundary.
  Camera 3 completed a canonical-API automatic→daytime→automatic cycle with verified cleanup;
  validation deliberately did not select the IR/night state.
- Orientation is limited to normal/180° and was validated through the canonical production API on
  camera 3 with an idempotent normal baseline, verified inverted write and verified normal cleanup.
  Frames captured from the local go2rtc restream independently matched the 180° transform and then
  the original orientation; no additional RTSP producer was opened on the camera.
- Smart protection exposes only its boolean guard master switch. Reads and writes address the
  proven leaf, so toggling protection cannot overwrite sensitivity, detector flags, automatic
  reactions or the weekly schedule stored alongside it in `guardParm`.
- Weekly automation uses a shared immutable `WeeklySchedule` domain value and a
  `weekly_schedule` descriptor kind. The HTTP boundary validates `HH:MM`, known unique weekdays
  and a non-empty day set before dispatch. Yoosee maps that type to the complete Sunday-first
  `guardParm.plan`; the dashboard reads it only when its dedicated modal is explicitly opened.
- Alarm-voice catalogue decoding is socket-free and driver-owned. Only validated type-4 AMR
  resources survive parsing; signed URLs/tokens are discarded, full vendor `resId` values are
  private non-repr fields, and the UI selector uses semantic `system-N`/`custom-N` keys
  resolved against a fresh catalogue rather than accepting a resource id from HTTP.
- Its resource-service codec fixes the only allowed request to POST `resfile/queryres`, rejects
  extra fields/resource types and implements bounded C0/C1 framing plus checksummed fragment
  reassembly/ACK. A narrow memory-safe decoder implements only the APK's QuickLZ 1.5 level-2
  profile, caps output to the gute protocol limit and rejects unsupported levels and malformed
  lengths/references. It is an independent implementation: production neither links nor copies the
  GPL laboratory oracle.
- The corresponding resource-service session permits one fixed request in flight, applies bounded
  retries/deadlines, acknowledges each validated fragment and exposes compression as an explicit
  decoder seam. The bounded decoder is the internal default; callers can explicitly disable it,
  and any decode failure or wrong output length keeps the response away from the JSON parser.
- The alarm-catalogue orchestrator opens the exact enrolled camera session, queries system and
  custom sources separately and returns only sanitized option metadata. It was validated read-only
  against camera 3 with four Portuguese system resources and an empty custom catalogue.
- Runtime choices use the vendor-neutral `ControlOption` contract and are available only when a
  driver advertises a `choice` descriptor with `dynamic_options`. The LAN-only, authenticated,
  no-store options endpoint exposes semantic value/label/group/detail fields; a later write must
  resolve the semantic value against a fresh catalogue before the driver can access its private
  native identifier.
- The selection codec accepts an internal `AlarmVoiceResource`, never a raw string from HTTP. It
  performs `resFile` preflight, is idempotent by the stable type/number prefix and requires fresh
  logical-number readback. The driver registers it only behind the dynamic option lookup: every PUT
  resolves the semantic key through a fresh catalogue. Camera 3 completed a canonical-API
  `Zumbido 1→Zumbido 2→Zumbido 1` cycle with ACK, error zero and exact readback; no siren/action or
  audio playback was invoked, and a final independent read confirmed the original selection.
- In-repository drivers remain explicitly registered. Automatic filesystem imports are rejected:
  registration order affects detection and implicit imports make startup and security auditing less
  predictable. Python entry points may be added later if out-of-tree plugins become a real need.
- Registry construction fails fast on duplicate keys, a missing/misplaced generic fallback, or an
  onboarding adapter whose declared `driver_key` differs from its owning driver. A bad plugin can
  no longer silently replace routing entries in the key index.
- A family driver may contain model/firmware profiles. We do not create one application-level
  driver instance per physical camera, nor assume every model of a brand shares one wire contract.

## Consequences

- Adding an implementation of an existing semantic control no longer changes the HTTP router or
  frontend. Unsupported cameras fail through the driver contract and never inherit another
  family's P2P features.
- Vendor transports can keep rich typed internal results while returning a stable public result.
- New semantic control kinds still require an intentional contract/UI addition; this is preferable
  to an unsafe generic command tunnel.
- ADRs 0025–0027 subsequently remove MAC from media, recording and registry identity. The proprietary
  P2P implementation and account persistence are now inside the Yoosee driver. ADR 0028 completes
  the next boundary: generic API/startup code reaches factory provisioning through a driver-owned
  onboarding port rather than importing the Yoosee implementation.
