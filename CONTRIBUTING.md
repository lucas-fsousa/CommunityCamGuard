# Contributing to Community Cam Guard

Thanks for helping! CCG aims to support **as many generic ONVIF/RTSP cameras as possible**.
The most useful contribution is usually **adding support for a camera you own**.

## Dev setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                       # run the test suite (no cameras/network needed)
python -m backend.app.main   # defaults to 0.0.0.0:3200; set HOST=127.0.0.1 for loopback-only dev
```

See `README.md` for the full architecture and `docs/DECISIONS.md` for the design rationale.

## Adding support for a new camera — write a driver

All camera-family knowledge lives in **one place**: the `backend/app/drivers/` package. Each brand
gets its own package (`drivers/mybrand/`), even when it currently supplies only discovery metadata.
Start with `driver.py` and an `__init__.py` export; add model profiles, controls and protocol adapters
only when implemented. Generic API code accepts only semantic operations and dispatches
them through `CameraDriver`; never add vendor imports or raw command payloads to an API router.

### Non-negotiable capability rule

Selecting a driver identifies **who knows how to inspect and operate the camera**; it does not imply
that every camera claimed by that driver supports every feature implemented in its package. Support
is resolved for the concrete camera, model and firmware:

- `probe(camera)` records discovered media/device capabilities;
- `control_catalog(camera)` returns only semantic controls supported by that exact camera;
- `supports_audio_messages(camera)` and `supports_audio_streams(camera)` gate the corresponding UI;
- an unknown, stale or failed capability check fails closed and does not produce a button.

The dashboard consumes only those per-camera descriptors/booleans. It must never branch on brand,
model, driver key, open port, enrollment presence alone or a family-wide static feature list. A
driver may use a runtime probe or a verified model/firmware profile internally, but the generic API
and frontend must not know that mapping. Adding another brand means plugging in another driver—not
adding brand conditionals to the monitoring core.

### 1. Discovery-only driver (just RTSP paths)

Create `backend/app/drivers/mybrand/driver.py`:

```python
from ..base import CameraDriver, DetectContext

class MyBrandDriver(CameraDriver):
    key = "mybrand"                          # short id
    label = "MyBrand 1080p"                  # human name
    rtsp_paths = ("/live/ch0", "/live/ch1")  # ordered path templates, main first
    transport = "udp"                        # "auto" | "tcp" | "udp" (media-layer hint)

    def matches(self, ctx: DetectContext) -> bool:      # recognise the family
        return "mybrand" in ctx.vendor.lower()
```

Re-export `MyBrandDriver` from `drivers/mybrand/__init__.py`, then register the class in
`drivers/__init__.py`. Keep discovery-only support explicit; see
[the partial-driver inventory](docs/internal/driver-package-audit.md). The generic fallback and
shared contracts stay outside vendor packages.

For ambiguous families, override `match_confidence()` (`0..100`) instead. Strong manufacturer,
model, serial or protocol evidence should outrank a shared open-port fingerprint. Add a collision
test proving that another registered family is not incorrectly claimed.

Do not manufacture a MAC for a device that has none. Derive its opaque registry identity from the
driver's durable `serial` or `vendor_device` value; `camera_id` is the registry/media/archive key and
MAC is only optional native discovery metadata. See ADR 0027.

Path templates may use `[USERNAME]`, `[PASSWORD]`, `[CHANNEL]` — a template with `[PASSWORD]`
is only tried when credentials are supplied. The generic capability probe (video/audio tracks
+ codecs from the RTSP SDP) works for you automatically.

### 2. Add controls (PTZ, reboot, ...)

Override the hooks and reuse the ONVIF toolbox in `control/` (`ptz.py`, `device.py`) — or keep a
non-ONVIF adapter inside the family package. See `drivers/yoosee/` for the current example:

```python
    def _probe_controls(self, camera, caps):  # fill family-specific capabilities
        if ptz.supports_ptz(camera.last_ip):
            caps.ptz = True; caps.ptz_protocol = "onvif"

    def ptz(self, camera, direction, action="step"):
        ...  # start/stop/step -> ptz.start/halt/move
