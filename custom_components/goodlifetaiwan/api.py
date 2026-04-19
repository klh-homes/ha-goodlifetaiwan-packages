"""HTTP client for life-spi.glf.tw and auth.epictech.com.tw.

Exact request fingerprint (app-info, User-Agent, timestamp, traceparent, communityid)
is reproduced from the mobile app to maximise compatibility with the undocumented API.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import (
    APP_INFO,
    BASE_URL_API,
    BASE_URL_AUTH,
    RESPONSE_CODE_SUCCESS,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
MAX_RETRIES = 3
BACKOFF_BASE_SEC = 1.0


class GoodLifeApiError(Exception):
    """Base for API errors."""


class NetworkError(GoodLifeApiError):
    """Transient network failure (timeout, connection reset, DNS)."""


class ApiResponseError(GoodLifeApiError):
    """Non-success life-spi response (code != COM00001 or unexpected HTTP status).

    ``code`` is the server-supplied error code (e.g. ``ERR9999``) when known.
    """

    def __init__(
        self, message: str, *, status: int, code: str | None = None, body: Any = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body


class AuthRejected(ApiResponseError):
    """Server refused authentication (401 or refresh rejection).

    Callers should transition to the ``auth_needed`` state and surface a re-login flow.
    """


@dataclass(slots=True)
class TokenBundle:
    """New access+refresh tokens returned by login / refresh endpoints."""

    access_token: str
    refresh_token: str
    expired: str  # ISO8601 server-reported access-token expiry (diagnostic only; JWT exp is authoritative)
    user_id: str | None = None


def _epoch_ms() -> str:
    return str(int(time.time() * 1000))


def _traceparent() -> str:
    return str(uuid.uuid4())


def _auth_headers() -> dict[str, str]:
    return {
        "app-info": APP_INFO,
        "User-Agent": USER_AGENT,
        "timestamp": _epoch_ms(),
        "traceparent": _traceparent(),
        "Accept-Encoding": "gzip",
    }


def _api_headers(
    access_token: str,
    community_id: int | None,
    community_unit_id: int | None,
) -> dict[str, str]:
    headers = _auth_headers()
    headers["Authorization"] = f"Bearer {access_token}"
    if community_id is not None:
        headers["communityid"] = str(community_id)
    if community_unit_id is not None:
        headers["communityunitid"] = str(community_unit_id)
    return headers


class GoodLifeApi:
    """Thin wrapper around aiohttp implementing the endpoints we care about."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    # --- auth host -----------------------------------------------------

    async def send_verify_sms(self, phone_number: str) -> str:
        """Request an SMS code. Returns verifyId."""
        data = await self._post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/SendVerifySms",
            body={"data": {"phoneNumber": phone_number}},
            auth_host=True,
        )
        verify_id = data.get("verifyId")
        if not verify_id:
            raise ApiResponseError(
                "missing verifyId in SendVerifySms response", status=200, body=data
            )
        return verify_id

    async def verify_sms_code(self, verify_id: str, sms_code: str) -> str:
        """Verify SMS code. Returns verifyToken JWT."""
        data = await self._post(
            f"{BASE_URL_AUTH}/api/v2/smsVerify/verifySmsCode",
            body={"data": {"smsCode": sms_code, "verifyId": verify_id}},
            auth_host=True,
        )
        verify_token = data.get("verifyToken")
        if not verify_token:
            raise ApiResponseError(
                "missing verifyToken in verifySmsCode response", status=200, body=data
            )
        return verify_token

    async def member_login(self, verify_token: str) -> TokenBundle:
        """Exchange verifyToken for access+refresh tokens."""
        data = await self._post(
            f"{BASE_URL_AUTH}/api/v2/Member/Login",
            body={"data": {"identityToken": verify_token}},
            auth_host=True,
        )
        return _token_bundle(data)

    async def refresh_token(self, refresh_token: str) -> TokenBundle:
        # refresh endpoint wraps the token as a plain string under "data"
        # (unlike all other auth endpoints which use nested objects)
        data = await self._post(
            f"{BASE_URL_AUTH}/api/v2/Token/RefreshMemberToken",
            body={"data": refresh_token},
            auth_host=True,
        )
        return _token_bundle(data)

    # --- life-spi host -------------------------------------------------

    async def member_info(self, access_token: str) -> dict[str, Any]:
        return await self._get(
            f"{BASE_URL_API}/resident/api/Member/MemberInfo",
            access_token=access_token,
            community_id=None,
            community_unit_id=None,
        )

    async def unpicked_packages(
        self,
        access_token: str,
        community_id: int,
        community_unit_id: int,
    ) -> list[dict[str, Any]]:
        data = await self._get(
            f"{BASE_URL_API}/resident/api/v76/Package/UnpickedPackages",
            access_token=access_token,
            community_id=community_id,
            community_unit_id=community_unit_id,
        )
        return list(data.get("items") or [])

    async def package_detail(
        self,
        access_token: str,
        community_id: int,
        community_unit_id: int,
        package_id: int,
    ) -> dict[str, Any]:
        """Fetch a single package's extended detail.

        Beyond what UnpickedPackages returns, this includes ``barcode``, ``remark``,
        ``packageTags[]`` and ``logistics``. Not polled automatically — use on-demand
        when richer info is needed (e.g. to enrich an event payload).
        """
        return await self._get(
            f"{BASE_URL_API}/resident/api/v76/Package/PackageDetail/{int(package_id)}",
            access_token=access_token,
            community_id=community_id,
            community_unit_id=community_unit_id,
        )

    async def create_checkout_verification_code(
        self,
        access_token: str,
        community_id: int,
        community_unit_id: int,
    ) -> dict[str, Any]:
        return await self._post(
            f"{BASE_URL_API}/resident/api/Package/CreateCheckOutVerificationCode",
            body={},
            access_token=access_token,
            community_id=community_id,
            community_unit_id=community_unit_id,
        )

    # --- low-level -----------------------------------------------------

    async def _get(
        self,
        url: str,
        *,
        access_token: str,
        community_id: int | None,
        community_unit_id: int | None,
    ) -> dict[str, Any]:
        headers = _api_headers(access_token, community_id, community_unit_id)
        return await self._request("GET", url, headers=headers, body=None)

    async def _post(
        self,
        url: str,
        *,
        body: Any,
        auth_host: bool = False,
        access_token: str | None = None,
        community_id: int | None = None,
        community_unit_id: int | None = None,
    ) -> dict[str, Any]:
        if auth_host:
            headers = _auth_headers()
            headers["Content-Type"] = "application/json"
        else:
            assert access_token is not None
            headers = _api_headers(access_token, community_id, community_unit_id)
            headers["Content-Type"] = "application/json"
        return await self._request("POST", url, headers=headers, body=body)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: Any,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                async with self._session.request(
                    method,
                    url,
                    headers=headers,
                    json=body if body is not None else None,
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    status = resp.status
                    try:
                        payload = await resp.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError) as parse_err:
                        text = await resp.text()
                        if 500 <= status < 600:
                            last_error = ApiResponseError(
                                f"server error {status}", status=status, body=text
                            )
                            await self._backoff(attempt)
                            continue
                        raise ApiResponseError(
                            f"non-JSON response ({status})", status=status, body=text
                        ) from parse_err

                    code = payload.get("code") if isinstance(payload, dict) else None
                    if status == 401:
                        raise AuthRejected("auth rejected", status=status, code=code, body=payload)
                    if 500 <= status < 600:
                        last_error = ApiResponseError(
                            f"server error {status}", status=status, code=code, body=payload
                        )
                        await self._backoff(attempt)
                        continue
                    if status == 429:
                        last_error = ApiResponseError(
                            "rate limited", status=status, code=code, body=payload
                        )
                        await self._backoff(attempt)
                        continue
                    if status != 200:
                        raise ApiResponseError(
                            f"unexpected HTTP {status}", status=status, code=code, body=payload
                        )
                    if code != RESPONSE_CODE_SUCCESS:
                        raise ApiResponseError(
                            payload.get("message") or f"app-level error {code}",
                            status=status,
                            code=code,
                            body=payload,
                        )
                    return payload.get("data") or {}
            except TimeoutError as err:
                last_error = NetworkError(f"timeout calling {url}")
                last_error.__cause__ = err
                await self._backoff(attempt)
            except aiohttp.ClientError as err:
                last_error = NetworkError(f"network error calling {url}: {err}")
                last_error.__cause__ = err
                await self._backoff(attempt)

        assert last_error is not None
        raise last_error

    @staticmethod
    async def _backoff(attempt: int) -> None:
        await asyncio.sleep(BACKOFF_BASE_SEC * (2**attempt))


def _token_bundle(data: dict[str, Any]) -> TokenBundle:
    try:
        return TokenBundle(
            access_token=data["accessToken"],
            refresh_token=data["refreshToken"],
            expired=data.get("expired", ""),
            user_id=data.get("userId"),
        )
    except KeyError as err:
        raise ApiResponseError(f"missing token field: {err}", status=200, body=data) from err
