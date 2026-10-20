# Experimental Android ARM64 server with Termux

This path runs the Python server and Linux ARM64 go2rtc inside Ubuntu/PRoot on
Android. It does **not** require Docker, and does not certify Docker Compose on
native Linux ARM64.

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
For an initial media-only test, set `CONTROL_SESSION_WARMUP=false`, as in the phone
experiment. Choose recording retention/storage limits that fit the phone; footage
can quickly exhaust internal storage. Do not publish `.env` or database backups.
Changing the primary key later affects decryption of stored camera credentials.

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
and start the same command. This guide intentionally does not install a boot service.
Android may suspend/kill background processes; charging, battery management and heat
need device-specific validation before relying on unattended monitoring. A wake lock
alone is not proof of reliability. Keep the server on a trusted LAN; this recipe
does not establish safe public-internet exposure or HTTPS/Web Bluetooth availability.
