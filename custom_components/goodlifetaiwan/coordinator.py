"""Polling coordinator: fetch unpicked packages per community, diff, fire events.

Also owns the QR lifecycle: generation, expiry-time cache, optional auto-regen.
The single ``async_generate_pickup_code`` method is shared by the service handler, the
button entity, and the expiry timer so all three paths behave identically.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiResponseError, GoodLifeApi, NetworkError
from .auth import AuthManager, AuthRequired
from .const import (
    AUTH_STATE_OK,
    CONF_AUTO_REGENERATE_PICKUP_CODE,
    DEFAULT_SCAN_INTERVAL_SEC,
    DOMAIN,
    EVENT_PACKAGE_ARRIVED,
    EVENT_PACKAGE_PICKED,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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
    # Cancel handle for the scheduled expiry callback.
    qr_expire_unsub: CALLBACK_TYPE | None = None


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

    # --- QR generation & lifecycle ---------------------------------------

    async def async_generate_pickup_code(self, community_unit_id: int) -> QrSnapshot:
        """Generate a fresh QR for the community and publish it to entities.

        Shared by three call sites:
        - ``request_pickup_code`` service handler
        - ``button.*_request_pickup_code`` entity press
        - auto-regen timer (when the toggle is on)

        Raises :class:`AuthRequired`, :class:`ApiResponseError`, or
        :class:`NetworkError` on failure; callers are expected to translate
        these to their appropriate user-facing error types.
        """
        state = self.communities.get(community_unit_id)
        if state is None:
            raise ValueError(f"unknown community_unit_id={community_unit_id}")

        async with state.qr_lock:
            access = await self.auth.async_ensure_access_token()
            data = await self.api.create_checkout_verification_code(
                access, state.community_id, state.community_unit_id
            )
            code = str(data.get("verificationCode") or "")
            if not code:
                raise ApiResponseError("missing verificationCode", status=200, body=data)
            png_bytes = await self.hass.async_add_executor_job(_render_qr_png, code)
            snap = QrSnapshot(
                code=code,
                expires_at=str(data.get("expiredTime") or ""),
                generated_at=_now_iso(),
                community_id=state.community_id,
                png_bytes=png_bytes,
            )

        await self.async_set_qr_snapshot(community_unit_id, snap)
        return snap

    async def async_set_qr_snapshot(self, community_unit_id: int, snap: QrSnapshot) -> None:
        state = self.communities.get(community_unit_id)
        if state is None:
            return

        # Replace any pending expiry timer with one pointing at the new expires_at.
        self._cancel_expire_timer(state)

        state.qr = snap
        # Push update to entities immediately — CoordinatorEntity listeners will
        # call async_write_ha_state, which (for image.qr) now updates the
        # image_last_updated attribute and exposes a fresh ISO-timestamp state.
        self.async_set_updated_data(self.data or self.communities)

        expiry = _parse_expires_at(snap.expires_at)
        if expiry is not None:
            state.qr_expire_unsub = async_track_point_in_time(
                self.hass, self._on_qr_expired_factory(community_unit_id), expiry
            )

    def _cancel_expire_timer(self, state: CommunityState) -> None:
        if state.qr_expire_unsub is not None:
            state.qr_expire_unsub()
            state.qr_expire_unsub = None

    def _on_qr_expired_factory(self, community_unit_id: int) -> Callable[[datetime], None]:
        """Return a HA-callable suited for async_track_point_in_time."""

        @callback
        def _fire(_now: datetime) -> None:
            self.hass.async_create_task(self._handle_expiry(community_unit_id))

        return _fire

    async def _handle_expiry(self, community_unit_id: int) -> None:
        state = self.communities.get(community_unit_id)
        if state is None:
            return
        state.qr_expire_unsub = None

        auto = self.entry.options.get(CONF_AUTO_REGENERATE_PICKUP_CODE, False)
        if auto and self.auth.state == AUTH_STATE_OK:
            try:
                await self.async_generate_pickup_code(community_unit_id)
                return
            except (AuthRequired, ApiResponseError, NetworkError) as err:
                _LOGGER.warning(
                    "auto-regenerate QR failed for community=%s: %s",
                    state.community_id,
                    err,
                )
                # fall through and clear state below

        # Either auto-regen is off, auth is not OK, or regen failed — clear
        # the stale snapshot so dashboards show "no active code" rather than
        # an expired one.
        state.qr = None
        self.async_set_updated_data(self.data or self.communities)

    async def async_shutdown_qr_timers(self) -> None:
        """Cancel every pending expiry timer. Called from async_unload_entry."""
        for state in self.communities.values():
            self._cancel_expire_timer(state)


def _render_qr_png(payload: str) -> bytes:
    """Render a PNG for the given payload. Runs in an executor (PIL is blocking)."""
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _parse_expires_at(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


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
