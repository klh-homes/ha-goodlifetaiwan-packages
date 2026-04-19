"""Shared helpers for entity setup (DeviceInfo, unique_id)."""

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
