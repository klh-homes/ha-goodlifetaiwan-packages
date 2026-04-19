"""Tests for goodlifetaiwan.request_pickup_code / send_sms / submit_code."""

from __future__ import annotations

import base64

import pytest
from aioresponses import aioresponses
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.goodlifetaiwan.const import (
    AUTH_STATE_AUTH_NEEDED,
    AUTH_STATE_OK,
    BASE_URL_API,
    BASE_URL_AUTH,
    CONF_COMMUNITY_UNIT_IDS,
    CONF_MEMBER_INFO,
    CONF_PHONE_NUMBER,
    DOMAIN,
    EVENT_AUTH_SMS_SENT,
    EVENT_AUTH_SUCCESS,
    SERVICE_REQUEST_PICKUP_CODE,
    SERVICE_SEND_SMS,
    SERVICE_SUBMIT_CODE,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
)
from tests.conftest import jwt_with_exp

pytestmark = pytest.mark.asyncio


def _make_entry(hass, community_ids: list[int] | None = None) -> MockConfigEntry:
    community_ids = community_ids or [1001]
    units = [
        {
            "communityId": 101,
            "communityUnitId": 1001,
            "communityName": "Test A",
            "shortAddress": "1號1樓",
        }
    ]
    if 1003 in community_ids:
        units.append(
            {
                "communityId": 103,
                "communityUnitId": 1003,
                "communityName": "Test B",
                "shortAddress": "8號",
            }
        )
    # Explicit auto_regen=False per community so expiry timers fired during
    # teardown don't attempt a real HTTP regen after aioresponses closes.
    from custom_components.goodlifetaiwan.const import auto_regenerate_key

    options = {auto_regenerate_key(cu_id): False for cu_id in community_ids}
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        options=options,
        data={
            CONF_PHONE_NUMBER: "+886912345678",
            CONF_COMMUNITY_UNIT_IDS: community_ids,
            CONF_MEMBER_INFO: {"communityUnits": units, "memberId": "m1", "name": "T"},
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _seed_tokens(hass, entry_id: str, access: str, refresh: str) -> None:
    store = Store(
        hass,
        STORAGE_VERSION,
        STORAGE_KEY_FMT.format(entry_id=entry_id),
        private=True,
    )
    await store.async_save({"access_token": access, "refresh_token": refresh, "refreshed_at": None})


async def _setup(hass, entry: MockConfigEntry, fresh_access: str, refresh: str) -> None:
    await _seed_tokens(hass, entry.entry_id, fresh_access, refresh)
    # Pre-stock an empty unpicked response so the coordinator's first refresh succeeds.
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_request_pickup_code_happy_path(hass, fresh_access_token, long_refresh_token):
    entry = _make_entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload={
                "code": "COM00001",
                "data": {
                    "communityId": 101,
                    "verificationCode": "52229",
                    "expiredTime": "2026-04-19T14:12:47+08:00",
                },
            },
        )
        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_REQUEST_PICKUP_CODE,
            {},
            blocking=True,
            return_response=True,
        )

    assert result["code"] == "52229"
    assert result["community_id"] == 101
    png = base64.b64decode(result["image_b64"])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


async def test_request_pickup_code_when_auth_needed_raises(
    hass, fresh_access_token, long_refresh_token
):
    entry = _make_entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    # Manually flip the auth state to simulate token rejection
    bundle = hass.data[DOMAIN][entry.entry_id]
    bundle["auth"]._transition(AUTH_STATE_AUTH_NEEDED, reason="test")

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_REQUEST_PICKUP_CODE, {}, blocking=True, return_response=True
        )


async def test_request_pickup_code_ambiguous_community_raises(
    hass, fresh_access_token, long_refresh_token
):
    entry = _make_entry(hass, community_ids=[1001, 1003])
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_REQUEST_PICKUP_CODE, {}, blocking=True, return_response=True
        )


