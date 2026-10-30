# Native-owner capability refresh — 2026-10-30

The demand-driven capability worker now uses the existing native account when
that camera has a native owner. It does not contend for a second account, log in,
renew credentials, displace video or fall back to the legacy collector if the
owner is reconnecting. Other cameras retain the nonblocking legacy reservation.
The single background worker and five-minute attempt backoff are unchanged.

`shared_capability_refresh.py` collects the six fixed identity/property B7 roots
sequentially, optionally including the siren metadata root with B7 (not the AC
siren action). It then requests type-12 lamp status, never changing illumination.
The complete batch is bounded to20s. Each waiter is cancelled/consumed; every
read and final publication require the same registered enrollment and native
session. Unknown, replaced, rejected or incomplete identity stays untrusted.
Raw values, credentials and vendor responses are neither logged nor published.

Native collection must not use snapshot `begin()`: its intentional invalidation
would revoke the running media owner's recorded identity before the batch could
finish. Instead a generation is read without modifying evidence. A successful
batch atomically replaces the whole snapshot and increments that generation.
An explicit invalidation, newer batch, rule revision or newer receipt wins over
the old collection. Failed collection leaves the original expiry unchanged;
expired controls remain unavailable, not granted by persistent media identity.
There is no new schema, cache lifetime, public endpoint or model-wide grant.
Driver operation proofs, exact identity and generic permission gates still apply.

Offline coverage uses an actual bounded broker mailbox with synthetic completions:
same-owner collection, OFF lamp state, optional siren metadata, errors, deadlines,
enrollment/owner changes, waiter cleanup, CAS supersession/invalidation and expired
evidence. 211 focused tests passed under192MiB/no swap/50% CPU (157.2MiB peak).
Mypy377 files and Ruff passed. No physical commands were used. Deployment and
read-only production observation are recorded below when completed.
