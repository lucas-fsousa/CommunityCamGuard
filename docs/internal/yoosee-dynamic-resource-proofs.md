# Yoosee dynamic-resource proof boundary

Status: offline foundation implemented; no runtime rollout or camera action.

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

1. Audit original exact-unit resource IDs for the recorded reversible selection and retain source
   provenance; labels such as “Zumbido 1” alone are not identity proof.
2. Persist resource-specific profiles independently of static control options, with revision,
   exact identity, source hashes and conservative revocation/expiry behaviour.
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
