"""Regression tests for v0.2.0 changes.

Covers:
- Button: ``button.*_request_pickup_code`` press generates a fresh QR (same path as service).
- Image state: coordinator update publishes the snapshot timestamp immediately
  (previously stuck at "unknown" until a later event).
- Pickup-code expiry — auto_regenerate_pickup_code=False: clears the snapshot.
- Pickup-code expiry — auto_regenerate_pickup_code=True: triggers a fresh code.
- No service_health sensor (v0.2.0 removal — the service_health aggregate is gone,
  but the account device it lived on is back to host CONFIG-category entities).
- scan_interval + auto_regenerate are CONFIG-category entities (number + switch)
  on the account device; Options flow was removed in v0.2.0.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from aioresponses import aioresponses
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
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
    scan_interval_key,
)

# Convenience — this test file's fixture uses community_unit_id=1001.
_CU = 1001
_SCAN_KEY = scan_interval_key(_CU)
_AUTO_KEY = auto_regenerate_key(_CU)

pytestmark = pytest.mark.asyncio


def _entry(hass, options: dict | None = None) -> MockConfigEntry:
    # Default auto_regen OFF per community: prevents the v0.3.2 setup hook
    # from trying to hit CreateCheckoutVerificationCode on every test's
    # startup. Tests that care about auto_regen on/off flip it explicitly.
    merged = {_AUTO_KEY: False, **(options or {})}
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        options=merged,
        data={
            CONF_PHONE_NUMBER: "+886912345678",
            CONF_COMMUNITY_UNIT_IDS: [1001],
            CONF_MEMBER_INFO: {
                "communityUnits": [
                    {
                        "communityId": 101,
                        "communityUnitId": 1001,
                        "communityName": "Test A",
                        "isRepresentative": False,
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


async def _setup(hass, entry, fresh_access, refresh) -> None:
    await _seed(hass, entry.entry_id, fresh_access, refresh)
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _qr_payload(code: str, expires_at: str, community_id: int = 101) -> dict:
    return {
        "code": "COM00001",
        "data": {
            "communityId": community_id,
            "verificationCode": code,
            "expiredTime": expires_at,
        },
    }


# --- button press ------------------------------------------------------


async def test_button_press_generates_fresh_qr(hass, fresh_access_token, long_refresh_token):
    entry = _entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    button_id = "button.goodlifetaiwan_test_a_request_pickup_code"
    # confirm the entity was created
    assert hass.states.get(button_id) is not None

    expires = (datetime.now().astimezone() + timedelta(minutes=10)).isoformat()
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_qr_payload("77777", expires),
        )
        await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.goodlifetaiwan_test_a_pickup_code")
    assert state is not None
    assert state.state == "77777"


# --- image state publishes immediately ---------------------------------


async def test_image_state_updates_on_snapshot(hass, fresh_access_token, long_refresh_token):
    """Image entity state must reflect the fresh ISO timestamp as soon as the
    snapshot is published, not lazily on the next HA event."""
    entry = _entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    image_id = "image.goodlifetaiwan_test_a_qr_image"
    # Before any QR, the state should be unknown.
    before = hass.states.get(image_id)
    assert before is not None
    assert before.state in ("unknown", "unavailable")

    expires = (datetime.now().astimezone() + timedelta(minutes=10)).isoformat()
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_qr_payload("88888", expires),
        )
        await hass.services.async_call(
            DOMAIN, "request_pickup_code", {}, blocking=True, return_response=True
        )
        await hass.async_block_till_done()

    after = hass.states.get(image_id)
    assert after is not None
    # State is an ISO timestamp string now, not "unknown".
    assert after.state not in ("unknown", "unavailable"), (
        f"expected ISO timestamp, got {after.state!r}"
    )
    # Sanity: parseable as a datetime.
    datetime.fromisoformat(after.state)


# --- expiry: auto_regenerate off clears state --------------------------


async def test_expiry_clears_snapshot_when_auto_regen_off(
    hass, fresh_access_token, long_refresh_token
):
    entry = _entry(hass, options={_AUTO_KEY: False})
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    # Schedule the QR expiry ~30s in the future relative to HA's clock.
    expires_at = dt_util.utcnow() + timedelta(seconds=30)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_qr_payload("99999", expires_at.isoformat()),
        )
        # Keep the unpicked endpoint mocked across the advance below so the
        # coordinator's periodic poll doesn't blow up on a missing mock.
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        await hass.services.async_call(
            DOMAIN, "request_pickup_code", {}, blocking=True, return_response=True
        )
        await hass.async_block_till_done()

        code_sensor_id = "sensor.goodlifetaiwan_test_a_pickup_code"
        assert hass.states.get(code_sensor_id).state == "99999"

        # Advance HA's scheduler past expiry — the timer fires, and with
        # auto_regen=False the snapshot is cleared.
        async_fire_time_changed(hass, expires_at + timedelta(seconds=1))
        await hass.async_block_till_done()

    state = hass.states.get(code_sensor_id)
    assert state is not None
    assert state.state in ("unknown", "unavailable"), (
        f"expected unknown after clear, got {state.state!r}"
    )


# --- expiry: auto_regenerate on triggers new QR ------------------------


async def test_expiry_regenerates_when_auto_regen_on(hass, fresh_access_token, long_refresh_token):
    # Start with auto_regen off — we don't want the v0.3.2 setup hook to race
    # the test's expiry timing. The manual request_pickup_code call below sets
    # up the initial code, then we flip the switch on to arm the auto-regen.
    entry = _entry(hass, options={_AUTO_KEY: False})
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    now = dt_util.utcnow()
    first_expiry = now + timedelta(seconds=30)
    second_expiry = first_expiry + timedelta(minutes=10)  # new 10-min window after regen

    with aioresponses() as m:
        # First call: manual request.
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_qr_payload("11111", first_expiry.isoformat()),
        )
        # Second call: automatic regen triggered by timer.
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_qr_payload("22222", second_expiry.isoformat()),
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )

        await hass.services.async_call(
            DOMAIN, "request_pickup_code", {}, blocking=True, return_response=True
        )
        await hass.async_block_till_done()

        code_sensor_id = "sensor.goodlifetaiwan_test_a_pickup_code"
        assert hass.states.get(code_sensor_id).state == "11111"

        # Now flip auto_regen on (code already present → no initial regen;
        # only the expiry-timer regen path matters here).
        hass.config_entries.async_update_entry(entry, options={**entry.options, _AUTO_KEY: True})

        # Advance past first expiry → timer fires, triggers auto-regen.
        async_fire_time_changed(hass, first_expiry + timedelta(seconds=1))
        await hass.async_block_till_done()

        state = hass.states.get(code_sensor_id)
        assert state is not None
        assert state.state == "22222", f"expected auto-regenerated code, got {state.state!r}"


async def test_expiry_during_refresh_does_not_clear(hass, fresh_access_token, long_refresh_token):
    """v0.3.4 regression. When auth is mid-refresh (both the 10-min access
    token refresh and the 10-min pickup code expiry naturally align), the
    expiry handler used to gate on ``auth.state == ok`` and clear all
    pickup entities. Fixed to only skip regen on ``auth_needed``; ``refreshing``
    is fine because ``async_ensure_access_token`` serialises on the auth
    refresh lock internally.
    """
    from custom_components.goodlifetaiwan.const import AUTH_STATE_REFRESHING

    entry = _entry(hass, options={_AUTO_KEY: False})
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    first_expiry = dt_util.utcnow() + timedelta(seconds=30)
    second_expiry = first_expiry + timedelta(minutes=10)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_qr_payload("11111", first_expiry.isoformat()),
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        await hass.services.async_call(
            DOMAIN, "request_pickup_code", {}, blocking=True, return_response=True
        )
        await hass.async_block_till_done()

    code_sensor_id = "sensor.goodlifetaiwan_test_a_pickup_code"
    assert hass.states.get(code_sensor_id).state == "11111"

    # Arm auto-regen AND force auth into `refreshing` state before firing
    # expiry, simulating the alignment. Pre-fix, the expiry handler's
    # `auth.state == ok` gate would fail here and clear the snapshot.
    hass.config_entries.async_update_entry(entry, options={**entry.options, _AUTO_KEY: True})
    auth = hass.data[DOMAIN][entry.entry_id]["auth"]
    auth._state = AUTH_STATE_REFRESHING

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload=_qr_payload("22222", second_expiry.isoformat()),
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        async_fire_time_changed(hass, first_expiry + timedelta(seconds=1))
        await hass.async_block_till_done()

    state = hass.states.get(code_sensor_id)
    assert state is not None
    # Post-fix: regen ran despite auth.state == refreshing.
    assert state.state == "22222", (
        f"expected regen to fire during refreshing state, got {state.state!r} "
        "(pre-fix the gate would have cleared the snapshot)"
    )


# --- v0.2.0 removal: service_health sensor + account device gone ------


async def test_service_health_sensor_not_created(hass, fresh_access_token, long_refresh_token):
    entry = _entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    # Previously this would exist as sensor.goodlifetaiwan_account_*_service.
    for eid in hass.states.async_entity_ids("sensor"):
        assert "service" not in eid or "goodlifetaiwan" not in eid, (
            f"legacy service_health sensor still present: {eid}"
        )


# --- number / switch entities (replacing the removed Options flow) ------


async def test_scan_interval_number_entity_mutates_coordinator(
    hass, fresh_access_token, long_refresh_token
):
    from datetime import timedelta

    entry = _entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    number_id = "number.goodlifetaiwan_test_a_poll_interval"
    state = hass.states.get(number_id)
    assert state is not None, (
        f"scan_interval number entity missing; known entities: "
        f"{[e for e in hass.states.async_entity_ids() if 'goodlifetaiwan' in e]}"
    )
    # Default is DEFAULT_SCAN_INTERVAL_SEC (300)
    assert float(state.state) == 300.0

    with aioresponses() as m:
        # Setting the value triggers async_request_refresh on the coordinator.
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": number_id, "value": 300},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[_SCAN_KEY] == 300
    coordinators = hass.data[DOMAIN][entry.entry_id]["coordinators"]
    assert coordinators[_CU].update_interval == timedelta(seconds=300)


async def test_scan_interval_rejects_out_of_range(hass, fresh_access_token, long_refresh_token):
    entry = _entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    number_id = "number.goodlifetaiwan_test_a_poll_interval"
    # 30 is below MIN_SCAN_INTERVAL_SEC=60. NumberEntity schema clamps / rejects.
    # HA raises ServiceValidationError for out-of-range values on number.set_value;
    # pin to that rather than bare Exception so we catch regressions on upgrade.
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": number_id, "value": 30},
            blocking=True,
        )


async def test_auto_regenerate_switch_toggles_option(hass, fresh_access_token, long_refresh_token):
    # Fixture default opts auto_regen off for the rest of the suite; here we
    # want to verify the on/off toggle flow, so explicitly start from off.
    entry = _entry(hass, options={_AUTO_KEY: False})
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    switch_id = "switch.goodlifetaiwan_test_a_auto_regenerate_pickup_code"
    state = hass.states.get(switch_id)
    assert state is not None
    assert state.state == "off"

    # Flip on — triggers initial regen, so we need to mock the endpoint.
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    expiry = (dt_util.utcnow() + timedelta(minutes=10)).isoformat()
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload={
                "code": "COM00001",
                "data": {
                    "communityId": 101,
                    "verificationCode": "42424",
                    "expiredTime": expiry,
                },
            },
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        await hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
        await hass.async_block_till_done()

    assert entry.options[_AUTO_KEY] is True
    assert hass.states.get(switch_id).state == "on"

    # Flip off — no regen, just option update.
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
    await hass.async_block_till_done()

    assert entry.options[_AUTO_KEY] is False
    assert hass.states.get(switch_id).state == "off"
