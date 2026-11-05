# Alarm resources on the native stream owner

The existing `alarm_voice` options and selection use the active native account,
not a standalone login. The generic API/UI and per-device driver capability gates
remain unchanged. Without a native owner, the existing legacy path remains.
Recovering/mismatched owners never cause fallback or credential renewal.

## Fixed, silent catalogue reads

Separate small job/inbox/pump modules implement only the existing type4 resource
query: system and custom, page0/size20, language pt-BR. No URL, method, arbitrary
query, device/access ID or vendor resource ID comes from an HTTP client. Queries
share the account's socket and monotonic sequence allocator; each is sent once.
The sole reader handles authenticated fragments and ordinary message receipts.
C0 ACK alone is not success: C1 must match peer/session/generation/sequence before
bounded QuickLZ expansion, and its complete decoded catalogue must match the
requested source type. No partial/truncated list is silently treated as complete.
Signed download URLs are discarded by the existing sanitized decoder.

One generation-bound slot includes queued work and unconsumed results. Deadline
is2seconds per part. Cancellation/close retire private work, never the shared
sequence allocator or media session. No new socket, thread, SDK account or cache.

## Typed selection, not playback

Resolve every semantic option key against a fresh catalogue. Managed devices
still intersect exact-unit/resource-identity proofs; revocation is checked again
after preflight. Submit one typed `AlarmVoiceWriteSpec` containing the internally
resolved resource, producing only `ProWritable.resFile.setVal.resId` D2.
The existing control slot/pump correlates D3; an ACK is not acceptance. Exact
`resFile` support/state is read before and after the write. Matching only the
logical number is insufficient: a replacement resource in the same slot must not
inherit confirmation. Unchanged selection is read-only. Rejection, owner change,
timeout or readback mismatch never replays a write or opens another account.

This operation selects a future siren sound; it does not invoke `Action.expelCtrl`,
upload/create/delete custom recordings, play audio, or broaden model support.
Shared-native siren pulse scheduling/cleanup remains separate pending work.

## Verification

Focused tests cover fixed wire shape, typed admission, one send, correlation,
compression/fragmentation, malformed/partial data, bounded inbox cancellation,
silent option conversion, exact resource readback, no-op, failures/revocation and
sole-reader media-route/B7/heartbeat coexistence. Write actuation is simulated;
no real resource selection or audible test has been performed for this integration.
245 focused tests passed with a192MiB/no-swap/50% CPU cap, peak109.3MiB.
Ruff and Mypy393 passed. Full CI42034903925 passed5821tests/1existing skip,
all frontend contracts and type/lint checks; Image42034903946 also passed.

## Deployment and the remaining device-specific gate

Deployed `d5f02a5`, image `f76c74689ace`, dashboard `b-3e810903bb32`.
Only the app container was replaced. Camera3 resumed native/active99; the other
two stayed legacy/active0. Initial MSE during startup returned1013; a later
connection received3→6chunks (1355/286145/495249/268394/471063/283306bytes), with
native revision99 unchanged. All3 subsequently reported online/recording and
their latest archives were growing/recent; no OOM or automatic app restart.

The ordinary `alarm_voice/options` request returned501 BEFORE the native reader
was reached. This is not accepted as a live catalogue test: exact-device support
evidence is supported, but the separate resource-selection profile expired at
2026-10-11T16:30:10Z. Its September11 review explicitly forbids automatic renewal.
The implementation did not extend that expiry, register new permissions, open a
standalone connection or change a selected sound to get around the gate.

Next: perform a deliberate operator review against the original exact camera3
identity/resource digests, using silent catalogue collection on the running native
owner, then explicitly renew only those already-proven resources if they still
match. Do not use the old standalone homologation script while native streaming
owns the account. No new audio or light test is needed to inspect the catalogue.
Until that review is renewed, camera3's public sound selector correctly remains
unavailable; no physical acceptance or working public selection is claimed here.

### Silent operator review transport

The existing opt-in `/api/internal/diagnostics/broker-reads` trigger now accepts a
server-only `BROKER_READ_DIAGNOSTIC_CATALOG=true` profile. It additionally requires
`BROKER_READ_DIAGNOSTIC_ENABLED=true` and the reviewed camera/device settings.
Like the original diagnostic, it is direct-loopback/primary-session only, hidden,
single-use per process and accepts no body/query/target from HTTP. Heartbeat/worker
profiles cannot be mixed with it. This profile requires the existing native owner;
it never falls back, authenticates, writes a camera setting or registers/renews a
proof. Its output contains sanitized options and resource identity digests only.
Disable temporary diagnostic settings after the deliberate review.

The actual native-owner review returned200 with complete system4/custom0 at
native revision102 unchanged. The two previously proven resource identity digests
matched exactly; a deliberate operator re-review renewed ONLY those two until
2026-12-05T13:30:24Z. No automatic expiry extension was added to the application.
Private provenance: `re/notes/native-alarm-resource-review-2026-11-05.md`.
No resource-selection write or playback was performed. The first deployment's
expired-profile gate is thus resolved; repeat the public option/MSE check after
the separate transport failure below is recovered.

## Separate observed transport failure

Camera3 later blocked103 after native/active102: worker protocol site
`feed:47/_fail:31` identifies the strict MTP TCP framer's invalid record-length
rejection. Socket closure and outer broker release were confirmed; the inner
CLOSE receipt was not. The former classifier lacked this exact label and left
the source blocked. Wire contents were not retained, so no offending length,
underlying firmware cause or causal link to the catalogue query is asserted.

The follow-up maps ONLY `invalid MTP TCP record length` to `mtp_record_length`.
The parser still rejects it and discards the entire connection. Sequential
backoff/reconnect is admitted only after confirmed outer release, physically
closed broker/TCP sockets and confirmed motion cleanup. Unknown framing/checksum,
authentication/evidence failures and uncertain cleanup remain blocked. This does
not make malformed bytes valid or expand packet/memory limits.182 focused tests
passed under192MiB/no swap/50% CPU, peak110.8MiB. CI also exposed an omitted
server-only settings-inventory classification for the new diagnostic flag;
that explicit classification is fixed without exposing it through the dashboard.

## Final deployment and ordinary dashboard acceptance

Deployed correction `22fc8d1`, image `1cd4f7556c1b`, build `b-c66ccfb36649`.
The already-blocked old generation was restored through the existing trusted
operator checkpoint AFTER confirming closure of both old Docker namespaces under
the exclusive server lock. This was a brief declared app/hub interruption, not
camera reboot or proof-free recovery. The new invalid-length retry classification
has synthetic coverage; this manual restoration is NOT an injected-fault proof
of automatic recovery.

Normal startup restored native/active105. Ordinary authenticated GET
`alarm_voice/options` returned200 with exactly Zumbido1/Zumbido2, not the other
two unproven resources. The same MSE socket advanced3→6chunks
(1355/250826/496147/247678/503523/271643bytes); source revision105 stayed active.
All three cameras reported online/recording, app restart count0/no OOM.
The internal diagnostic returned404 after its temporary settings were disabled;
the ignored Compose override was removed. No real sound-selection write, siren,
audio, light or movement was sent during these follow-ups.
