# Changelog

All notable changes to this integration are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows [Semantic Versioning](https://semver.org/).

While the integration is pre-1.0, minor versions may include breaking changes to the public event / service / entity surface. Breaking changes are called out under their own heading in the release notes.

## [Unreleased]

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
- Refresh tokens are single-use rotating; running the integration on two HA instances for the same account will break one of them.

[Unreleased]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/klh-homes/ha-goodlifetaiwan-packages/releases/tag/v0.1.0
