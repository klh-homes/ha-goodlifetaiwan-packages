"""End-to-end config flow + options flow + init bootstrap migration tests."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses
from homeassistant import config_entries, data_entry_flow
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.goodlifetaiwan.const import (
    BASE_URL_API,
    BASE_URL_AUTH,
    CONF_COMMUNITY_UNIT_IDS,
    CONF_MEMBER_INFO,
    CONF_PHONE_NUMBER,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
)
from tests.conftest import jwt_with_exp

pytestmark = pytest.mark.asyncio


async def test_flow_happy_path_single_community(hass):
    access = jwt_with_exp(2000000000)
    refresh = jwt_with_exp(2100000000, {"jti": "new"})
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
                    "accessToken": access,
                    "refreshToken": refresh,
                    "expired": "2026-04-19T00:00:00+08:00",
                },
            },
        )
        m.get(
            f"{BASE_URL_API}/resident/api/Member/MemberInfo",
            payload={
                "code": "COM00001",
                "data": {
                    "memberId": "m1",
                    "name": "T",
                    "communityUnits": [
                        {
                            "communityId": 1777,
                            "communityUnitId": 110412,
                            "communityName": "社區A",
                            "shortAddress": "6號",
                        }
                    ],
                },
            },
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PHONE_NUMBER: "0912345678"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "sms_verify"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_PHONE_NUMBER] == "+886912345678"
        assert result["data"][CONF_COMMUNITY_UNIT_IDS] == [110412]
        assert "_bootstrap_access_token" in result["data"]

    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    # __init__ migration should have moved bootstrap tokens into the Store
    entry = entries[0]
    assert "_bootstrap_access_token" not in entry.data
    store = Store(
        hass, STORAGE_VERSION, STORAGE_KEY_FMT.format(entry_id=entry.entry_id), private=True
    )
    stored = await store.async_load()
    assert stored["access_token"] == access
    assert stored["refresh_token"] == refresh


async def test_flow_multi_community_prompts_selection(hass):
    access = jwt_with_exp(2000000000)
    refresh = jwt_with_exp(2100000000, {"jti": "new"})
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
                    "accessToken": access,
                    "refreshToken": refresh,
                    "expired": "2026-04-19T00:00:00+08:00",
                },
            },
        )
        m.get(
            f"{BASE_URL_API}/resident/api/Member/MemberInfo",
            payload={
                "code": "COM00001",
                "data": {
                    "memberId": "m1",
                    "name": "T",
                    "communityUnits": [
                        {
                            "communityId": 1777,
                            "communityUnitId": 110412,
                            "communityName": "社區A",
                            "shortAddress": "6號",
                        },
                        {
                            "communityId": 1778,
                            "communityUnitId": 110413,
                            "communityName": "社區B",
                            "shortAddress": "8號",
                        },
                    ],
                },
            },
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PHONE_NUMBER: "0912345678"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "community"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"communities": [110413]}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_COMMUNITY_UNIT_IDS] == [110413]


async def test_flow_wrong_code_stays_on_sms_verify(hass):
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "v1"}},
        )
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/verifySmsCode",
            payload={"code": "ERR9999", "message": "wrong"},
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PHONE_NUMBER: "0912345678"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "sms_verify"
    assert result["errors"] == {"code": "code_incorrect"}


async def test_flow_already_configured_aborts(hass):
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        data={CONF_PHONE_NUMBER: "+886912345678", CONF_COMMUNITY_UNIT_IDS: []},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PHONE_NUMBER: "0912345678"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_scan_interval(hass, fresh_access_token, long_refresh_token):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
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
                ]
            },
        },
    )
    entry.add_to_hass(hass)
    store = Store(
        hass,
        STORAGE_VERSION,
        STORAGE_KEY_FMT.format(entry_id=entry.entry_id),
        private=True,
    )
    await store.async_save(
        {
            "access_token": fresh_access_token,
            "refresh_token": long_refresh_token,
            "refreshed_at": None,
        }
    )
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["step_id"] == "init"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: 300}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL] == 300


async def test_options_flow_rejects_out_of_range(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        data={CONF_PHONE_NUMBER: "+886912345678", CONF_COMMUNITY_UNIT_IDS: []},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    # voluptuous raises MultipleInvalid / Invalid; its exact type surface has
    # shifted across HA versions so we catch by module rather than class.
    import voluptuous as vol

    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: 10}
        )
