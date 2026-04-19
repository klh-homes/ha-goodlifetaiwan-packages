# Configuration

## Adding the integration

1. **Settings → Devices & Services → Add Integration**, search for _GoodLifeTaiwan_.
2. Enter the phone number on the 中保好生活 account. Accepted formats:
   - Taiwan short form: `0912345678` (integration adds `+886`).
   - Full E.164: `+886912345678`.
3. Enter the 6-digit SMS code within 3 minutes.
4. If the account has more than one community, pick which to import.

## Entities created

v0.3 runs one coordinator per community. Every entity — state and CONFIG-category settings — lives on that community's device. There is no account-level device.

Per community:

| Entity                                 | Default       | Range / values | Purpose                                                                                                                                 |
| -------------------------------------- | ------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `sensor.*_unpicked`                    | —             | int            | Count of unpicked packages; full list in `items` attribute.                                                                             |
| `sensor.*_auth_status`                 | —             | enum           | Auth state (`ok` / `refreshing` / `auth_needed` / `error`).                                                                             |
| `sensor.*_pickup_code`                 | —             | 5-digit string | Most recent 5-digit pickup code.                                                                                                        |
| `sensor.*_pickup_code_expires`         | —             | timestamp      | Expiry timestamp for the most recent pickup code.                                                                                       |
| `image.*_qr`                           | —             | PNG            | Most recent QR code image.                                                                                                              |
| `button.*_request_pickup_code`         | —             | button         | Press to request a fresh pickup code. Same effect as calling the `goodlifetaiwan.request_pickup_code` service with this community's id. |
| `number.*_poll_interval`               | `300` (5 min) | `60`–`3600` s  | Polling cadence for this community's coordinator. Setting it mutates the live coordinator — no reload.                                  |
| `switch.*_auto_regenerate_pickup_code` | `on`          | on/off         | `on`: always keep a fresh pickup code in state. Requests one at HA startup / switch-on / after re-auth if none is cached, and again whenever the current code's 10-minute window expires. `off`: never auto-request; the sensor clears on expiry and stays blank until the button / service is called manually. |

The last two are tagged `EntityCategory.CONFIG`, so HA hides them from the default dashboard but they show up under the community device's Configuration section and are fully automatable.

**Auto-regenerate default is `on` since v0.3.** Earlier versions defaulted it off because the contract speculated that issuing a new pickup code might invalidate the prior one. Live-testing against the server (documented in CHANGELOG v0.1 known-limitations) proved that's not the case — old codes remain valid up to expiry, and the always-fresh default is safe.

## Re-auth flow

When the refresh token is rejected (after 90 days or server-side revocation) the integration:

1. Transitions to `auth_needed`.
2. Fires `goodlifetaiwan_auth_required`.
3. Opens the reauth flow — HA shows a repair notification.

You can also trigger re-auth manually by calling the `goodlifetaiwan.send_sms` service followed by `goodlifetaiwan.submit_code` with the 6-digit code you received.

## Token storage

Tokens live in `.storage/goodlifetaiwan_tokens_<entry_id>` and are covered by HA backup. The integration never writes them back into `ConfigEntry.data` because a fresh refresh token comes back on every refresh (~10 min cadence) — keeping the moving target out of `ConfigEntry.data` avoids constant entry-update events.
