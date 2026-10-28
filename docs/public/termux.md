# Experimental Android ARM64 server with Termux

This path runs the Python server and Linux ARM64 go2rtc inside Ubuntu/PRoot on
Android. It does **not** require Docker, and does not certify Docker Compose on
native Linux ARM64.

Deployment status (2026-10-28): this phone experiment was discontinued and the
active installation returned to Docker on WSL. Three simultaneous software-encoded
HD views were not accepted on the tested phone, even with bounded thread counts.
The installation recipe below is retained for experimentation, not as a recommended
always-on deployment. CCG phone services were stopped/disabled; removal of their
files remains pending because the phone SSH service became unavailable.

## What actually passed

On a Redmi Note8 (Android11, aarch64, about3.55GiB RAM), Ubuntu26.04.1/PRoot ran
three original-resolution recorders, one1080p viewer or three640×360 viewers in
bounded tests. Three simultaneous1080p viewers did not pass. These were encoded-media
probes, not a visual browser soak test. Controls, Bluetooth onboarding, background
suspension, thermal behaviour and continuous24/7 service were not homologated on
that phone. See the [measurements](../internal/arm64-termux-check.md).

The recipe below documents the installation approach; a fresh installation of the
latest dependencies has not been re-run as part of this documentation update.

## Install the Linux userland

