"""Number platform: scan_interval as a user-adjustable per-entry setting.

Replaces the pre-v0.2 Options flow for polling cadence. Lives on the
account device as a CONFIG-category entity, so it's discoverable on the
device page and automatable (e.g., raise cadence while out, drop at
night) without being visible on the default dashboard.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PHONE_NUMBER,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_SEC,
    DOMAIN,
    MAX_SCAN_INTERVAL_SEC,
    MIN_SCAN_INTERVAL_SEC,
)
from .coordinator import GoodLifeCoordinator
from .entity import account_device_info, unique_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinator: GoodLifeCoordinator = bundle["coordinator"]
    async_add_entities([ScanIntervalNumber(coordinator, entry)])


class ScanIntervalNumber(CoordinatorEntity[GoodLifeCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "scan_interval"
    _attr_icon = "mdi:timer-sync-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = float(MIN_SCAN_INTERVAL_SEC)
    _attr_native_max_value = float(MAX_SCAN_INTERVAL_SEC)
    _attr_native_step = 10.0
    _attr_native_unit_of_measurement = "s"

    def __init__(self, coordinator: GoodLifeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = unique_id(entry.entry_id, None, "scan_interval")
        self._attr_device_info = account_device_info(
            entry.entry_id, entry.data.get(CONF_PHONE_NUMBER, "")
        )

    @property
    def native_value(self) -> float:
        return float(self._entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC))

    async def async_set_native_value(self, value: float) -> None:
        new_val = int(value)
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_SCAN_INTERVAL: new_val},
        )
        # Live-mutate the coordinator's interval and force an immediate poll
        # so the new cadence is picked up without rebuilding the coordinator
        # (which would lose in-memory package cache + QR snapshot).
        self.coordinator.update_interval = timedelta(seconds=new_val)
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
