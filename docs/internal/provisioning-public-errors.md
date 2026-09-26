# Provisioning public-error boundaries

Checkpoint: 2026-09-26. Source only; no backend rollout, camera commands, actual
Wi-Fi scans or container operations.

## Completed: Wi-Fi selection and QR generation

Manual selection, QR preparation and BLE preparation no longer expose raw
`WifiSelectionError` text. The Wi-Fi domain supplies a vendor-neutral typed reason:
invalid selection, expired selection, invalid SSID length or unsupported security.
The HTTP projection owns fixed messages, preserving recovery hints such as scanning
again when a selection expires. Unknown reasons fall back to the invalid-selection
message; exception messages are never parsed, formatted or returned.

QR provider `ValueError`s now return a fixed instruction to check SSID, password
and security mode. This intentionally replaces driver-specific raw error text.
These failures preserve HTTP 422 and add `Cache-Control: no-store` and
`Pragma: no-cache`. They suppress exception chaining at the HTTP boundary. Success
payloads, selection signing/expiry, driver dispatch and authentication are unchanged.

This module does not classify capabilities or implement vendor behavior in the API:
the driver still owns label inspection, QR encoding and camera feature support.

## Evidence

84 focused tests passed, covering typed reasons, real validation producers,
malicious synthetic exception text, an unhashable unknown reason, HTTP cache headers,
QR provider failures and authentication denial. Existing provisioning and sensitive
validation suites also passed. Tests stub scanners/providers; no hardware calls.
Tests ran serially with 512 MiB memory / 75% CPU limits: 87.4 MiB peak, zero swap.

## Completed: BLE session/material failures

The driver-neutral onboarding input error now carries an optional typed reason.
Yoosee translates codec errors into that reason without forwarding raw codec text.
Preparation and response decoding project known reasons into fixed HTTP 422 messages:
restart an expired attempt, restart for the correct camera, renew unavailable/expired
material, or ask the administrator to restrict file permissions. Unknown reasons
receive a generic input/retry instruction. No matching against exception text occurs.

Preparation's missing-material errors retain 503; account/transport retrieval errors
retain 502, with fixed account/retry instructions. These responses use no-store and
no-cache headers. Success payloads, expiry durations, frame encoding and actual
camera behavior are unchanged. No new reason field is exposed in the HTTP contract.

110 focused tests passed, including provider/codec translation, expired attempts,
missing material, all public reasons and synthetic secret-bearing exceptions. Tests
ran at 82.2 MiB peak memory with no swap under the same 512 MiB / 75% CPU caps.
This remains source-only, not a physical BLE homologation or container rollout.

## Completed: identification and handled privileged failures

Driver-resolution and label failures now return fixed HTTP 422 instructions rather
than raw lookup/parser text. Handled enrollment state errors retain 409 with
operation-specific guidance: restart a setup session, check a fresh Wi-Fi handoff
before binding, or check durable enrollment material for P2P/completion. Transport
and completion failures retain 502 with fixed connectivity/account/media guidance.

Completion does not expose the driver-supplied stage or exception message. This is
an intentional reduction in diagnostic detail; neither arbitrary stage strings nor
message matching are a public error contract. The API selects an internal enum by
operation/error type; it does not infer a specific root cause. All these projections
set no-store/no-cache headers and suppress exception chaining at the HTTP boundary.
Driver behavior, successful results, authentication and local-only guards are unchanged.

113 focused tests passed, with one non-applicable combination skipped (online-status
has a handled state failure but no transport-error handler). Synthetic failures cover
all changed routes and credential-bearing completion stage/message values. Existing
provisioning/completion/BLE/schema suites passed. Peak test memory: 100 MiB, zero
swap, capped at 512 MiB and 75% CPU. No cameras, scans or containers were operated.

## Still pending

- Unexpected exceptions, successful BLE payloads and lower-level logs need a separate
  review; the handled HTTP error projections are not a complete credential audit.
- Unexpected exceptions and routes without an explicit domain-error handler (such as
  privileged status) remain outside these fixed projections. Logging is a separate
  boundary; suppressing HTTP exception chaining does not sanitize lower-level logs.
- Successful driver payloads, SDK logs and browser/proxy acceptance remain unaudited
  in this step. Backend deployment is separate from source validation.
