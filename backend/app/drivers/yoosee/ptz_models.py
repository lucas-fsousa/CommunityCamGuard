"""Driver-owned PTZ compatibility; no per-camera IDs or frontend brand decisions."""

from .capability_identity import CapabilityIdentity

# Model/firmware profile validated for all four finite directions and STOP.
# Hardware/version variants are separate profiles until compatibility is established.
_PROFILES = {
    ("6442451494", "GW-IPC-AK-AV100.25", 1, "40.1.14", "16.20.16355", ""):
        frozenset({"left", "right", "up", "down"}),
}


def directions_for(identity: CapabilityIdentity) -> frozenset[str]:
    return _PROFILES.get((identity.product_id, identity.model, identity.revision,
                          identity.firmware, identity.sdk, identity.hardware), frozenset())
