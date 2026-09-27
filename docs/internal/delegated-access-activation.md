# Delegated access activation in source — 2026-09-27

The owner's requested policy is now implemented: each key chooses its own features
and may expire at a fixed UTC instant or never expire. Creation/revocation and
server administration always require a fresh primary-key session. No delegated
grant can create credentials, enroll/remove/probe cameras or alter server settings.

## Scope and enforcement

The creation modal grants live audio/video, server recordings (view + download),
PTZ, intercom, reboot and individual canonical camera controls. Grants apply to all
configured cameras, intersected with each driver's capability evidence. Per-camera
scoping and separate download/listen-only grants are not implemented. New control
names deny by default and require an explicit platform permission addition; a new
driver cannot automatically expand a guest's authority.

Login first checks the primary key, otherwise validates a generated delegated key.
It signs only session identity and key ID, not client-supplied rights. Every HTTP
operation reloads current key validity and grants. Null expiry never exempts the
key from revocation or the signed cookie's independent seven-day ceiling. Old
staged keys migrate to their previous live/recordings grants; IDs, verifiers and
revocations are preserved. No plaintext key is persisted.

Live access is limited to the existing bounded MSE bridge and registered camera
HD/web stream IDs. It checks the live grant before opening upstream, then checks
validity while delivering. Delegated WebRTC/arbitrary upstream proxying stays
denied. Intercom checks its own grant and keeps the existing local-only restriction,
PCM bounds and periodic socket cancellation. HTTP audio messages/finite commands
already accepted may complete: revocation cannot undo an already executed camera
action. Recording delivery retains its periodic transfer cancellation guard.

The dashboard fetches only authorized feature data on startup, filters controls
without changing driver evidence, blocks administrative navigation, suspends live
players without the live grant, and clears access state on logout. `/me` drives
the existing one-second invalidation watcher. Server validity is authoritative;
browser polling is not an authorization substitute. Normal invalidation takes the
poll/check interval, not mathematically instantaneous cancellation. Previously
downloaded/decoded data cannot be recalled.

## Verification and rollout

ASGI tests cover owner creation, delegated login, selected rights, denial of key
management, revocation, exact expiration and independent primary access. A real
WebSocket-route test proves a PTZ-only key cannot open a registered live source.
Fake-media/Node contracts cover grant intersection, navigation, secret disposal,
uncertain creation, modal dismissal and session cleanup. Existing channel guards,
PCM endpoints and recording delivery are exercised with synthetic data only.
Targeted authentication/channel/frontend tests passed (226 tests before the final
registered-source denial addition); peak observed local test memory was 121.1 MiB
with zero swap. The full GitHub CI passed for activation commit `155f6d5`, including
all Python and Node suites. Later UI polish distinguishes denied permissions from
unsupported hardware and hides video-only controls for control-only guests.

This is source implementation, **not deployment or browser/device homologation**.
No production keys, camera commands, browser, containers or WSL-heavy SDK process
were started. Before rollout: back up the registry database, deploy backend and
frontend together (new modules require the static allowlist), then test an owner
and a guest in separate browsers, expiration/revocation during live/recording/talk,
and desktop/mobile dialog layout. Never run two software versions against a shared
database during the schema migration; rollback requires a compatible schema or
the database backup. Internet exposure still requires the separate proxy/security
validation; adding access keys is not an internet-security certification.
