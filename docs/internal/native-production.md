# Native media delivery, not another finite trial

The delivery target is camera3 on the ordinary Docker app and existing dashboard,
with persistent source selection, original HEVC/AAC recording and shared native
PTZ. Other cameras remain on their existing transports. No sound/light/motion
commands are required for installation.

The host operator command `scripts.enable_yoosee_native` stops the old ingress,
backs up the actual database/configuration privately, registers the target's
driver-reviewed media/combined PTZ catalogue without dropping its existing controls,
and uses normal native readiness/clean shutdown to create the startup checkpoint.
The app container then resumes it normally. It does not keep a trial server or
emulator running, nor copy fabricated checkpoints from a test database. Its
systemd cleanup hook restores the backup only if installation has not committed.
Recording files are never deleted by installation/rollback.

Native protocol support uses recorded exact model/firmware identity and explicit
driver review, not the30-minute age of a control-state observation. Expiring that
cache is not expiring authentication or changing the camera's supported protocol.
Clock expiry alone therefore no longer kills native video/PTZ. Explicit probe
invalidation, different identity/enrollment, revoked catalogue, authentication,
packet validation, scoped motion/STOP and single-capture exclusion remain enforced.
Other control-state freshness policy is unchanged.

The native source reports recording activity through the generic source registry,
so dashboard online/recording indicators do not depend on a legacy go2rtc producer.
Docker gives the app45s for a clean stop. Clean restart is supported; abrupt-kill
recovery and prolonged soak/extra hardening are deferred, not release gates.

Only streaming and reviewed shared PTZ/state reads are provided by this native
owner. Features that still require a separate account connection remain subject
to the existing shared-account exclusion; this delivery does not pretend all
native shared-write features have been implemented. Other-camera foreground
controls can cause a clean native-session yield/reconnect through existing logic.

## Deployment follow-up (2026-10-29)

The first ordinary-container installation reached readiness but then blocked;
the follow-up installation rolled back because the container did not stay ready.
Do not confuse an installation marker or a passing finite trial with current
production availability. The other two cameras retain their legacy transports.

The native failure log now records only local closure facts and fixed protocol
classifications. TCP/AV error sanitization previously hid the underlying KCP
classification, so the narrow, already implemented timeout reconnection could
not run for failures raised during receive. The worker now walks a bounded
exception-context chain to recover that classification, without logging messages
or payloads. Reconnection still requires confirmed outer route release, both
sockets closed, confirmed motor stop when applicable and retired consumers.
Unknown protocol failures remain blocked. This correction passed 51 targeted
worker/recovery tests.

The corrected installation committed successfully in the ordinary app container
(`5de4e7f`, build `b-6932aecc11d2`). Camera3 is native/active at revision11;
the other two cameras remain legacy/active at revision0. An authenticated request
to the existing dashboard MSE WebSocket received H264/AAC initialization and three
binary fragments (1,165,374 bytes). All three cameras report online/recording and
all three recording destinations grew during the follow-up check. The installer
exited (169.5MiB peak, no swap); only the ordinary containers remain serving.
No sound, light or movement commands were sent. Existing shared native PTZ
implementation/physical acceptance is retained, not retested as a release gate.
