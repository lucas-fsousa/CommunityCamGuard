# Bluetooth onboarding

This is the currently homologated way to put a factory-new compatible Yoosee/Gwell camera on the
local Wi-Fi network without navigating the vendor application's setup UI. It was physically
validated end-to-end: the camera accepted the credentials, joined the LAN and appeared in Community
Cam Guard discovery.

## Requirements

- Community Cam Guard running with `docker compose up -d` and reachable by the browser device.
- A strong `DASHBOARD_SECRET_KEY`; the dashboard listens on the LAN at port 3200 by default.
- A phone or computer with **Bluetooth**, a supported Web Bluetooth browser and network access to
  the dashboard. The server may be connected by Ethernet; the browser is the device contributing
  the Bluetooth radio.
- A 2.4 GHz Wi-Fi network supported by the camera and its password.
- The camera powered in Bluetooth pairing/reset mode, advertising `GW_BLE_<deviceId>`.
- A vendor account configured once through Community Cam Guard. The backend logs in, renews its
  session and fetches fresh handshake material with its own implementation. The vendor cloud is
  still required and is not the desired final LAN-only architecture.

Web Bluetooth requires a secure context. These are valid:

- `http://localhost:3200` when the browser runs on a Bluetooth-capable server;
- trusted HTTPS on the LAN; or
- an authenticated temporary HTTPS tunnel with `PROVISIONING_REMOTE_BLE_ENABLED=true`.

Plain `http://192.168.x.x:3200` works for monitoring and administration but browsers do not expose
Web Bluetooth there. Close a temporary tunnel and restore the flag to `false` after onboarding.

## Configure the temporary cloud dependency

The onboarding modal shows a compact **Vendor account for Bluetooth setup** section until the
account has been configured. Submit it once from a direct trusted-LAN client. The endpoint is
intentionally unavailable through the temporary public tunnel. The equivalent API call is:

```bash
curl -b jar.txt -X POST http://127.0.0.1:3200/api/provisioning/vendor-account/login \
  -H 'Content-Type: application/json' \
  -d '{"account_type":"email","account":"<account>","password":"<password>"}'
```

The password is converted to the vendor's password-equivalent digest and stored with the renewable
session in one encrypted SQLite payload. Neither the identity, digest nor session token is returned.
At BLE prepare time the backend renews the session, obtains a new TanKey/random/bind token and pins
that exact material to an opaque three-minute in-memory attempt. Android, the vendor application,
an emulator, Frida and capture files are not runtime dependencies.

This still contacts the vendor service. It will disappear only when the cloud bootstrap itself is
replaced by a native LAN-only handshake. Changing the vendor password requires enrolling the
account again.

## Dashboard procedure

1. Open **Cameras → Set up new camera** and scan/upload the printed QR label or enter its identity.
2. Select the target Wi-Fi network. If the server has no Wi-Fi radio, enter the SSID and security
   type explicitly. Enter the password only in the password field.
3. Select **Find Bluetooth camera** and approve the browser picker for the exact
   `GW_BLE_<deviceId>` advertisement.
4. Start provisioning while the temporary material is fresh. Keep the page open and the camera
   powered.
5. The dashboard validates the secure challenge, asks the camera for its own Wi-Fi scan, sends the
   encrypted configuration and waits only for the camera's Wi-Fi confirmation.
6. After `0x83` acknowledges delivery, the dashboard follows the APK and waits for either the
   asynchronous BLE result `0x85` or the read-only cloud `devresult` confirmation for that exact
   `configToken`. Only then does the P2P section become actionable. Choose between **Finish Wi-Fi
   only** or **Link P2P access**. Neither choice enables RTSP.
7. Close the modal and run the main **Scan Network** action. This is the sole LAN discovery step.

Choosing **Finish Wi-Fi only** ends onboarding without binding the camera. **Link P2P access** is a
separate, explicit action that binds it to the vendor account, but still does not enable RTSP,
choose RTSP credentials or add a stream to Community Cam Guard. Those later stages must never be
reported as consequences of Wi-Fi success.

## Post-Wi-Fi P2P and RTSP stage

The complete post-Wi-Fi flow has now been physically validated on a
`GW-IPC-AK-AV100.25` running firmware `40.1.14`. The current dashboard separates these operations
because Wi-Fi success, account binding and a playable local stream are three different facts:

