# Changelog

All notable changes to this integration are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows [Semantic Versioning](https://semver.org/).

While the integration is pre-1.0, minor versions may include breaking changes to the public event / service / entity surface. Breaking changes are called out under their own heading in the release notes.

## [Unreleased]

## [0.3.2] - 2026-04-19

### Added

- **Auto-populate pickup code when auto-regenerate is on.** Previously, even with `switch.*_auto_regenerate_pickup_code` enabled, dashboards showed `unknown` for `sensor.*_pickup_code` until the user manually pressed the button or called the service at least once — the regen timer only fired on _expiry_ of an existing code, so a cold start with no code was a no-op. v0.3.2 warms a fresh code at three trigger points when auto-regen is on **and** no current code is held:
  1. On entry setup (e.g. HA restart).
  2. When the switch transitions `off → on`.
  3. After a successful re-auth via `goodlifetaiwan.submit_code` (useful when a long offline window expired both tokens and the prior code).
     Idempotent: already-held codes are left untouched. No-op when auth is not `ok` (re-auth flow will trigger it once tokens return).

## [0.3.1] - 2026-04-19

### Fixed

- `request_pickup_code` (service, button press, and auto-regen timer) is now idempotent when the server returns the same `verificationCode` we already hold. Previously every call unconditionally replaced the snapshot, fired three state_changed events (`sensor.*_pickup_code`, `sensor.*_pickup_code_expires`, `image.*_qr`), bumped `image_last_updated`, and re-armed the expiry timer — even when nothing actually changed. v0.3.1 short-circuits: if the code matches, entities and timer are left alone and the existing snapshot is returned. The service response shape is unchanged.

## [0.3.0] - 2026-04-19

### Breaking

- **Per-community coordinators.** v0.2 ran one `GoodLifeCoordinator` per entry, holding every community's state. v0.3 runs one coordinator per community. Each owns its own poll cadence, QR snapshot, expiry timer, and auto-regenerate policy. Practical consequences:
  - One API error on community A no longer fails the whole cycle for community B.
  - Per-community polling cadences are possible (fast on a busy community, slow on a quiet one).
  - Event order is unchanged (each community fires its own `package_arrived` / `_picked` events).
- **Account device removed (final).** v0.1 added it, early-v0.2 removed it, mid-v0.2 re-added it to host per-entry CONFIG entities. v0.3 moves those CONFIG entities to the community device, so the account device has nothing to host and is gone. Each entry now shows exactly one device per community.
- **Entity ID renames.** `number.*_poll_interval` and `switch.*_auto_regenerate_pickup_code` moved from the account device to the community device, so their auto-generated entity IDs change. Old ones become orphans on upgrade — delete them manually via **Settings → Devices & Services → goodlifetaiwan → Entities**.
- **Option schema migration.** v0.2 stored `scan_interval_seconds` and `auto_regenerate_pickup_code` as flat entry-level keys. v0.3 stores them per `community_unit_id` with suffixed keys (`scan_interval_seconds__{cu_id}`, `auto_regenerate_pickup_code__{cu_id}`). On first v0.3 setup the integration copies the flat values into each community's suffixed key, then removes the flat keys — so any custom value a v0.2 user set carries forward.

### Added

- `number.*_poll_interval` per community. Setting it mutates only that community's coordinator's `update_interval` and triggers an immediate poll; the other communities keep their cadence.
- `switch.*_auto_regenerate_pickup_code` per community. Toggling it flips the option under that community's key only.
- **`switch.*_auto_regenerate_pickup_code` default changed from `off` to `on`.** Earlier versions were conservative because the RE contract speculated that issuing a new pickup code might invalidate prior ones. Live-testing proved that assumption wrong (see the v0.1 known-limitations note that was corrected) — old codes remain valid, so always-fresh is now the default.

### Changed

- `coordinator.async_generate_pickup_code()` takes no arguments (the coordinator already knows its one community). The service handler resolves `community_id` → target coordinator, then calls the no-arg method.
- `coordinator.async_shutdown_qr_timers()` → `async_shutdown_qr_timer()` (singular). `__init__.async_unload_entry` iterates all coordinators.
- Coordinator name in HA logs: `goodlifetaiwan_<entry_id_prefix>_c<community_unit_id>` so multi-community setups can be distinguished in debug output.

### Removed

- `community_by_id` and `communities` attributes on `GoodLifeCoordinator` (coordinator now has a single `community` attribute).
- `account_device_info` helper (no more account device).

## [0.2.0] - 2026-04-19

### Breaking

- Removed `sensor.*_service` (service_health aggregate). Per-community `sensor.*_auth_status` already mirrors the auth state, and the aggregate's unique attribute (`communities[]`) has no automation use-case the per-community sensors don't already cover. Users with automations targeting `sensor.*_service` need to retarget to `sensor.*_auth_status` for a specific community or listen for the `goodlifetaiwan_auth_required` event.
- Renamed entities and the service for accuracy — the 5-digit string was never a QR; it's the pickup code, and the QR image just encodes it. The old names stay in the entity registry as orphans after upgrading; delete them manually from **Settings → Devices & Services → goodlifetaiwan → entities** or the entity registry panel.
  - `sensor.*_qr_code` → `sensor.*_pickup_code`
  - `sensor.*_qr_expires` → `sensor.*_pickup_code_expires`
  - `button.*_request_qr` → `button.*_request_pickup_code`
  - `goodlifetaiwan.request_qr` service → `goodlifetaiwan.request_pickup_code`
  - Vocabulary note: "verification code" / 驗證碼 is reserved throughout the integration for the SMS code used at login (config flow, `submit_code` service). The 5-digit code used at the pickup counter is "pickup code" / 取件碼 everywhere it's user-facing. The upstream API calls both `verificationCode` on the wire; `api.py` preserves the server vocabulary because it's a thin binding, but user-facing strings/entities/services do not.
- **Removed the Options flow.** Settings previously exposed via the **Configure** button on the integration card now live as CONFIG-category entities on the account device:
  - `number.*_poll_interval` (replaces `scan_interval_seconds` option)
  - `switch.*_auto_regenerate_pickup_code` (replaces the toggle under the same name in Options)
  - These entities are automatable and discoverable on the device page. They're tagged `EntityCategory.CONFIG` so HA hides them from the default dashboard; they show up under the account device's Configuration section.
  - **Account device is back.** An earlier draft of v0.2 removed it along with `sensor.*_service`; having `number` + `switch` entities at the per-entry level without a home was messier than bringing it back. It now hosts only CONFIG-category entities, not user-visible state.

### Added

- `button.*_request_pickup_code` per community — a UI-native way to trigger pickup-code generation without going through Developer Tools. Shares the same code path as the `request_pickup_code` service and the auto-regenerate timer.
- Pickup-code expiry handling. When the 10-minute code expires, the integration clears the snapshot by default (sensors go to `unknown`, dashboard shows "no active code"). Users who prefer an always-fresh code can flip `switch.*_auto_regenerate_pickup_code` on.
- `number.*_poll_interval` (60–3600 s, default 300) — tune polling cadence live without rebuilding the coordinator. Default lowered from 600 s (v0.1) to 300 s: 5-minute latency on `package_arrived` events matches typical "new package" notification expectations better than 10. Doubles API call volume (from ~144 to ~288 per community per day), still well under any reasonable rate limit.
- `switch.*_auto_regenerate_pickup_code` (default off) — toggle silent regeneration at expiry. Caveat noted in strings.json: the server may invalidate prior codes when new ones are issued, which can disrupt an in-progress pickup.

### Fixed

- `image.*_qr` entity state is published immediately when a snapshot arrives. Previously it stayed `unknown` until an unrelated coordinator event triggered a state write — the PNG bytes were served correctly, but the state text lagged. The timestamp update is now inside `_handle_coordinator_update` so `async_write_ha_state` picks it up.

### Changed

- Pickup-code generation logic consolidated into `coordinator.async_generate_pickup_code`. The `request_pickup_code` service, the button entity, and the auto-regen timer all share this one method so behaviour (locking, error mapping, snapshot publishing, expiry scheduling) is identical across entry points.

### Removed

- Dead `community_slug` helper (never referenced in entity IDs — HA derives those from the device name). Three `test_community_slug_*` tests removed accordingly.
- `SERVICE_HEALTH_STATES` const and `service_health` translation keys.

## [0.1.0] - 2026-04-19

Initial release.

### Added

- Config flow: phone number → SMS verification → optional community selection. Reauth pre-fills the phone number and skips community selection.
- Options flow: `scan_interval_seconds` (60–3600, default 600).
- HTTP client for `life-spi.glf.tw` and `auth.epictech.com.tw` with app-fingerprint headers (`app-info`, `User-Agent`, `timestamp`, `traceparent`, `communityid`, `communityunitid`).
- Token lifecycle: Store-backed persistence, auto-refresh with 30s margin, per-entry `asyncio.Lock` to serialise concurrent refreshes.
- Coordinator polling per community with package-set diffing.
- Entities per community: `sensor.*_unpicked`, `sensor.*_auth_status`, `sensor.*_qr_code`, `sensor.*_qr_expires`, `image.*_qr`. Per entry: `sensor.*_service` (aggregate health).
- Services (all response-capable): `request_qr`, `send_sms`, `submit_code`. Per-entry `sms_lock` serialises `send_sms` / `submit_code`. `request_qr` has a per-community QR lock.
- Events (public API): `goodlifetaiwan_package_arrived`, `_package_picked`, `_auth_required`, `_auth_sms_sent`, `_auth_success`, `_auth_failed`.
- i18n: English and zh-TW.

### Known limitations

- Only the `UnpickedPackages` surface is polled. Picked / deposited / returned packages are out of scope for v0.1.
- `auth_required` event debounces across the `auth_needed → refreshing → auth_needed` cycle; one notification per auth failure cycle, not per retry.
- The module-level rate-limit for `send_sms` resets on HA restart. Acceptable — real-world call frequency is near zero.
- Refresh tokens' lifecycle was initially documented (reverse-engineered) as "single-use rolling". Live-tested against the server in April 2026: that's not accurate. Calling `RefreshMemberToken` issues a new refresh token but does **not** invalidate the caller's original. Verified empirically — three back-to-back calls (reusing the same R0, then a mix of R0 and R1) all returned `COM00001`. So multiple concurrent consumers of the same account (mobile app, HA, dev HA) all coexist safely, each keeping its tokens alive independently up to their 90-day expiry. Tokens are still _rotated_ in the sense that a fresh one comes back on every refresh — the integration writes the latest one to storage — but old ones remain valid for their lifetime.

[Unreleased]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/releases/tag/v0.3.1
[0.3.0]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/releases/tag/v0.3.0
[0.2.0]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/releases/tag/v0.2.0
[0.1.0]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/releases/tag/v0.1.0
