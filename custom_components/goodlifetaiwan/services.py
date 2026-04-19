"""Service handlers: request_qr, send_sms, submit_code.

Services are registered globally (once across all entries) in __init__.py. The
handlers here resolve which entry / community the caller addressed, then run
the underlying operation.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import time
from datetime import UTC, datetime

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import ApiResponseError, NetworkError
from .auth import AuthRequired, CodeRejected, LoginFailed
from .const import (
    AUTH_STATE_AUTH_NEEDED,
    AUTH_STATE_OK,
    DOMAIN,
    SEND_SMS_RATE_LIMIT_SEC,
    SERVICE_REQUEST_QR,
    SERVICE_SEND_SMS,
    SERVICE_SUBMIT_CODE,
)
from .coordinator import CommunityState, GoodLifeCoordinator, QrSnapshot

_LOGGER = logging.getLogger(__name__)

_SERVICE_REQUEST_QR_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("community_id"): vol.Coerce(int),
    }
)

_SERVICE_SEND_SMS_SCHEMA = vol.Schema({vol.Optional("entry_id"): cv.string})

_SERVICE_SUBMIT_CODE_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Required("code"): cv.string,
    }
)

_SEND_SMS_LAST_CALLED: dict[str, float] = {}


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_REQUEST_QR):
        return

    async def _request_qr(call: ServiceCall) -> ServiceResponse:
        return await _handle_request_qr(hass, call)

    async def _send_sms(call: ServiceCall) -> ServiceResponse:
        return await _handle_send_sms(hass, call)

    async def _submit_code(call: ServiceCall) -> ServiceResponse:
        return await _handle_submit_code(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_QR,
        _request_qr,
        schema=_SERVICE_REQUEST_QR_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_SMS,
        _send_sms,
        schema=_SERVICE_SEND_SMS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SUBMIT_CODE,
        _submit_code,
        schema=_SERVICE_SUBMIT_CODE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for svc in (SERVICE_REQUEST_QR, SERVICE_SEND_SMS, SERVICE_SUBMIT_CODE):
        if hass.services.has_service(DOMAIN, svc):
            hass.services.async_remove(DOMAIN, svc)


# --- request_qr -----------------------------------------------------------


async def _handle_request_qr(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry_id = _resolve_entry_id(hass, call.data.get("entry_id"))
    bundle = hass.data[DOMAIN][entry_id]
    coordinator: GoodLifeCoordinator = bundle["coordinator"]
    auth = bundle["auth"]

    if auth.state == AUTH_STATE_AUTH_NEEDED:
        raise ServiceValidationError(
            "Re-login via goodlifetaiwan.send_sms",
            translation_domain=DOMAIN,
            translation_key="auth_required",
        )

    state = _resolve_community(coordinator, call.data.get("community_id"))

    async with state.qr_lock:
        try:
            access = await auth.async_ensure_access_token()
        except AuthRequired as err:
            raise ServiceValidationError(
                "auth_required",
                translation_domain=DOMAIN,
                translation_key="auth_required",
            ) from err
        except NetworkError as err:
            raise HomeAssistantError(f"network_error: {err}") from err

        started = time.time()
        try:
            data = await coordinator.api.create_checkout_verification_code(
                access, state.community_id, state.community_unit_id
            )
        except ApiResponseError as err:
            raise HomeAssistantError(f"api_error: {err}") from err
        except NetworkError as err:
            raise HomeAssistantError(f"network_error: {err}") from err

        code = str(data.get("verificationCode") or "")
        expires_at = str(data.get("expiredTime") or "")
        if not code:
            raise HomeAssistantError("api_error: missing verificationCode")

        png_bytes = await hass.async_add_executor_job(_render_qr_png, code)
        generated_at = _now_iso()

        snap = QrSnapshot(
            code=code,
            expires_at=expires_at,
            generated_at=generated_at,
            community_id=state.community_id,
            png_bytes=png_bytes,
        )
        await coordinator.async_set_qr_snapshot(state.community_unit_id, snap)

        duration_ms = int((time.time() - started) * 1000)
        _LOGGER.info(
            "service=request_qr entry=%s community=%s duration=%sms code=%s",
            entry_id[:8],
            state.community_id,
            duration_ms,
            code,
        )

    return {
        "code": code,
        "image_b64": base64.b64encode(png_bytes).decode("ascii"),
        "expires_at": expires_at,
        "community_id": state.community_id,
        "generated_at": generated_at,
    }


def _render_qr_png(payload: str) -> bytes:
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- send_sms -------------------------------------------------------------


async def _handle_send_sms(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry_id = _resolve_entry_id(hass, call.data.get("entry_id"))
    bundle = hass.data[DOMAIN][entry_id]
    auth = bundle["auth"]

    async with bundle["sms_lock"]:
        if auth.state == AUTH_STATE_OK:
            raise ServiceValidationError(
                "already_authenticated",
                translation_domain=DOMAIN,
                translation_key="already_authenticated",
            )

        last = _SEND_SMS_LAST_CALLED.get(entry_id, 0.0)
        now = time.time()
        if now - last < SEND_SMS_RATE_LIMIT_SEC:
            cooldown = int(SEND_SMS_RATE_LIMIT_SEC - (now - last))
            raise ServiceValidationError(
                f"rate_limited: retry in {cooldown}s",
                translation_domain=DOMAIN,
                translation_key="rate_limited",
            )

        try:
            pending = await auth.async_start_sms()
        except ApiResponseError as err:
            raise HomeAssistantError(f"api_error: {err}") from err
        except NetworkError as err:
            raise HomeAssistantError(f"network_error: {err}") from err

        _SEND_SMS_LAST_CALLED[entry_id] = now
        _LOGGER.info(
            "service=send_sms entry=%s verify_id=%s",
            entry_id[:8],
            pending.verify_id[:8],
        )

        return {
            "sent_at": _iso(pending.sent_at),
            "expires_at": _iso(pending.expires_at),
            "verify_id_hint": pending.verify_id[:8],
        }


# --- submit_code ----------------------------------------------------------


async def _handle_submit_code(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry_id = _resolve_entry_id(hass, call.data.get("entry_id"))
    bundle = hass.data[DOMAIN][entry_id]
    auth = bundle["auth"]
    coordinator: GoodLifeCoordinator = bundle["coordinator"]

    code = str(call.data.get("code") or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ServiceValidationError(
            "invalid_code_format",
            translation_domain=DOMAIN,
            translation_key="invalid_code_format",
        )

    # Serialise with send_sms: two concurrent submit_code calls would otherwise
    # both see pending_verification, the first would clear it, and the second
    # would hit the AuthManager with stale state.
    async with bundle["sms_lock"]:
        if auth.pending_verification is None:
            raise ServiceValidationError(
                "no_pending_verification",
                translation_domain=DOMAIN,
                translation_key="no_pending_verification",
            )

        try:
            await auth.async_submit_sms_code(code)
        except CodeRejected as err:
            # User-recoverable: wrong code or expired verify_id. verify_id TTL may
            # still be valid, so the user can retry with a fresh code.
            raise HomeAssistantError(f"code_rejected: {err}") from err
        except LoginFailed as err:
            # Server-side failure after the code verified — retry won't help, user
            # should restart the SMS flow.
            raise HomeAssistantError(f"login_failed: {err}") from err
        except NetworkError as err:
            raise HomeAssistantError(f"network_error: {err}") from err

        # Kick a refresh so entities update immediately.
        await coordinator.async_request_refresh()

        _LOGGER.info("service=submit_code entry=%s success=true", entry_id[:8])

        return {
            "success": True,
            "access_token_exp": auth.access_token_exp,
            "refresh_token_exp": auth.refresh_token_exp,
        }


# --- resolvers ------------------------------------------------------------


def _resolve_entry_id(hass: HomeAssistant, supplied: str | None) -> str:
    entries = hass.data.get(DOMAIN) or {}
    known = [eid for eid in entries if eid != "_services_registered"]
    if supplied is not None:
        if supplied not in known:
            raise ServiceValidationError(
                f"unknown entry_id: {supplied}",
                translation_domain=DOMAIN,
                translation_key="unknown_entry",
            )
        return supplied
    if len(known) == 1:
        return known[0]
    raise ServiceValidationError(
        "ambiguous — specify entry_id (multiple GoodLifeTaiwan accounts configured)",
        translation_domain=DOMAIN,
        translation_key="ambiguous_entry",
    )


def _resolve_community(coordinator: GoodLifeCoordinator, supplied: int | None) -> CommunityState:
    if supplied is not None:
        state = coordinator.community_by_id(int(supplied))
        if state is None:
            raise ServiceValidationError(
                f"unknown community_id: {supplied}",
                translation_domain=DOMAIN,
                translation_key="unknown_community",
            )
        return state
    if len(coordinator.communities) == 1:
        return next(iter(coordinator.communities.values()))
    raise ServiceValidationError(
        "ambiguous — specify community_id (entry has multiple communities)",
        translation_domain=DOMAIN,
        translation_key="ambiguous_community",
    )


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).astimezone().isoformat(timespec="seconds")
