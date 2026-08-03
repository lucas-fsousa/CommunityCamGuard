# Contributing to Community Cam Guard

Thanks for helping! CCG aims to support **as many generic ONVIF/RTSP cameras as possible**.
The most useful contribution is usually **adding support for a camera you own**.

## Dev setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                       # run the test suite (no cameras/network needed)
python -m backend.app.main   # run the app on 127.0.0.1:3200
```

See `README.md` for the full architecture and `docs/DECISIONS.md` for the design rationale.

## Adding support for a new camera — write a driver

All camera-family knowledge lives in **one place**: the `backend/app/drivers/` package. A
**driver** is the single plug-in unit — it bundles discovery paths, family detection, and
controls (PTZ, reboot, ...). The rest of the app (discovery, the capability probe, the API,
the dashboard) is generic and talks only to the `CameraDriver` interface, so adding a brand
is **one new file + one line in the registry** — no engine changes.

### 1. Discovery-only driver (just RTSP paths)

Create `backend/app/drivers/mybrand.py`:

```python
from .base import CameraDriver, DetectContext

class MyBrandDriver(CameraDriver):
    key = "mybrand"                          # short id
    label = "MyBrand 1080p"                  # human name
    rtsp_paths = ("/live/ch0", "/live/ch1")  # ordered path templates, main first
    transport = "udp"                        # "auto" | "tcp" | "udp" (media-layer hint)

    def matches(self, ctx: DetectContext) -> bool:      # recognise the family
        return "mybrand" in ctx.vendor.lower()
```

Path templates may use `[USERNAME]`, `[PASSWORD]`, `[CHANNEL]` — a template with `[PASSWORD]`
is only tried when credentials are supplied. The generic capability probe (video/audio tracks
+ codecs from the RTSP SDP) works for you automatically.

### 2. Add controls (PTZ, reboot, ...)

Override the hooks and reuse the ONVIF toolbox in `control/` (`ptz.py`, `device.py`) — or add
a new toolbox for a non-ONVIF brand. See `drivers/yoosee.py` for a full example (ONVIF PTZ on
port 5000 + device info):

```python
    features = frozenset({"ptz"})           # advertise what you support

    def _probe_controls(self, camera, caps):  # fill family-specific capabilities
        if ptz.supports_ptz(camera.last_ip):
            caps.ptz = True; caps.ptz_protocol = "onvif"

    def ptz(self, camera, direction, action="step"):
        ...  # start/stop/step -> ptz.start/halt/move
```

Anything you don't override stays **`Unsupported`** (the API returns 501), so a partial driver
is fine and honest.

### 3. Register it

Add your driver to `DRIVERS` in `backend/app/drivers/__init__.py` (most-specific first; the
generic fallback stays last).

**Finding your camera's paths:** [iSpyConnect](https://www.ispyconnect.com/cameras) is a great
per-model database; confirm with `ffprobe -rtsp_transport udp "rtsp://user:pass@IP:554/<path>"`.

Note the exact model(s)/firmware you verified in the module docstring, and add a case to
`tests/test_drivers.py` (paths / detection / control gating).

## Code standards

CI (`.github/workflows/ci.yml`) runs three gates on every push/PR — run them locally first:

```bash
ruff check backend tests   # lint (config in pyproject.toml)
mypy backend/app           # type-check
pytest                     # tests (throwaway DB, no cameras/network)
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
2. Make the three gates above green; add/adjust tests.
3. Note the exact camera model(s)/firmware you verified (for driver PRs) in the module docstring.
4. Open the PR with a short *why*. Match the surrounding style; keep modules cohesive.

## Guidelines

- **Be gentle with cameras.** These cheap devices hang under connection pressure — discovery
  reuses one connection, throttles, and caps concurrency. Keep it that way.
- Keep it **standard-library-first** in the discovery layer (no heavy ONVIF deps).
- Prefer the driver interface over per-vendor `if` branches scattered through the app.
