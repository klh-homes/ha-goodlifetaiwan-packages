"""Tests for the life-spi / auth HTTP client."""

from __future__ import annotations

import asyncio

import pytest
from aioresponses import aioresponses
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.goodlifetaiwan.api import (
    ApiResponseError,
    AuthRejected,
    GoodLifeApi,
    NetworkError,
)
from custom_components.goodlifetaiwan.const import BASE_URL_API, BASE_URL_AUTH

pytestmark = pytest.mark.asyncio


# All tests use the shared HA-managed aiohttp session so pytest_homeassistant_custom_component's
# verify_cleanup fixture can drain aiohttp's internal _run_safe_shutdown_loop thread
# on teardown. A raw ``aiohttp.ClientSession()`` leaves that daemon thread behind and
# trips the harness's "no threads after test" check on Python 3.12.


async def test_send_verify_sms_returns_verify_id(hass):
    api = GoodLifeApi(async_get_clientsession(hass))
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "abc-123"}},
        )
        verify_id = await api.send_verify_sms("+886912345678")
    assert verify_id == "abc-123"


async def test_refresh_wraps_token_as_plain_string(hass):
    api = GoodLifeApi(async_get_clientsession(hass))
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/Token/RefreshMemberToken",
            payload={
                "code": "COM00001",
                "data": {
                    "userId": "0912345678",
                    "accessToken": "newA",
                    "refreshToken": "newR",
                    "expired": "2026-04-19T00:00:00+08:00",
                },
            },
        )
        bundle = await api.refresh_token("oldR")
        assert bundle.access_token == "newA"
        assert bundle.refresh_token == "newR"

        # verify request body wraps refresh token as plain string under "data"
        req = next(iter(m.requests.values()))[0]
        assert req.kwargs["json"] == {"data": "oldR"}


async def test_refresh_401_raises_auth_rejected(hass):
    api = GoodLifeApi(async_get_clientsession(hass))
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/Token/RefreshMemberToken",
            status=401,
            payload={"code": "ERR0001", "message": "Invalid"},
        )
        with pytest.raises(AuthRejected):
            await api.refresh_token("stale")


async def test_response_code_err_raises_api_response_error(hass):
    api = GoodLifeApi(async_get_clientsession(hass))
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/verifySmsCode",
            payload={"code": "ERR9999", "message": "wrong code"},
        )
        with pytest.raises(ApiResponseError) as exc:
            await api.verify_sms_code("vid", "123456")
        assert exc.value.code == "ERR9999"


async def test_unpicked_packages_returns_items(hass, load_fixture):
    api = GoodLifeApi(async_get_clientsession(hass))
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload=load_fixture("unpicked_packages_two.json"),
        )
        items = await api.unpicked_packages("access", 1777, 110412)
    assert len(items) == 2
    assert items[0]["packageId"] == 3561900


async def test_life_spi_headers_include_community(hass, fresh_access_token):
    api = GoodLifeApi(async_get_clientsession(hass))
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
        )
        await api.unpicked_packages(fresh_access_token, 1777, 110412)

        req = next(iter(m.requests.values()))[0]
        hdrs = req.kwargs["headers"]
        assert hdrs["Authorization"] == f"Bearer {fresh_access_token}"
        assert hdrs["communityid"] == "1777"
        assert hdrs["communityunitid"] == "110412"
        assert hdrs["app-info"].startswith("Android/14 Beer/")
        assert hdrs["User-Agent"] == "Dart/3.8 (dart:io)"
        assert hdrs["timestamp"].isdigit()


async def test_package_detail_hits_versioned_path(hass):
    api = GoodLifeApi(async_get_clientsession(hass))
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/PackageDetail/3561900",
            payload={
                "code": "COM00001",
                "data": {
                    "packageId": 3561900,
                    "barcode": "TW1234567890",
                    "remark": "易碎",
                    "packageTags": [{"tagName": "冷藏"}],
                    "logistics": {"companyName": "黑貓"},
                },
            },
        )
        data = await api.package_detail("access", 1777, 110412, 3561900)
    assert data["barcode"] == "TW1234567890"
    assert data["logistics"]["companyName"] == "黑貓"


async def test_network_error_retries_then_raises(hass):
    api = GoodLifeApi(async_get_clientsession(hass))
    with aioresponses() as m:
        # three failures → three retries
        for _ in range(3):
            m.get(
                f"{BASE_URL_API}/resident/api/Member/MemberInfo",
                exception=TimeoutError(),
            )
        # make backoff instant
        from custom_components.goodlifetaiwan import api as api_mod

        orig = api_mod.GoodLifeApi._backoff
        api_mod.GoodLifeApi._backoff = staticmethod(lambda *_a, **_k: asyncio.sleep(0))
        try:
            with pytest.raises(NetworkError):
                await api.member_info("access")
        finally:
            api_mod.GoodLifeApi._backoff = orig
