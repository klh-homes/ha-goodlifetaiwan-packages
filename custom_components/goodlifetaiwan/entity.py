"""Shared helpers for entity setup (DeviceInfo, slug, unique_id)."""

from __future__ import annotations

import re

from homeassistant.helpers.device_registry import DeviceInfo

from .auth import mask_phone
from .const import DOMAIN
from .coordinator import CommunityState


def community_slug(name: str, community_unit_id: int, community_id: int) -> str:
    """Derive a stable, ASCII-only slug for display.

    Strategy:
    - Lower-snake-case ASCII letters/digits from the community name.
    - Append last-4 of the unit id for uniqueness across same-name communities.
    - If nothing ASCII survives (e.g., pure Chinese name), fall back to ``community_id``.
    """
    ascii_only = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    last4 = str(community_unit_id)[-4:]
    if ascii_only:
        return f"{ascii_only}_{last4}"
    return f"c{community_id}_{last4}"


def account_device_info(entry_id: str, phone_number: str) -> DeviceInfo:
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
