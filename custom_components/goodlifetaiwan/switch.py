"""Switch platform: per-community auto-regenerate-on-expiry toggle."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, auto_regenerate_key
from .coordinator import GoodLifeCoordinator
from .entity import community_device_info, unique_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[int, GoodLifeCoordinator] = bundle["coordinators"]
    async_add_entities(
        [AutoRegeneratePickupCodeSwitch(coord, entry) for coord in coordinators.values()]
    )


class AutoRegeneratePickupCodeSwitch(CoordinatorEntity[GoodLifeCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "auto_regenerate_pickup_code"
    _attr_icon = "mdi:refresh-auto"

    def __init__(self, coordinator: GoodLifeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._cu_id = coordinator.community_unit_id
        self._attr_unique_id = unique_id(entry.entry_id, self._cu_id, "auto_regenerate_pickup_code")
        self._attr_device_info = community_device_info(entry.entry_id, coordinator.community)

    @property
    def is_on(self) -> bool:
        # Default on — most users want a fresh QR always ready; we tested and
        # confirmed the server does NOT invalidate prior codes when a new one
        # is issued (see CHANGELOG v0.1 known-limitations note for the live
        # probe), so the former caveat is moot.
        return bool(self._entry.options.get(auto_regenerate_key(self._cu_id), True))

    async def async_turn_on(self, **_: object) -> None:
        await self._set(True)

    async def async_turn_off(self, **_: object) -> None:
        await self._set(False)

    async def _set(self, value: bool) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={
                **self._entry.options,
                auto_regenerate_key(self._cu_id): value,
            },
        )
        # No coordinator reload needed — _handle_expiry reads the option live
        # at each expiry tick, so the next expiry picks up the new value.
        self.async_write_ha_state()