Use the official [Termux installation instructions](https://github.com/termux/termux-app#installation).
Keep the repository, database and secrets in Termux private storage, not shared
Android storage. In Termux:

```sh
pkg update
pkg install proot-distro
proot-distro install ubuntu
proot-distro login ubuntu
```

If Ubuntu already exists, log into it rather than resetting/reinstalling it.
Check `proot-distro --help` for your installed version; current upstream also accepts
versioned OCI images. See [PRoot-Distro documentation](https://github.com/termux/proot-distro).
Within Ubuntu, confirm `uname -m` reports `aarch64` before using the binary below.

```sh
apt update
apt install -y python3 python3-venv python3-pip ffmpeg libopencore-amrnb0 git curl ca-certificates nano
umask 077
git clone https://github.com/lucas-fsousa/CommunityCamGuard.git
cd CommunityCamGuard
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --only-binary=:all: -e .
mkdir -p bin
curl -fL https://github.com/AlexxIT/go2rtc/releases/download/v1.9.14/go2rtc_linux_arm64 -o bin/go2rtc
echo '359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50  bin/go2rtc' | sha256sum -c -
```

**Stop if the checksum fails.** Version1.9.14 is the binary used in the recorded
test, not a claim that it is the newest release. Then:

```sh
chmod 700 bin/go2rtc
cp .env.example .env
chmod 600 .env
nano .env
```

Set a unique strong `DASHBOARD_SECRET_KEY` before adding cameras. Keep
`GO2RTC_BIN=./bin/go2rtc`, `MANAGE_GO2RTC=true` and `AUTOSTART_SERVICES=true`.

Source checkouts also offer `python build_tools/install_go2rtc.py --arch arm64 --output bin/go2rtc`,
using the same pinned artifact/digest as the manual recipe above. The phone's prior
acceptance used the manual installation; it does not validate the new owned-ingress
worker's memfd/parent-death behaviour under PRoot.

For an initial media-only test, set `CONTROL_SESSION_WARMUP=false`, as in the phone
experiment. Choose recording retention/storage limits that fit the phone; footage
can quickly exhaust internal storage. Do not publish `.env` or database backups.
Changing the primary key later affects decryption of stored camera credentials.

For PRoot, start with `LIVE_FFMPEG_THREADS=1`. This bounds the input decoder,
filter graphs and software H.264 encoder independently; it does not lower resolution
or bitrate. The generic default is2 threads per stage. Unbounded FFmpeg defaults
created60–68 threads per live HD encoder on the tested phone, alongside three
recorders, and the HD players repeatedly reconnected. A thread budget is not a
promise that this phone can encode three HD views in real time: use the existing
per-camera SD/Auto controls or a single HD view when needed. The dashboard's normal
per-camera default remains explicit HD; the grid Auto budget does not override a
user's explicit HD selection.

If a dependency lacks an ARM64 wheel, stop and review it instead of starting a large
native compilation on the phone. Do not disable authentication to bypass setup errors.

## Run and validate

From the repository directory inside Ubuntu, with the venv active:

```sh
python -m backend.app.main
```

Keep this foreground terminal open. On the phone, visit `http://127.0.0.1:3200`;
from another LAN device, use `http://<phone-lan-ip>:3200`. Log in with your own key.
Only the dashboard port should be exposed; keep go2rtc's internal ports loopback-only.
Discovery/multicast can differ on Android; configure the local subnet or add known
RTSP camera coordinates if discovery is unavailable. This guide does not certify
phone-based discovery or controls.

Start with one camera, verify a nonempty recording and playback, then select reduced
live quality before increasing viewer count. Recordings keep their original resolution.
Do not run the phone and another NVR against the same low-powered cameras during
a performance comparison without budgeting the extra connections. Arrange any handover
explicitly; stopping the primary NVR creates a recording gap.

Stop with Ctrl+C. Next launch: log into Ubuntu, enter the repository, activate `.venv`,
and start the same command. For supervised operation and separate development/stable
releases, see the workflow below; neither recipe by itself installs Android boot startup.
Android may suspend/kill background processes; charging, battery management and heat
need device-specific validation before relying on unattended monitoring. A wake lock
alone is not proof of reliability. Keep the server on a trusted LAN; this recipe
does not establish safe public-internet exposure or HTTPS/Web Bluetooth availability.

## Stable phone, development workstation

Keep the validated server on the phone and development on the workstation. Docker
images are **not** the deployment artifact for this PRoot installation: publish a
committed source release, with Linux ARM64 dependencies installed on the phone.
Never upload a dirty working tree, `.env`, RE files or a complete workspace archive.

Build only a revision whose CI passed:

```sh
python scripts/package_release.py --revision <validated-commit> --output temp/releases
```

The resulting source TAR has a JSON manifest containing its full commit and SHA256.
Verify the digest after transfer. Install each release in its own directory and
virtual environment; use binary wheels (`pip install --only-binary=:all: -e .`)
and run `pip check` before activation. Do not compile dependencies on the phone.

Recommended layout inside Ubuntu:

```text
/opt/ccg/
  current -> releases/<validated-commit>
  releases/<validated-commit>/   # backend, frontend, .venv
  shared/
    .env                       # private; stable key and absolute paths
    data/                      # registry, accounts, temporary accesses, settings
    recordings/                # original-resolution footage
    bin/go2rtc
    backups/                   # private SQLite snapshots before upgrades
```

Each release links `.env`, `data`, `recordings` and `bin` to `shared`. Keep the old
release and a SQLite online-backup snapshot before switching `current`. Rollback
requires stopping the service first; if an upgrade migrated the schema, restore a
compatible database snapshot too. Changing only the symlink is not a general database
rollback. Never run two releases against the same database or cameras.

### Supervised services and HTTPS

In **Termux itself**, not inside Ubuntu:

```sh
pkg install termux-services cloudflared
```

Templates in [deploy/termux](../../deploy/termux) provide `ccg.run`,
`cloudflared.run` and a size-limited `log.run`/`log.config`. Install them as the
`run` and `log/run` files of separate `ccg` and `ccg-cloudflared` services under
`$PREFIX/var/service`. Install `log.config` as `log/logs/config`. Make `run` files
executable and keep `down` files present until configuration is ready. Restart
the Termux shell after installing termux-services, or initialize it explicitly:

```sh
export SVDIR="$PREFIX/var/service"
service-daemon start
sv up "$SVDIR/ccg"
sv status "$SVDIR/ccg"
sv -w 40 down "$SVDIR/ccg"
```

Also install `launch.py` as `/opt/ccg/shared/launch.py` inside Ubuntu and
`stop.control` as executable `ccg/control/t` in the Termux service directory.
`ccg.run` binds its private `state` directory into the guest. The stop hook validates
the recorded child's command line before sending TERM to the actual application;
sending TERM only to the PRoot tracer did not stop Uvicorn in the real deployment.

`sv up` starts a normally-down service for this session. Once accepted, `sv-enable
ccg` enables automatic supervision on future Termux service starts. Android may
still suspend or kill Termux; supervision is not a24/7 or boot reliability claim.
PRoot's kill-on-exit behavior must remain enabled so an exited session cannot leave
old encoders running. Confirm port/process closure before starting a replacement.
See the official [Termux services instructions](https://github.com/termux/termux-services).

Use a **dedicated Cloudflare Tunnel** for this phone and point the dashboard hostname
at `http://127.0.0.1:3200`. Reusing a workstation's multi-application tunnel token
would create another connector for that same tunnel and can send unrelated hostnames
to the phone. Keep the dedicated token in
`$HOME/.config/ccg/cloudflared.token`, mode600, in Termux private storage. The template
uses `--token-file`, never a command-line token, and binds metrics to loopback.
See [Cloudflare run parameters](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/).
Configure the public hostname/tunnel routing in Cloudflare before enabling this
service; do not expose go2rtc or change authentication to make the tunnel work.

Set `DASHBOARD_PUBLIC_ORIGIN` to the exact HTTPS origin and
`DASHBOARD_ALLOW_LOCAL_ORIGIN=true` if direct LAN/localhost access is also wanted.
Verify primary and temporary-key login, cookies, WebSocket delivery and recorded
video Range requests through HTTPS. The browser's HTTPS endpoint is independent
of the loopback HTTP connection from cloudflared to the app.

### Handover and subsequent updates

First start with `AUTOSTART_SERVICES=false`: check health, dependency imports,
authentication and blocked unauthenticated APIs without opening camera captures.
Import registry/accounts/settings/access keys over private SSH with the original
encryption/signing keys; do not publish them in the release manifest. Historical
footage on the workstation does not move with the registry. Keep it there unless a
separate storage migration is arranged; do not advertise absent recordings on the phone.

Only after this preparation: stop the workstation's app **and** media hub, confirm
their processes exited, then enable phone media and validate growing recordings,
one bounded viewer and HTTPS routing. Preserve an immediate workstation rollback
until those checks pass. During later development, keep media autostart disabled
on WSL; an intentional camera3 test must first release that camera on the stable
server rather than create competing captures. Deploy a new commit only after its
tests pass, then repeat the bounded smoke checks; leave the stable release untouched
if preparation fails.
