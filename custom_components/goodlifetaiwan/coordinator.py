"""Per-community coordinator: one instance per community_unit_id.

Each coordinator owns:
- A single ``CommunityState`` with packages, last_success, QR snapshot.
- Its own ``update_interval`` and auto-regenerate preference, read from
  per-community keys in ``entry.options``.
- The QR expiry timer for its community.

This is a v0.3 change from v0.2's "one coordinator, many communities" shape.
Coordinators share the entry's ``AuthManager`` so token refreshes are
serialised across communities.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiResponseError, GoodLifeApi, NetworkError
from .auth import AuthManager, AuthRequired
from .const import (
    AUTH_STATE_OK,
    DEFAULT_SCAN_INTERVAL_SEC,
    DOMAIN,
    EVENT_PACKAGE_ARRIVED,
    EVENT_PACKAGE_PICKED,
    auto_regenerate_key,
    scan_interval_key,
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
    # Cancel handle for the scheduled expiry callback.
    qr_expire_unsub: CALLBACK_TYPE | None = None


class GoodLifeCoordinator(DataUpdateCoordinator[CommunityState]):
    """One coordinator per community. Owns that community's poll cadence, QR state, expiry timer."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: GoodLifeApi,
        auth: AuthManager,
        community: CommunityState,
    ) -> None:
        self.entry = entry
        self.api = api
        self.auth = auth
        self.community: CommunityState = community
        self._first_poll_done = False
        self._last_check: str | None = None

        interval = int(
            entry.options.get(
                scan_interval_key(community.community_unit_id),
                DEFAULT_SCAN_INTERVAL_SEC,
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id[:8]}_c{community.community_unit_id}",
            update_interval=timedelta(seconds=interval),
        )

    # Convenience shortcuts for entity code that used to read `coordinator.communities`.
    @property
    def community_unit_id(self) -> int:
        return self.community.community_unit_id

    @property
    def last_check(self) -> str | None:
        return self._last_check

    @property
    def next_poll(self) -> str | None:
        if self.update_interval is None:
            return None
        target = datetime.now(UTC) + self.update_interval
        return target.astimezone().isoformat(timespec="seconds")

    async def _async_update_data(self) -> CommunityState:
        self._last_check = _now_iso()
        try:
            access = await self.auth.async_ensure_access_token()
        except AuthRequired as err:
            raise UpdateFailed("auth_required") from err
        except NetworkError as err:
            raise UpdateFailed(f"network: {err}") from err

        state = self.community
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
            self._diff_and_fire(new_map)

        state.packages = new_map
        state.last_success = self._last_check
        self._first_poll_done = True
        return state

    def _diff_and_fire(self, new_map: dict[int, PackageSummary]) -> None:
        state = self.community
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

    async def async_generate_pickup_code(self) -> QrSnapshot:
        """Generate a fresh pickup code + QR PNG and publish to entities.

        Shared by three call sites:
        - ``request_pickup_code`` service handler
        - ``button.*_request_pickup_code`` entity press
        - auto-regen timer (when the toggle is on)

        If the server returns the same ``verificationCode`` we already have
        in state, this is a no-op — we skip PNG rendering, don't touch
        ``sensor.*_pickup_code`` / ``sensor.*_pickup_code_expires`` /
        ``image.*_qr``, and don't re-arm the expiry timer. The existing
        snapshot is returned unchanged so the service response shape stays
        stable for callers.

        Raises :class:`AuthRequired`, :class:`ApiResponseError`, or
        :class:`NetworkError` on failure; callers translate to their own
        user-facing error types.
        """
        state = self.community
        async with state.qr_lock:
            access = await self.auth.async_ensure_access_token()
            data = await self.api.create_checkout_verification_code(
                access, state.community_id, state.community_unit_id
            )
            code = str(data.get("verificationCode") or "")
            if not code:
                raise ApiResponseError("missing verificationCode", status=200, body=data)

            # Server returned the same code we already hold — nothing to do.
            if state.qr is not None and state.qr.code == code:
                _LOGGER.debug(
                    "request_pickup_code returned unchanged code for community=%s; "
                    "skipping entity update",
                    state.community_id,
                )
                return state.qr

            png_bytes = await self.hass.async_add_executor_job(_render_qr_png, code)
            snap = QrSnapshot(
                code=code,
                expires_at=str(data.get("expiredTime") or ""),
                generated_at=_now_iso(),
                community_id=state.community_id,
                png_bytes=png_bytes,
            )

        await self.async_set_qr_snapshot(snap)
        return snap

    async def async_set_qr_snapshot(self, snap: QrSnapshot) -> None:
        state = self.community
        self._cancel_expire_timer()
        state.qr = snap
        # Push update to entities immediately.
        self.async_set_updated_data(state)

        expiry = _parse_expires_at(snap.expires_at)
        if expiry is not None:
            state.qr_expire_unsub = async_track_point_in_time(
                self.hass, self._on_qr_expired, expiry
            )

    def _cancel_expire_timer(self) -> None:
        state = self.community
        if state.qr_expire_unsub is not None:
            state.qr_expire_unsub()
            state.qr_expire_unsub = None

    @callback
    def _on_qr_expired(self, _now: datetime) -> None:
        self.hass.async_create_task(self._handle_expiry())

    async def _handle_expiry(self) -> None:
        state = self.community
        state.qr_expire_unsub = None

        auto = self.entry.options.get(auto_regenerate_key(state.community_unit_id), True)
        if auto and self.auth.state == AUTH_STATE_OK:
            try:
                await self.async_generate_pickup_code()
                return
            except (AuthRequired, ApiResponseError, NetworkError) as err:
                _LOGGER.warning(
                    "auto-regenerate pickup code failed for community=%s: %s",
                    state.community_id,
                    err,
                )
                # fall through — clear state below

        # Auto-regen off, auth not OK, or regen failed → clear snapshot.
        state.qr = None
        self.async_set_updated_data(state)

    async def async_shutdown_qr_timer(self) -> None:
        """Cancel the pending expiry timer. Called from async_unload_entry."""
        self._cancel_expire_timer()

    async def async_maybe_generate_initial(self) -> None:
        """Generate a pickup code iff auto-regen is on and none is held yet.

        Safe to call from multiple triggers (entry setup, switch off→on,
        post-reauth). The per-community ``qr_lock`` plus the v0.3.1
        same-code idempotence make accidental double-calls harmless.

        Errors are swallowed with a warning: this is a best-effort "warm
        the QR" helper, not a hard dependency. The real ``request_pickup_code``
        service / button press still surface errors to the user.
        """
        if self.community.qr is not None:
            return
        if self.auth.state != AUTH_STATE_OK:
            return
        auto = self.entry.options.get(auto_regenerate_key(self.community_unit_id), True)
        if not auto:
            return
        try:
            await self.async_generate_pickup_code()
        except (AuthRequired, ApiResponseError, NetworkError) as err:
            _LOGGER.warning(
                "initial pickup code generation for community=%s failed: %s",
                self.community.community_id,
                err,
            )


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
