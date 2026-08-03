# 0014 — Dashboard i18n with no build step

**Status:** accepted · **Date:** 2026-07-28

## Context

The dashboard shipped en-US only. It should read in the user's language while keeping English as the
open-source default — **without** adopting a framework or build step (the dashboard is a plain
HTML/JS app served static, by design).

## Decision

A tiny localisation layer, `frontend/i18n.js`:

- A `STRINGS` dict (`en`, `pt-BR`) and `t(key, params)` that fills `{token}`s and **falls back**
  en→key, so a missing translation never blanks the UI.
- `applyI18n()` fills static markup via `data-i18n` (textContent), `data-i18n-ph` (placeholder) and
  `data-i18n-title` (title). Dynamic strings in `app.js` go through `t()`. Loaded before `app.js`;
  exposes `t`/`applyI18n`/`setLang`/`getLang` on `window` (no module system).
- **Language pick:** `localStorage` (`ccg_lang`) wins, else the browser language (`pt*` → `pt-BR`),
  else `en`. A header `<select>` switches live (re-fills static labels and rebuilds dynamic strings —
  no reload).

Adding a locale is **one dict block** in `STRINGS` (en↔pt-BR parity kept). The product name
(*Community Cam Guard*) and protocol identifiers (RTSP paths like `/onvif1`, codec tokens like
`H265`, `PTZ`) are intentionally **not** translated — they're identifiers, not prose.

## Consequences

- Localised UI with zero build tooling; a new language needs no code, just a dict block.
- No pytest (frontend); validated with `node --check`, a `t()` smoke test (interpolation/fallback/
  switch) and key-parity checks.
- **Project convention:** the *documentation* (README, ADRs, code comments, commits) is
  English-only; the pt-BR strings here are UI content, the one deliberate exception.
