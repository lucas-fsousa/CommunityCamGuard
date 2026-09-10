# Yoosee homologation migration audit

Offline audit of saved research notes. These are historical proofs, not a current
camera inventory or a production model allowlist. No live tests were repeated.
Operational identifiers and credentials remain in ignored RE material.

## Proofs and limits

| Control | Recorded proof | Limit for migration |
|---|---|---|
| Orientation | `1 → 3 → 1`, readback/restoration on 40.1.22 and test unit 40.1.14 | Only normal/inverted; no mirror/fisheye inference |
| Smart Protection master | Test unit `1 → 0 → 1`, D3 success and B7 readback | Independent from siren, detection and schedule |
| Smart Protection schedule | Test unit weekday mask `127 → 126 → 127`, complete plan restored | Master support does not prove schedule; needs its own structured evidence |
| Night vision | Test unit 40.1.14 `0 → 1 → 0`, readback | Proven options `automatic`/`daytime`; `night` (2) is mapped, not proven by this recorded test |
| Speaker volume | Garage raw `10 → 7 → 10`, readback | Does not certify every UI volume option or every unit |
| Two-way audio | User physically passed message and hold-to-speak on all three units | Successful route was proprietary LAN RTSP; do not transfer proof to P2P fallback |
| Alarm resources | Catalogue query and selection contract documented | Dynamic enumeration/selection requires a separate proof representation |

Sources (ignored local notes): `selected-model-inventory.md`, `CAPABILITY-MAP.md`,
`smart-protection-schedule.md`, `sound-picture-night-controls.md`,
`two-way-audio.md`, `alarm-voice-resources.md` in `re/notes/`.
Some source sections precede later successful tests; the later test entries above
take precedence over earlier statements that a test was pending.

## Identity reconciliation

The reviewed inventory identifies product `6442451494`, model `GW-IPC-AK-AV100.25`,
SDK `16.20.16355`, and installed software 40.1.22 for the configured pair versus
40.1.14 for the test unit. It does not include the complete per-unit revision/hardware
and opaque camera association required by `ValidatedProfile`. Do not fill these gaps
by copying another unit's identity or assuming the announced OTA version is installed.
Reconcile original saved identity payloads with the backend's current association before
importing profiles; the summaries alone are insufficient. No runtime profile was installed.

### Original capture reconciliation

The bounded offline auditor `python -m scripts.audit_yoosee_identity` recovered the
missing fields from `capture-iotvideo-20260824-025721.log`:

| Source line | Historical software | Revision | Hardware field |
|---|---|---|---|
| 571 | 40.1.22 | 1 | Explicit empty string |
| 588 | 40.1.22 | 1 | Explicit empty string |
| 594 (test unit) | 40.1.14 | 1 | Explicit empty string |

All three callbacks provide product/model/SDK as recorded above. A read-only lookup
against local `data/ccg.db` associated the test unit with an opaque camera ID; the
other two callbacks had no association returned by that database. This is a statement
about the inspected file, not proof that all running deployments lack associations.
The exact mapping remains in ignored research notes. No database rows were changed.

The auditor consumes at most 32 MiB per explicit capture and 1 MiB per line, outputs
only normalized identity, line/source and fingerprinted device IDs, and uses SQLite
`mode=ro`. It does not recursively scan, import profiles, consult cameras or expose
tokens. Historical callback adaptation into the pure normalizer is not authentication
proof for a current session. Original per-unit identity gaps are now closed for this
capture; current identity and deployment association still require reconciliation before
runtime migration. The successful physical tests need not be repeated merely to fill
these historical fields.

### Current test-unit identity verified (2026-09-10)

Container mount inspection confirmed `ccg-app` uses the repository's `data` directory;
its effective DB path is `data/ccg.db`. One exact-test-unit read was then performed
using the existing enrollment, without credential renewal, media allocation or actions.
The four allowlisted roots completed in 2.61 seconds, each with transport ACK and
correlated application success (`error=0`). The full identity matched the historical
test-unit record: product/model/SDK above, revision 1, hardware empty, firmware 40.1.14.

Normalized property evidence: night vision, orientation and Smart Protection supported;
cry detection unsupported. Video/guard property timestamps were older than the server's
receipt time, reinforcing why configuration timestamps are not receipt/expiry clocks.
This is live validation of the correlated B8 collector and identity normalization, not
a new physical write/audio test or proof that the broker bypassed its cache.

