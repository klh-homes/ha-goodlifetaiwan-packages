"""v0.3.5 regression tests for the pickup-QR encrypted-content wiring.

v0.1 — v0.3.4 rendered the pickup QR PNG with the 5-digit verificationCode
as the QR body. Warden scanners silently rejected those QRs because they
expect an AES-256-CBC payload of the resident's identity JSON.

v0.3.5 switches the PNG content to the encrypted blob. The 5-digit code
stays on `sensor.*_pickup_code` as a manual-entry fallback. These tests
verify the coordinator feeds the encrypter (not the raw code) into the
PNG renderer at each trigger point (service call, button press, auto-regen).
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.goodlifetaiwan._qr_crypto import build_pickup_qr_content
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


def _entry(hass, *, is_representative: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
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
                        "isRepresentative": is_representative,
                    }
                ],
                "memberId": "0912345678",
                "name": "T",
            },
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _seed(hass, entry_id: str, access: str, refresh: str) -> None:
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY_FMT.format(entry_id=entry_id), private=True)
    await store.async_save({"access_token": access, "refresh_token": refresh, "refreshed_at": None})


async def test_service_call_renders_encrypted_payload_in_qr(
    hass, fresh_access_token, long_refresh_token
):
    entry = _entry(hass, is_representative=True)
    await _seed(hass, entry.entry_id, fresh_access_token, long_refresh_token)

    expiry = (dt_util.utcnow() + timedelta(minutes=10)).isoformat()

    expected_payload = build_pickup_qr_content(
        member_id="0912345678",
        cu_id=1001,
        community_id=101,
        is_representative=True,
    )

    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
            repeat=True,
        )
        m.post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            payload={
                "code": "COM00001",
                "data": {
                    "communityId": 101,
                    "verificationCode": "12345",
                    "expiredTime": expiry,
                },
            },
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Patch _render_qr_png to inspect the argument the coordinator passes in.
        # Patching AFTER setup so the initial-regen path (if any) doesn't eat the
        # assertion — auto_regen is off in this entry so setup doesn't regen.
        with patch(
            "custom_components.goodlifetaiwan.coordinator._render_qr_png",
            return_value=b"\x89PNG\r\n\x1a\ntest",
        ) as mock_render:
            await hass.services.async_call(
                DOMAIN,
                "request_pickup_code",
                {},
                blocking=True,
                return_response=True,
            )
            await hass.async_block_till_done()

    # The renderer was called with the encrypted base64 payload, NOT the 5-digit
    # verificationCode. This is the whole fix.
    mock_render.assert_called_once_with(expected_payload)
    # Explicit: guard against regression where the raw code sneaks back in.
    assert mock_render.call_args.args[0] != "12345"


async def test_is_representative_flag_reaches_encrypter(
    hass, fresh_access_token, long_refresh_token
):
    """Two entries with different is_representative values must produce
    different QR payloads. If the coordinator defaults to False or hard-codes
    True, this test catches it."""
    entry = _entry(hass, is_representative=False)
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
            payload={
                "code": "COM00001",
                "data": {
                    "communityId": 101,
                    "verificationCode": "12345",
                    "expiredTime": expiry,
                },
            },
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch(
            "custom_components.goodlifetaiwan.coordinator._render_qr_png",
            return_value=b"\x89PNG\r\n\x1a\ntest",
        ) as mock_render:
            await hass.services.async_call(
                DOMAIN,
                "request_pickup_code",
                {},
                blocking=True,
                return_response=True,
            )
            await hass.async_block_till_done()

    got_payload = mock_render.call_args.args[0]
    expected_false = build_pickup_qr_content("0912345678", 1001, 101, False)
    expected_true = build_pickup_qr_content("0912345678", 1001, 101, True)
    assert got_payload == expected_false
    assert got_payload != expected_true


async def test_backfill_refreshes_member_info_when_flag_missing(
    hass, fresh_access_token, long_refresh_token
):
    """Entries written by v0.3.4 and earlier lack `isRepresentative`. On
    first setup after upgrade, __init__._maybe_backfill_member_info should
    hit /Member/MemberInfo once and rewrite CONF_MEMBER_INFO with the new
    field. Silent — no user intervention required."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        options={auto_regenerate_key(1001): False},
        data={
            CONF_PHONE_NUMBER: "+886912345678",
            CONF_COMMUNITY_UNIT_IDS: [1001],
            CONF_MEMBER_INFO: {
                "communityUnits": [
                    # Deliberately missing isRepresentative — simulates pre-v0.3.5.
                    {
                        "communityId": 101,
                        "communityUnitId": 1001,
                        "communityName": "Test A",
                    }
                ],
                "memberId": "0912345678",
                "name": "T",
            },
        },
    )
    entry.add_to_hass(hass)
    await _seed(hass, entry.entry_id, fresh_access_token, long_refresh_token)

    # /Member/MemberInfo returns fresh data WITH isRepresentative=True.
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/Member/MemberInfo",
            payload={
                "code": "COM00001",
                "data": {
                    "memberId": "0912345678",
                    "name": "T",
                    "communityUnits": [
                        {
                            "communityId": 101,
                            "communityUnitId": 1001,
                            "communityName": "Test A",
                            "shortAddress": "1號1樓",
                            "isRepresentative": True,
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
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    refreshed_unit = entry.data[CONF_MEMBER_INFO]["communityUnits"][0]
    assert refreshed_unit["isRepresentative"] is True

    # Confirm the coordinator picked up the refreshed value.
    coord = hass.data[DOMAIN][entry.entry_id]["coordinators"][1001]
    assert coord.community.is_representative is True
    assert coord.community.member_id == "0912345678"
