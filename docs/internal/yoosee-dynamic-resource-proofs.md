# Yoosee dynamic-resource proof boundary

Status: exact-resource listing/selection enforcement deployed and explicitly enabled on camera 3.

## Runtime enforcement — 2026-09-11

`alarm_resource_controls.py` now applies the same intersection to dynamic option listing and
fresh pre-write resolution. Each request obtains a strict correlated, complete C0/C1 catalogue;
there is no background catalogue polling or reusable browser authorization. Profile expiry,
revocation and capability evidence are checked again after network I/O. The selection path holds
the existing per-device mutex across resolution and write. Raw resource IDs remain driver-private.

The capability collector now reads six fixed roots in its existing bounded session, including
`ProWritable.resFile`. Explicit support and a valid current type-4 ID are required; unknown data
does not enable selection. Snapshot rule revision 6 invalidates older evidence. Dynamic descriptors
use a separate resource profile, not fabricated static operation proofs. Rollout is explicitly
per-camera/per-control; other units retain their previous behavior.

Migrated selection additionally requires correlated B7 preflight/readback and full native-ID
equality, including idempotence. A replacement resource in the same logical slot cannot be
mistaken for the certified selection. Legacy callers retain logical-slot compatibility.

Camera 3 was refreshed and opted in. An authenticated production HTTP listing returned exactly
Zumbido 1 and Zumbido 2; neither Bip nor Latido was offered. This deployment check was read-only,
with no playback, siren, light or reboot commands. The prior reversible proof below remains the
physical selection evidence; the new enforcement has automated write-path regression coverage.
Build `b-14c9a2bbd7e4` was deployed by recreating only the app. The media container's start time
did not change; neither container reported an OOM kill. Build limits: 512 MiB and one CPU.

Known follow-up: the first options request returned 501 while the demand-driven capability
refresh invalidated the prior snapshot; after refresh, the same request succeeded. Improve the
refresh/temporary-unavailability UX without retaining stale permissions across identity changes.
Remaining migration includes siren/intercom gates and additional exact-unit proofs; enumeration
alone must never certify an untested resource or another camera/model.

Validation: 299 focused tests, Ruff and mypy (154 source files). Ignored operational helpers are
`re/activate_camera3_capability_rollout.py` and `re/verify_camera3_alarm_options_http.py`.

The sections below record the earlier milestones and their then-current boundaries.

## Native identities bound by fresh silent validation — 2026-09-11

Camera 3 was revalidated using its complete ProConst identity and fresh catalogue. A controlled
selection changed Zumbido 1 to Zumbido 2 and restored Zumbido 1 in unconditional cleanup. Both
writes returned ACK/error zero. Independent **correlated B7** reads compared full `resFile.setVal`
against the expected native ID and then the complete original state, not just the logical slot.
No siren action/audio playback/light/reboot was invoked. Only camera 3 was contacted.

The two native-resource identity digests and source provenance are now registered in the local
backend resource-profile table with explicit 30-day expiry. A subsequent fresh strict catalogue
intersected with the persisted profile to return exactly those two options. Bip, Latido and custom
resources were not certified. Operational IDs, digests and the exact execution record stay in
ignored `re/notes/alarm-resource-identity-homologation.md`; the bounded repeatable harness is
`re/homologate_camera3_alarm_resources.py` and defaults to read-only unless `--execute` is passed.

### Catalogue correlation correction

An explicit strict mode was added to the C0/C1 reader. It requires encrypted mode 2, matching
access-node session and C1 request correlation; transport ACK sequence is checked separately.
Stale C1 replies are rejected before decompression. The account resource service is not a
device-specific capability claim: the per-device selection proof remains necessary.

Live diagnostics established that the **outer fragmentation identity is not the node session**.
An initial experimental check incorrectly dropped those fragments; it was removed before any
deployment. The existing bounded reassembler groups them, then the inner encrypted packet is
session/request-correlated. Strict system/custom catalogues also require complete reported counts
and correct source classifications. Partial/filtered pages cannot be labelled complete.

