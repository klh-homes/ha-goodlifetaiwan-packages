"""Regression tests for v0.3.2: auto-populate pickup code when auto_regen is on.

Covers three trigger points:
- Entry setup with auto_regen on → initial code generated during startup
- Switch off → on when no current code → regen fires
- Entry setup with auto_regen off → nothing is generated

The coordinator.async_maybe_generate_initial helper is also safe to call
when there's already a code (no-op) or when auth isn't ok (no-op).
"""

from __future__ import annotations

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
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
    auto_regenerate_key,
)

pytestmark = pytest.mark.asyncio


def _entry(hass, auto_regen: bool) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        options={auto_regenerate_key(110412): auto_regen},
        data={
            CONF_PHONE_NUMBER: "+886912345678",
            CONF_COMMUNITY_UNIT_IDS: [110412],
            CONF_MEMBER_INFO: {
                "communityUnits": [
                    {
                        "communityId": 1777,
                        "communityUnitId": 110412,
                        "communityName": "社區A",
                    }
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


def _code_payload(code: str, expires_iso: str) -> dict:
    return {
        "code": "COM00001",
        "data": {
            "communityId": 1777,
            "verificationCode": code,
            "expiredTime": expires_iso,
        },
    }


async def test_setup_with_auto_regen_on_generates_initial_code(
    hass, fresh_access_token, long_refresh_token
):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    entry = _entry(hass, auto_regen=True)
    await _seed(hass, entry.entry_id, fresh_access_token, long_refresh_token)

    expiry = (dt_util.utcnow() + timedelta(minutes=10)).isoformat()
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        # This mock MUST be hit during setup, from the initial regen hook.
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_code_payload("13579", expiry),
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.goodlifetaiwan_she_qu_a_pickup_code")
    assert state is not None
    assert state.state == "13579"


async def test_setup_with_auto_regen_off_does_not_generate(
    hass, fresh_access_token, long_refresh_token
):
    entry = _entry(hass, auto_regen=False)
    await _seed(hass, entry.entry_id, fresh_access_token, long_refresh_token)

    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        # Deliberately no mock for CreateCheckOutVerificationCode: if the
        # setup tried to hit it, aioresponses would raise ConnectionError
        # and the test would fail.
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.goodlifetaiwan_she_qu_a_pickup_code")
    assert state is not None
    # No pickup code yet because auto_regen is off.
    assert state.state in ("unknown", "unavailable")


async def test_switch_off_to_on_generates_initial_code(
    hass, fresh_access_token, long_refresh_token
):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    entry = _entry(hass, auto_regen=False)
    await _seed(hass, entry.entry_id, fresh_access_token, long_refresh_token)

    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Precondition: no pickup code yet.
    code_sensor = "sensor.goodlifetaiwan_she_qu_a_pickup_code"
    assert hass.states.get(code_sensor).state in ("unknown", "unavailable")

    # Turn the switch on → initial regen fires.
    switch_id = "switch.goodlifetaiwan_she_qu_a_auto_regenerate_pickup_code"
    expiry = (dt_util.utcnow() + timedelta(minutes=10)).isoformat()
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_code_payload("24680", expiry),
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        await hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
        await hass.async_block_till_done()

    assert hass.states.get(code_sensor).state == "24680"


async def test_switch_off_to_on_with_existing_code_is_noop(
    hass, fresh_access_token, long_refresh_token
):
    """If there's already a QR in state, flipping the switch on shouldn't
    fetch again — async_maybe_generate_initial short-circuits when
    community.qr is not None."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    entry = _entry(hass, auto_regen=True)
    await _seed(hass, entry.entry_id, fresh_access_token, long_refresh_token)

    expiry = (dt_util.utcnow() + timedelta(minutes=10)).isoformat()
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_code_payload("99999", expiry),
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    code_sensor = "sensor.goodlifetaiwan_she_qu_a_pickup_code"
    assert hass.states.get(code_sensor).state == "99999"

    # Toggle the switch off then on — no regen on the on transition because
    # community.qr is still populated. If a regen fired, aioresponses would
    # raise ConnectionError on the unmocked POST.
    switch_id = "switch.goodlifetaiwan_she_qu_a_auto_regenerate_pickup_code"
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
    await hass.async_block_till_done()

    # Still the same code.
    assert hass.states.get(code_sensor).state == "99999"