No snapshot was persisted, no profile imported and no dashboard gate changed. Credentials
were loaded through a read-only database connection and never printed. Exact local run
details are kept in `re/notes/capability-identity-reconciliation.md`. Next implementation
can bind the existing test-unit operation proofs to this now-matched exact identity;
the other cameras remain outside test scope. Keep source provenance and distinguish
the proven options and transports listed in the audit.

### Exact test-unit proof registration

`capability_profiles` now stores backend-reviewed per-unit profiles atomically with
source digest/locator per operation, complete identity, review time and rule revision.
Older/equal-time reviews cannot overwrite newer ones; lookup requires exact identity
and excludes future reviews. References are audit provenance, not a substitute for
human physical confirmation. No public registration endpoint or brand allowlist exists.

After rechecking the local enrollment association, the test unit's reviewed historical
profile was registered in the local application database. Only these operations were
included: orientation write (`normal`, `inverted`), night-vision write (`automatic`,
`daytime`), Smart Protection master read/write. Sources are the hashed CAPABILITY-MAP
and smart-protection-schedule notes; exact operational data stays in ignored RE files.
No night option 2, siren, audio, schedule or other camera was registered by this step.

`controls.stored_catalog` connects this profile source to the evidence-backed migration
preview. The default catalogue still does not invoke it. Registration itself enables
nothing: the preview requires a valid separately collected snapshot. No live read or
camera action was performed in this registration step; only the new profile table/row
was written locally. Next: collect/persist a fresh test-unit snapshot, verify the stored
preview, then plan runtime transition without accidentally suppressing unrelated controls.

### Controlled runtime rollout completed

The test-unit profile's three controls are now explicitly opted in through the
`yoosee_capability_rollout` record after a successful fresh collection and stored-preview
check. The other five legacy controls and the other units were not migrated by this step.
The default catalogue now uses the stored exact-unit profile and valid evidence for these
selected keys. The generic API enforces advertised options as well, not just the UI.

Evidence refresh is demand-driven and asynchronous with one worker/no queue, a five-minute
attempt interval per unit and one-hour local validity. It uses only the bounded four-root
read, without credential renewal or media/actions. Unknown evidence removes selected controls
until a subsequent successful refresh; it never grants unsupported options. Full source-cache
freshness and broader feature/audio migration remain separate limitations.

Deployment: only `ccg-app` was rebuilt/recreated; `ccg-go2rtc` retained its start time.
The build used a confirmed 512 MiB/one-CPU container limit. Added `temp/` to Docker exclusions
(577 MiB of local material); resulting context was approximately 3.4 MB. No assertion is made
that this alone explains previous WSL failures. Authenticated HTTP smoke check returned all
three registered cameras, camera-3 normal/inverted and automatic/daytime options, guard-master
read/write, and HTTP 200 for status. Deployed build: `b-2c117c71d5fa`.

89 targeted tests passed, plus Ruff and mypy. No camera control write, sound, siren or light
test was sent. Local operational scripts/proofs remain ignored. The migration implemented
here is complete for these three test-unit controls, not for every Yoosee feature or camera.

### Fourth migrated control: guard schedule

The existing camera-3 schedule homologation (complete plan mask 127→126→127, successful
readback/restoration) now has its own profile entry and source digest/locator. Independent
structured evidence checks the complete plan, not just master enable. Its parser is shared
with the production schedule operation; no inferred feature-specific wire commands were added.
Snapshot rule revision 3 was verified with a fresh four-root read before activating schedule
in the exact-unit rollout. HTTP smoke check confirms all four migrated controls, including
weekly_schedule read/write. Build b-d5a1f44f01fe deployed; no new write to the camera occurred.

## Implemented from this audit

The existing four-root collector already retrieves both relevant roots, so no extra
network requests are needed for orientation and Smart Protection master evidence:

- `videoParm.multiFlip` 1/3 is supported, -1 unavailable, other values unknown;
- `guardParm.enable` 0/1 is supported; zero is disabled, not unsupported;
- booleans, missing values and error-domain numbers do not become support;
- neither rule grants schedule/siren/audio support or executes an operation.

Snapshot rule revision is now 2, invalidating earlier persisted evidence until an
explicit refresh. The legacy catalogue is unaffected. The migration preview can use
these states once exact-unit operation proofs are supplied by a trusted backend source.

Next: reconcile saved per-unit identity/association, represent proof provenance and
audio transport separately, then migrate the default catalogue with mixed-camera tests.
Keep unknown models unknown without erasing the historical successful physical tests.
