"""Button platform: UI-native way to request a fresh pickup code per community."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ApiResponseError, NetworkError
from .auth import AuthRequired
from .const import AUTH_STATE_AUTH_NEEDED, DOMAIN
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
    async_add_entities([RequestPickupCodeButton(coord, entry) for coord in coordinators.values()])


class RequestPickupCodeButton(CoordinatorEntity[GoodLifeCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "request_pickup_code"
    _attr_icon = "mdi:qrcode-scan"

    def __init__(self, coordinator: GoodLifeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._cu_id = coordinator.community_unit_id
        self._attr_unique_id = unique_id(entry.entry_id, self._cu_id, "request_pickup_code_button")
        self._attr_device_info = community_device_info(entry.entry_id, coordinator.community)

    async def async_press(self) -> None:
        auth = self.hass.data[DOMAIN][self._entry.entry_id]["auth"]
        if auth.state == AUTH_STATE_AUTH_NEEDED:
            raise ServiceValidationError(
                "Re-login via goodlifetaiwan.send_sms",
                translation_domain=DOMAIN,
                translation_key="auth_required",
            )

        try:
            await self.coordinator.async_generate_pickup_code()
        except AuthRequired as err:
            raise ServiceValidationError(
                "auth_required",
                translation_domain=DOMAIN,
                translation_key="auth_required",
            ) from err
        except ApiResponseError as err:
            raise HomeAssistantError(f"api_error: {err}") from err
        except NetworkError as err:
            raise HomeAssistantError(f"network_error: {err}") from err
