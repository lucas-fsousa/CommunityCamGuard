"""Driver-owned PTZ compatibility; no per-camera IDs or frontend brand decisions."""

from .capability_identity import CapabilityIdentity

# Finite directions/STOP physically validated on 40.1.14. The 40.1.22 variant
# shares the exact product/model/revision/SDK/hardware and correlated four-axis
# schema, verified read-only on both installed cameras. Compatibility is a driver
# decision; physical response on that variant remains a separate field check.
_PROFILES = {
    ("6442451494", "GW-IPC-AK-AV100.25", 1, "40.1.14", "16.20.16355", ""):
        frozenset({"left", "right", "up", "down"}),
    ("6442451494", "GW-IPC-AK-AV100.25", 1, "40.1.22", "16.20.16355", ""):
        frozenset({"left", "right", "up", "down"}),
}


def directions_for(identity: CapabilityIdentity) -> frozenset[str]:
    return _PROFILES.get((identity.product_id, identity.model, identity.revision,
                          identity.firmware, identity.sdk, identity.hardware), frozenset())
