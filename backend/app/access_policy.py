"""Vendor-neutral delegated grants. Unknown/future controls never inherit access."""

from typing import Literal

Permission = Literal[
    "live", "recordings", "ptz", "intercom", "reboot", "white_light",
    "orientation", "siren_pulse", "speaker_volume", "night_vision",
    "smart_protection", "smart_protection_schedule", "alarm_voice",
]

# Existing staged keys had read-only access; schema migration preserves that ceiling.
LEGACY_PERMISSIONS: tuple[Permission, ...] = ("live", "recordings")
