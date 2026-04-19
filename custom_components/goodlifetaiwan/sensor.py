"""Sensor platform: unpicked count, auth status, pickup code, code expiry."""

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
from .const import AUTH_STATES, DOMAIN
from .coordinator import CommunityState, GoodLifeCoordinator
from .entity import community_device_info, unique_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[int, GoodLifeCoordinator] = bundle["coordinators"]
    auth: AuthManager = bundle["auth"]

    entities: list[SensorEntity] = []
    for coord in coordinators.values():
        entities.append(UnpickedSensor(coord, entry))
        entities.append(AuthStatusSensor(coord, auth, entry))
        entities.append(PickupCodeSensor(coord, entry))
        entities.append(PickupCodeExpiresSensor(coord, entry))

    async_add_entities(entities)


class _BaseCommunitySensor(CoordinatorEntity[GoodLifeCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoodLifeCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._cu_id = coordinator.community_unit_id
        self._attr_device_info = community_device_info(entry.entry_id, coordinator.community)

    @property
    def _state(self) -> CommunityState:
        return self.coordinator.community


class UnpickedSensor(_BaseCommunitySensor):
    _attr_translation_key = "unpicked"
    _attr_icon = "mdi:package-variant-closed"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = unique_id(entry.entry_id, self._cu_id, "unpicked")

    @property
    def native_value(self) -> int:
        return len(self._state.packages)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
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
    ) -> None:
        super().__init__(coordinator)
        self._auth = auth
        self._entry = entry
        self._cu_id = coordinator.community_unit_id
        self._attr_unique_id = unique_id(entry.entry_id, self._cu_id, "auth_status")
        self._attr_device_info = community_device_info(entry.entry_id, coordinator.community)
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
        state = self.coordinator.community
        return {
            "last_success": state.last_success,
            "access_token_exp": self._auth.access_token_exp,
            "refresh_token_exp": self._auth.refresh_token_exp,
            "last_error": self._auth.last_error,
        }


class PickupCodeSensor(_BaseCommunitySensor):
    _attr_translation_key = "pickup_code"
    _attr_icon = "mdi:numeric-5-box-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = unique_id(entry.entry_id, self._cu_id, "pickup_code")

    @property
    def native_value(self) -> str | None:
        qr = self._state.qr
        return qr.code if qr is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        qr = self._state.qr
        if qr is None:
            return None
        return {
            "expires_at": qr.expires_at,
            "generated_at": qr.generated_at,
            "community_id": qr.community_id,
        }


class PickupCodeExpiresSensor(_BaseCommunitySensor):
    _attr_translation_key = "pickup_code_expires"
    _attr_icon = "mdi:clock-end"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = unique_id(entry.entry_id, self._cu_id, "pickup_code_expires")

    @property
    def native_value(self) -> datetime | None:
        qr = self._state.qr
        if qr is None:
            return None
        try:
            return datetime.fromisoformat(qr.expires_at)
        except ValueError:
            return None
