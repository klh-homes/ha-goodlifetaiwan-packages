"""Tests for AuthManager: state machine, refresh lock, SMS flow events."""

from __future__ import annotations

import asyncio
import time

import pytest
from aioresponses import aioresponses
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.goodlifetaiwan.api import (
    GoodLifeApi,
    TokenBundle,
)
from custom_components.goodlifetaiwan.auth import (
    AuthManager,
    AuthRequired,
    CodeRejected,
)
from custom_components.goodlifetaiwan.const import (
    AUTH_STATE_AUTH_NEEDED,
    AUTH_STATE_OK,
    BASE_URL_AUTH,
    EVENT_AUTH_FAILED,
    EVENT_AUTH_REQUIRED,
    EVENT_AUTH_SMS_SENT,
    EVENT_AUTH_SUCCESS,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
)
from tests.conftest import jwt_with_exp

pytestmark = pytest.mark.asyncio


# All tests borrow the hass-managed aiohttp session via ``async_get_clientsession``
# so the harness's verify_cleanup fixture can drain aiohttp's internal
# _run_safe_shutdown_loop daemon on teardown. Raw ``aiohttp.ClientSession()`` leaks
# that thread and trips the "no threads after test" check on Python 3.12 CI.


def _make_auth(hass, entry_id: str = "e1") -> AuthManager:
    return AuthManager(
        hass=hass,
        entry_id=entry_id,
        phone_number="+886912345678",
        api=GoodLifeApi(async_get_clientsession(hass)),
    )


async def _seed_store(hass, entry_id: str, *, access: str | None, refresh: str | None) -> None:
    store = Store(
        hass,
        STORAGE_VERSION,
        STORAGE_KEY_FMT.format(entry_id=entry_id),
        private=True,
    )
    await store.async_save(
        {
            "access_token": access,
            "refresh_token": refresh,
            "refreshed_at": None,
        }
    )


async def _load_store(hass, entry_id: str) -> dict | None:
    store = Store(
        hass,
        STORAGE_VERSION,
        STORAGE_KEY_FMT.format(entry_id=entry_id),
        private=True,
    )
    return await store.async_load()


async def test_load_empty_store_starts_auth_needed(hass):
    mgr = _make_auth(hass)
    await mgr.async_load()
    assert mgr.state == AUTH_STATE_AUTH_NEEDED


async def test_load_valid_tokens_sets_ok(hass, fresh_access_token, long_refresh_token):
    await _seed_store(hass, "e1", access=fresh_access_token, refresh=long_refresh_token)
    mgr = _make_auth(hass)
    await mgr.async_load()
    assert mgr.state == AUTH_STATE_OK


async def test_load_expired_refresh_sets_auth_needed(hass, fresh_access_token):
    dead = jwt_with_exp(int(time.time()) - 10)
    await _seed_store(hass, "e1", access=fresh_access_token, refresh=dead)
    mgr = _make_auth(hass)
    await mgr.async_load()
    assert mgr.state == AUTH_STATE_AUTH_NEEDED


async def test_ensure_access_returns_fresh_token_without_refresh(
    hass, fresh_access_token, long_refresh_token
):
    """A fresh-enough access token short-circuits the refresh path."""
    await _seed_store(hass, "e1", access=fresh_access_token, refresh=long_refresh_token)
    mgr = _make_auth(hass)
    await mgr.async_load()

    with aioresponses() as m:
        # no HTTP mock registered → if refresh is called, the test fails
        token = await mgr.async_ensure_access_token()
        assert token == fresh_access_token
        assert len(m.requests) == 0


async def test_ensure_access_refreshes_when_near_expiry(hass, long_refresh_token):
    """Access token inside the 30s margin triggers refresh and persists rotated pair."""
    # exp 5s in future → below 30s margin
    near_dead = jwt_with_exp(int(time.time()) + 5)
    await _seed_store(hass, "e1", access=near_dead, refresh=long_refresh_token)
    mgr = _make_auth(hass)
    await mgr.async_load()

    new_access = jwt_with_exp(int(time.time()) + 600)
    new_refresh = jwt_with_exp(int(time.time()) + 7776000, {"jti": "new"})
    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/Token/RefreshMemberToken",
            payload={
                "code": "COM00001",
                "data": {
                    "accessToken": new_access,
                    "refreshToken": new_refresh,
                    "expired": "2026-04-19T00:00:00+08:00",
                },
            },
        )
        token = await mgr.async_ensure_access_token()
    assert token == new_access
    assert mgr.state == AUTH_STATE_OK

    stored = await _load_store(hass, "e1")
    assert stored["access_token"] == new_access
    assert stored["refresh_token"] == new_refresh


async def test_ensure_access_refresh_rejected_fires_auth_required(hass, long_refresh_token):
    near_dead = jwt_with_exp(int(time.time()) + 5)
    await _seed_store(hass, "e1", access=near_dead, refresh=long_refresh_token)
    mgr = _make_auth(hass)
    await mgr.async_load()

    events = async_capture_events(hass, EVENT_AUTH_REQUIRED)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/Token/RefreshMemberToken",
            status=401,
            payload={"code": "ERR0001", "message": "invalid refresh"},
        )
        with pytest.raises(AuthRequired):
            await mgr.async_ensure_access_token()

    assert mgr.state == AUTH_STATE_AUTH_NEEDED
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["reason"] == "refresh_rejected"
    assert events[0].data["phone_masked"] == "***5678"


