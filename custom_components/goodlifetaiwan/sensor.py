"""Sensor platform: unpicked count, auth status, QR code/expiry, service health."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .auth import AuthManager
from .const import AUTH_STATES, DOMAIN, SERVICE_HEALTH_STATES
from .coordinator import CommunityState, GoodLifeCoordinator
from .entity import (
    account_device_info,
    community_device_info,
    community_slug,
    unique_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinator: GoodLifeCoordinator = bundle["coordinator"]
    auth: AuthManager = bundle["auth"]

    entities: list[SensorEntity] = [ServiceHealthSensor(coordinator, auth, entry)]
    for state in coordinator.communities.values():
        entities.append(UnpickedSensor(coordinator, entry, state))
        entities.append(AuthStatusSensor(coordinator, auth, entry, state))
        entities.append(QrCodeSensor(coordinator, entry, state))
        entities.append(QrExpiresSensor(coordinator, entry, state))

    async_add_entities(entities)


class _BaseCommunitySensor(CoordinatorEntity[GoodLifeCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoodLifeCoordinator,
        entry: ConfigEntry,
        state: CommunityState,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._cu_id = state.community_unit_id
        self._slug = state.slug or community_slug(
            state.community_name, state.community_unit_id, state.community_id
        )
        self._attr_device_info = community_device_info(entry.entry_id, state)

    @property
    def _state(self) -> CommunityState | None:
        return self.coordinator.communities.get(self._cu_id)


class UnpickedSensor(_BaseCommunitySensor):
    _attr_translation_key = "unpicked"
    _attr_icon = "mdi:package-variant-closed"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, state: CommunityState) -> None:
        super().__init__(coordinator, entry, state)
        self._attr_unique_id = unique_id(entry.entry_id, state.community_unit_id, "unpicked")

    @property
    def native_value(self) -> int | None:
        state = self._state
        return len(state.packages) if state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self._state
        if state is None:
            return None
        return {
            "items": [pkg.as_attr() for pkg in state.packages.values()],
            "last_updated": state.last_success,
            "community_id": state.community_id,
            "community_unit_id": state.community_unit_id,
        }


class AuthStatusSensor(CoordinatorEntity[GoodLifeCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "auth_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(AUTH_STATES)

    def __init__(
        self,
        coordinator: GoodLifeCoordinator,
        auth: AuthManager,
        entry: ConfigEntry,
        state: CommunityState,
    ) -> None:
        super().__init__(coordinator)
        self._auth = auth
        self._entry = entry
        self._cu_id = state.community_unit_id
        self._attr_unique_id = unique_id(entry.entry_id, state.community_unit_id, "auth_status")
        self._attr_device_info = community_device_info(entry.entry_id, state)
        self._unsub_auth: Any = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_auth = self._auth.register_state_listener(self._on_auth_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_auth is not None:
            self._unsub_auth()
        await super().async_will_remove_from_hass()

    @callback
    def _on_auth_state(self, _new_state: str) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return self._auth.state

    @property
    def icon(self) -> str:
        return (
            "mdi:shield-lock-outline"
            if self._auth.state == "auth_needed"
            else "mdi:shield-check-outline"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.communities.get(self._cu_id)
        return {
            "last_success": state.last_success if state else None,
            "access_token_exp": self._auth.access_token_exp,
            "refresh_token_exp": self._auth.refresh_token_exp,
            "last_error": self._auth.last_error,
        }


class QrCodeSensor(_BaseCommunitySensor):
    _attr_translation_key = "qr_code"
    _attr_icon = "mdi:numeric-5-box-outline"

    def __init__(self, coordinator, entry, state: CommunityState) -> None:
        super().__init__(coordinator, entry, state)
        self._attr_unique_id = unique_id(entry.entry_id, state.community_unit_id, "qr_code")

    @property
    def native_value(self) -> str | None:
        state = self._state
        if state is None or state.qr is None:
            return None
        return state.qr.code

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self._state
        if state is None or state.qr is None:
            return None
        return {
            "expires_at": state.qr.expires_at,
            "generated_at": state.qr.generated_at,
            "community_id": state.qr.community_id,
        }


class QrExpiresSensor(_BaseCommunitySensor):
    _attr_translation_key = "qr_expires"
    _attr_icon = "mdi:clock-end"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry, state: CommunityState) -> None:
        super().__init__(coordinator, entry, state)
        self._attr_unique_id = unique_id(entry.entry_id, state.community_unit_id, "qr_expires")

    @property
    def native_value(self) -> datetime | None:
        state = self._state
        if state is None or state.qr is None:
            return None
        try:
            return datetime.fromisoformat(state.qr.expires_at)
        except ValueError:
            return None


class ServiceHealthSensor(CoordinatorEntity[GoodLifeCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "service_health"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(SERVICE_HEALTH_STATES)
    _attr_icon = "mdi:heart-pulse"

    def __init__(
        self,
        coordinator: GoodLifeCoordinator,
        auth: AuthManager,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._auth = auth
        self._entry = entry
        self._attr_unique_id = unique_id(entry.entry_id, None, "service_health")
        self._attr_device_info = account_device_info(
            entry.entry_id, entry.data.get("phone_number", "")
        )
        self._unsub_auth: Any = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_auth = self._auth.register_state_listener(self._on_auth_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_auth is not None:
            self._unsub_auth()
        await super().async_will_remove_from_hass()

    @callback
    def _on_auth_state(self, _new_state: str) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        state = self._auth.state
        if state == "refreshing":
            # service_health only reports ok/auth_needed/error per contract
            return "ok"
        return state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last_success = next(
            (c.last_success for c in self.coordinator.communities.values() if c.last_success),
            None,
        )
        return {
            "last_check": self.coordinator.last_check,
            "last_success": last_success,
            "access_exp": self._auth.access_token_exp,
            "refresh_exp": self._auth.refresh_token_exp,
            "communities": self.coordinator.community_ids,
            "next_poll": self.coordinator.next_poll,
            "last_error": self._auth.last_error,
        }
