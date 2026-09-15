# Native AV meter responder and reserved diagnostic — 2026-09-15

## Meter evidence and implementation

A bounded streaming scan of the existing ignored `re/pcapdroid/pcap.pcap` found
MTP meter frames of 74/78 bytes, request/response kinds 1/2, channel types 3/4,
and requests with roles 1/2/3. The 78-byte variant carries a call ID; the 74-byte
variant does not. Only aggregate shape counts were printed; no endpoint, token,
device ID or payload was logged. This historical capture does not prove the
current camera's timing, firmware behavior or need for unsolicited keepalives.

`p2p/av_meter.py` responds only to channel-4 requests from the pinned endpoint
with the expected link, source device and destination access identity. Record
length and role must match supported shapes; a call ID, when present, must match.
The shorter observed form is correlated by the remaining route identity, not by
inventing a call ID. MTP checksum verification uses the existing parser.

The responder reuses `build_media_meter_ack()`, already used during media bootstrap.
It does not infer a new response schema from the capture. Requests may retry their
ACK, but only 64 responses are allowed per probe; overflow is terminal. ACKs are
never ACKed. No periodic timer or unsolicited meter request was introduced.
Responses share the probe's time/traffic budgets and do not establish AV readiness
or refresh its media-progress deadline. The result reports the response count.

## Internal reserved invocation

`diagnostics/yoosee_av.py::run_reviewed_native_av()` accepts a trusted operator's
reviewed camera-ID/device-ID pair and fixes reception to three seconds. It verifies
the registry and durable enrollment under the **same in-process operation lock**
used by dashboard PTZ starts, voice and semantic controls. Contention fails without
opening a route; the lock covers preparation, media and B9 cleanup and releases
even on exceptions. PTZ STOP keeps its existing bypass behavior.

This brand-specific composition intentionally lives outside generic HTTP/services.
It does not register a public control, change advertised capabilities or change
RTSP. The reviewed pair is an explicit trusted input, not a hardcoded per-unit
driver capability policy. The operator must select camera 3 for the first run.

**The function must execute inside the server process.** A host CLI or `docker exec`
Python process would have independent locks and is not a safe substitute. It does
not exclude other processes, background capability reads, reboot/registry operations
or the existing RTSP producer. There is currently no HTTP/CLI trigger or automatic
background job, and no live camera invocation was performed.

## Validation and remaining work

Eleven meter cases cover both lengths, route/call/identity mismatches, other channel,
invalid lengths/checksum, wrong peer, duplicate budget and probe integration. Five
diagnostic cases verify the shared lock, fixed duration, target rejection, enrollment
rejection and exception cleanup. The 256-test selected regression group passed;
six architecture tests passed after placing the diagnostic outside generic services.
Ruff and Mypy (185 backend source files) passed. Python checks were serial with a
512 MiB address-space cap; no browser, emulator, decoder or container rebuild ran.

Next: provide a narrowly gated, authenticated same-process operator invocation,
confirm the reviewed camera-3 association and observe one three-second run alongside
RTSP health. Report actual negotiation/media/CLOSE/B9 outcomes separately. Do not
claim native live streaming is homologated or launch a standalone probe that bypasses
the application's operation locks. Broader keepalive timing, reconnect and source
handoff remain unvalidated.
