# Staged temporary live media — 2026-09-25

Implemented in source, not deployed. Public temporary login remains disabled.
Primary/legacy media behavior is unchanged; temporary intercom remains denied.

`api/temporary_media.py` owns a separate restricted bridge. Only a single `src`
query parameter identifying a registered `cam_<24 hex>_hd` or `_web` is accepted.
Arbitrary URLs, FFmpeg sources, raw/base/sub feeds and extra query parameters are
rejected before acceptance. This boundary is platform-wide, independent of drivers.

The initial handshake must arrive within 10 seconds and contain exactly `type:mse`
and a comma-separated subset of the bundled player's codec list. Requests over 512
characters, duplicate JSON fields/codecs, binary uploads and further negotiation
are rejected. No temporary WebRTC/HLS negotiation is forwarded. The upstream opens
only after validation; its timeout is 5 seconds, max message size 4 MiB and queue
limit 4. These are transport bounds, not a total-memory guarantee. The message cap
still requires real-camera/browser validation before rollout.

An Origin header, when supplied, must match the socket's scheme/authority;
cross-site fetch metadata is rejected. Missing Origin supports authenticated
non-browser clients. The bridge does not directly trust forwarded headers; trusted
proxy configuration and cookie policy still require review before activation.
Follow-up: this now uses the [shared origin policy](browser-origin-policy.md),
including optional canonical public origin, same-site rejection and Referer fallback.

Fresh key validity and registry membership are checked initially (5-second timeout)
and through the shared channel watcher (normally every second, verification timeout
5 seconds). Revocation, expiry, removal or verification failure close with 1008 and
cancel relay tasks/upstream. Unexpected upstream failures close with 1011. This is
bounded periodic enforcement, not zero-latency revocation; already delivered bytes
cannot be recalled. No credentials are logged.

71 focused tests passed across this boundary, session channels and temporary
sessions, using an isolated database and fake upstream under a 512 MiB address-space
cap. No camera command, production stream/key or container restart was involved.

Remaining: final ordinary-control permissions, login abuse resistance, cookie/proxy/
CSRF review, real multi-tab/browser/proxy validation, then public login/UI activation
and deployment. See [activation gates](temporary-access-keys.md).
