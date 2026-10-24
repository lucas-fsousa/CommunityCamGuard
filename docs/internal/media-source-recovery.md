# Source availability after restart — 2026-10-24

Status: read-only reporting and dashboard behaviour implemented/tested; not deployed.
No camera command, production database change or local container restart/build.

## Intent is not a live session

The source runtimes deliberately start empty. An `active` native/owned row left
by an interrupted process does not recreate its worker, broker reservation, driver
evidence or cleanup receipt. The existing backend already rejects unavailable
owned/native admission without silently returning to the direct camera URL.
Hub health alone previously left this distinction invisible to the dashboard.

`media/source_status.py` now projects current durable intent and local runtime
metadata without changing either. The existing authenticated `/api/media/streams`
manifest includes `source_states`, keyed only by configured opaque camera ID:
The manifest and its status-read error responses are marked `Cache-Control:
no-store`; initial and recovery browser reads also bypass cached responses.

| Field | Meaning |
| --- | --- |
| `mode`, `phase`, `revision` | Current durable source selection; not activation authority |
| `status` | `legacy`, `owned`, native `starting`/`running`/`stopping`/`blocked`, persisted `transition`/`blocked`, `runtime_missing` or `runtime_conflict` |
| `available` | A route is locally admissible in this snapshot; **not** proof of healthy packets, decoded frames, audio or browser playback |

Active legacy intent retains its existing route. Owned availability requires a
matching ready binding, revision and worker. Native availability requires an
active native selection and running registry entry. Missing runtime, transition,
blocked/corrupt intent and conflicting native reservations report unavailable.
A native claim is never an invitation to advertise the old hub as fallback.
Registry shutdown reports stopping rather than running, including the interval
before individual stop signals finish.

Reads retain the policy snapshot while checking runtime metadata. No subscription,
driver factory, decoder, account authentication, thread or camera probe is created.
There are no endpoint URLs, domains, credentials, names or MACs in `source_states`.
Database/read failures return sanitized503, not an empty permissive manifest.
The endpoint's existing primary/live-delegated authorization remains in force;
anonymous and PTZ-only access cannot read it.

## Dashboard contract

At session boot, unavailable sources get a small localized text placeholder inside
the existing video tile. No player custom element, reconnect timer or watchdog
recovery is created for that placeholder. PTZ and other controls retain their
existing per-device driver/permission gates; media state does not grant or remove
unrelated features.

The existing refresh button rechecks the manifest for unavailable/nonlegacy
sources. If still unavailable, it retains the placeholder without POSTing producer
recovery. If a verified server-side activation has since completed, it can create
a new reader. A failed metadata check never becomes an optimistic restart.
End-session cleanup clears the source snapshot, and legacy active behaviour stays
unchanged. Older manifests without the optional field retain their prior client
behaviour; the backend remains the authority.

This is a snapshot, not a live subscription or automatic source restoration.
An already-open player may observe a later failure through existing socket/session
guards and watchdog; its next nonlegacy refresh rechecks source state. Manual page
refresh also reloads the manifest. No new polling worker or per-frame DB query.

## Evidence and remaining recovery work

Focused groups of106 source/config/settings/frontend tests and75 source/delivery/
auth/handoff tests passed at128.8/161.8MiB, zero swap, under192MiB/50%-CPU scopes
(overlapping groups), plus43 source/lifespan/registry cases at133.1MiB. Mypy340
files and Ruff passed. Node contracts check localized placeholders, unchanged
legacy playback, fresh-reader recovery and metadata-only refresh/error handling.
Two successive real app lifespans preserve native/owned intent, report missing
runtime and start no source threads. Native output in fixtures is structural MP4,
not physical playback acceptance.

The [server ownership lock](server-source-owner.md) now excludes duplicate app
owners of the same database and covers partial startup/uncertain service shutdown.
This prerequisite does not supply a source-retirement receipt. Actual restoration
still needs verified old-session retirement,
fresh per-device driver admission/evidence, bounded preparation and publication,
and a defined policy for interrupted transitions/unknown cleanup. No stored flag,
HTTP recovery request or UI refresh supplies that authority. First external shared
hub migration and native foreground-control resumption remain separate blockers.
