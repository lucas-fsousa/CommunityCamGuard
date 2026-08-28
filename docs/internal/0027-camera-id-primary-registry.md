# 0027 — `camera_id` is the registry primary key; MAC is optional native metadata

**Status:** accepted · **Date:** 2026-08-28

## Context

The API, controls, media and recordings had moved to opaque `camera_id`, but the SQLite `cameras`
table still used `mac` as its physical primary key. The `Camera` model and write path therefore could
not represent a device identified only by serial, vendor device ID or certificate. This left the
central registry less generic than every consumer built on top of it.

Existing installations contain encrypted credentials and operational metadata in the MAC-keyed
table. The migration must preserve those bytes and stable IDs without requiring re-enrollment.

## Decision

- Make `camera_id` the SQLite primary key. Keep `mac` as a non-null compatibility string that may be
  empty, with a partial unique index only for non-empty values.
- Rebuild legacy SQLite tables in one explicit transaction after deterministically backfilling IDs.
  Copy encrypted password blobs, capabilities and timestamps without decoding/re-encrypting them.
- Resolve writes by canonical ID first. A driver may create a camera with no MAC by supplying a
  supported identity namespace/value, and later update it explicitly by `camera_id`.
- Retain exact-MAC lookup/deletion helpers temporarily for discovery and old internal consumers.
  New deletion and cross-driver updates use `camera_id`.
- Treat a MAC correction as an update to native metadata, never a row-identity change.
- Do not expose arbitrary client-selected opaque IDs: IDs are derived by the backend from a durable
  namespaced identity, while the explicit `camera_id` update argument is for trusted internal flows.

## Consequences

- Drivers for cameras without a usable MAC can share the same registry, media and recording stack.
- MAC/ARP/ONVIF discovery remains useful without defining product identity.
- Existing installations upgrade in place with their credentials and public references preserved.
- The current manual `POST /cameras` form still enrolls LAN RTSP cameras by MAC; future driver-owned
  onboarding flows can call the canonical registry path without first inventing a fake MAC.
- A later identity-alias table may associate multiple authoritative native identities with one
  camera; this decision establishes the stable parent key it will reference.
