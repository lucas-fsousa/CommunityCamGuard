# Yoosee capability evidence checkpoint

The core and frontend consume semantic capabilities for an opaque camera ID. Driver
selection and account enrollment establish identity/transport availability, not feature
support. Model names such as IPC and a firmware version alone are not unique profiles.

## Current implementation gap

`yoosee.controls.catalog` currently returns all descriptors after an enrollment check.
`yoosee.audio.supported` also accepts enrollment as evidence for the P2P fallback.
Neither rule constitutes per-feature certification. They remain unchanged in this
checkpoint pending durable device evidence and migration of existing registered units.

## Evidence interpretation

`capability_evidence.py` is an offline building block, not yet a runtime gate.

| Observation | Interpretation |
|---|---|
| Missing, malformed, failed read, or timestamp zero | Unknown; never infer supported/off |
| APK-defined timestamp -1 | Unavailable in that observation |
| Positive timestamp and recognized field value | Property supported; transport still needs validation |
| Unrecognized numeric/error-domain value | Unknown; never normalize into a switch |
| Enrollment or branded model name alone | No feature evidence |

Rules are feature-specific: nightViewMode zero is supported automatic mode; cryDetectEn
zero is an unsupported sentinel. Booleans must not be accepted as numeric switch values.
Usable tfInfo does not prove file-list/playback support, which requires separate certification.

## Next integration steps

Operation-policy checkpoint: `capability_policy.select_controls` intersects exact-unit
backend operation proofs with supported property evidence. Reads do not authorize writes;
choice/action options are intersected with proven options. Duplicate proofs, mismatched
identity, unknown/unsupported evidence and dynamic option enumeration without a dedicated
validated flow are excluded. No production profiles were fabricated or inferred from brand,
enrollment, firmware alone or generic client capability metadata.

`controls.validated_catalog` provides a backend-only migration preview using the current
enrollment association and durable evidence. `resolve_features` reads all feature states
from one snapshot row in one query, avoiding mixed collection generations and repeated DB
connections. Tests exercise the full preview with temporary persistence, expiry and changed
enrollment, as well as per-operation/option restrictions. This preview does not yet replace
`catalog`; existing dashboard controls and audio remain unchanged. Next step: inventory
recorded homologations, bind proofs to complete identities and obtain a trusted backend
profile source before switching the runtime catalogue. Audio needs its own transport proof;
the two current property rules cannot certify it. Dynamic siren-resource options also remain
outside this selector until their enumeration/selection proof is represented explicitly.

Atomic persistence checkpoint: `capability_snapshot_store` stores one complete row
per opaque camera ID, with all identity dimensions and sanitized feature states.
`begin` reserves a database-issued generation before network I/O and invalidates the
previous snapshot. `save` conditionally publishes identity, evidence, receipt time,
expiry and rule revision in one UPDATE, only for the latest generation and only once.
An older-started job cannot win by finishing later. Failed refreshes remain unknown;
missing features cannot inherit support from an older batch. Property t is never used
for ordering or validity and is excluded from this persisted representation.

`capability_refresh.refresh` now connects reservation, existing bounded collection and
atomic publication. It is explicit/backend-only, not called by dashboard refresh or a
scheduler. Validity must be chosen by its caller (positive, at most one day); this bound
is a local cache policy, not proof of broker freshness. Exact backend identity, current
rule revision and server receipt/expiry boundaries are required for lookup. Old per-feature
`capability_store` rows are not imported: their flattened identity cannot certify these
snapshots. The new driver store supersedes that prototype for the snapshot flow.
Tests exercise inverted job completion, duplicate publication, batch rejection, failed
refresh invalidation, expiry and all identity dimensions using isolated temporary databases.
No production database, cameras or containers were touched. Next: validated operation
profiles and controlled migration of catalogue/audio gates; do not equate property evidence
with permission to execute or declare source cache freshness resolved.

Snapshot checkpoint: `collect_snapshot` now connects the correlated collector to
identity normalization and immutable, sanitized property evidence in a single batch.
The server samples `collected_at` after collection. Raw JSON and credentials are not
retained in the snapshot; duplicate paths, foreign-device observations and incomplete
identity invalidate it. Missing/failed feature reads stay unknown. Initial enum rules
cover only night vision and cry detection, not sound/siren/SD transport certification.

Timestamp finding: APK `device_setting/tdevice/soundandpicture/a.java` (lines 279,
314) and `device_setting/tdevice/record/a.java` (255 onward) assign property `t`
from `System.currentTimeMillis()/1000` when writing settings. It is therefore not
evidence of when our server queried the camera. The snapshot preserves this signed
integer separately; old positive timestamps do not imply unsupported hardware.
`t=-1` keeps the existing unavailable interpretation and malformed/out-of-range
timestamps yield unknown. No camera-clock timestamp is used as receipt/expiry time.
This does not certify broker-cache freshness, nor an atomic camera-side view of
four sequential properties. Persistence must use a complete exact-identity snapshot
transaction and prevent old collection jobs overwriting newer jobs. Runtime catalogue
integration, expiry policy and that transactional store remain pending; no public API
or automatic collection has been enabled by this checkpoint.

