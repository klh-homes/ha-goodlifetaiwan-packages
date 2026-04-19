# Configuration

## Adding the integration

1. **Settings → Devices & Services → Add Integration**, search for _GoodLifeTaiwan_.
2. Enter the phone number on the 中保好生活 account. Accepted formats:
   - Taiwan short form: `0912345678` (integration adds `+886`).
   - Full E.164: `+886912345678`.
3. Enter the 6-digit SMS code within 3 minutes.
4. If the account has more than one community, pick which to import.

## Options

Accessible via **Configure** on the integration card.

| Option                        | Default        | Range       | Notes                                                                                                                                      |
| ----------------------------- | -------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `scan_interval_seconds`       | `600` (10 min) | `60`–`3600` | Polling cadence for unpicked packages.                                                                                                     |
| `auto_regenerate_pickup_code` | `false`        | bool        | When the 10-minute pickup code expires: `false` clears the sensor (press the button to regenerate); `true` silently requests a new one. ⚠️ |

⚠️ **`auto_regenerate_pickup_code` caveat:** the upstream API does not document whether issuing a new code invalidates the previous one. With the toggle on, the integration requests a fresh code every ~10 minutes whether anyone is using the current one, which could theoretically invalidate a code the user is actively trying to use at the pickup counter. Leaving it off (the default) means the dashboard shows "no active code" after 10 minutes and you press the button / call the service when you actually need one.

## Entities created

Per community:

| Entity                         | Purpose                                                                                                                    |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| `sensor.*_unpicked`            | Count of unpicked packages; full list in `items` attribute.                                                                |
| `sensor.*_auth_status`         | Per-community auth state (`ok` / `refreshing` / `auth_needed` / `error`).                                                  |
| `sensor.*_pickup_code`         | Most recent 5-digit pickup code (clears on expiry unless `auto_regenerate_pickup_code` is on).                             |
| `sensor.*_pickup_code_expires` | Expiry timestamp for the most recent pickup code.                                                                          |
| `image.*_qr`                   | Most recent QR code as PNG.                                                                                                |
| `button.*_request_pickup_code` | Press to request a fresh pickup code. Equivalent to calling the `goodlifetaiwan.request_pickup_code` service with no args. |

## Re-auth flow

When the refresh token is rejected (after 90 days or server-side revocation) the integration:

1. Transitions to `auth_needed`.
2. Fires `goodlifetaiwan_auth_required`.
3. Opens the reauth flow — HA shows a repair notification.

You can also trigger re-auth manually by calling the `goodlifetaiwan.send_sms` service followed by `goodlifetaiwan.submit_code` with the 6-digit code you received.

## Token storage

Tokens live in `.storage/goodlifetaiwan_tokens_<entry_id>` and are covered by HA backup. The integration never writes them back into `ConfigEntry.data` because refresh tokens rotate on every use — keeping them out of `ConfigEntry.data` avoids constant entry-update events.
