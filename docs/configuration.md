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

| Option                  | Default        | Range       | Notes                                  |
| ----------------------- | -------------- | ----------- | -------------------------------------- |
| `scan_interval_seconds` | `600` (10 min) | `60`–`3600` | Polling cadence for unpicked packages. |

## Entities created

Per community:

| Entity                 | Purpose                                                                   |
| ---------------------- | ------------------------------------------------------------------------- |
| `sensor.*_unpicked`    | Count of unpicked packages; full list in `items` attribute.               |
| `sensor.*_auth_status` | Per-community auth state (`ok` / `refreshing` / `auth_needed` / `error`). |
| `sensor.*_qr_code`     | Most recent 5-digit pickup code (updated on-demand).                      |
| `sensor.*_qr_expires`  | Expiry timestamp for the most recent code.                                |
| `image.*_qr`           | Most recent QR code as PNG.                                               |

Per entry (account):

| Entity             | Purpose                                            |
| ------------------ | -------------------------------------------------- |
| `sensor.*_service` | Aggregate health (`ok` / `auth_needed` / `error`). |

## Re-auth flow

When the refresh token is rejected (after 90 days or server-side revocation) the integration:

1. Transitions to `auth_needed`.
2. Fires `goodlifetaiwan_auth_required`.
3. Opens the reauth flow — HA shows a repair notification.

You can also trigger re-auth manually by calling the `goodlifetaiwan.send_sms` service followed by `goodlifetaiwan.submit_code` with the 6-digit code you received.

## Token storage

Tokens live in `.storage/goodlifetaiwan_tokens_<entry_id>` and are covered by HA backup. The integration never writes them back into `ConfigEntry.data` because refresh tokens rotate on every use — keeping them out of `ConfigEntry.data` avoids constant entry-update events.