async def test_refresh_lock_serialises_concurrent_callers(hass, long_refresh_token):
    """Two overlapping ensure_access calls must result in exactly one refresh HTTP call."""
    near_dead = jwt_with_exp(int(time.time()) + 5)
    await _seed_store(hass, "e1", access=near_dead, refresh=long_refresh_token)
    mgr = _make_auth(hass)
    await mgr.async_load()

    new_access = jwt_with_exp(int(time.time()) + 600)
    new_refresh = jwt_with_exp(int(time.time()) + 7776000, {"jti": "new"})
    call_count = 0

    async def _fake_refresh(refresh_token: str) -> TokenBundle:
        nonlocal call_count
        call_count += 1
        # Yield so the second caller joins the lock queue
        await asyncio.sleep(0.01)
        return TokenBundle(
            access_token=new_access,
            refresh_token=new_refresh,
            expired="2026-04-19T00:00:00+08:00",
        )

    mgr.api.refresh_token = _fake_refresh  # type: ignore[method-assign]

    a, b = await asyncio.gather(
        mgr.async_ensure_access_token(),
        mgr.async_ensure_access_token(),
    )
    assert a == new_access and b == new_access
    assert call_count == 1


async def test_start_sms_fires_auth_sms_sent(hass):
    mgr = _make_auth(hass)
    await mgr.async_load()

    events = async_capture_events(hass, EVENT_AUTH_SMS_SENT)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "0bf82c45-xxx"}},
        )
        pending = await mgr.async_start_sms()

    await hass.async_block_till_done()
    assert pending.verify_id == "0bf82c45-xxx"
    assert len(events) == 1
    assert events[0].data["verify_id_hint"] == "0bf82c45"
    assert events[0].data["phone_masked"] == "***5678"
    assert mgr.pending_verification is pending


async def test_submit_sms_code_happy_path(hass):
    mgr = _make_auth(hass)
    await mgr.async_load()

    new_access = jwt_with_exp(int(time.time()) + 600)
    new_refresh = jwt_with_exp(int(time.time()) + 7776000, {"jti": "new"})
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
        await mgr.async_start_sms()
        bundle = await mgr.async_submit_sms_code("123456")

    assert bundle.access_token == new_access
    assert mgr.state == AUTH_STATE_OK
    assert mgr.pending_verification is None

    stored = await _load_store(hass, "e1")
    assert stored["access_token"] == new_access

    await hass.async_block_till_done()
    assert len(success_events) == 1
    assert success_events[0].data["phone_masked"] == "***5678"


async def test_submit_sms_code_wrong_code_fires_auth_failed(hass):
    mgr = _make_auth(hass)
    await mgr.async_load()

    failed_events = async_capture_events(hass, EVENT_AUTH_FAILED)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "v1"}},
        )
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/verifySmsCode",
            payload={"code": "ERR9999", "message": "wrong code"},
        )
        await mgr.async_start_sms()
        with pytest.raises(CodeRejected):
            await mgr.async_submit_sms_code("000000")

    await hass.async_block_till_done()
    assert len(failed_events) == 1
    assert failed_events[0].data["stage"] == "verify_code"
    assert failed_events[0].data["error_code"] == "ERR9999"


async def test_submit_sms_code_without_pending_raises(hass):
    mgr = _make_auth(hass)
    await mgr.async_load()
    with pytest.raises(ValueError):
        await mgr.async_submit_sms_code("123456")


async def test_pending_verification_ttl_expires(hass):
    mgr = _make_auth(hass)
    await mgr.async_load()

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            payload={"code": "COM00001", "data": {"verifyId": "v1"}},
        )
        pending = await mgr.async_start_sms()

    assert mgr.pending_verification is not None
    # simulate TTL expiry
    pending.expires_at = time.time() - 1
    assert mgr.pending_verification is None


async def test_auth_required_event_debounced(hass, long_refresh_token):
    """Re-entering auth_needed twice should only fire the event once."""
    near_dead = jwt_with_exp(int(time.time()) + 5)
    await _seed_store(hass, "e1", access=near_dead, refresh=long_refresh_token)
    mgr = _make_auth(hass)
    await mgr.async_load()

    events = async_capture_events(hass, EVENT_AUTH_REQUIRED)

    with aioresponses() as m:
        m.post(
            f"{BASE_URL_AUTH}/api/v2/Token/RefreshMemberToken",
            status=401,
            payload={"code": "ERR0001"},
            repeat=True,
        )
        with pytest.raises(AuthRequired):
            await mgr.async_ensure_access_token()
        with pytest.raises(AuthRequired):
            await mgr.async_ensure_access_token()

    await hass.async_block_till_done()
    # Second call skips transition (already auth_needed) → no duplicate event.
    assert len(events) == 1
