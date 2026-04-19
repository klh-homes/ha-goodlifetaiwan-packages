# Configuration

## Adding the integration

1. **Settings → Devices & Services → Add Integration**, search for _GoodLifeTaiwan_.
2. Enter the phone number on the 中保好生活 account. Accepted formats:
   - Taiwan short form: `0912345678` (integration adds `+886`).
   - Full E.164: `+886912345678`.
3. Enter the 6-digit SMS code within 3 minutes.
4. If the account has more than one community, pick which to import.

## Entities created

Per community:

| Entity                         | Purpose                                                                                                                    |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| `sensor.*_unpicked`            | Count of unpicked packages; full list in `items` attribute.                                                                |
| `sensor.*_auth_status`         | Per-community auth state (`ok` / `refreshing` / `auth_needed` / `error`).                                                  |
| `sensor.*_pickup_code`         | Most recent 5-digit pickup code (clears on expiry unless `switch.*_auto_regenerate_pickup_code` is on).                    |
| `sensor.*_pickup_code_expires` | Expiry timestamp for the most recent pickup code.                                                                          |
| `image.*_qr`                   | Most recent QR code as PNG.                                                                                                |
| `button.*_request_pickup_code` | Press to request a fresh pickup code. Equivalent to calling the `goodlifetaiwan.request_pickup_code` service with no args. |

Per entry (account device, Configuration section):

| Entity                                 | Default        | Range / values | Purpose                                                                                                                                   |
| -------------------------------------- | -------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `number.*_poll_interval`               | `600` (10 min) | `60`–`3600` s  | Coordinator polling cadence. Setting it here updates the live coordinator — no integration reload needed.                                 |
| `switch.*_auto_regenerate_pickup_code` | `off`          | on/off         | When the 10-minute pickup code expires: `off` clears the sensor; `on` silently requests a new one. ⚠️ see caveat below.                   |

⚠️ **`switch.*_auto_regenerate_pickup_code` caveat:** the upstream API does not document whether issuing a new code invalidates the previous one. With the toggle on, the integration requests a fresh code every ~10 minutes whether anyone is using the current one, which could theoretically invalidate a code the user is actively trying to use at the pickup counter. Leaving it off (the default) means the dashboard shows "no active code" after 10 minutes and you press the button / call the service when you actually need one.

These per-entry entities live on the **account device** and are tagged `EntityCategory.CONFIG`, so HA hides them from the default dashboard but they appear under the account device's Configuration section and can be referenced in automations.

## Re-auth flow

When the refresh token is rejected (after 90 days or server-side revocation) the integration:

1. Transitions to `auth_needed`.
2. Fires `goodlifetaiwan_auth_required`.
3. Opens the reauth flow — HA shows a repair notification.

You can also trigger re-auth manually by calling the `goodlifetaiwan.send_sms` service followed by `goodlifetaiwan.submit_code` with the 6-digit code you received.

## Token storage

Tokens live in `.storage/goodlifetaiwan_tokens_<entry_id>` and are covered by HA backup. The integration never writes them back into `ConfigEntry.data` because refresh tokens rotate on every use — keeping them out of `ConfigEntry.data` avoids constant entry-update events.
