"""Coordinator tests: diff + event firing."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.goodlifetaiwan.api import GoodLifeApi
from custom_components.goodlifetaiwan.auth import AuthManager
from custom_components.goodlifetaiwan.const import (
    BASE_URL_API,
    EVENT_PACKAGE_ARRIVED,
    EVENT_PACKAGE_PICKED,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
)
from custom_components.goodlifetaiwan.coordinator import CommunityState, GoodLifeCoordinator

pytestmark = pytest.mark.asyncio


def _pkg(pid: int, name: str = "Alice", placement: str = "櫃台") -> dict:
    return {
        "packageId": pid,
        "packageNo": f"{pid:04d}",
        "toContactInfo": {"name": name, "phone": "0912"},
        "packagePlacement": {"packagePlacementName": placement},
        "checkedInDate": "2026-04-16T16:21:56",
        "isPackageOwner": True,
        "fileInfos": [{"fileUrl": "u"}],
    }


async def _make_coordinator(hass, fresh_access_token, long_refresh_token) -> GoodLifeCoordinator:
    """Build a coordinator using hass's shared aiohttp session.

    Going through ``async_get_clientsession`` lets the harness's verify_cleanup
    fixture drain aiohttp's internal shutdown-loop thread on teardown.
    """
    api = GoodLifeApi(async_get_clientsession(hass))
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY_FMT.format(entry_id="e1"), private=True)
    await store.async_save(
        {
            "access_token": fresh_access_token,
            "refresh_token": long_refresh_token,
            "refreshed_at": None,
        }
    )
    auth = AuthManager(hass, "e1", "+886912345678", api)
    await auth.async_load()

    from custom_components.goodlifetaiwan.const import auto_regenerate_key

    class _FakeEntry:
        entry_id = "e1"
        # Explicit opt-out so expiry timers fired during teardown don't try to
        # auto-regen against a closed aioresponses mock (v0.3 default is True).
        options: dict = {auto_regenerate_key(1001): False}

    state = CommunityState(
        community_id=101,
        community_unit_id=1001,
        community_name="測試社區",
        slug="",
    )
    return GoodLifeCoordinator(hass, _FakeEntry(), api, auth, state)


async def test_first_poll_does_not_fire_events(hass, fresh_access_token, long_refresh_token):
    coord = await _make_coordinator(hass, fresh_access_token, long_refresh_token)
    arrived = async_capture_events(hass, EVENT_PACKAGE_ARRIVED)
    picked = async_capture_events(hass, EVENT_PACKAGE_PICKED)

    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={
                "code": "COM00001",
                "data": {"items": [_pkg(1), _pkg(2)]},
            },
        )
        await coord.async_refresh()
        assert coord.last_update_success

    await hass.async_block_till_done()
    assert len(arrived) == 0
    assert len(picked) == 0
    # state should still be populated
    assert len(coord.community.packages) == 2


async def test_second_poll_new_package_fires_arrived(hass, fresh_access_token, long_refresh_token):
    coord = await _make_coordinator(hass, fresh_access_token, long_refresh_token)
    arrived = async_capture_events(hass, EVENT_PACKAGE_ARRIVED)

    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": [_pkg(1)]}},
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={
                "code": "COM00001",
                "data": {"items": [_pkg(1), _pkg(2, name="Bob", placement="左側")]},
            },
        )
        await coord.async_refresh()
        await coord.async_refresh()

    await hass.async_block_till_done()
    assert len(arrived) == 1
    ev = arrived[0].data
    assert ev["entry_id"] == "e1"
    assert ev["community_id"] == 101
    assert ev["community_name"] == "測試社區"
    assert ev["package_id"] == 2
    assert ev["recipient_name"] == "Bob"
    assert ev["placement"] == "左側"
    assert ev["is_owner"] is True
    assert ev["has_photo"] is True


async def test_second_poll_missing_package_fires_picked(
    hass, fresh_access_token, long_refresh_token
):
    coord = await _make_coordinator(hass, fresh_access_token, long_refresh_token)
    picked = async_capture_events(hass, EVENT_PACKAGE_PICKED)

    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={
                "code": "COM00001",
                "data": {
                    "items": [
                        _pkg(1, name="Alice", placement="櫃台"),
                        _pkg(2, name="Bob"),
                    ]
                },
            },
        )
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": [_pkg(2, name="Bob")]}},
        )
        await coord.async_refresh()
        await coord.async_refresh()

    await hass.async_block_till_done()
    assert len(picked) == 1
    ev = picked[0].data
    # picked payload must come from the last-known snapshot
    assert ev["package_id"] == 1
    assert ev["recipient_name"] == "Alice"
    assert ev["placement"] == "櫃台"
    assert ev["community_id"] == 101


async def test_qr_snapshot_updates_state_and_notifies(hass, fresh_access_token, long_refresh_token):
    coord = await _make_coordinator(hass, fresh_access_token, long_refresh_token)
    with aioresponses() as m:
        m.get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            payload={"code": "COM00001", "data": {"items": []}},
        )
        await coord.async_refresh()

    from custom_components.goodlifetaiwan.coordinator import QrSnapshot

    snap = QrSnapshot(
        code="52229",
        expires_at="2026-04-19T14:12:47+08:00",
        generated_at="2026-04-19T14:02:47+08:00",
        community_id=101,
        png_bytes=b"\x89PNG\r\n\x1a\n",
    )
    await coord.async_set_qr_snapshot(snap)
    assert coord.community.qr is snap