async def test_request_pickup_code_resolves_explicit_community(
    hass, fresh_access_token, long_refresh_token
):
    entry = _make_entry(hass, community_ids=[1001, 1003])
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload={
                "code": "COM00001",
                "data": {
                    "communityId": 103,
                    "verificationCode": "11111",
                    "expiredTime": "2026-04-19T14:12:47+08:00",
                },
            },
        )
        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_REQUEST_PICKUP_CODE,
            {"community_id": 103},
            blocking=True,
            return_response=True,
        )
    assert result["community_id"] == 103
    assert result["code"] == "11111"


async def test_send_sms_already_authenticated_raises(hass, fresh_access_token, long_refresh_token):
    entry = _make_entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_SEND_SMS, {}, blocking=True, return_response=True
        )


async def test_send_sms_in_auth_needed_fires_event(hass, fresh_access_token, long_refresh_token):
    entry = _make_entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)
    bundle = hass.data[DOMAIN][entry.entry_id]
    bundle["auth"]._transition(AUTH_STATE_AUTH_NEEDED, reason="test")
    # reset rate limiter so the two-call tests below can run independently
    from custom_components.goodlifetaiwan import services as svc_mod

    svc_mod._SEND_SMS_LAST_CALLED.clear()

    events = async_capture_events(hass, EVENT_AUTH_SMS_SENT)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "0bf82c45-xxx"}},
        )
        result = await hass.services.async_call(
            DOMAIN, SERVICE_SEND_SMS, {}, blocking=True, return_response=True
        )

    assert result["verify_id_hint"] == "0bf82c45"
    await hass.async_block_till_done()
    assert len(events) == 1


async def test_send_sms_rate_limit(hass, fresh_access_token, long_refresh_token):
    entry = _make_entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)
    bundle = hass.data[DOMAIN][entry.entry_id]
    bundle["auth"]._transition(AUTH_STATE_AUTH_NEEDED, reason="test")
    from custom_components.goodlifetaiwan import services as svc_mod

    svc_mod._SEND_SMS_LAST_CALLED.clear()

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "v1"}},
        )
        await hass.services.async_call(
            DOMAIN, SERVICE_SEND_SMS, {}, blocking=True, return_response=True
        )
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, SERVICE_SEND_SMS, {}, blocking=True, return_response=True
            )


async def test_submit_code_invalid_format_raises(hass, fresh_access_token, long_refresh_token):
    entry = _make_entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUBMIT_CODE,
            {"code": "12345"},  # 5 digits
            blocking=True,
            return_response=True,
        )


async def test_submit_code_no_pending_raises(hass, fresh_access_token, long_refresh_token):
    entry = _make_entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUBMIT_CODE,
            {"code": "123456"},
            blocking=True,
            return_response=True,
        )


async def test_submit_code_happy_path(hass, fresh_access_token, long_refresh_token):
    entry = _make_entry(hass)
    await _setup(hass, entry, fresh_access_token, long_refresh_token)
    bundle = hass.data[DOMAIN][entry.entry_id]
    bundle["auth"]._transition(AUTH_STATE_AUTH_NEEDED, reason="test")
    from custom_components.goodlifetaiwan import services as svc_mod

    svc_mod._SEND_SMS_LAST_CALLED.clear()

    new_access = jwt_with_exp(2000000000)
    new_refresh = jwt_with_exp(2100000000, {"jti": "new"})
    success_events = async_capture_events(hass, EVENT_AUTH_SUCCESS)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "v1"}},
        )
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/verifySmsCode",
            payload={"code": "COM00001", "data": {"verifyToken": "vt1"}},
        )
        m.post(
            f"{BASE_URL_AUTH}/api/v2/Member/Login",
            payload={
                "code": "COM00001",
                "data": {
                    "accessToken": new_access,
                    "refreshToken": new_refresh,
                    "expired": "2026-04-19T00:00:00+08:00",
                },
            },
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        await hass.services.async_call(
            DOMAIN, SERVICE_SEND_SMS, {}, blocking=True, return_response=True
        )
        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_SUBMIT_CODE,
            {"code": "123456"},
            blocking=True,
            return_response=True,
        )

    assert result["success"] is True
    await hass.async_block_till_done()
    assert len(success_events) == 1
    assert hass.data[DOMAIN][entry.entry_id]["auth"].state == AUTH_STATE_OK
