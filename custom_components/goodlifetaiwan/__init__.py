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

from .api import ApiResponseError, GoodLifeApi, NetworkError
from .auth import AuthManager, AuthRequired
from .const import (
    AUTH_STATE_OK,
    CONF_COMMUNITY_UNIT_IDS,
    CONF_MEMBER_INFO,
    CONF_PHONE_NUMBER,
    DOMAIN,
    LEGACY_CONF_AUTO_REGENERATE_PICKUP_CODE,
    LEGACY_CONF_SCAN_INTERVAL,
    PLATFORMS,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
    auto_regenerate_key,
    scan_interval_key,
)
from .coordinator import CommunityState, GoodLifeCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    await _maybe_migrate_bootstrap_tokens(hass, entry)
    _maybe_migrate_legacy_options(hass, entry)

    session = aiohttp_client.async_get_clientsession(hass)
    api = GoodLifeApi(session)
    auth = AuthManager(
        hass=hass,
        entry_id=entry.entry_id,
        phone_number=entry.data[CONF_PHONE_NUMBER],
        api=api,
    )
    await auth.async_load()

    # v0.3.5: refresh CONF_MEMBER_INFO from the API if the stored snapshot
    # is missing isRepresentative — older snapshots dropped the field and
    # the pickup-QR encrypter needs it. Silent; failure is non-fatal
    # (fallback to isRepresentative=False, fixable on re-auth).
    await _maybe_backfill_member_info(hass, entry, api, auth)

    communities = _build_community_states(entry)
    if not communities:
        _LOGGER.warning(
            "entry %s has no community selection; integration will sit idle",
            entry.entry_id[:8],
        )

    coordinators: dict[int, GoodLifeCoordinator] = {
        state.community_unit_id: GoodLifeCoordinator(hass, entry, api, auth, state)
        for state in communities
    }

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "auth": auth,
        "coordinators": coordinators,
        # Per-entry lock serialising send_sms + submit_code handlers so they
        # can't race across the Store write / state transition boundary.
        "sms_lock": asyncio.Lock(),
    }

    # If we have tokens, poll each community immediately; otherwise set up
    # entities cold and wait for reauth.
    if auth.state == "ok" and coordinators:
        for coord in coordinators.values():
            try:
                await coord.async_config_entry_first_refresh()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "first refresh failed for entry=%s community=%s: %s "
                    "(entities will still set up)",
                    entry.entry_id[:8],
                    coord.community.community_id,
                    err,
                )
        # If auto-regenerate is on for a community, warm its pickup code now
        # so dashboards show a valid code immediately after HA startup
        # (instead of waiting for the user to press the button). Awaited
        # here (not fire-and-forget) so tests don't leave dangling tasks
        # hitting the network after teardown.
        for coord in coordinators.values():
            await coord.async_maybe_generate_initial()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)

    # If auth_needed on setup, ask HA to open the reauth flow.
    if auth.state == "auth_needed":
        entry.async_start_reauth(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Cancel QR expiry timers on every community's coordinator before tearing
    # down platforms so stray callbacks don't fire on dismantled coordinators.
    bundle = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if bundle is not None:
        for coord in bundle["coordinators"].values():
            await coord.async_shutdown_qr_timer()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Tear services down if no entries remain.
        remaining = [k for k in hass.data.get(DOMAIN, {}) if k != "_services_registered"]
        if not remaining:
            async_unregister_services(hass)
    return unload_ok


def _build_community_states(entry: ConfigEntry) -> list[CommunityState]:
    member_info = entry.data.get(CONF_MEMBER_INFO) or {}
    member_id = str(member_info.get("memberId") or "")
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
        states.append(
            CommunityState(
                community_id=community_id,
                community_unit_id=cu_id,
                community_name=name,
                slug="",  # reserved; HA derives entity_ids from device name
                member_id=member_id,
                is_representative=bool(unit.get("isRepresentative")),
            )
        )
    return states


def _maybe_migrate_legacy_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Copy v0.2 flat option keys into v0.3 per-community keys.

    v0.2 stored ``scan_interval_seconds`` and ``auto_regenerate_pickup_code`` as
    entry-level options. v0.3 stores them per community_unit_id. On first v0.3
    setup we propagate each legacy value to every selected community so the
    user's previous preference isn't silently reset, then strip the legacy keys.
    Safe to call repeatedly: if no legacy keys exist it's a no-op.
    """
    legacy_interval = entry.options.get(LEGACY_CONF_SCAN_INTERVAL)
    legacy_auto = entry.options.get(LEGACY_CONF_AUTO_REGENERATE_PICKUP_CODE)
    if legacy_interval is None and legacy_auto is None:
        return

    community_unit_ids = [int(i) for i in entry.data.get(CONF_COMMUNITY_UNIT_IDS) or []]
    new_options: dict[str, Any] = {
        k: v
        for k, v in entry.options.items()
        if k
        not in {
            LEGACY_CONF_SCAN_INTERVAL,
            LEGACY_CONF_AUTO_REGENERATE_PICKUP_CODE,
        }
    }
    for cu_id in community_unit_ids:
        if legacy_interval is not None:
            new_options.setdefault(scan_interval_key(cu_id), int(legacy_interval))
        if legacy_auto is not None:
            new_options.setdefault(auto_regenerate_key(cu_id), bool(legacy_auto))

    hass.config_entries.async_update_entry(entry, options=new_options)
    _LOGGER.info(
        "migrated legacy options for entry=%s to per-community keys",
        entry.entry_id[:8],
    )


async def _maybe_backfill_member_info(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api: GoodLifeApi,
    auth: AuthManager,
) -> None:
    """Re-fetch member_info if the stored snapshot is missing fields added in v0.3.5.

    v0.3.4 and earlier dropped ``isRepresentative`` when snapshotting the
    ``/Member/MemberInfo`` response into ``ConfigEntry.data``. v0.3.5 needs
    that field to build the encrypted pickup-QR payload the warden scanner
    accepts. Fetch once, silently, only when needed. If we're in
    ``auth_needed`` or the call fails, fall through — the coordinator uses
    ``isRepresentative=False`` as a safe default and the next re-auth via
    the SMS flow will refresh the snapshot.
    """
    member_info = entry.data.get(CONF_MEMBER_INFO) or {}
    units = member_info.get("communityUnits") or []
    if units and all("isRepresentative" in u for u in units):
        return
    if auth.state != AUTH_STATE_OK:
        return

    try:
        access = await auth.async_ensure_access_token()
        fresh = await api.member_info(access)
    except (AuthRequired, ApiResponseError, NetworkError) as err:
        _LOGGER.warning(
            "member_info backfill failed for entry=%s: %s; pickup QR will use "
            "isRepresentative=False until next re-auth",
            entry.entry_id[:8],
            err,
        )
        return

    from .config_flow import _member_info_snapshot

    snapshot = _member_info_snapshot(fresh)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_MEMBER_INFO: snapshot},
    )
    _LOGGER.info(
        "refreshed member_info snapshot for entry=%s (v0.3.5 backfill)",
        entry.entry_id[:8],
    )


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