1. Wait for the vendor-compatible `devresult` lookup to report the exact setup `configToken`
   online. A BLE `0x83` response alone confirms only delivery of the Wi-Fi payload.
2. Explicitly bind the device. A successful bind returns an ephemeral `devToken`; starting a new
   P2P session must then list the camera online before privileged commands are attempted.
3. Read `ProWritable.onvifEn`, set it to `1` when necessary and read it back. This opens the local
   RTSP service but does not configure its password.
4. Choose an 8–30 character alphanumeric RTSP password. The modern camera command receives the
   lowercase hexadecimal HA1 `MD5("admin:HIipCamera:<password>")` in
   `{"type":3,"data":{"password":"<HA1>"}}`; it does **not** receive the clear password.
5. Treat the P2P `onAck` only as command delivery. Validate the clear password end-to-end against
   the camera's Digest-authenticated RTSP endpoint and require actual media before saving it.
6. Register the camera only after validation, with its stable MAC, current IP, user `admin`, RTSP
   path and encrypted password. Community Cam Guard then regenerates go2rtc and attaches recording
   and dashboard consumers to the shared server-side stream.

For this validated model, `/onvif1` is HEVC 1920x1080 plus G.711 A-law mono at 16 kHz and
`/onvif2` is HEVC 640x360. Its RTSP server works over UDP and can reject interleaved TCP with a
transport mismatch, so verification must try UDP rather than declaring a valid credential broken.
No real password, device token or account token belongs in documentation, logs or source control.

The low-level P2P/RTSP stage is homologated but not yet exposed as one automatic dashboard action.
Until that integration lands, a successful **Link P2P access** still reports RTSP as pending rather
than claiming that the stream was configured.

New binds now preserve the terminal access identity and device subscription token as a single
encrypted database record. This closes the earlier restart gap where successful enrollment existed
only in process memory. The authenticated `p2p-probe` endpoint can use that durable record to
certify an access-node session, inspect aggregate device visibility and confirm a heartbeat. The
separate `p2p-route-probe` performs a bounded brokered CALLING and direct CA/CB handshake with only
the selected camera. Both stages are read-only: they neither open media nor send `onvifEn`, RTSP,
light, audio or reboot commands.

## Homologated wire sequence

- GATT service `8922a5c3-1e44-403e-a587-bcf972e398b4`.
- Response notifications over FED8 with the observed FED7 compatibility channel.
- MTU **256**, matching the Android client. MTU 23 is invalid for this firmware in practice: it
  processes only the last fragment of the 32-byte challenge and therefore never installs TanKey.
- Plain challenge `0x70 → 0x71`.
- Encrypted transport selection `0x72` (optional `0x73`, not awaited) with
  `{"linkType":1,"linkTypeName":"WIFI"}`. The app sends this before requesting the Wi-Fi list;
  the APK proceeds immediately after the write callback. Blocking on `0x73` prevents
  SSID/password delivery on firmware that stays silent for this command.
- Encrypted camera Wi-Fi scan `0x80 → 0x81`.
- Encrypted Wi-Fi configuration `0x82 → 0x83` (immediate transport acknowledgement).
- The APK races two success paths after `0x83`: asynchronous `0x85` carries `connectStatus` and a
  one-time `confirmKey`; independently, it polls
  `POST /openapi/netcfg/cloud/netcfg/devresult` every five seconds with the same `configToken`.
  A `status == 1` result permits the subsequent bind without a `confirmKey`. Waiting exclusively
  for `0x85` is therefore incorrect for firmware that completes only through the cloud result.
  This fallback is proven statically from the APK and covered by local wire-contract tests; a fresh
  physical onboarding pass is still required to homologate the live vendor response.
- Plain finish `0x88`, sent after **Finish Wi-Fi only**, or after an explicitly successful P2P bind
  when the user chooses the full onboarding continuation.
- AES-CBC uses the recovered fixed `iotVideo` IV, encrypts complete 16-byte blocks and leaves the
  final partial block unchanged, matching the vendor native library.

Wi-Fi passwords and transient cloud material are not persisted in the database, returned to the
browser or logged. In particular, the decrypted `0x83` echo is discarded server-side because it
contains the complete provisioning payload. Public/cross-site provisioning requests are rejected
by the backend even when the dashboard itself is intentionally reachable on the LAN.
