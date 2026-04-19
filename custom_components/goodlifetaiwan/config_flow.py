"""Config flow: SMS-driven setup wizard + reauth + options."""

from __future__ import annotations

import logging
import re
from datetime import UTC
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.storage import Store

from .api import ApiResponseError, GoodLifeApi, NetworkError, TokenBundle
from .const import (
    CONF_COMMUNITY_UNIT_IDS,
    CONF_MEMBER_INFO,
    CONF_PHONE_NUMBER,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_SEC,
    DOMAIN,
    MAX_SCAN_INTERVAL_SEC,
    MIN_SCAN_INTERVAL_SEC,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

_PHONE_NUMBER_SCHEMA = vol.Schema({vol.Required(CONF_PHONE_NUMBER): str})
_SMS_CODE_SCHEMA = vol.Schema({vol.Required("code"): str})


def normalize_phone(raw: str) -> str:
    s = raw.strip().replace(" ", "").replace("-", "")
    if s.startswith("+"):
        return s
    if s.startswith("09") and len(s) == 10:
        return f"+886{s[1:]}"
    if s.startswith("886") and len(s) == 12:
        return f"+{s}"
    raise vol.Invalid("invalid_phone_format")


def _mask(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    return f"***{digits[-4:]}" if len(digits) >= 4 else phone


class GoodLifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Walk the user through phone → SMS code → (optional) community selection."""

    VERSION = 1

    def __init__(self) -> None:
        self._phone: str | None = None
        self._verify_id: str | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._communities: list[dict[str, Any]] = []
        self._member_info: dict[str, Any] | None = None
        self._reauth_entry: ConfigEntry | None = None

    # ---------- initial / reauth entry points ------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        default_phone = ""
        if self._reauth_entry is not None:
            default_phone = self._reauth_entry.data.get(CONF_PHONE_NUMBER, "")

        if user_input is not None:
            try:
                phone = normalize_phone(user_input[CONF_PHONE_NUMBER])
            except vol.Invalid as err:
                errors["base"] = str(err)
            else:
                if self._reauth_entry is None:
                    await self.async_set_unique_id(phone)
                    self._abort_if_unique_id_configured()
                elif phone != self._reauth_entry.data.get(CONF_PHONE_NUMBER):
                    # In reauth mode the phone shouldn't change; reject.
                    errors["base"] = "phone_mismatch"
                if not errors:
                    api = _make_api(self.hass)
                    try:
                        self._verify_id = await api.send_verify_sms(phone)
                    except ApiResponseError as err:
                        _LOGGER.warning("SendVerifySms failed: %s", err)
                        errors["base"] = "api_error"
                    except NetworkError as err:
                        _LOGGER.warning("SendVerifySms network: %s", err)
                        errors["base"] = "network"
                    else:
                        self._phone = phone
                        return await self.async_step_sms_verify()

        schema = vol.Schema({vol.Required(CONF_PHONE_NUMBER, default=default_phone): str})
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_user()

    async def async_step_sms_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._phone and self._verify_id
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["code"].strip()
            if not re.fullmatch(r"\d{6}", code):
                errors["code"] = "invalid_code_format"
            else:
                api = _make_api(self.hass)
                try:
                    verify_token = await api.verify_sms_code(self._verify_id, code)
                except ApiResponseError as err:
                    _LOGGER.info("verifySmsCode rejected: %s", err)
                    errors["code"] = "code_incorrect"
                except NetworkError:
                    errors["base"] = "network"
                else:
                    try:
                        bundle = await api.member_login(verify_token)
                    except ApiResponseError:
                        errors["base"] = "api_error"
                    except NetworkError:
                        errors["base"] = "network"
                    else:
                        self._access_token = bundle.access_token
                        self._refresh_token = bundle.refresh_token
                        return await self._after_login()

        return self.async_show_form(
            step_id="sms_verify",
            data_schema=_SMS_CODE_SCHEMA,
            errors=errors,
            description_placeholders={"phone_masked": _mask(self._phone)},
        )

    async def _after_login(self) -> ConfigFlowResult:
        """Fetch member info, branch into community selection or finalize."""
        assert self._access_token and self._refresh_token

        if self._reauth_entry is not None:
            # Re-auth path: route through the running AuthManager so the
            # auth_success event fires and state transitions happen exactly
            # like they would for a service-call-driven reauth. If the entry
            # isn't loaded (unusual), fall back to a direct Store write.
            bundle = TokenBundle(
                access_token=self._access_token,
                refresh_token=self._refresh_token,
                expired="",
            )
            entry_data = self.hass.data.get(DOMAIN, {}).get(self._reauth_entry.entry_id)
            if entry_data and (auth := entry_data.get("auth")) is not None:
                await auth.async_store_tokens_from_flow(bundle)
            else:
                await _save_tokens(
                    self.hass,
                    self._reauth_entry.entry_id,
                    self._access_token,
                    self._refresh_token,
                )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        api = _make_api(self.hass)
        try:
            self._member_info = await api.member_info(self._access_token)
        except (ApiResponseError, NetworkError) as err:
            _LOGGER.warning("MemberInfo after login failed: %s", err)
            return self.async_abort(reason="cannot_connect")

        units = list(self._member_info.get("communityUnits") or [])
        self._communities = units
        if len(units) <= 1:
            selected = [int(u["communityUnitId"]) for u in units]
            return await self._create_entry(selected)

        return await self.async_step_community()

    async def async_step_community(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        options = {int(u["communityUnitId"]): _community_label(u) for u in self._communities}

        if user_input is not None:
            selected = user_input.get("communities") or []
            if not selected:
                errors["communities"] = "at_least_one"
            else:
                return await self._create_entry([int(i) for i in selected])

        schema = vol.Schema(
            {
                vol.Required("communities"): vol.All(
                    vol.Coerce(list),
                    [vol.In(list(options))],
                )
            }
        )
        # HA renders a multi-select automatically when the schema uses a list of In().
        return self.async_show_form(
            step_id="community",
            data_schema=schema,
            errors=errors,
            description_placeholders={"count": str(len(options))},
        )

    async def _create_entry(self, community_unit_ids: list[int]) -> ConfigFlowResult:
        assert self._phone and self._access_token and self._refresh_token
        # Tokens ride in entry.data as bootstrap fields; __init__ moves them
        # into a dedicated Store on first setup, then strips them from entry.data.
        data = {
            CONF_PHONE_NUMBER: self._phone,
            CONF_COMMUNITY_UNIT_IDS: community_unit_ids,
            CONF_MEMBER_INFO: _member_info_snapshot(self._member_info or {}),
            "_bootstrap_access_token": self._access_token,
            "_bootstrap_refresh_token": self._refresh_token,
        }
        title = f"中保好生活 {_mask(self._phone)}"
        return self.async_create_entry(title=title, data=data)

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:  # type: ignore[override]
        return GoodLifeOptionsFlow(config_entry)


class GoodLifeOptionsFlow(OptionsFlow):
    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC)
        schema = vol.Schema(
            {
                vol.Optional(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL_SEC, max=MAX_SCAN_INTERVAL_SEC),
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


# --- helpers ---------------------------------------------------------------


def _make_api(hass) -> GoodLifeApi:
    return GoodLifeApi(aiohttp_client.async_get_clientsession(hass))


def _community_label(unit: dict[str, Any]) -> str:
    name = unit.get("communityName") or f"community_{unit.get('communityId')}"
    short = unit.get("shortAddress") or ""
    return f"{name} — {short}" if short else name


def _member_info_snapshot(member: dict[str, Any]) -> dict[str, Any]:
    # Keep only fields useful for display / slug derivation; drop PII heavy bits.
    units = member.get("communityUnits") or []
    trimmed = [
        {
            "communityId": u.get("communityId"),
            "communityUnitId": u.get("communityUnitId"),
            "communityName": u.get("communityName"),
            "shortAddress": u.get("shortAddress"),
        }
        for u in units
    ]
    return {
        "communityUnits": trimmed,
        "memberId": member.get("memberId"),
        "name": member.get("name"),
    }


async def _save_tokens(hass, entry_id: str, access_token: str, refresh_token: str) -> None:
    """Persist tokens to Store keyed by entry_id. Safe to call before the entry is finalised."""
    store = Store(
        hass,
        STORAGE_VERSION,
        STORAGE_KEY_FMT.format(entry_id=entry_id),
        private=True,
    )
    from datetime import datetime

    await store.async_save(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "refreshed_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        }
    )
