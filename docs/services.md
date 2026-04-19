# Services

All services are response-capable (HA 2023.7+). Call with `?return_response=true` to receive the response.

## `goodlifetaiwan.request_pickup_code`

Generate a fresh 5-digit pickup verification code plus a QR PNG.

### Input

| Field          | Required    | Type   | Description                                     |
| -------------- | ----------- | ------ | ----------------------------------------------- |
| `entry_id`     | conditional | string | Required if multiple accounts configured.       |
| `community_id` | conditional | int    | Required if the entry has multiple communities. |

### Response

```yaml
code: "52229"
image_b64: "iVBORw0KGgo..."
expires_at: "2026-04-19T14:12:47+08:00"
community_id: 1777
generated_at: "2026-04-19T14:02:47+08:00"
```

### Example (iOS Shortcut)

```
POST /api/services/goodlifetaiwan/request_pickup_code?return_response=true
Authorization: Bearer <HA_LONG_LIVED_TOKEN>
Content-Type: application/json

{}
```

Decode `image_b64` and display the PNG in a "Show Result" step.

### Errors

- `ServiceValidationError("ambiguous_entry")` — multiple entries and none specified.
- `ServiceValidationError("auth_required")` — entry is in `auth_needed`; call `send_sms` first.
- `HomeAssistantError("api_error")` — server returned an error.
- `HomeAssistantError("network_error")` — timeout or connection failure.

## `goodlifetaiwan.send_sms`

Request an SMS code to your registered phone for re-login.

### Input

| Field      | Required    | Description                               |
| ---------- | ----------- | ----------------------------------------- |
| `entry_id` | conditional | Required if multiple accounts configured. |

### Response

```yaml
sent_at: "2026-04-19T13:48:34+08:00"
expires_at: "2026-04-19T13:51:34+08:00"
verify_id_hint: "0bf82c45"
```

### Rate limit

One call per entry per 60 seconds. Further calls within the cooldown raise `rate_limited`.

### Errors

- `ServiceValidationError("rate_limited")` — too soon after previous call.
- `ServiceValidationError("already_authenticated")` — entry is in `ok` state.
- `HomeAssistantError("api_error" | "network_error")`.

## `goodlifetaiwan.submit_code`

Complete SMS login by submitting the 6-digit code.

### Input

| Field      | Required    | Description                               |
| ---------- | ----------- | ----------------------------------------- |
| `entry_id` | conditional | Required if multiple accounts configured. |
| `code`     | yes         | 6-digit SMS code.                         |

### Response

```yaml
success: true
access_token_exp: "2026-04-19T13:57:33+08:00"
refresh_token_exp: "2026-07-18T13:57:33+08:00"
```

### Errors

- `ServiceValidationError("invalid_code_format")` — not 6 digits.
- `ServiceValidationError("no_pending_verification")` — `send_sms` not called, or its 3-min TTL expired.
- `HomeAssistantError("code_rejected")` — server rejected the code (typo).
- `HomeAssistantError("network_error")`.

On `code_rejected` you can retry with a fresh code while the `verify_id` TTL is still valid (3 min from `send_sms`).
