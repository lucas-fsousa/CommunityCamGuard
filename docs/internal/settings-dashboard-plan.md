# Dashboard settings: inventory and implementation gates — 2026-09-24

## Current behavior, not a new settings feature

**Implementation update:** [two-field runtime persistence](runtime-settings.md) and
its primary-only API are now implemented in source, pending deployment. No settings
screen exists yet. The inventory-time analysis below remains relevant for every
other field and for service-restart/destructive application requirements.

Reviewed all **42 declared Settings fields**, their consumers and the session
boundary, without reading/printing the deployed `.env` values. The companion
`settings-inventory.json` is a review artifact, **not an API allowlist or runtime
configuration source**. CI requires every field to have exactly one classification;
future settings must be reviewed rather than automatically exposed.

`get_settings()` is process-cached. Editing `.env` does not hot-reload it; Docker
env-file injection also requires container recreation to update the environment.
Clearing the cache alone is insufficient: Recorder, RetentionCleaner,
StorageMonitor, Warmer and Go2rtc copy settings into instance fields. A screen
which only saves values would falsely report applied changes.

## Proposed exposure and application

All classifications below are **future candidates**, not currently editable fields.

| Classification | Fields | Application/risks |
| --- | --- | --- |
| Next operation candidates | `grid_hd_max_cameras`, `playback_cache_mb` | Grid metadata is read by the API, but open clients need an explicit refresh/event; cache cap is read on eviction/warm checks. Lowering a cap evicts only derived media, not originals. `0` means unbounded cache, not disabled playback. Neither is live-editable today. |
| Recorder/retention restart candidates | `segment_seconds`, `recording_retention_days` | Instances snapshot values. Segment length changes require supervised recorder replacement. Reducing retention can delete originals: require explicit impact preview/confirmation and never run purge as a save-validation test. `0` means keep forever. |
| Media regeneration/restart candidates | `live_fps`, `live_quality` | go2rtc configuration encodes these settings; safe regeneration/recovery is required, not only updating the UI. Changes affect existing consumers and resource usage. Per-camera quality choices remain separate. |
| Warmer restart candidate | `playback_pretranscode` | Constructor snapshots enabled state; starts/stops a worker. Must stay opt-in and share encoder admission, not create an independent conversion loop. |
| Storage-monitor reconfiguration candidates | `storage_alert_percent`, `storage_full_percent`, `storage_resume_percent`, `storage_check_seconds` | Constructor snapshots values. Validate the policy as one transaction: `0 < alert <= resume < full <= 100`, bounded positive interval. Changes can pause/resume recording; disclose impact before applying. |
| Server-only | Authentication/signing keys, AWS credentials, addresses/ports, paths, process ownership, hardware acceleration, network scan ranges, BLE overrides/material and native diagnostics | Keep operator control. Do not return secret values, file contents, credential-bearing URLs or enable diagnostics via a generic settings editor. Network ranges and hardware options need independent security/deployment review. Exact names in JSON. |
| Not implemented | `discovery_timeout`, `storage_backend`, `s3_bucket`, `s3_prefix`, `aws_region` | Search found declarations only, no runtime consumers. AWS credential fields are likewise unused but remain server-only. Do not advertise S3 or an effective discovery timeout control until the underlying feature is implemented. |

Current `.env` validators clamp some values or fall back to defaults. A future
write API must reject invalid requests explicitly instead of silently accepting a
different value. The proposed storage ordering is a new API requirement, not a
claim that the existing environment parser enforces it.

## Required architecture

1. Keep bootstrap settings/credentials in environment-only Settings. Add a small,
   explicit runtime-settings schema for approved non-secret fields; no generic
   `setattr`, `model_dump()` of Settings or raw `.env` endpoint.
2. Resolve eligible values as **DB override > environment baseline > code default**.
   Removing an override restores the baseline. Environment-only names cannot be
   written through DB/API. Document this precedence before implementation.
3. Separate persisted desired values from effective applied values and revision.
   Validate the whole patch before saving. Use optimistic revision checks for
   concurrent tabs. A service-application failure must not be reported as success;
   retain/reconcile the prior effective state and expose sanitized pending/error
   state, without exception dumps or credentials.
4. Add service-owned application hooks, not direct UI access to recorder/media
   internals. Restart-required fields remain clearly pending until applied through
   a reviewed lifecycle. No automatic mass camera reconnect or process duplication.
5. Use a dedicated localized settings view with grouped controls and explicit
   save/apply impact, not a generated form of all environment variables.
6. Camera capabilities stay in drivers; platform storage/auth/settings must remain
   independent of brand/model. None of this inventory grants camera capabilities.

## Authentication gate before management endpoints

**Update:** the [session-principal foundation](session-principal.md) is implemented
and tested, pending deployment. The paragraphs below describe the inventory-time
baseline and requirements; primary/legacy parsing and the management dependency
are now covered by that follow-up. Temporary keys and management endpoints remain absent.

Current cookies are signed, seven-day **bearer credentials**, not safe to disclose.
The cookie payload has no session identity, key identity or role; verification
currently validates signature/age, with no revocation store. `/me` returns only
authentication status. No temporary-key implementation exists yet, and adding a
form alone cannot distinguish primary-key sessions from temporary-key sessions.

Before exposing settings/key management: introduce a trusted session principal
and a server-side primary-key-only dependency, with negative tests. Keep the main
key environment-only; temporary keys must not change configuration or issue keys.
Then implement hashed temporary-key records/expiry/revocation and channel-specific
enforcement. The media WebSocket currently checks at connection establishment;
per-request verification alone would not disconnect an already-open socket.
Existing cookies require an explicit migration/invalidation decision, not silently
upgrading unknown payloads to administrator sessions.

Also still pending: login abuse limits, origin/CSRF policy, HTTPS cookie policy and
static/API secret-exposure audit. These are identified review needs, not proof of
an authentication bypass and not resolved by this inventory. Do not expand remote
BLE access or publish a public configuration endpoint as part of this work.

## Next bounded implementation

The session-principal boundary and two-field next-operation persistence are implemented
(see the linked rollout notes). Next, build the localized settings view over that
revisioned API; leave destructive retention and service restarts for separate reviewed
steps. No live setting, camera command or service restart was
performed during this inventory.
