# Delegated access: bounded browser validation — 2026-09-28

## Rendered video invalidation — 2026-10-15

`scripts/check_browser_media.py` runs the production dashboard/player against
real camera3 media. Test-only instrumentation filters the actual `/api/cameras`
response to the requested camera, selects its existing reduced-quality option
in the disposable browser profile, and rejects other stream URLs and control
writes. Server permissions are unchanged: the live grant still covers cameras
generally. This is not evidence of per-camera authorization or full-grid behavior.

After login, the check observes `requestVideoFrameCallback` and advancing
`mediaTime`, not merely arriving fMP4 bytes or a changing wall clock. The final
probe counts only increasing media timestamps (re-presenting an old image does
not count). It requires eight progressing frames, then expiry/revocation and:
login visible/dashboard hidden, player detached, video paused/source cleared,
all tracked sockets closed, no mounted player and no reconnect during2seconds.
The frames are decoded/presented by headless Chromium, not saved or photographed.

Successful results:

| Transport / operation | Login and media stopped | Peak RAM |
| --- | --- | --- |
| Local revocation diagnostic | 621ms | 229.4MiB |
| Public HTTPS expiry | 657ms after scheduled expiry | 237.4MiB |
| Public HTTPS revocation | 690ms | 242.8MiB |

Each successful run had one socket, no blocked attempts/control writes and no
reconnect in the observation window. The expiry/local runs used the earlier
eight-callback/advancing-timestamp check; the final public revocation additionally
required all eight timestamps to advance. All runs used512MiB/no swap/75%CPU/
128tasks/90sec caps. Afterward: six known test keys revoked, zero Chromium
processes, zero consumers/active producers for the test source, and three cameras
online/recording.21 focused Python tests passed (79.5MiB/no swap), plus Node
isolation/frame/teardown contracts, now included in CI. No app rebuild needed.

An initial test defect was isolated: CDP `Page` domain was not enabled before
installing the new-document safety script. The first live attempt therefore
did not establish camera isolation and may have briefly subscribed to other
configured streams; its result is discarded. No camera control was sent. A
subsequent login timeout remains unattributed. The local diagnostic then confirmed
the missing script. `Browser.page()` now enables the Page domain, and the media
probe refuses login if its isolation observer is absent. Regression tests cover
both preconditions. Corrected isolated runs passed without changing the app.

Example, only on an authorized test camera and one run at a time:

```sh
systemd-run --user --wait --pipe \
  -p WorkingDirectory=/absolute/repo \
  -p MemoryMax=512M -p MemorySwapMax=0 -p CPUQuota=75% \
  -p TasksMax=128 -p RuntimeMaxSec=90 \
  /absolute/repo/.venv/bin/python -m scripts.check_browser_media \
  --browser /absolute/path/to/chrome --camera-id <registered-test-camera-id> \
  --mode expiry
```

Also supports `--mode revoke`; `--loopback` is a diagnostic, not HTTPS proof.
Creates one live-only50second key per run and revokes its known ID in cleanup.
No microphone, speaker/siren/light/PTZ command, media file or primary credential
in the browser. May start one on-demand reduced encoder; check resource headroom
and cleanup. Physical Android/iOS, full-grid load and long-duration stability
remain separate from this bounded desktop-Chromium acceptance.

## Full dashboard through public HTTPS — 2026-10-15

`scripts/check_browser_session.py` now exercises the actual production login form
and dashboard in two real Chromium tabs sharing the browser cookie. The primary
key stays in the Python HTTP client; Chromium receives only a labeled test key
with zero permissions and a45second expiry. No camera control/media is requested.
Known test keys are revoked in cleanup, including after test failure; audit rows
remain. An unknown creation result is reported, not retried. The short expiry
also bounds credentials if the process is forcibly killed before cleanup.

Public HTTPS results, sequential runs:

| Operation | Viewport | Both tabs at login after invalidation | Peak RAM |
| --- | --- | --- | --- |
| Owner revokes test key | 1280px | 1098ms | 261.9MiB |
| Key expires | 375px | 1728ms after scheduled expiry | 228.9MiB |
| Logout in first tab | 375px | 1214ms | 233.5MiB |

Reloading both tabs kept the login visible. The final logout run also directly
confirmed unauthenticated `/api/me` in each reloaded tab. This is application DOM
state verification, not screenshot review or physical mobile/touch validation.
The zero-grant test does not verify decoded live frames or media cleanup in a
browser. Server-side media expiry/revocation has separate acceptance evidence.

First attempt ended in RuntimeError before stage diagnostics were added (25.1s,
455.8MiB); cause not established. Repeat passed without an application change.
Do not describe that intermittent failure as fixed. No production rebuild needed.

Example (replace absolute paths, use exactly one run at a time):

```sh
systemd-run --user --wait --pipe \
  -p WorkingDirectory=/absolute/repo \
  -p MemoryMax=512M -p MemorySwapMax=0 -p CPUQuota=75% \
  -p TasksMax=128 -p RuntimeMaxSec=90 \
  /absolute/repo/.venv/bin/python -m scripts.check_browser_session \
  --browser /absolute/path/to/chrome --mode expiry --width 375
```

Modes: `revoke`, `expiry`, `logout`; widths:375 or1280. Browser binary must be an
ELF, not a launcher escaping the cgroup. Uses only configured public HTTPS origin,
normal certificate validation and a disposable profile. No credentials/screenshots
are logged or retained. The CDP helper owns and terminates its browser process
group; it never kills unrelated browser processes.39 focused tests passed with
85MiB peak/no swap, plus Node session lifecycle contracts. CI now lints both
browser and WebSocket acceptance helpers as well.

## Earlier isolated component evidence

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

After the secret-visibility wording correction, the 320×568 check passed again
(6.84 seconds, 423.2 MiB peak, zero swap) under the same caps. Node tests verify
that the 60-second timeout clears the field/instructions, explains that hiding
does not revoke access, and sends no additional request. Operator-check ASGI tests
also cover a lost first or second creation response: known IDs are revoked while
the unknown outcome is reported for manual review, without retry or bulk revocation.

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
