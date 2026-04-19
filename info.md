<p align="center">
  <img src="assets/icon.png" alt="GoodLifeTaiwan integration icon" width="128" />
</p>

# Home Assistant integration for 中保好生活 package tracking and pickup

**Features**

- Sensor with count + full list of unpicked packages.
- On-demand pickup QR code (image entity + service call returning base64 PNG).
- HA events (`package_arrived`, `package_picked`, `auth_required`, ...) for user automations.
- SMS-driven first-run auth; automatic token refresh for 90 days.

**Unofficial integration.** Not affiliated with 中興保全 (Taiwan Secom) or the operator of glf.tw. The API is undocumented and may change without notice.

See the [README](./README.md) and [docs](./docs/) for full details.
