# Changelog

All notable changes to this integration are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows [Semantic Versioning](https://semver.org/).

While the integration is pre-1.0, minor versions may include breaking changes to the public event / service / entity surface. Breaking changes are called out under their own heading in the release notes.

## [Unreleased]

### Internal

- Added brand icon at `assets/icon.png` and embedded it as a header in `README.md` and `info.md` (HACS preview). Size variants (256×256 + 512×512) staged under `assets/brands/` as a regeneration source; no upstream `home-assistant/brands` PR is planned, so HA UI integration cards stay without a brand icon (status-quo).

## [0.3.4] - 2026-04-20

### Fixed

- `image.*_qr_image` stayed `unknown` on a fresh install with auto-regenerate on. The v0.3.2 initial-regen hook fires during `async_setup_entry` _before_ `async_forward_entry_setups`, so the QR snapshot lands in `coordinator.data` before the image entity exists as a listener — `_handle_coordinator_update` never runs for that first snapshot and `_attr_image_last_updated` stays `None`, which `ImageEntity` exposes as state `unknown`. Sensors worked because they read `coordinator.data` live on every state query; the image entity doesn't. Fix: `QrImage.async_added_to_hass` now syncs `_attr_image_last_updated` from the current `coordinator.community.qr` at join time. `sensor.*_pickup_code` and `sensor.*_pickup_code_expires` were unaffected.
- Pickup-code entities went to `unknown` whenever the 10-minute pickup-code expiry happened to tick in the same second as the proactive access-token refresh (both are ~10-minute cycles and naturally align). `_handle_expiry` gated auto-regen on `auth.state == ok`, so when the expiry fired during the ~1s window where `auth.state == refreshing`, the gate failed and the fallback cleared the snapshot. The fix loosens the gate to `auth.state != auth_needed` — `auth_needed` is the only state where we know the call will fail (no valid refresh token; user must re-auth). In `refreshing` the regen call blocks on `AuthManager._refresh_lock` until the in-flight refresh completes, then uses the fresh token. Same gate in `async_maybe_generate_initial` loosened similarly.

### Internal

- Test fixtures and docs examples: replaced stale identifiers lifted from the original reverse-engineering captures (real community IDs, real phone numbers, real community names) with clearly-synthetic placeholders. No runtime or public-API impact; same change could land in any patch release.

## [0.3.3] - 2026-04-19

First public release. The integration had gone through five pre-releases (v0.1 → v0.3.2) during private iteration; the working tree that landed in v0.3.3 is the consolidation of those, re-released as a clean single commit.

### Features

- **Per community.** One Home Assistant device per 社區 you import. Each device owns its own polling cadence, auth state, QR snapshot, expiry timer, and auto-regenerate policy. One API error on community A never fails community B.
- **Pickup code + QR.** `sensor.*_pickup_code` (5-digit), `sensor.*_pickup_code_expires` (timestamp), `image.*_qr_image` (PNG). `button.*_request_pickup_code` generates a fresh one without leaving the UI. The code is the same one the 中保好生活 mobile app shows at the pickup counter.
- **Package list.** `sensor.*_unpicked` = count of unpicked packages; full list on `extra_state_attributes.items`. Events `goodlifetaiwan_package_arrived` / `_picked` fire per package as the polled list changes.
- **Auth.** SMS-based first-run login. Access token auto-refreshes before the 10-minute expiry. Refresh token valid 90 days. `goodlifetaiwan_auth_required` fires once per expiry cycle; re-auth via `goodlifetaiwan.send_sms` + `goodlifetaiwan.submit_code`.
- **CONFIG entities instead of an Options flow.** `number.*_poll_interval` (60–3600s, default 300) and `switch.*_auto_regenerate_pickup_code` (default on) live on each community's device. Tagged `EntityCategory.CONFIG` so they don't clutter dashboards but are fully automatable.
- **Auto-populate pickup code when auto-regenerate is on.** On entry setup, switch-on transition, or after re-auth — if auto-regen is on and no code is cached, a fresh code is fetched immediately. No more `unknown` on dashboards waiting for the user to press the button.
- **Idempotent code generation.** If the server returns a code matching the one already cached, entities, expiry timer, and image are left untouched — no spurious state_changed events.
- **Services (all response-capable).** `goodlifetaiwan.request_pickup_code`, `goodlifetaiwan.send_sms`, `goodlifetaiwan.submit_code`.
- **Events.** `goodlifetaiwan_package_arrived`, `_package_picked`, `_auth_required`, `_auth_sms_sent`, `_auth_success`, `_auth_failed`.
- **i18n.** English and zh-TW.

### Known details worth flagging

- Refresh tokens are re-issued on every `RefreshMemberToken` call, but the _prior_ refresh token stays valid until its 90-day `exp` — live-verified against the server. Running the mobile app and HA in parallel on the same account is safe; two HA instances holding the same token file will also both succeed, though they'll each double the polling load (not recommended but not broken).
- Only `UnpickedPackages` is polled. Picked / deposited / returned package history is out of scope.
- The module-level `send_sms` rate limit resets on HA restart. Acceptable — real-world call frequency is once every few months at most.

[Unreleased]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/compare/v0.3.4...HEAD
[0.3.4]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/releases/tag/v0.3.4
[0.3.3]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/releases/tag/v0.3.3
