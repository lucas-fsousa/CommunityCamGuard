# Public files and exceptional routes — 2026-09-26

Source checkpoint, not deployed. Temporary login remains disabled. Inspection and
tests did not read production secret values, access camera hardware, start encoders,
build images or restart containers. No claim of a completed internet-facing audit.

## Findings and changes

1. The static mount previously served any file under `FRONTEND_DIR`. Although this
   was not the repository root by default, accidentally copied `.env`, backups or
   RE material inside that directory could become public. `frontend_static.py` now
   permits only the 21 explicitly reviewed HTML/JS/CSS assets (plus `/` → index).
   Unlisted files/directories, source maps and asset symlinks return 404. No HTML
   fallback or directory index is enabled. Ordinary HEAD/Range/ETag behavior remains,
   with `X-Content-Type-Options: nosniff`. Adding a public asset requires updating the
   allowlist and its inventory test. Query-string build identifiers still work.
2. Archive routes already required authentication and resolved containment inside
   `recordings`, including rejecting symlinks outside that root. However, any file
   inside the root was eligible, including non-video files with `original=true` or
   download. The shared resolver now requires a `.mp4` resolved target and no hidden
   path components relative to that root, before naming/probing/conversion. Original
   delivery, preparation, playback status and downloads share the restriction.
3. `.dockerignore` excluded the primary `.env` but not all environment variants or
   common nested secret/backup filenames. Added environment variants, nested VCS,
   key/certificate/database/backup patterns and the root backups directory. Dockerfile
   uses explicit COPY paths, not COPY of the entire repository. Its stale loopback-only
   app comment was corrected: the authenticated dashboard defaults to LAN port 3200.

These are defense-in-depth boundaries. Listed assets must actually contain public
code; renaming a secret to `app.js` or `.mp4` is not made safe by suffix checks. The
asset allowlist does not replace filesystem ownership or defend against a local
writer racing filesystem checks. Production values were not scanned, and no image
artifact/context tarball was built or inspected. Build-rule tests check declarations,
not every possible sensitive filename or a Docker daemon's implementation.

## Route inventory and remaining GET work

Follow-up: [GET conversion initiation was removed in source](recording-get-boundary.md).
The finding below records the audit-time behavior; deployment remains pending.

`tests/test_route_auth_inventory.py` inspects current router wiring/dependencies
without invoking endpoints. Ordinary HTTP routes require `require_auth` or
`require_primary_session`; the native AV diagnostic retains its custom wrapper that
first authenticates, then checks locality/direct loopback, server opt-in and reviewed
target. The explicitly public application routes are login/logout, `/api/me`,
`/api/build` and `/health`. API schema, Swagger/ReDoc and Swagger's redirect page
are intentionally public. Public assets or schema do not grant authenticated API
access. The two custom-auth sockets (media and intercom) retain explicit cookie and
origin checks. Unreviewed public routes/router wiring fail the inventory test.

This is structural coverage, not a proof that every dependency implementation or
response is safe. Existing runtime origin/auth/channel tests provide separate evidence.

The GET review found no explicitly declared PTZ/reboot/light/siren mutation endpoint
using GET. However, reads are not uniformly work-free:

- `GET /api/recordings/file` can start bounded compatibility conversion on a cache
  miss. Preserve compatibility in this checkpoint; follow up by moving initiation
  exclusively to authenticated POST `/recordings/prepare` and testing legacy callers.
- Playback-status may probe codecs; discovery/network/control/status reads can query
  services/devices or populate caches. Do not describe all GETs as side-effect-free.
- Anonymous errors, diagnostics, media error payloads and exceptional/custom-auth
  routes still need deeper secret-redaction and method/authority review.

## Verification and next work

119 focused tests passed: public/hidden/unlisted assets, synthetic secret markers,
encoded paths, symlink rejection, expected asset compatibility, rejected archive
paths before work, route authority inventory, existing recording/session/origin
regressions. Test peak 94.8 MiB, no swap, under a 512 MiB/75% CPU cgroup. Mypy passed
208 files and ruff passed. No production files or camera commands were exercised.

Next: remove conversion initiation from the legacy GET path with compatibility tests,
continue response/error redaction and exceptional-route audit, then final temporary
permissions and real browser/proxy acceptance before login/UI activation and deployment.
