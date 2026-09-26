# Public control/account errors — 2026-09-26

Implemented in source, not deployed. No real camera/account requests or production
secret values were used. Temporary login remains disabled.

## Findings and scope

Generic controls, compatibility vendor controls, intercom and PTZ were returning
`str(exc)` from drivers/transports. Vendor-account login/refresh did the same.
Exceptions can contain credential-bearing URLs, tokens or provider response bodies;
being authenticated is not justification for reflecting that material.

`api/control_errors.py` now projects fixed messages by exception category, retaining
404 (missing camera), 501 (unsupported), 409 (busy/not-ready) and 502 (operation failed).
These HTTP errors carry no-store. PTZ parameter errors retain 400 with fixed wording.
Intercom WebSocket errors also use the projection; generic transport failures and
timeouts have fixed messages. No exception string is included in those frames.
Mapped HTTP control failures suppress their original exception chain.

Vendor-account errors retain 422/409/502 but return fixed parameter/session/login/
refresh messages with no-store and suppressed exception context. Selected add-camera,
discovery and service-resync warnings now log exception type, not exception text.
Camera identifiers in those logs remain operational metadata, not anonymized data.

Driver capabilities/control selection are unchanged and remain owned by drivers.
The helper is brand-neutral. Error text is intentionally less specific: clients must
use status/state rather than parsing arbitrary vendor exception prose. More detailed
public reasons should use a reviewed typed contract, not a regex scrubber or raw
transport messages. Successful provider payloads and all SDK logs are not covered
by this patch.

## Verification

129 focused tests passed across sanitized projections, controls, audio, PTZ, account
provisioning and existing route behavior. Synthetic errors contain a fake RTSP URL,
password and token marker; tests verify they do not appear in HTTP/WebSocket output
or a selected resync log. Hardware dispatch is replaced by fakes, including the
audio queue. Peak memory 89.7 MiB, no swap, capped at 512 MiB/75% CPU. Mypy passed
209 files and ruff passed. No audio/light/siren command was sent.

## Outstanding security work

Follow-up: [discovery query credentials have been removed in source](discovery-credential-body.md),
with query-free bundled HTTP access logs. Deployment/proxy validation remains pending.

- **Priority:** `/api/discovery/scan` currently accepts username/password in query
  parameters. Even a later response scrub cannot erase a URL already captured by
  an HTTP server/proxy log. Move credentials to a bounded JSON body, stop generating
  credential-bearing URLs, and document legacy-client migration. Do not inspect
  historical production logs or print credentials as part of that change.
- Audit remaining BLE/privileged/onboarding errors separately: some convey required
  operator recovery guidance and need typed safe reasons rather than blind deletion.
- Validation errors outside login can still echo submitted fields. Review sensitive
  request schemas and introduce credential-safe validation responses.
- Audit successful camera/driver payloads, exceptional errors and transport/SDK logs.
  This checkpoint is not global secret redaction, a proof of no historical leakage,
  or an approval for internet exposure. Browser/proxy acceptance and rollout remain.
