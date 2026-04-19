"""Regression tests for the three review findings.

Covers:
1. Setup with auth_needed triggers a reauth config flow.
2. submit_code distinguishes code_rejected (verify stage) from login_failed
   (Member/Login stage); payload on the failing call reflects the right stage.
3. Reauth flow completion fires ``goodlifetaiwan_auth_success`` (previously
   skipped because the flow wrote tokens directly to the Store).
"""

from __future__ import annotations

import pytest
from aioresponses import aioresponses
from homeassistant import config_entries, data_entry_flow
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
    EVENT_AUTH_SUCCESS,
    SERVICE_SEND_SMS,
    SERVICE_SUBMIT_CODE,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
    auto_regenerate_key,
)
from tests.conftest import jwt_with_exp

pytestmark = pytest.mark.asyncio


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        # auto_regen off so the v0.3.2 setup hook doesn't try to hit
        # CreateCheckOutVerificationCode on tests that aren't exercising it.
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
                        "shortAddress": "6號",
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


# --- Claim 1: setup with auth_needed triggers reauth flow ------------------


async def test_setup_with_auth_needed_starts_reauth_flow(hass):
    """No tokens in Store → AuthManager enters auth_needed → reauth flow starts."""
    entry = _entry(hass)
    # deliberately do NOT seed tokens; AuthManager.async_load() will go auth_needed
    with aioresponses():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    reauth = [
        f
        for f in flows
        if f["handler"] == DOMAIN
        and f["context"].get("source") == config_entries.SOURCE_REAUTH
        and f["context"].get("entry_id") == entry.entry_id
    ]
    assert len(reauth) == 1


async def test_setup_with_valid_tokens_does_not_start_reauth(
    hass, fresh_access_token, long_refresh_token
):
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

    flows = hass.config_entries.flow.async_progress()
    assert not any(
        f["handler"] == DOMAIN and f["context"].get("source") == config_entries.SOURCE_REAUTH
        for f in flows
    )


# --- Claim 2: submit_code distinguishes code_rejected vs login_failed ------


async def test_submit_code_verify_stage_failure_reports_code_rejected(
    hass, fresh_access_token, long_refresh_token
):
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

    bundle = hass.data[DOMAIN][entry.entry_id]
    bundle["auth"]._transition(AUTH_STATE_AUTH_NEEDED, reason="test")
    from custom_components.goodlifetaiwan import services as svc_mod

    svc_mod._SEND_SMS_LAST_CALLED.clear()

    failed_events = async_capture_events(hass, EVENT_AUTH_FAILED)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "v1"}},
        )
        # verifySmsCode fails → stage should be verify_code, service should
        # surface code_rejected (NOT login_failed).
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/verifySmsCode",
            payload={"code": "ERR9999", "message": "wrong"},
        )
        await hass.services.async_call(
            DOMAIN, SERVICE_SEND_SMS, {}, blocking=True, return_response=True
        )
        with pytest.raises(HomeAssistantError) as exc:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SUBMIT_CODE,
                {"code": "000000"},
                blocking=True,
                return_response=True,
            )

    assert "code_rejected" in str(exc.value)
    assert "login_failed" not in str(exc.value)
    await hass.async_block_till_done()
    assert len(failed_events) == 1
    assert failed_events[0].data["stage"] == "verify_code"


async def test_submit_code_login_stage_failure_reports_login_failed(
    hass, fresh_access_token, long_refresh_token
):
    """verifySmsCode succeeds but Member/Login fails — must surface login_failed."""
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

    bundle = hass.data[DOMAIN][entry.entry_id]
    bundle["auth"]._transition(AUTH_STATE_AUTH_NEEDED, reason="test")
    from custom_components.goodlifetaiwan import services as svc_mod

    svc_mod._SEND_SMS_LAST_CALLED.clear()

    failed_events = async_capture_events(hass, EVENT_AUTH_FAILED)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "v1"}},
        )
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/verifySmsCode",
            payload={"code": "COM00001", "data": {"verifyToken": "vt1"}},
        )
        # 200 + non-success code is a non-retryable app-level error; surfaces
        # as ApiResponseError in auth.py and then LoginFailed in the service.
        m.post(
            f"{BASE_URL_AUTH}/api/v2/Member/Login",
            payload={"code": "ERR9001", "message": "login rejected"},
        )
        await hass.services.async_call(
            DOMAIN, SERVICE_SEND_SMS, {}, blocking=True, return_response=True
        )
        with pytest.raises(HomeAssistantError) as exc:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SUBMIT_CODE,
                {"code": "123456"},
                blocking=True,
                return_response=True,
            )

    assert "login_failed" in str(exc.value)
    assert "code_rejected" not in str(exc.value)
    await hass.async_block_till_done()
    # auth_failed event fires with stage=login
    assert len(failed_events) == 1
    assert failed_events[0].data["stage"] == "login"


# --- Claim 3: reauth completion fires auth_success -------------------------


async def test_reauth_flow_completion_fires_auth_success(
    hass, fresh_access_token, long_refresh_token
):
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

    success_events = async_capture_events(hass, EVENT_AUTH_SUCCESS)
    new_access = jwt_with_exp(2000000000)
    new_refresh = jwt_with_exp(2100000000, {"jti": "rot"})

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

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PHONE_NUMBER: "0912345678"}
        )
        assert result["step_id"] == "sms_verify"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    await hass.async_block_till_done()
    assert len(success_events) == 1
    assert success_events[0].data["entry_id"] == entry.entry_id
    assert success_events[0].data["phone_masked"] == "***5678"

    # Store should now hold the rotated tokens
    stored = await Store(
        hass, STORAGE_VERSION, STORAGE_KEY_FMT.format(entry_id=entry.entry_id), private=True
    ).async_load()
    assert stored["access_token"] == new_access
    assert stored["refresh_token"] == new_refresh
