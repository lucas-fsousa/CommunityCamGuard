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

## Still pending

- Label/driver-resolution business errors currently return raw exception text.
- BLE material/session failures need safe typed recovery reasons (renew material,
  reconnect, or retry) before replacing their current messages.
- Privileged enrollment, P2P and completion errors require the same review, including
  completion stage values. This checkpoint does not harden those paths.
- Successful driver payloads, SDK logs and browser/proxy acceptance remain unaudited
  in this step. Backend deployment is separate from source validation.