```

Anything you don't override stays **`Unsupported`** (the API returns 501), so a partial driver
is fine and honest. Do not add a static family-wide feature set: two models—or two firmware
revisions—handled by the same driver may expose different controls.

For an existing semantic control, implement `control_catalog`, `read_control` and/or
`write_control`. Return the neutral descriptors/results from `drivers/contracts.py`; translate to
the vendor protocol only inside the family package. Do not expose a generic JSON/opcode sender.
Structured controls must use an explicit shared domain type. For recurring automation, reuse
`WeeklySchedule`/`weekly_schedule`; a driver must not advertise a free-form object control.
Runtime choices use `ControlOption` and a `choice` descriptor with `dynamic_options=True`. Return
only semantic values and display metadata; keep native IDs/tokens private and resolve the selected
semantic value again from fresh driver-owned metadata before writing.

### 3. Add factory onboarding (optional)

Keep label parsing, QR/BLE codecs, cloud handshakes and post-Wi-Fi enrollment inside
`drivers/mybrand/`. Implement the structural `OnboardingPort` from `drivers/onboarding.py` and
return the adapter from `MyBrandDriver.onboarding()`. The port crosses into generic API code only
through typed, secret-free DTOs; never return native tokens, peer coordinates, raw frames or a
generic command sender.

Set a stable `driver_key` matching the registered driver key and a human/provider identifier. The
shared provisioning request contracts carry the driver key. Omission is accepted only while the
registry contains exactly one onboarding provider, so tests for a new provider must exercise
explicit selection and ambiguous-selection rejection.

Use the Yoosee package as a layout example, not as a protocol dependency: another family must not
import its codecs, account store or P2P implementation.

### 4. Register it

Add your driver to `DRIVERS` in `backend/app/drivers/__init__.py` (most-specific first; the
generic fallback stays last). Driver keys must be unique, and an onboarding adapter's `driver_key`
must match its registered owner; startup and CI fail fast when these invariants are violated.

**Finding your camera's paths:** [iSpyConnect](https://www.ispyconnect.com/cameras) is a great
per-model database; confirm with `ffprobe -rtsp_transport udp "rtsp://user:pass@IP:554/<path>"`.

Note the exact model(s)/firmware you verified in the module docstring, and add a case to
`tests/test_drivers.py` (paths / detection / control gating).

## Code standards

CI (`.github/workflows/ci.yml`) runs six gates on every push/PR — run them locally first:

```bash
ruff check backend tests   # lint (config in pyproject.toml)
mypy backend/app           # type-check
pytest                     # tests (throwaway DB, no cameras/network)
node --max-old-space-size=64 tests/frontend/camera-controls.cjs  # DOM/control contracts
node --max-old-space-size=64 tests/frontend/recording-playback.cjs  # archive player lifecycle
node --max-old-space-size=64 tests/frontend/settings.cjs  # primary-only settings UI
```

- **Types:** annotate public functions; `mypy` must pass. New modules should be typed.
- **Style:** `ruff` enforces imports, pyupgrade and bugbear rules; line length 100. (`black` is
  configured in `pyproject.toml` but not yet enforced repo-wide — don't mass-reformat existing files.)
- **Tests:** every bug fix gets a regression test; keep coverage **≥90%**
  (`pytest --cov=backend/app` — currently ~91%). Tests must be fast and offline — mock the
  network/subprocess layer.
- **Secrets:** never commit real camera credentials, IPs/MACs, tokens or `.env`. Use fake examples
  (`aa:bb:cc:dd:ee:ff`, `192.168.1.x`). The `.gitignore` already excludes `data/`, `re/`, `.env`.

## PR flow

1. Branch off `main`; keep the change focused.
2. Make the six gates above green; add/adjust tests.
3. Note the exact camera model(s)/firmware you verified (for driver PRs) in the module docstring.
4. Open the PR with a short *why*. Match the surrounding style; keep modules cohesive.

After pushing, check the workflow for the **exact pushed SHA** and wait for its terminal
result before declaring the milestone verified. Local focused tests do not establish CI
success. With GitHub CLI, locate the run using `gh run list --commit <sha>`, inspect it
with `gh run view <run-id>`, and read failures with `gh run view <run-id> --log-failed`.
Fix failures before continuing unrelated milestones; never silently disable a failing gate.
Use Python 3.12, matching CI, for full-suite reproduction. On memory-constrained WSL hosts,
run serially in a capped disposable container without mounting `.env`, data or recordings;
keep focused local checks separate from full-suite/remote CI evidence.

## Guidelines

- **Be gentle with cameras.** These cheap devices hang under connection pressure — discovery
  reuses one connection, throttles, and caps concurrency. Keep it that way.
- Keep it **standard-library-first** in the discovery layer (no heavy ONVIF deps).
- Prefer the driver interface over per-vendor `if` branches scattered through the app.

### Dashboard build identity

Do not add or increment manual `?v=` asset versions. The server hashes the executable source and
`frontend/boot.js` applies that content ID automatically. A frontend edit is visible after reload in
the compose bind-mount workflow; rebuild the app image only when backend code changes. CI tests fail
if date-based asset versions are reintroduced.

### Settings, documentation and browser validation

When adding/removing a `Settings` field, update
[`settings-inventory.json`](docs/internal/settings-inventory.json) and its
[application/security plan](docs/internal/settings-dashboard-plan.md). CI checks every field is
classified exactly once. The inventory is documentation, not a runtime allowlist. Never serialize
the full Settings object to the browser or make credentials/paths/operator overrides writable
through a generic form. Future management routes must use `require_primary_session` server-side;
UI flags alone are not authorization. See the [session migration contract](docs/internal/session-principal.md).

Keep the root README, [documentation index](docs/README.md), relevant public API/guide and roadmap
consistent with shipped behavior. Preserve historical ADR decisions with dated amendments and
links to current implementation notes. Separate code/unit evidence, isolated browser tests and
physical-camera validation; never promote an unverified driver or planned screen to a feature.

The optional [`check_recording_browser.py`](scripts/check_recording_browser.py) uses a short trusted
H.264 fixture and the real playback controller, without opening the dashboard or contacting a
camera. Follow the [capped execution instructions](docs/internal/recordings-native-playback.md#isolated-real-browser-validation--2026-09-24):
cap the entire browser process tree, not just its launcher; keep audio muted and ensure teardown.
Do not run an uncapped browser/emulator on a shared memory-constrained WSL host. This test forces
capability responses/autoplay and is not native HEVC or mobile-browser homologation.
