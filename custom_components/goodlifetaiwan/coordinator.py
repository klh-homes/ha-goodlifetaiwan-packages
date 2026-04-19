"""Polling coordinator: fetch unpicked packages per community, diff, fire events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiResponseError, GoodLifeApi, NetworkError
from .auth import AuthManager, AuthRequired
from .const import (
    DEFAULT_SCAN_INTERVAL_SEC,
    DOMAIN,
    EVENT_PACKAGE_ARRIVED,
    EVENT_PACKAGE_PICKED,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PackageSummary:
    """Subset of /UnpickedPackages item surfaced to HA users + events."""

    package_id: int
    package_no: str
    recipient_name: str
    recipient_phone: str
    placement: str
    checked_in_date: str
    is_owner: bool
    has_photo: bool
    community_id: int
    community_unit_id: int
    community_name: str

    def as_attr(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "package_no": self.package_no,
            "recipient_name": self.recipient_name,
            "recipient_phone": self.recipient_phone,
            "placement": self.placement,
            "checked_in_date": self.checked_in_date,
            "is_owner": self.is_owner,
            "has_photo": self.has_photo,
        }


@dataclass(slots=True)
class QrSnapshot:
    code: str
    expires_at: str
    generated_at: str
    community_id: int
    png_bytes: bytes


@dataclass(slots=True)
class CommunityState:
    community_id: int
    community_unit_id: int
    community_name: str
    slug: str
    packages: dict[int, PackageSummary] = field(default_factory=dict)
    last_success: str | None = None
    qr: QrSnapshot | None = None
    qr_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class GoodLifeCoordinator(DataUpdateCoordinator[dict[int, CommunityState]]):
    """One coordinator per config entry; fans out to every selected community."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: GoodLifeApi,
        auth: AuthManager,
        communities: list[CommunityState],
        scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL_SEC,
    ) -> None:
        self.entry = entry
        self.api = api
        self.auth = auth
        self.communities: dict[int, CommunityState] = {c.community_unit_id: c for c in communities}
        self._first_poll_done = False
        self._last_check: str | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id[:8]}",
            update_interval=timedelta(seconds=scan_interval_seconds),
        )

    @property
    def last_check(self) -> str | None:
        return self._last_check

    @property
    def community_ids(self) -> list[int]:
        return [c.community_id for c in self.communities.values()]

    @property
    def next_poll(self) -> str | None:
        if self.update_interval is None:
            return None
        target = datetime.now(UTC) + self.update_interval
        return target.astimezone().isoformat(timespec="seconds")

    def community_by_id(self, community_id: int) -> CommunityState | None:
        for state in self.communities.values():
            if state.community_id == community_id:
                return state
        return None

    async def _async_update_data(self) -> dict[int, CommunityState]:
        self._last_check = _now_iso()
        try:
            access = await self.auth.async_ensure_access_token()
        except AuthRequired as err:
            raise UpdateFailed("auth_required") from err
        except NetworkError as err:
            raise UpdateFailed(f"network: {err}") from err

        results: dict[int, CommunityState] = {}
        for cu_id, state in self.communities.items():
            try:
                items = await self.api.unpicked_packages(
                    access, state.community_id, state.community_unit_id
                )
            except ApiResponseError as err:
                raise UpdateFailed(f"api error: {err}") from err
            except NetworkError as err:
                raise UpdateFailed(f"network: {err}") from err

            new_map = {
                item["packageId"]: _summarize(item, state) for item in items if "packageId" in item
            }

            if self._first_poll_done:
                self._diff_and_fire(state, new_map)

            state.packages = new_map
            state.last_success = self._last_check
            results[cu_id] = state

        self._first_poll_done = True
        return results

    def _diff_and_fire(self, state: CommunityState, new_map: dict[int, PackageSummary]) -> None:
        old_ids = set(state.packages)
        new_ids = set(new_map)

        for pkg_id in new_ids - old_ids:
            pkg = new_map[pkg_id]
            self.hass.bus.async_fire(
                EVENT_PACKAGE_ARRIVED,
                {
                    "entry_id": self.entry.entry_id,
                    "community_id": pkg.community_id,
                    "community_name": pkg.community_name,
                    "package_id": pkg.package_id,
                    "package_no": pkg.package_no,
                    "recipient_name": pkg.recipient_name,
                    "recipient_phone": pkg.recipient_phone,
                    "placement": pkg.placement,
                    "checked_in_date": pkg.checked_in_date,
                    "is_owner": pkg.is_owner,
                    "has_photo": pkg.has_photo,
                },
            )

        for pkg_id in old_ids - new_ids:
            # sourced from last-known snapshot since the API no longer returns it
            pkg = state.packages[pkg_id]
            self.hass.bus.async_fire(
                EVENT_PACKAGE_PICKED,
                {
                    "entry_id": self.entry.entry_id,
                    "community_id": pkg.community_id,
                    "community_name": pkg.community_name,
                    "package_id": pkg.package_id,
                    "package_no": pkg.package_no,
                    "recipient_name": pkg.recipient_name,
                    "placement": pkg.placement,
                    "checked_in_date": pkg.checked_in_date,
                },
            )

    async def async_set_qr_snapshot(self, community_unit_id: int, snap: QrSnapshot) -> None:
        state = self.communities.get(community_unit_id)
        if state is None:
            return
        state.qr = snap
        # Make the new QR visible without waiting for the next poll.
        self.async_set_updated_data(self.data or self.communities)


def _summarize(item: dict[str, Any], state: CommunityState) -> PackageSummary:
    contact = item.get("toContactInfo") or {}
    placement = (item.get("packagePlacement") or {}).get("packagePlacementName") or ""
    files = item.get("fileInfos") or []
    return PackageSummary(
        package_id=int(item["packageId"]),
        package_no=str(item.get("packageNo", "")),
        recipient_name=str(contact.get("name") or ""),
        recipient_phone=str(contact.get("phone") or ""),
        placement=placement,
        checked_in_date=str(item.get("checkedInDate") or ""),
        is_owner=bool(item.get("isPackageOwner")),
        has_photo=len(files) > 0,
        community_id=state.community_id,
        community_unit_id=state.community_unit_id,
        community_name=state.community_name,
    )


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")
