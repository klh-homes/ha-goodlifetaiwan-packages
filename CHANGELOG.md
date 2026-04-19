# Changelog

All notable changes to this integration are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows [Semantic Versioning](https://semver.org/).

While the integration is pre-1.0, minor versions may include breaking changes to the public event / service / entity surface. Breaking changes are called out under their own heading in the release notes.

## [Unreleased]

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

[Unreleased]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/compare/v0.3.3...HEAD
[0.3.3]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/releases/tag/v0.3.3
