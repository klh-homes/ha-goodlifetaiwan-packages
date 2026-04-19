"""Regression test for v0.3.1: request_pickup_code is idempotent when the
server returns the same verificationCode we already hold.

Motivation: if the upstream ever decides "you already have a valid code,
here's the same one again" instead of minting a fresh one (or a future
API change produces that behaviour), we don't want to churn HA state —
no state_changed events, no image_last_updated bump, no re-armed expiry
timer.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from aioresponses import aioresponses
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

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


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        # Opt out of auto-regen so expiry timers don't interfere with the test.
        options={auto_regenerate_key(110412): False},
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


async def _setup(hass, entry, fresh_access, refresh) -> None:
    store = Store(
        hass, STORAGE_VERSION, STORAGE_KEY_FMT.format(entry_id=entry.entry_id), private=True
    )
    await store.async_save(
        {"access_token": fresh_access, "refresh_token": refresh, "refreshed_at": None}
    )
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _code_payload(code: str, expires_at: str) -> dict:
    return {
        "code": "COM00001",
        "data": {
            "communityId": 1777,
            "verificationCode": code,
            "expiredTime": expires_at,
        },
    }


async def test_same_code_is_noop(hass, fresh_access_token, long_refresh_token):
    """Second request returning the identical code must not emit entity
    state_changed events for pickup_code / pickup_code_expires / qr image."""
    entry = _entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    first_expiry = dt_util.utcnow() + timedelta(minutes=10)
    second_expiry = dt_util.utcnow() + timedelta(minutes=10, seconds=30)

    # First call — populates state.
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_code_payload("55555", first_expiry.isoformat()),
        )
        await hass.services.async_call(
            DOMAIN, "request_pickup_code", {}, blocking=True, return_response=True
        )
        await hass.async_block_till_done()

    code_id = "sensor.goodlifetaiwan_she_qu_a_pickup_code"
    expires_id = "sensor.goodlifetaiwan_she_qu_a_pickup_code_expires"
    image_id = "image.goodlifetaiwan_she_qu_a_qr_image"

    before_code = hass.states.get(code_id)
    before_expires = hass.states.get(expires_id)
    before_image = hass.states.get(image_id)
    assert before_code.state == "55555"

    # Capture state_changed events ONLY for our three entities, from now on.
    tracked = {code_id, expires_id, image_id}
    events = []

    def _capture(ev):
        if ev.data.get("entity_id") in tracked:
            events.append(ev)

    unsub = hass.bus.async_listen("state_changed", _capture)

    # Second call — server returns the *same* verificationCode.
    # Expires has a different value just to stress the "do nothing" guarantee:
    # we don't update even when the server's expiry shifts.
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_code_payload("55555", second_expiry.isoformat()),
        )
        result = await hass.services.async_call(
            DOMAIN, "request_pickup_code", {}, blocking=True, return_response=True
        )
        await hass.async_block_till_done()

    # Service still returns a valid response.
    assert result["code"] == "55555"

    # No churn on any of the three entities.
    assert events == [], f"expected no state_changed events, got {[e.data for e in events]}"
    # States unchanged.
    assert hass.states.get(code_id).state == before_code.state
    assert hass.states.get(expires_id).state == before_expires.state
    assert hass.states.get(image_id).state == before_image.state
    unsub()


async def test_different_code_does_update(hass, fresh_access_token, long_refresh_token):
    """Sanity: when the server returns a *different* code, we DO update."""
    entry = _entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    first_expiry = dt_util.utcnow() + timedelta(minutes=10)
    second_expiry = dt_util.utcnow() + timedelta(minutes=20)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_code_payload("11111", first_expiry.isoformat()),
        )
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_code_payload("22222", second_expiry.isoformat()),
        )

        await hass.services.async_call(
            DOMAIN, "request_pickup_code", {}, blocking=True, return_response=True
        )
        await hass.async_block_till_done()
        assert hass.states.get("sensor.goodlifetaiwan_she_qu_a_pickup_code").state == "11111"

        await hass.services.async_call(
            DOMAIN, "request_pickup_code", {}, blocking=True, return_response=True
        )
        await hass.async_block_till_done()
        assert hass.states.get("sensor.goodlifetaiwan_she_qu_a_pickup_code").state == "22222"
