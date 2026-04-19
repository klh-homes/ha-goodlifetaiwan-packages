"""Regression tests for review points 3 + 4.

- Point 3: send_sms / submit_code acquire a per-entry ``sms_lock`` so concurrent
  callers cannot race the Store write / state transition boundary.
- Point 4: ``auth_failed`` events always carry ``error_code`` (empty string on
  NetworkError) so downstream Jinja2 templates can access the field
  unconditionally.
"""

from __future__ import annotations

import asyncio

import pytest
from aioresponses import aioresponses
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.goodlifetaiwan.const import (
    AUTH_STATE_AUTH_NEEDED,
    BASE_URL_API,
    BASE_URL_AUTH,
    CONF_COMMUNITY_UNIT_IDS,
    CONF_MEMBER_INFO,
    CONF_PHONE_NUMBER,
    DOMAIN,
    EVENT_AUTH_FAILED,
    SERVICE_SEND_SMS,
    SERVICE_SUBMIT_CODE,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
    auto_regenerate_key,
)

pytestmark = pytest.mark.asyncio


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        # auto_regen off so setup doesn't hit CreateCheckOutVerificationCode.
        options={auto_regenerate_key(1001): False},
        data={
            CONF_PHONE_NUMBER: "+886912345678",
            CONF_COMMUNITY_UNIT_IDS: [1001],
            CONF_MEMBER_INFO: {
                "communityUnits": [
                    {
                        "communityId": 101,
                        "communityUnitId": 1001,
                        "communityName": "Test A",
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


async def _setup_in_auth_needed(hass, fresh_access_token, long_refresh_token) -> MockConfigEntry:
    entry = _entry(hass)
    await _seed(hass, entry.entry_id, fresh_access_token, long_refresh_token)
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    hass.data[DOMAIN][entry.entry_id]["auth"]._transition(AUTH_STATE_AUTH_NEEDED, reason="test")
    # clear module-level rate limiter for test isolation
    from custom_components.goodlifetaiwan import services as svc_mod

    svc_mod._SEND_SMS_LAST_CALLED.clear()
    return entry


# --- Point 3: per-entry sms_lock serialises submit_code ---------------------


async def test_sms_lock_registered_on_entry_setup(hass, fresh_access_token, long_refresh_token):
    entry = await _setup_in_auth_needed(hass, fresh_access_token, long_refresh_token)
    lock = hass.data[DOMAIN][entry.entry_id].get("sms_lock")
    assert isinstance(lock, asyncio.Lock)


async def test_concurrent_submit_code_serialised(hass, fresh_access_token, long_refresh_token):
    """Two overlapping submit_code calls: one must succeed and the other must
    see the pending_verification cleared by the first — surfacing as
    ``no_pending_verification``. Without the lock both would enter the AuthManager
    together and race the Store write.
    """
    await _setup_in_auth_needed(hass, fresh_access_token, long_refresh_token)

    from tests.conftest import jwt_with_exp

    new_access = jwt_with_exp(2000000000)
    new_refresh = jwt_with_exp(2100000000, {"jti": "rot"})

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "v1"}},
        )
        # verifySmsCode is awaited twice only in the buggy (unlocked) path;
        # with the lock, the second caller bails at pending_verification check.
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/verifySmsCode",
            payload={"code": "COM00001", "data": {"verifyToken": "vt1"}},
            repeat=True,
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
            repeat=True,
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )

        await hass.services.async_call(
            DOMAIN, SERVICE_SEND_SMS, {}, blocking=True, return_response=True
        )

        results = await asyncio.gather(
            hass.services.async_call(
                DOMAIN,
                SERVICE_SUBMIT_CODE,
                {"code": "123456"},
                blocking=True,
                return_response=True,
            ),
            hass.services.async_call(
                DOMAIN,
                SERVICE_SUBMIT_CODE,
                {"code": "123456"},
                blocking=True,
                return_response=True,
            ),
            return_exceptions=True,
        )

    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    # The loser must see the cleared pending_verification, not a login crash.
    assert "no_pending_verification" in str(failures[0]).lower()


# --- Point 4: error_code always present in auth_failed ----------------------


async def test_auth_failed_includes_error_code_on_api_error(
    hass, fresh_access_token, long_refresh_token
):
    await _setup_in_auth_needed(hass, fresh_access_token, long_refresh_token)
    failed_events = async_capture_events(hass, EVENT_AUTH_FAILED)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "v1"}},
        )
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/verifySmsCode",
            payload={"code": "ERR9999", "message": "wrong"},
        )
        await hass.services.async_call(
            DOMAIN, SERVICE_SEND_SMS, {}, blocking=True, return_response=True
        )
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SUBMIT_CODE,
                {"code": "000000"},
                blocking=True,
                return_response=True,
            )

    await hass.async_block_till_done()
    assert len(failed_events) == 1
    payload = failed_events[0].data
    assert "error_code" in payload
    assert payload["error_code"] == "ERR9999"


async def test_auth_failed_includes_empty_error_code_on_network_error(
    hass, fresh_access_token, long_refresh_token
):
    """Contract requires ``error_code`` on every auth_failed payload. On a
    NetworkError the field must still be present (empty string) so Jinja2
    templates don't get ``undefined``.
    """
    await _setup_in_auth_needed(hass, fresh_access_token, long_refresh_token)
    failed_events = async_capture_events(hass, EVENT_AUTH_FAILED)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "v1"}},
        )
        # Simulate network failure on verifySmsCode. aioresponses keeps raising
        # for every retry the api client attempts (3× with backoff).
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/verifySmsCode",
            exception=TimeoutError("simulated network failure"),
            repeat=True,
        )

        # Keep the test fast — patch backoff to no-op.
        from custom_components.goodlifetaiwan import api as api_mod

        orig = api_mod.GoodLifeApi._backoff
        api_mod.GoodLifeApi._backoff = staticmethod(lambda *_a, **_k: asyncio.sleep(0))
        try:
            await hass.services.async_call(
                DOMAIN, SERVICE_SEND_SMS, {}, blocking=True, return_response=True
            )
            with pytest.raises(HomeAssistantError):
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_SUBMIT_CODE,
                    {"code": "123456"},
                    blocking=True,
                    return_response=True,
                )
        finally:
            api_mod.GoodLifeApi._backoff = orig

    await hass.async_block_till_done()
    assert len(failed_events) == 1
    payload = failed_events[0].data
    assert "error_code" in payload, "error_code key must always be present"
    assert payload["error_code"] == "", "NetworkError has no server code"
    assert payload["stage"] == "verify_code"
