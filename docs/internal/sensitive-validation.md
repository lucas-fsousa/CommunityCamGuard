# Sensitive HTTP validation errors

Source checkpoint: 2026-09-26. Backend not deployed; no real camera commands,
credentials, scans or container restarts were used.

## Boundary

FastAPI request validation errors can contain rejected input, locations and custom
validator context. `SecretStr` protects a successfully parsed value, not every
invalid raw input. The main application now registers one `RequestValidationError`
handler returning HTTP 422 with `{"detail":"Invalid request parameters"}`,
`Cache-Control: no-store` and `Pragma: no-cache`.

The handler never reads or serializes the exception, its body, `errors()`, locations
or validator messages. This covers HTTP body, JSON, path and query schema failures,
including future routes registered on this application.

## Compatibility and limits

- Framework validation details change from an array to a fixed string. Clients must
  not depend on framework input/error structures. Any future field-specific feedback
  needs an explicitly reviewed safe contract.
- Authentication failures, business `HTTPException`s and successful responses are
  unchanged. Login, discovery and access-key management retain their own safe errors.
- Schema/auth execution order and body limits are unchanged: malformed JSON may be
  rejected before normal dependencies run. This is not a request admission limit.
- Response validation, WebSocket validation, logs and separate/mounted applications
  are outside this handler. Applications importing only routers must register it.
- Remaining provisioning business errors, successful driver payloads and SDK logs
  still need review; this is not a claim that all credential exposure is eliminated.

## Evidence and next steps

Focused coverage uses synthetic invalid camera/account/Wi-Fi/BLE inputs, malformed
JSON, path/query values and an exception that fails if inspected. It checks the
application registration, cache headers and preserved authentication denial. Hardware
and storage dispatch are blocked in these tests.

Final local validation: 146 focused tests passed under a 512 MiB / 75% CPU cap;
measured peak memory 87.7 MiB, zero swap. Ruff and diff whitespace checks passed.

Backend rollout and real browser/proxy acceptance remain separate pending work.
Next: typed, safe provisioning business errors without losing recovery instructions.
Follow-up: [Wi-Fi/QR error projection](provisioning-public-errors.md) is now implemented;
Handled BLE/session errors now also use typed recovery reasons; label and privileged
business errors remain pending.
