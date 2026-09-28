# Delegated access: bounded browser validation — 2026-09-28

The real Chromium check reproduced a layout defect missed by fake DOM tests:
the global full-width input rule stretched permission checkboxes and squeezed
their labels on narrow screens. Scoped checkbox dimensions now keep the labels
readable. The modal has a compact dark header, a sticky explicit close button,
bounded scrolling and locked background scrolling. Closing/navigation restores
the previous body overflow and clears the one-time credential.

`tests/frontend/access-keys-browser.html` uses the production settings/access
modules and stylesheet with intercepted, synthetic API responses. No real login,
key, camera command or production dashboard is involved. The browser verified:

- No grants preselected; optional expiration; exact PTZ-only creation payload.
- Form hidden after creation; secret cleared on X and dialog removed on navigation.
- No horizontal overflow at 375×667, 320×568 and 1280×900.
- Close button still reachable after scrolling; no dismissal from cancel/click.
- Desktop two-column grants and narrow-screen single-column layout, inspected in screenshots.

The old settings fixture was missing the new modules' import-map entries. Those
entries and the test server asset map are updated, with a Python regression walking
each fixture's transitive production imports. Settings also passed at 375 pixels
with both numeric fields remaining 88 pixels wide.

Runs were **sequential**, in 512 MiB / zero-swap / 75%-CPU / 128-task cgroups with
65-second runtime ceilings, using the Chromium ELF directly. The first cold run
peaked at 469.3 MiB; successful repeat runs used 172–214 MiB and lasted about two
seconds. A first desktop run completed its checks but hit a profile-cleanup race;
the runner now owns and terminates its browser process group before cleanup. A
rerun exited successfully; the leftover synthetic profile was removed. No browser
was left running. The harness now refuses uncapped cgroups and launcher scripts.

Example (replace repository/browser paths; screenshot destination should be ignored):

```sh
systemd-run --user --wait --pipe --collect \
  -p MemoryMax=512M -p MemorySwapMax=0 -p CPUQuota=75% \
  -p TasksMax=128 -p RuntimeMaxSec=65 \
  /absolute/repo/.venv/bin/python /absolute/repo/scripts/check_recording_browser.py \
  --browser /absolute/path/to/chrome --access-keys --width 375 --height 667
```

These are real desktop-Chromium viewport/component tests, not physical Android/iOS,
full production dashboard or HTTPS-proxy homologation. The lifecycle HTTP checks
and revoked test-key audit rows remain documented in
[deployment evidence](delegated-access-activation.md). Frontend edits are served
through the existing bind mount; no app/go2rtc restart was needed for this repair.
