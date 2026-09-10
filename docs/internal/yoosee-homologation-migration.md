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