Identity checkpoint (2026-09-10): `capability_identity.normalize_identity` accepts
only successful, authenticated, exact-path product/version observations belonging to
the expected device. It preserves product ID, model, revision, firmware, SDK and
hardware separately in an immutable value. Empty hardware version is a known valid
shape; missing hardware/version fields remain unknown. Decimal string/integer product
IDs normalize equally, but floats, booleans, alternate representations, control
characters and partial roots are rejected. Transport ACK is not required when the
application response succeeded.

Schema evidence: decompiled `com.jwkj.t_saas.bean.ProConst.ProductInfo/VersionInfo`
(6.36 APK) and existing identity observations in `re/notes/thing-model.md`. These
ProConst roots contain plain fields, not `setVal`/`t` envelopes. `revisionUtc` must
not be mistaken for collection time. No family-wide model/firmware equivalence is
inferred. This pure normalizer must receive the correlated collector's output;
an `authenticated` boolean on an arbitrary/client-created DTO is not provenance.
It is not yet wired to persistence/catalogue. Tests are synthetic, not new live
homologation. Next: define the backend snapshot/validity boundary, validate writable
property timestamps without assuming a camera clock is trustworthy, and bind exact
identity to evidence atomically. Do not flatten this identity into a model-only key.

Collection checkpoint: `capability_collector.collect` now reads only product/version/video/guard
roots in one brokered session, one attempt per root, with a 20-second deadline. It requires
linked camera identity, verifies the selected target and closes the socket on exceptions. A
timeout/error stops the batch. It is explicit and not invoked by dashboard refreshes.
It returns private observations only; it does not persist raw property values or enable controls.

Before connecting collector to persistence/catalogue, validate correlated response shapes and
cache timestamps, normalize authoritative product/version identity, then implement the
evidence/profile bridge. Correlation alone must not certify freshness or physical support.

### Offline correlation checkpoint (2026-09-10)

The collector now opts into `require_correlated_response`: B8 responses must match the
selected device, session and originating request sequence. All AA reports are excluded,
even exact-root reports, because they can be unsolicited. This supersedes the temporary
`exact_reports_only` policy introduced in b9e22ac. Existing control reads retain their
legacy AA behavior, but their direct B8 responses also require request correlation.
Transport B7 ACKs must match the request sequence at offset `0x0c` in both modes.

Native evidence: Google Play 6.45 arm64 `libiotvideomulti.so`,
`giot_eif_get_gdm_data_object` at `0x274824` assigns the request sequence at
`0x274a4c`; `giot_get_gdm_data_object_ack` compares offset `0x0c` at `0x273734`.
`gat_on_rcvpkt_GATFRM_GetDevGdmDatResp` at `0x23fdfc` confirms device ID at
`0x18`, data-present bit at `0x20`, status at `0x24`, length at `0x26`, JSON at
`0x28`. Its callback receives offset `0x10` (`0x240044`), **not** offset `0x20`.
The apparent checksum conflict is now resolved by the native response constructor:
`iv_gute_init_frm_resp` (`0x1e6d78`) copies request `0x0c` into response `0x10`
at `0x1e6dbc`–`0x1e6dc4`, then sets flag bit 21 at `0x1e6ddc`. It also copies
the identity at `0x04` and sets subtype to request subtype + 1 (B7 → B8).
`iv_gute_frm_rc5_decrypt` skips checksum validation when bit 21 is set
(`0x1e4b84` for mode 1; `0x1e4ca0` for mode 2). No receive-side offset relocation
is needed. `iv_gutes_on_rcvfrm_resp` compares this field to the pending request ID
at `0x1e9b64` and checks the subtype pairing at `0x1e9b84`.

The parser requires the response flag before interpreting `0x10` as a sequence; ACKs,
invalid declared length and AA layouts cannot satisfy a correlated B8 read. Tests use
synthetic mode-2 encrypted frames following these SDK offsets, not a newly captured
camera exchange. They cover old ACKs, unsolicited exact/parent/child reports, wrong
device/session/sequence, missing bit 21, truncated JSON, preserved timestamps and
correlated errors without JSON. Matching a request does not prove cache freshness:
the profile bridge still needs identity normalization and timestamp/shape validation.
AA-only peers may time out in the collector; do not restore an ambiguous fallback.
No live camera calls or container rebuild were performed.

Persistence checkpoint: `capability_store.py` now stores only sanitized per-feature states,
bound to opaque camera ID, native device ID, product, firmware, observation/expiration times
and rule revision. Missing, expired, future, mismatched and old-revision observations resolve
to unknown. Older responses cannot overwrite newer observations. The store has no public
write endpoint and is not yet populated by runtime collectors or used to enable controls.
The generic control service already checks catalogue membership before API operations.
Next implementation is the driver collector/profile migration, followed by catalogue filtering;
the enrollment-only runtime gap remains open until those pieces are connected.

1. Collect allowlisted read-only evidence and bind it to device identity, product, firmware,
   observation time and rule revision; keep secrets out of this record.
2. Invalidate evidence on identity/firmware changes. Temporary read failures should distinguish
   unavailable transport from unsupported hardware; do not silently manufacture support.
3. Combine property evidence with validated operation profiles; filter the catalogue and enforce
   the same decision on direct read/write/audio endpoints.
4. Migrate existing units using their recorded proofs without enabling unknown models. Test mixed
   supported/unsupported cameras, stale evidence, firmware changes and forged client metadata.

No camera was contacted for this checkpoint. SD listing still needs an official SDK callback
or equivalent authoritative implementation evidence; no new Frida capture was available.
