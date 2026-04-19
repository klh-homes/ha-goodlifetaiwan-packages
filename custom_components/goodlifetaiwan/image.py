"""Image platform: the pickup-QR PNG, updated on-demand by the request_qr service."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CommunityState, GoodLifeCoordinator
from .entity import community_device_info, community_slug, unique_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinator: GoodLifeCoordinator = bundle["coordinator"]
    entities = [
        QrImage(hass, coordinator, entry, state) for state in coordinator.communities.values()
    ]
    async_add_entities(entities)


class QrImage(CoordinatorEntity[GoodLifeCoordinator], ImageEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "qr"
    _attr_icon = "mdi:qrcode"
    _attr_content_type = "image/png"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: GoodLifeCoordinator,
        entry: ConfigEntry,
        state: CommunityState,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._entry = entry
        self._cu_id = state.community_unit_id
        self._slug = state.slug or community_slug(
            state.community_name, state.community_unit_id, state.community_id
        )
        self._attr_unique_id = unique_id(entry.entry_id, state.community_unit_id, "qr")
        self._attr_device_info = community_device_info(entry.entry_id, state)
        self._last_seen_generated_at: str | None = None

    @property
    def _state(self) -> CommunityState | None:
        return self.coordinator.communities.get(self._cu_id)

    async def async_image(self) -> bytes | None:
        state = self._state
        if state is None or state.qr is None:
            return None
        # Bump image_last_updated when we see a fresh snapshot so HA invalidates caches.
        if state.qr.generated_at != self._last_seen_generated_at:
            self._last_seen_generated_at = state.qr.generated_at
            self._attr_image_last_updated = datetime.now(UTC)
        return state.qr.png_bytes
