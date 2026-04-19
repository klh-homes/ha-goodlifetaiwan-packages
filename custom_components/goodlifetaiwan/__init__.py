"""GoodLifeTaiwan integration entry points."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.storage import Store

from .api import GoodLifeApi
from .auth import AuthManager
from .const import (
    CONF_COMMUNITY_UNIT_IDS,
    CONF_MEMBER_INFO,
    CONF_PHONE_NUMBER,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_SEC,
    DOMAIN,
    PLATFORMS,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
)
from .coordinator import CommunityState, GoodLifeCoordinator
from .entity import community_slug
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    await _maybe_migrate_bootstrap_tokens(hass, entry)

    session = aiohttp_client.async_get_clientsession(hass)
    api = GoodLifeApi(session)
    auth = AuthManager(
        hass=hass,
        entry_id=entry.entry_id,
        phone_number=entry.data[CONF_PHONE_NUMBER],
        api=api,
    )
    await auth.async_load()

    communities = _build_community_states(entry)
    if not communities:
        _LOGGER.warning(
            "entry %s has no community selection; integration will sit idle",
            entry.entry_id[:8],
        )

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC)
    coordinator = GoodLifeCoordinator(
        hass, entry, api, auth, communities, scan_interval_seconds=scan_interval
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "auth": auth,
        "coordinator": coordinator,
        # Per-entry lock serialising send_sms + submit_code handlers so they
        # can't race across the Store write / state transition boundary.
        "sms_lock": asyncio.Lock(),
    }

    # If we have tokens, poll immediately; otherwise set up entities cold and wait for reauth.
    if auth.state == "ok" and communities:
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "first refresh failed for entry %s: %s (entities will still set up)",
                entry.entry_id[:8],
                err,
            )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # If auth_needed on setup, ask HA to open the reauth flow.
    if auth.state == "auth_needed":
        entry.async_start_reauth(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Tear services down if no entries remain.
        remaining = [k for k in hass.data.get(DOMAIN, {}) if k != "_services_registered"]
        if not remaining:
            async_unregister_services(hass)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _build_community_states(entry: ConfigEntry) -> list[CommunityState]:
    member_info = entry.data.get(CONF_MEMBER_INFO) or {}
    all_units = {
        int(u["communityUnitId"]): u
        for u in (member_info.get("communityUnits") or [])
        if u.get("communityUnitId") is not None
    }
    selected = [int(i) for i in entry.data.get(CONF_COMMUNITY_UNIT_IDS) or []]

    states: list[CommunityState] = []
    for cu_id in selected:
        unit = all_units.get(cu_id)
        if unit is None:
            _LOGGER.warning(
                "selected community_unit_id=%s no longer present in member_info; skipping",
                cu_id,
            )
            continue
        community_id = int(unit.get("communityId") or 0)
        name = str(unit.get("communityName") or f"c{community_id}")
        slug = community_slug(name, cu_id, community_id)
        states.append(
            CommunityState(
                community_id=community_id,
                community_unit_id=cu_id,
                community_name=name,
                slug=slug,
            )
        )
    return states


async def _maybe_migrate_bootstrap_tokens(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """On first setup, move tokens from entry.data into the dedicated Store."""
    access = entry.data.get("_bootstrap_access_token")
    refresh = entry.data.get("_bootstrap_refresh_token")
    if not access or not refresh:
        return

    store = Store(
        hass,
        STORAGE_VERSION,
        STORAGE_KEY_FMT.format(entry_id=entry.entry_id),
        private=True,
    )
    await store.async_save(
        {
            "access_token": access,
            "refresh_token": refresh,
            "refreshed_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        }
    )

    new_data: dict[str, Any] = {
        k: v
        for k, v in entry.data.items()
        if k not in {"_bootstrap_access_token", "_bootstrap_refresh_token"}
    }
    hass.config_entries.async_update_entry(entry, data=new_data)
