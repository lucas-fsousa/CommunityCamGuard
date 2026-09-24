# Allowlisted runtime settings — 2026-09-24

**API and dashboard view implemented in source; deployment pending.**

## Contract and authority

`GET /api/settings` and `PATCH /api/settings` both require a verified primary-key
session (`require_primary_session`). Missing/invalid sessions receive 401; exact
legacy sessions receive 403 and need a new primary-key login. This does not add
temporary keys. Responses use `Cache-Control: no-store` and contain only the two
explicit values, their overrides, revision and application semantics, never the
full environment, paths, credentials, session IDs or tokens.

PATCH requires `application/json`. An Origin, if supplied, must exactly match the
application request origin (case-insensitive); `Sec-Fetch-Site: cross-site` is
rejected. Form/text submissions receive 415. Scripts without Origin are permitted
with the same primary session. Forwarded-Host is not used to establish authority;
reverse-proxy trust/HTTPS deployment still requires independent review. This guard
is scoped to this new route, not a claim that every existing mutation is protected.

```json
{
  "revision": 0,
  "changes": {"grid_hd_max_cameras": 2, "playback_cache_mb": 2048}
}
```

Only strict JSON integers or null are accepted: grid limit 0..64, cache 0..65536 MiB.
Booleans, numeric strings, fractions, unknown names, empty changes, extra envelope
fields and negative/non-integer revisions are rejected with 422. These are write-API
bounds; existing environment baseline semantics are not silently clamped/changed.

## Persistence and application

The existing SQLite database gains a singleton `runtime_settings` table on first
access, with a revision and JSON containing only overrides. The store uses an
immediate transaction and revision comparison; stale writers get 409 and must
reload rather than force overwrite. Multiple workers read the same database;
there is no process-local override cache to become stale. This implementation
does not create per-field secret records or replace the camera registry.

Precedence is DB override > environment baseline > code default, for these two
fields only. Null deletes the named override, restoring the baseline. Omitted
fields are untouched. The environment file and cached bootstrap Settings object
are never modified. Runtime reads validate the stored shape too; API storage/
validation failures return a sanitized 503, not exception text or private paths.

| Field | When consumed | Important limit |
| --- | --- | --- |
| `grid_hd_max_cameras` | Next `/api/media/streams` read | Existing dashboards need reload/metadata refresh. A save does not restart/reconfigure cameras or change explicit HD/SD selections. |
| `playback_cache_mb` | Next cache eviction/warmer policy check | Saving does not immediately scan/delete files. Eviction concerns only derived transcodes; 0 means unbounded. It does not change the one-encoder budget. |

These are next-operation reads, so a separate desired/applied service-restart
state is not yet needed. Retention, segment length, live encoder configuration,
storage thresholds and warmer enablement remain environment-controlled; they must
not be added without service-owned application/rollback semantics and impact UI.

## Tests and rollout

The dedicated `frontend/modules/settings.js` view provides localized bounded
inputs, per-field baseline reset, explicit save, busy locking, conflict/uncertain
completion reload and request cleanup on navigation/logout. `app.js` only wires the
view/permission hint; the API remains authoritative. Save/reload updates this tab's
Auto budget; other tabs need refresh. Node contracts cover denied UI access,
duplicate saves, revision/reset payloads, conflict/403/failure reload and stale
completion disposal. See the [user guide](../public/settings.md). Full mobile visual
validation is pending; layout uses a centered bounded-width card and scrollable stage.

86 focused tests passed across settings, playback, warmer and recording/config
contracts. New coverage includes primary/legacy/unauthenticated requests, strict
validation/no partial writes, baseline restoration, fresh DB reads, stale revision,
two concurrent writers (one wins), Origin/content-type rejection, sanitized errors,
media metadata and eviction consumer wiring. Eviction used a synthetic temporary
derived file and verified that a separate original fixture was untouched.

No production DB override was written, no camera was contacted, and no container
was restarted. Backend/auth changes need rebuild/recreation before this API exists
in the running deployment. Next: deploy and validate the primary-only settings view.
Keep temporary-key/revocation/security-hardening work separately tracked.
