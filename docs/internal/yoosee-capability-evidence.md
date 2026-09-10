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

Collection checkpoint: `capability_collector.collect` now reads only product/version/video/guard
roots in one brokered session, one attempt per root, with a 20-second deadline. It requires
linked camera identity, verifies the selected target and closes the socket on exceptions. A
timeout/error stops the batch. It is explicit and not invoked by dashboard refreshes.
It returns private observations only; it does not persist raw property values or enable controls.

Before connecting collector to persistence/catalogue, audit GDM response correlation: current
`exchange_model_read` can accept a direct reply by device ID without request/path correlation,
and nested reports may omit the parent timestamp. Stopping at a timeout reduces one late-response
case but is not sufficient proof against duplicate/unrelated same-device reports. Recover exact
request correlation and reject ambiguous shapes before treating these reads as feature evidence.
Then normalize authoritative product/version identity and implement the evidence/profile bridge.

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
