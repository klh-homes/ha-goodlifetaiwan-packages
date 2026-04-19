"""Switch platform: auto-regenerate pickup code on expiry.

Off (default): when the 10-minute pickup code expires the sensor clears;
the user presses the button or calls ``request_pickup_code`` for a fresh
one. On: the integration silently requests a new code at each expiry.

Caveat documented in strings.json's option description: the server may
invalidate prior codes when new ones are issued, which could disrupt a
pickup already in progress.
"""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_AUTO_REGENERATE_PICKUP_CODE, CONF_PHONE_NUMBER, DOMAIN
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
    async_add_entities([AutoRegeneratePickupCodeSwitch(coordinator, entry)])


class AutoRegeneratePickupCodeSwitch(CoordinatorEntity[GoodLifeCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "auto_regenerate_pickup_code"
    _attr_icon = "mdi:refresh-auto"

    def __init__(self, coordinator: GoodLifeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = unique_id(entry.entry_id, None, "auto_regenerate_pickup_code")
        self._attr_device_info = account_device_info(
            entry.entry_id, entry.data.get(CONF_PHONE_NUMBER, "")
        )

    @property
    def is_on(self) -> bool:
        return bool(self._entry.options.get(CONF_AUTO_REGENERATE_PICKUP_CODE, False))

    async def async_turn_on(self, **_: object) -> None:
        await self._set(True)

    async def async_turn_off(self, **_: object) -> None:
        await self._set(False)

    async def _set(self, value: bool) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={
                **self._entry.options,
                CONF_AUTO_REGENERATE_PICKUP_CODE: value,
            },
        )
        # No coordinator reload needed — _handle_expiry reads the option live
        # at each expiry tick, so the next expiry picks up the new value.
        self.async_write_ha_state()
