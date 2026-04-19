"""Image platform: the pickup-QR PNG, updated on-demand by the request_pickup_code service."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
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
    entities = [QrImage(hass, coord, entry) for coord in coordinators.values()]
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
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._entry = entry
        self._cu_id = coordinator.community_unit_id
        self._attr_unique_id = unique_id(entry.entry_id, self._cu_id, "qr")
        self._attr_device_info = community_device_info(entry.entry_id, coordinator.community)
        self._last_seen_generated_at: str | None = None

    @property
    def _state(self) -> CommunityState:
        return self.coordinator.community

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # If the coordinator already has a QR snapshot the moment we're
        # added (v0.3.2's initial-regen hook fires during async_setup_entry
        # BEFORE platforms are forwarded, so the snapshot lands before the
        # image entity exists as a listener), pick it up now.
        # CoordinatorEntity.async_added_to_hass only installs the listener;
        # it doesn't replay pre-existing state in coordinator.data — without
        # this sync, _attr_image_last_updated stays None and image.state
        # reads back as "unknown" until the next coordinator push.
        state = self._state
        if state.qr is not None:
            self._last_seen_generated_at = state.qr.generated_at
            self._attr_image_last_updated = datetime.now(UTC)

    @callback
    def _handle_coordinator_update(self) -> None:
        # Update image_last_updated BEFORE async_write_ha_state so the state
        # (which ImageEntity derives from image_last_updated) publishes the
        # fresh ISO timestamp instead of None.
        state = self._state
        if state.qr is not None and state.qr.generated_at != self._last_seen_generated_at:
            self._last_seen_generated_at = state.qr.generated_at
            self._attr_image_last_updated = datetime.now(UTC)
        elif state.qr is None:
            # Snapshot cleared (expiry timer) — reset so the next snapshot
            # is treated as fresh.
            self._last_seen_generated_at = None
            self._attr_image_last_updated = None
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        qr = self._state.qr
        return qr.png_bytes if qr is not None else None
