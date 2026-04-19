"""Number platform: per-community poll interval.

v0.3 change: one entity per community, on that community's device. The
previous v0.2 design had a single account-level entity driving a shared
coordinator; v0.3 splits coordinators per community so each has its own
cadence.
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
    DEFAULT_SCAN_INTERVAL_SEC,
    DOMAIN,
    MAX_SCAN_INTERVAL_SEC,
    MIN_SCAN_INTERVAL_SEC,
    scan_interval_key,
)
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
    async_add_entities([PollIntervalNumber(coord, entry) for coord in coordinators.values()])


class PollIntervalNumber(CoordinatorEntity[GoodLifeCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "poll_interval"
    _attr_icon = "mdi:timer-sync-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = float(MIN_SCAN_INTERVAL_SEC)
    _attr_native_max_value = float(MAX_SCAN_INTERVAL_SEC)
    _attr_native_step = 10.0
    _attr_native_unit_of_measurement = "s"

    def __init__(self, coordinator: GoodLifeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._cu_id = coordinator.community_unit_id
        self._attr_unique_id = unique_id(entry.entry_id, self._cu_id, "poll_interval")
        self._attr_device_info = community_device_info(entry.entry_id, coordinator.community)

    @property
    def native_value(self) -> float:
        return float(
            self._entry.options.get(scan_interval_key(self._cu_id), DEFAULT_SCAN_INTERVAL_SEC)
        )

    async def async_set_native_value(self, value: float) -> None:
        new_val = int(value)
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={
                **self._entry.options,
                scan_interval_key(self._cu_id): new_val,
            },
        )
        # Live-mutate THIS community's coordinator and trigger an immediate
        # poll so the new cadence takes effect without rebuilding anything.
        self.coordinator.update_interval = timedelta(seconds=new_val)
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
