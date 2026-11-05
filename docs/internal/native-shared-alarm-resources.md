# Alarm resources on the native stream owner

The existing `alarm_voice` options and selection use the active native account,
not a standalone login. The generic API/UI and per-device driver capability gates
remain unchanged. Without a native owner, the existing legacy path remains.
Recovering/mismatched owners never cause fallback or credential renewal.

## Fixed, silent catalogue reads

Separate small job/inbox/pump modules implement only the existing type4 resource
query: system and custom, page0/size20, language pt-BR. No URL, method, arbitrary
query, device/access ID or vendor resource ID comes from an HTTP client. Queries
share the account's socket and monotonic sequence allocator; each is sent once.
The sole reader handles authenticated fragments and ordinary message receipts.
C0 ACK alone is not success: C1 must match peer/session/generation/sequence before
bounded QuickLZ expansion, and its complete decoded catalogue must match the
requested source type. No partial/truncated list is silently treated as complete.
Signed download URLs are discarded by the existing sanitized decoder.

One generation-bound slot includes queued work and unconsumed results. Deadline
is2seconds per part. Cancellation/close retire private work, never the shared
sequence allocator or media session. No new socket, thread, SDK account or cache.

## Typed selection, not playback

Resolve every semantic option key against a fresh catalogue. Managed devices
still intersect exact-unit/resource-identity proofs; revocation is checked again
after preflight. Submit one typed `AlarmVoiceWriteSpec` containing the internally
resolved resource, producing only `ProWritable.resFile.setVal.resId` D2.
The existing control slot/pump correlates D3; an ACK is not acceptance. Exact
`resFile` support/state is read before and after the write. Matching only the
logical number is insufficient: a replacement resource in the same slot must not
inherit confirmation. Unchanged selection is read-only. Rejection, owner change,
timeout or readback mismatch never replays a write or opens another account.

This operation selects a future siren sound; it does not invoke `Action.expelCtrl`,
upload/create/delete custom recordings, play audio, or broaden model support.
Shared-native siren pulse scheduling/cleanup remains separate pending work.

## Verification

Focused tests cover fixed wire shape, typed admission, one send, correlation,
compression/fragmentation, malformed/partial data, bounded inbox cancellation,
silent option conversion, exact resource readback, no-op, failures/revocation and
sole-reader media-route/B7/heartbeat coexistence. Write actuation is simulated;
no real resource selection or audible test has been performed for this integration.
Production read-only checks and CI results will be recorded below after deployment.
