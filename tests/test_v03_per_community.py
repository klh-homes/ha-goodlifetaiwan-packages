"""Regression tests for v0.3.0 per-community coordinators."""

from __future__ import annotations

from datetime import timedelta

import pytest
from aioresponses import aioresponses
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.goodlifetaiwan.const import (
    BASE_URL_API,
    CONF_COMMUNITY_UNIT_IDS,
    CONF_MEMBER_INFO,
    CONF_PHONE_NUMBER,
    DOMAIN,
    LEGACY_CONF_AUTO_REGENERATE_PICKUP_CODE,
    LEGACY_CONF_SCAN_INTERVAL,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
    auto_regenerate_key,
    scan_interval_key,
)

pytestmark = pytest.mark.asyncio


def _entry_two_communities(hass, options: dict | None = None) -> MockConfigEntry:
    # Default auto_regen OFF per community so the v0.3.2 setup hook doesn't
    # try to hit CreateCheckoutVerificationCode. Tests that need it on flip
    # it explicitly. Skip the override if the test is exercising the legacy
    # migration (where auto_regen comes from LEGACY_CONF_AUTO_REGENERATE_PICKUP_CODE).
    opts = options or {}
    defaults: dict = {}
    if LEGACY_CONF_AUTO_REGENERATE_PICKUP_CODE not in opts:
        defaults[auto_regenerate_key(110412)] = False
        defaults[auto_regenerate_key(220555)] = False
    merged = {**defaults, **opts}
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        options=merged,
        data={
            CONF_PHONE_NUMBER: "+886912345678",
            CONF_COMMUNITY_UNIT_IDS: [110412, 220555],
            CONF_MEMBER_INFO: {
                "communityUnits": [
                    {
                        "communityId": 1777,
                        "communityUnitId": 110412,
                        "communityName": "社區A",
                    },
                    {
                        "communityId": 2888,
                        "communityUnitId": 220555,
                        "communityName": "社區B",
                    },
                ],
                "memberId": "m1",
                "name": "T",
            },
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _seed(hass, entry_id: str, access: str, refresh: str) -> None:
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY_FMT.format(entry_id=entry_id), private=True)
    await store.async_save({"access_token": access, "refresh_token": refresh, "refreshed_at": None})


async def _setup(hass, entry, fresh_access, refresh) -> None:
    await _seed(hass, entry.entry_id, fresh_access, refresh)
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        # Mock the pickup-code endpoint too: some tests inherit auto_regen=True
        # (e.g. via legacy-key migration) and the v0.3.2 setup hook fires a
        # call during startup. Without a mock it retries 3× with backoff.
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload={
                "code": "COM00001",
                "data": {
                    "communityId": 1777,
                    "verificationCode": "00000",
                    "expiredTime": "2099-01-01T00:00:00+00:00",
                },
            },
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_legacy_options_migrate_to_per_community(
    hass, fresh_access_token, long_refresh_token
):
    """v0.2 stored scan_interval + auto_regen as flat keys. On v0.3 setup we
    copy each to every community's suffixed key so the user's preference
    survives the upgrade, then strip the legacy keys."""
    entry = _entry_two_communities(
        hass,
        options={
            LEGACY_CONF_SCAN_INTERVAL: 900,
            LEGACY_CONF_AUTO_REGENERATE_PICKUP_CODE: True,
        },
    )
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    # Both communities inherit the legacy values.
    assert entry.options[scan_interval_key(110412)] == 900
    assert entry.options[scan_interval_key(220555)] == 900
    assert entry.options[auto_regenerate_key(110412)] is True
    assert entry.options[auto_regenerate_key(220555)] is True
    # Legacy keys stripped.
    assert LEGACY_CONF_SCAN_INTERVAL not in entry.options
    assert LEGACY_CONF_AUTO_REGENERATE_PICKUP_CODE not in entry.options


async def test_two_communities_get_independent_coordinators(
    hass, fresh_access_token, long_refresh_token
):
    entry = _entry_two_communities(
        hass,
        options={
            scan_interval_key(110412): 120,
            scan_interval_key(220555): 600,
        },
    )
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    coordinators = hass.data[DOMAIN][entry.entry_id]["coordinators"]
    assert set(coordinators) == {110412, 220555}
    assert coordinators[110412].update_interval == timedelta(seconds=120)
    assert coordinators[220555].update_interval == timedelta(seconds=600)
    # Each coordinator owns exactly its own community state.
    assert coordinators[110412].community.community_unit_id == 110412
    assert coordinators[220555].community.community_unit_id == 220555
    assert coordinators[110412] is not coordinators[220555]


async def test_per_community_number_entity_mutates_only_its_coordinator(
    hass, fresh_access_token, long_refresh_token
):
    entry = _entry_two_communities(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    # Each community gets its own number entity, independent.
    a_number = "number.goodlifetaiwan_she_qu_a_poll_interval"
    b_number = "number.goodlifetaiwan_she_qu_b_poll_interval"
    assert hass.states.get(a_number) is not None
    assert hass.states.get(b_number) is not None

    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": a_number, "value": 120},
            blocking=True,
        )
        await hass.async_block_till_done()

    coordinators = hass.data[DOMAIN][entry.entry_id]["coordinators"]
    # Only A's coordinator changed.
    assert coordinators[110412].update_interval == timedelta(seconds=120)
    assert coordinators[220555].update_interval == timedelta(seconds=300)  # still default
    # Only A's per-community key was written.
    assert entry.options[scan_interval_key(110412)] == 120
    assert scan_interval_key(220555) not in entry.options


async def test_per_community_switch_independent(hass, fresh_access_token, long_refresh_token):
    # Fixture default sets both off; this test flips one on and verifies the
    # other stays off (i.e., per-community isolation of the toggle).
    entry = _entry_two_communities(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    a_switch = "switch.goodlifetaiwan_she_qu_a_auto_regenerate_pickup_code"
    b_switch = "switch.goodlifetaiwan_she_qu_b_auto_regenerate_pickup_code"
    assert hass.states.get(a_switch).state == "off"
    assert hass.states.get(b_switch).state == "off"

    # Flip only A on — triggers A's initial regen; B untouched.
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    expiry = (dt_util.utcnow() + timedelta(minutes=10)).isoformat()
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload={
                "code": "COM00001",
                "data": {
                    "communityId": 1777,
                    "verificationCode": "33333",
                    "expiredTime": expiry,
                },
            },
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        await hass.services.async_call("switch", "turn_on", {"entity_id": a_switch}, blocking=True)
        await hass.async_block_till_done()

    assert hass.states.get(a_switch).state == "on"
    assert hass.states.get(b_switch).state == "off"  # unchanged
    assert entry.options[auto_regenerate_key(110412)] is True
    # B's option untouched.
    assert entry.options[auto_regenerate_key(220555)] is False
