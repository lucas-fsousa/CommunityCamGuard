# 0015 — Content-addressed build identity and cache busting

**Status:** accepted · **Date:** 2026-08-11

## Context

The plain-JS dashboard used date/counter strings such as `?v=2026-08-11.1` in `index.html` and
`player.js`. Every edit required a human to increment several copies. Concurrent pull requests could
pick the same number, conflict in the HTML, or merge new code without changing the browser cache key.
A generated version file committed to Git would move rather than remove that conflict.

## Decision

`GET /api/build` hashes the runtime Python sources, frontend HTML/CSS/JS and `pyproject.toml`, returning
a short `b-<sha256>` identity. The stable, non-cached `boot.js` requests that identity and loads every
frontend asset with the value as its query-string cache key. `player.js` uses the same value for its
dynamic `video-rtc.js` import. The badge displays that content identity.

The fingerprint is computed from contents, not Git metadata or a mutable counter. This works in a
release image (where `.git` is intentionally absent) and with the compose frontend bind mount: local
frontend edits get a new ID on the next page load without an image rebuild.

## Consequences

- Any executable backend/frontend change automatically changes the build identity.
- Identical source produces an identical ID, including across contributors and CI jobs.
- PRs never edit or conflict over generated version state.
- `index.html`, `boot.js` and `/api/build` are always revalidated; content-keyed assets may be cached.
- The project remains plain JS with no Node/bundler build step.
