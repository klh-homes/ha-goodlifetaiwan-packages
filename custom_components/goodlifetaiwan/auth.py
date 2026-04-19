"""Token lifecycle, state machine, and SMS login flow.

Access tokens are short-lived (10 min) and refresh tokens are single-use-rotating
(90-day lifetime). We persist the pair via HA's Store helper; an asyncio.Lock
serialises refreshes so callers never race the rotation.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import ApiResponseError, AuthRejected, GoodLifeApi, NetworkError, TokenBundle
from .const import (
    ACCESS_TOKEN_REFRESH_MARGIN_SEC,
    AUTH_STATE_AUTH_NEEDED,
    AUTH_STATE_ERROR,
    AUTH_STATE_OK,
    AUTH_STATE_REFRESHING,
    EVENT_AUTH_FAILED,
    EVENT_AUTH_REQUIRED,
    EVENT_AUTH_SMS_SENT,
    EVENT_AUTH_SUCCESS,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class AuthRequired(Exception):
    """Raised when callers must trigger the re-auth flow."""


class CodeRejected(Exception):
    """Server rejected the SMS verification code (wrong code or expired verify_id).

    The user can retry with a fresh code while the 3-min verify_id TTL is valid.
    """


class LoginFailed(Exception):
    """Member/Login failed after verifySmsCode succeeded.

    Retrying the same code will not help — the user should restart the SMS flow.
    """


@dataclass(slots=True)
class TokenState:
    access_token: str | None = None
    refresh_token: str | None = None
    refreshed_at: str | None = None


@dataclass(slots=True)
class PendingVerification:
    verify_id: str
    phone_number: str
    sent_at: float
    expires_at: float


@dataclass(slots=True)
class AuthManager:
    """Per-entry auth state holder + operations."""

    hass: HomeAssistant
    entry_id: str
    phone_number: str
    api: GoodLifeApi
    _store: Store = field(init=False)
    _state: str = field(default=AUTH_STATE_AUTH_NEEDED, init=False)
    _tokens: TokenState = field(default_factory=TokenState, init=False)
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _last_error: str | None = field(default=None, init=False)
    _pending: PendingVerification | None = field(default=None, init=False)
    _state_listeners: list[Callable[[str], None]] = field(default_factory=list, init=False)
    # True once we've told the user to re-auth; flipped back only after a
    # successful transition to ``ok``. Prevents polling loops from respamming
    # the auth_required event on every cycle of auth_needed→refreshing→auth_needed.
    _auth_required_notified: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._store = Store(
            self.hass,
            STORAGE_VERSION,
            STORAGE_KEY_FMT.format(entry_id=self.entry_id),
            private=True,
        )

    # --- lifecycle ---------------------------------------------------------

    async def async_load(self) -> None:
        """Load tokens from Store. Must be awaited before first token use."""
        raw = await self._store.async_load() or {}
        self._tokens = TokenState(
            access_token=raw.get("access_token"),
            refresh_token=raw.get("refresh_token"),
            refreshed_at=raw.get("refreshed_at"),
        )
        if not self._tokens.refresh_token or not jwt_valid_structure(self._tokens.refresh_token):
            self._transition("auth_needed", reason="initial")
            return
        if jwt_exp(self._tokens.refresh_token) < time.time():
            self._transition("auth_needed", reason="refresh_expired")
            return
        self._transition(AUTH_STATE_OK)

    async def _persist(self) -> None:
        await self._store.async_save(
            {
                "access_token": self._tokens.access_token,
                "refresh_token": self._tokens.refresh_token,
                "refreshed_at": self._tokens.refreshed_at,
            }
        )

    # --- state accessors ---------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def access_token_exp(self) -> str | None:
        return _jwt_exp_iso(self._tokens.access_token)

    @property
    def refresh_token_exp(self) -> str | None:
        return _jwt_exp_iso(self._tokens.refresh_token)

    def register_state_listener(self, listener: Callable[[str], None]) -> Callable[[], None]:
        self._state_listeners.append(listener)

        def _unsub() -> None:
            if listener in self._state_listeners:
                self._state_listeners.remove(listener)

        return _unsub

    def _transition(self, new_state: str, *, reason: str | None = None) -> None:
        if self._state == new_state:
            return
        previous = self._state
        self._state = new_state
        _LOGGER.info(
            "auth state: %s → %s (entry=%s reason=%s)",
            previous,
            new_state,
            _short(self.entry_id),
            reason,
        )
        for listener in list(self._state_listeners):
            try:
                listener(new_state)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("state listener crashed")
        if new_state == AUTH_STATE_OK:
            self._auth_required_notified = False
        elif new_state == AUTH_STATE_AUTH_NEEDED and not self._auth_required_notified:
            self._auth_required_notified = True
            self.hass.bus.async_fire(
                EVENT_AUTH_REQUIRED,
                {
                    "entry_id": self.entry_id,
                    "phone_masked": mask_phone(self.phone_number),
                    "reason": reason or "unknown",
                    "at": _now_iso(),
                },
            )

    # --- access token management ------------------------------------------

    async def async_ensure_access_token(self) -> str:
        """Return a valid access token, refreshing if within margin. May raise AuthRequired."""
        async with self._refresh_lock:
            access = self._tokens.access_token
            if access and _remaining(access) > ACCESS_TOKEN_REFRESH_MARGIN_SEC:
                return access

            refresh = self._tokens.refresh_token
            if not refresh:
                self._transition(AUTH_STATE_AUTH_NEEDED, reason="no_refresh")
                raise AuthRequired

            self._transition(AUTH_STATE_REFRESHING)
            try:
                bundle = await self.api.refresh_token(refresh)
            except AuthRejected as err:
                _LOGGER.warning("refresh rejected: %s", err)
                self._transition(AUTH_STATE_AUTH_NEEDED, reason="refresh_rejected")
                raise AuthRequired from err
            except ApiResponseError as err:
                # Some ERR codes may indicate server hiccup rather than auth fail.
                # Treat unknown app-level errors on refresh as auth_needed to force
                # a clean re-login; this is conservative and matches the app behaviour.
                _LOGGER.warning("refresh failed: %s", err)
                self._last_error = str(err)
                self._transition(AUTH_STATE_AUTH_NEEDED, reason="refresh_rejected")
                raise AuthRequired from err
            except NetworkError as err:
                self._last_error = str(err)
                self._transition(AUTH_STATE_ERROR)
                raise

            await self._store_tokens(bundle)
            self._transition(AUTH_STATE_OK)
            return bundle.access_token

    async def _store_tokens(self, bundle: TokenBundle) -> None:
        self._tokens = TokenState(
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            refreshed_at=_now_iso(),
        )
        await self._persist()

    # --- SMS login flow ---------------------------------------------------

    async def async_start_sms(self) -> PendingVerification:
        verify_id = await self.api.send_verify_sms(self.phone_number)
        sent_at = time.time()
        pending = PendingVerification(
            verify_id=verify_id,
            phone_number=self.phone_number,
            sent_at=sent_at,
            expires_at=sent_at + 180,  # 3-minute verify_id TTL
        )
        self._pending = pending
        self.hass.bus.async_fire(
            EVENT_AUTH_SMS_SENT,
            {
                "entry_id": self.entry_id,
                "phone_masked": mask_phone(self.phone_number),
                "sent_at": _iso(sent_at),
                "expires_at": _iso(pending.expires_at),
                "verify_id_hint": verify_id[:8],
            },
        )
        return pending

    @property
    def pending_verification(self) -> PendingVerification | None:
        if self._pending is None:
            return None
        if time.time() > self._pending.expires_at:
            self._pending = None
            return None
        return self._pending

    def clear_pending_verification(self) -> None:
        self._pending = None

    async def async_submit_sms_code(self, code: str) -> TokenBundle:
        pending = self.pending_verification
        if pending is None:
            raise ValueError("no_pending_verification")

        try:
            verify_token = await self.api.verify_sms_code(pending.verify_id, code)
        except ApiResponseError as err:
            self._fire_auth_failed("verify_code", err)
            raise CodeRejected(str(err)) from err
        except NetworkError as err:
            self._fire_auth_failed("verify_code", err)
            raise

        try:
            bundle = await self.api.member_login(verify_token)
        except ApiResponseError as err:
            self._fire_auth_failed("login", err)
            raise LoginFailed(str(err)) from err
        except NetworkError as err:
            self._fire_auth_failed("login", err)
            raise

        await self._finalize_login(bundle)
        return bundle

    async def _finalize_login(self, bundle: TokenBundle) -> None:
        """Persist tokens, transition to ok, fire auth_success.

        Shared between the two ways a successful login can complete:
        ``async_submit_sms_code`` (programmatic service call) and
        ``async_store_tokens_from_flow`` (config flow reauth path).
        """
        await self._store_tokens(bundle)
        self._pending = None
        self._transition(AUTH_STATE_OK, reason="sms_login")
        self.hass.bus.async_fire(
            EVENT_AUTH_SUCCESS,
            {
                "entry_id": self.entry_id,
                "phone_masked": mask_phone(self.phone_number),
                "access_token_exp": self.access_token_exp,
                "refresh_token_exp": self.refresh_token_exp,
                "at": _now_iso(),
            },
        )

    def _fire_auth_failed(self, stage: str, err: Exception) -> None:
        # error_code is always present per contracts/events.md; empty string
        # when the error isn't an ApiResponseError (NetworkError, unexpected
        # types) so downstream Jinja2 templates can access it unconditionally.
        error_code = err.code or "" if isinstance(err, ApiResponseError) else ""
        self.hass.bus.async_fire(
            EVENT_AUTH_FAILED,
            {
                "entry_id": self.entry_id,
                "phone_masked": mask_phone(self.phone_number),
                "stage": stage,
                "error_code": error_code,
                "error_message": str(err),
                "at": _now_iso(),
            },
        )

    async def async_store_tokens_from_flow(self, bundle: TokenBundle) -> None:
        """Called by config_flow reauth after a successful login to persist fresh tokens.

        Fires the same auth_success event as async_submit_sms_code so user
        automations see reauth completions regardless of whether the user
        re-authenticated via the config flow UI or the send_sms/submit_code
        service pair.
        """
        await self._finalize_login(bundle)


# --- helpers ---------------------------------------------------------------


def jwt_valid_structure(token: str | None) -> bool:
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        _jwt_payload(token)
    except Exception:  # noqa: BLE001
        return False
    return True


def jwt_exp(token: str) -> int:
    payload = _jwt_payload(token)
    return int(payload.get("exp", 0))


def _jwt_payload(token: str) -> dict[str, Any]:
    segment = token.split(".")[1]
    padding = "=" * (-len(segment) % 4)
    decoded = base64.urlsafe_b64decode(segment + padding)
    return json.loads(decoded)


def _jwt_exp_iso(token: str | None) -> str | None:
    if not token or not jwt_valid_structure(token):
        return None
    return _iso(jwt_exp(token))


def _remaining(token: str) -> int:
    try:
        return int(jwt_exp(token) - time.time())
    except Exception:  # noqa: BLE001
        return 0


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).astimezone().isoformat(timespec="seconds")


def mask_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    last4 = digits[-4:] if len(digits) >= 4 else digits
    return f"***{last4}"


def _short(s: str) -> str:
    return s[:8] if s else ""
