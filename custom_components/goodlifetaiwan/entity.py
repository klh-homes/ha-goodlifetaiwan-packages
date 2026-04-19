"""Shared helpers for entity setup (DeviceInfo, unique_id)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .auth import mask_phone
from .const import DOMAIN
from .coordinator import CommunityState


def account_device_info(entry_id: str, phone_number: str) -> DeviceInfo:
    """Device hosting entry-level configuration entities (scan interval, auto-regen).

    v0.2.0 note: v0.2's initial commit removed this device because its only
    occupant (sensor.*_service) was redundant with per-community auth_status.
    Re-added once per-entry CONFIG-category entities (number + switch) needed
    a natural home — attaching them to an arbitrary community device would
    mislead users into thinking they were per-community settings.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}__account")},
        name=f"GoodLifeTaiwan account {mask_phone(phone_number)}",
        manufacturer="Taiwan Secom",
        model="account",
        configuration_url="https://www.glf.tw",
    )


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
