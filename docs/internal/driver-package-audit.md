# Partial driver inventory and package organization — 2026-09-15

Hikvision, Dahua and XiongMai were not empty placeholders: each supplied vendor
matching and RTSP path candidates. None implemented its own camera controls or
provisioning. They inherited bounded common RTSP probing, not generic ONVIF PTZ
support. Finding a stream or matching a vendor does not certify control support.

| Package | Existing discovery support | Still absent |
|---|---|---|
| `drivers/hikvision/` | Vendor match; main/sub RTSP channel paths | PTZ/reboot, semantic controls, two-way audio, SD access, onboarding |
| `drivers/dahua/` | Vendor match; channel/subtype RTSP templates | Same device-control features |
| `drivers/xiongmai/` | XiongMai/XMEye match; credential-bearing RTSP template | Same device-control features |

Each now owns `driver.py` and a small `__init__.py` re-export. Public imports such
as `from backend.app.drivers.dahua import DahuaDriver`, registry keys, detection
priority and RTSP templates remain compatible. XiongMai paths still require supplied
credentials; credentials are not fabricated. No discovery or media behavior changed.

`base.py`, `contracts.py`, registry and the generic fallback remain shared. Yoosee
keeps its existing package. Future brand-specific protocol/profile/control code goes
inside its brand package, not API routers, frontend or common monitoring services.
No empty feature folders were added merely to anticipate future implementation.

New tests verify package/public imports, registry detection, simultaneous selection
of multiple brands and generic fallback, plus absence of false controls/onboarding/
audio/SD support. Unsupported calls still raise `Unsupported`. Existing driver and
architecture tests are run alongside these contracts. These are software checks,
not hardware homologation of any additional brand.

Remaining work is hardware-backed implementation per model and protocol. Add a
runtime probe or compatible model profile, implement the semantic driver method,
and verify capability reporting and safe failure before enabling a control. The
directory cleanup itself grants no new camera capability.

Verification: 68 focused package/registry/base/Yoosee/architecture tests passed,
as did Ruff and Mypy (176 source files). Tests used a 512 MiB address-space cap;
no camera probes, package installation, container rebuild or service restart was
needed for this structural change. Production remains on its existing image until
the next deployment.
