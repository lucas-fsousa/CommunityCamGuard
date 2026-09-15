# Existing-camera P2P recovery — 2026-09-15

## Corrected prerequisite

Earlier investigation incorrectly treated the missing per-device subscription token
as a blocker for brokered controls. Code inspection and live read-only probes confirm
that `open_camera_session` authenticates using the account's access ID/token and
selects the exact device from authenticated inventory. It does not use `dev_token`.
That token remains required for separate subscription operations; none was invented.

The stored account successfully authenticated and saw all three cameras online.
Separate target probes confirmed Garagem and Quintal; term resolution returned false,
but brokered correlated property reads succeeded. This does not claim a direct-LAN
or internet-independent connection.

## Recovery performed

The exact device-ID/MAC associations already documented in ignored RE notes were
checked against the current registry. No IP/name/order matching was used. Before
insertion, both the device enrollment and camera link were checked to be absent.
Two access-only enrollments were stored through the existing encrypted persistence
API using the authenticated account credential. `dev_token` remains `None`.

No manufacturer bind, unbind, factory reset, credential replacement on the camera,
feature write, START, STOP, sound or light command was issued. Camera 3's enrollment
was not changed. The encrypted local database is ignored and is not in Git.

Read-only product/version/axis inspection on **both** installed units returned:

- Product `6442451494`, model `GW-IPC-AK-AV100.25`, revision 1.
- Firmware `40.1.22`, SDK `16.20.16355`, empty hardware descriptor.
- Current axis evidence for left/right/up/down.

Camera 3's physical proof uses firmware `40.1.14`. The driver now explicitly includes
`40.1.22` as a compatible variant of the same model/product/SDK/revision/hardware
and schema. This is a driver compatibility decision, not a claim of a physical
movement test on firmware 40.1.22. Unknown versions/SDKs/hardware still fall back.

## Validation and remaining work

51 focused preparation/integration/renewal tests passed, including both firmware
variants on different device IDs and rejection of unknown variants. Ruff, Mypy and
frontend toast/panel/PTZ contracts also passed. Physical responsiveness/STOP on the
installed units is intentionally not tested in this change.

This recovery was a scoped local maintenance operation, not a new generic auto-link
feature. Future UI recovery should accept an explicit documented device association,
verify authenticated account visibility, and preserve one-to-one registry identity.
Subscription-only features, native video, and LAN-only operation remain separate.

After deployment, `prepare_ptz_route(expected=None)` succeeded using each persisted
enrollment and the driver model selector: Garagem 2120 ms; Quintal 2082 ms. Each
socket was immediately closed without calling START/release/STOP. This proves live
authenticated preparation, not physical movement. The app serves build
`b-066879048dd0`, `/health` returned OK, and go2rtc was not recreated.
