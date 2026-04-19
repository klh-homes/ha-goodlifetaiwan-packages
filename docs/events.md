# Events

All events are fired on the HA event bus and can be consumed by automations via the `event` trigger. Payloads are flat JSON dicts, safe for Jinja2 templating.

## `goodlifetaiwan_package_arrived`

A package appeared in the unpicked list that wasn't present in the previous poll.

```yaml
entry_id: "<ha_config_entry_id>"
community_id: 1777
community_name: "社區名稱"
package_id: 3561900
package_no: "0095"
recipient_name: "收件人"
recipient_phone: "0912345678"
placement: "櫃台右側"
checked_in_date: "2026-04-16T16:21:56.66"
is_owner: true
has_photo: true
```

One event per new package. NOT fired for packages already present in the first poll after setup (to avoid spamming historical items).

## `goodlifetaiwan_package_picked`

A package previously seen as unpicked is no longer returned by the API.

```yaml
entry_id: "<...>"
community_id: 1777
community_name: "社區名稱"
package_id: 3561900
package_no: "0095"
recipient_name: "收件人"
placement: "櫃台右側"
checked_in_date: "2026-04-16T16:21:56.66"
```

Payload is sourced from the last-known snapshot (the package is no longer in the API response).

## `goodlifetaiwan_auth_required`

Fired once when the service health transitions into `auth_needed`.

```yaml
entry_id: "<...>"
phone_masked: "***5036"
reason: "initial" | "refresh_expired" | "refresh_rejected"
at: "2026-04-19T13:00:00+08:00"
```

Debounced — re-entering the state does not refire.

## `goodlifetaiwan_auth_sms_sent`

Fired after `send_sms` successfully asks the server to dispatch an SMS.

```yaml
entry_id: "<...>"
phone_masked: "***5036"
sent_at: "2026-04-19T13:48:34+08:00"
expires_at: "2026-04-19T13:51:34+08:00"
verify_id_hint: "0bf82c45"
```

## `goodlifetaiwan_auth_success`

Fired after `submit_code` completes the SMS + login round-trip.

```yaml
entry_id: "<...>"
phone_masked: "***5036"
access_token_exp: "2026-04-19T13:57:33+08:00"
refresh_token_exp: "2026-07-18T13:57:33+08:00"
at: "2026-04-19T13:57:33+08:00"
```

## `goodlifetaiwan_auth_failed`

Fired on any failure during the SMS login flow.

```yaml
entry_id: "<...>"
phone_masked: "***5036"
stage: "send_sms" | "verify_code" | "login"
error_code: "<server error code if known>"
error_message: "<human-readable>"
at: "2026-04-19T13:58:00+08:00"
```

## Stability guarantee

Fields documented here are stable across minor versions. Additions (new fields, new event types) are non-breaking; renames / removals bump the major version and are announced in release notes.
