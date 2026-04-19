"""Shared test fixtures."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import UTC
from pathlib import Path

import aiohttp
import pytest

pytest_plugins = ("pytest_homeassistant_custom_component",)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# Pre-warm aiohttp's internal daemon thread (`_run_safe_shutdown_loop`) at
# collection time. The thread is started lazily on first ``ClientSession``
# creation, and ``pytest_homeassistant_custom_component``'s ``verify_cleanup``
# fixture fails any test that leaves a "new" thread behind. By creating (and
# closing) one session here, the daemon starts once and is captured in every
# later test's ``threads_before`` baseline — no longer flagged as a per-test
# leak. The daemon is process-wide; the session object itself is disposable.
async def _prewarm() -> None:
    async with aiohttp.ClientSession():
        pass


asyncio.run(_prewarm())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA pick up our custom_components/ tree under tests/."""
    yield


def jwt_with_exp(exp: int, payload_extra: dict | None = None) -> str:
    """Construct a structurally valid (unsigned, fake-signature) JWT."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": "testuser", "exp": exp}
    if payload_extra:
        payload.update(payload_extra)

    def _seg(d: dict) -> str:
        raw = json.dumps(d, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{_seg(header)}.{_seg(payload)}.fake"


@pytest.fixture
def fresh_access_token() -> str:
    return jwt_with_exp(int(time.time()) + 600)


@pytest.fixture
def expired_access_token() -> str:
    return jwt_with_exp(int(time.time()) - 10)


@pytest.fixture
def long_refresh_token() -> str:
    return jwt_with_exp(int(time.time()) + 7776000, {"jti": "testjti"})


@pytest.fixture
def load_fixture():
    def _load(name: str) -> dict:
        path = FIXTURE_DIR / name
        return json.loads(path.read_text())

    return _load


@pytest.fixture
def mock_config_entry(hass, fresh_access_token, long_refresh_token):
    """Config entry with a single community; tokens already in the Store."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.goodlifetaiwan.const import (
        CONF_COMMUNITY_UNIT_IDS,
        CONF_MEMBER_INFO,
        CONF_PHONE_NUMBER,
        DOMAIN,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="+886912345678",
        title="中保好生活 ***5678",
        data={
            CONF_PHONE_NUMBER: "+886912345678",
            CONF_COMMUNITY_UNIT_IDS: [110412],
            CONF_MEMBER_INFO: {
                "communityUnits": [
                    {
                        "communityId": 1777,
                        "communityUnitId": 110412,
                        "communityName": "測試社區",
                        "shortAddress": "1號1樓",
                    }
                ],
                "memberId": "m1",
                "name": "測試",
            },
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def seed_tokens(hass, fresh_access_token, long_refresh_token):
    """Factory: write a token file for a given entry_id into HA's Store."""
    from datetime import datetime, timezone

    from homeassistant.helpers.storage import Store

    from custom_components.goodlifetaiwan.const import STORAGE_KEY_FMT, STORAGE_VERSION

    async def _seed(entry_id: str, *, access: str | None = None, refresh: str | None = None):
        store = Store(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY_FMT.format(entry_id=entry_id),
            private=True,
        )
        await store.async_save(
            {
                "access_token": access or fresh_access_token,
                "refresh_token": refresh or long_refresh_token,
                "refreshed_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
            }
        )

    return _seed
