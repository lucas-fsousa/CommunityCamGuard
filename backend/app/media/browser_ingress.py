"""Hub browser admission tied to a configured ID and durable source revision.

This is a signaling/MSE guard, not a physical ingress-retirement receipt. A
negotiated WebRTC peer may outlive signaling; the coordinator still owns drain.
"""

from dataclasses import dataclass

from ..camera_identity import valid_camera_id
from ..db import registry, source_policy
from .owned_bindings import BoundIngress, OwnedIngressBindings


def camera_for_source(source: str, *, allow_base: bool = True) -> str | None:
    """Accept exact public aliases, never URLs, substreams or nested suffixes."""
    if type(source) is not str:
        return None
    if allow_base and valid_camera_id(source):
        return source
    for suffix in ("_hd", "_web"):
        if source.endswith(suffix) and valid_camera_id(source[:-len(suffix)]):
            return source[:-len(suffix)]
    return None


@dataclass(frozen=True)
class HubViewer:
    camera_id: str
    selection: source_policy.Selection
    binding: BoundIngress | None = None

    def valid(self) -> bool:
        return (registry.get_camera_by_id(self.camera_id) is not None
                and source_policy.read_for((self.camera_id,))[self.camera_id] == self.selection
                and (self.selection.permits_legacy_rtsp
                     or (self.selection.mode == "owned_rtsp" and self.selection.phase == "active"
                         and self.binding is not None and self.binding.url is not None)))


def admit_hub(camera_id: str, owned_ingress: OwnedIngressBindings | None = None) -> HubViewer | None:
    """Storage errors propagate for fail-closed callers; never change selection."""
    if not valid_camera_id(camera_id) or registry.get_camera_by_id(camera_id) is None:
        return None
    selection = source_policy.read_for((camera_id,))[camera_id]
    if selection.permits_legacy_rtsp:
        return HubViewer(camera_id, selection)
    binding = owned_ingress.binding(camera_id, selection) if isinstance(owned_ingress, OwnedIngressBindings) else None
    return HubViewer(camera_id, selection, binding) if binding is not None else None