The strict path was live-verified for system and custom queries; compatibility mode remains the
default for unmigrated callers. No container rebuild/restart or public gate change was needed.
64 focused tests, Ruff and mypy (153 source files) passed. The next step is connecting fresh
observations and the stored profile to both enumeration and pre-write enforcement before rollout.

## Durable backend proof storage — 2026-09-11

`capability_resource_profiles.py` now persists exact-unit resource profiles separately from
static operation profiles. Registration requires an explicit expiry, review time and source
digest/locator for enumeration and **each** selected resource. Reads match every identity field
and rule revision, exclude future/expired reviews and revalidate bounded stored JSON/provenance.
At most 200 selection proofs and 64 KiB per JSON field are accepted; malformed, oversized or
deeply nested stored data fails closed.

Reviews replace the complete profile atomically; they do not merge old permissions. An empty
profile with enumeration false revokes selection. The row remains after expiry, so older/equal
reviews cannot resurrect removed permissions. No automatic renewal, public registration API,
production profile import or dynamic catalogue rollout was added.

Bounded historical audit of `re/notes/alarm-voice-resources.md` and `cameras-local.md` confirms
camera-3 reversible “Zumbido 1 → Zumbido 2 → Zumbido 1” selection with fresh catalogue resolution
and readback. The reviewed summaries deliberately omit full native resource IDs; one semantic
key (`system-7270`) appears, but labels/logical slots do not establish the digest required by the
new policy. No exact-resource write grants were reconstructed from names. Original evidence or
an explicitly recorded fresh reversible validation must bind the native identities before import.

Storage validation: 70 focused tests, Ruff and mypy (153 source files) passed. Tests used isolated
databases; no production database was changed and no camera, media or container was restarted.

Static option proofs cannot safely grant a complete camera-provided alarm-sound catalogue.
Enumeration and resource selection are separate operations. New/custom entries must not become
certified just because the device can return them or their semantic slot was used previously.

`capability_resources.py` adds a pure intersection of:

- A backend-reviewed exact-unit identity/profile with explicit enumeration proof and per-resource
  selection proofs. Successful enumeration alone yields no selectable entries.
- A complete, authenticated, sanitized catalogue observation for that same identity, with backend
  receipt/expiry times. Defaults are unverified; partial/unauthenticated observations fail closed.
- Currently present resources matching both the semantic key and a SHA-256 digest of native
  resource ID + semantic key + audio format. Labels may change with language without invalidating
  the identity. Reusing a slot for another resource cannot inherit permission.

Bounds: at most 200 resources/proofs, no duplicate keys, finite non-boolean clocks, positive
receipt time, no future observations and expiry at most one day after receipt. The one-day bound
is a ceiling, not a chosen production cache TTL. Missing/stale/ambiguous evidence yields nothing.
Identity changes in device/product/model/revision/firmware/SDK/hardware reject the observation.

The digest is an **identity fingerprint, not a hash of the audio file**. If a provider changes
audio contents while retaining the same native ID, this metadata-only policy cannot detect it.
It certifies the recovered selection operation, not the resulting sound's contents or loudness.
No signed URLs/tokens are retained by the existing sanitized `AlarmVoiceResource` contract.

## Remaining runtime integration

1. Exact-unit resource identities are now bound by a fresh reversible validation with provenance;
   labels alone were not used to import historical grants.
2. Durable resource-specific profile storage is implemented with revision, exact identity,
   source hashes and revocation/expiry protection; the two camera-3 proofs are registered locally.
3. Produce authenticated/complete observations only from validated catalogue exchanges; choose
   bounded refresh/TTL without introducing cloud/media polling or unbounded fragment retention.
4. Filter both displayed options and fresh pre-write resolution through the same intersection.
   A browser-submitted key never carries authorization; vanished/replaced resources are rejected.
5. Only then opt the exact test unit into the dynamic capability gate. Other units and legacy
   gates remain untouched. No enumeration/selection/preview/playback is triggered by this module.

The current `select_controls` deliberately still excludes dynamic descriptors. This implementation
does not remove that guard, import a production resource profile, or claim completed migration.

Validation: 54 focused tests passed, plus Ruff and mypy (152 source files). No live camera calls,
container rebuild/restart or production database updates were needed for this offline boundary.
