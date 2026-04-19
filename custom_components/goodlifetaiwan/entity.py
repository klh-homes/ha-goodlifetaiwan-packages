"""Shared helpers for entity setup (DeviceInfo, unique_id).

v0.3 note: the account device was added in v0.1 to host ``sensor.*_service``,
removed early in v0.2 along with that sensor, re-added later in v0.2 to host
per-entry CONFIG entities (``number.*_poll_interval``,
``switch.*_auto_regenerate_pickup_code``), and now removed again in v0.3 —
those CONFIG entities became per-community and moved to the community device.
So each entry shows exactly one device per community, nothing else.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import CommunityState


def community_device_info(entry_id: str, state: CommunityState) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}__community__{state.community_unit_id}")},
        name=f"GoodLifeTaiwan {state.community_name}",
        manufacturer="Taiwan Secom",
        model=f"community_id={state.community_id}, unit={state.community_unit_id}",
        configuration_url="https://www.glf.tw",
    )


def unique_id(entry_id: str, community_unit_id: int | None, kind: str) -> str:
    if community_unit_id is None:
        return f"{entry_id}__{kind}"
    return f"{entry_id}__{community_unit_id}__{kind}"
