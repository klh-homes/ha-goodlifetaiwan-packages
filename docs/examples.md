# Automation examples

## Notify Discord when a package arrives

```yaml
- alias: GLT — notify Discord on new package
  trigger:
    platform: event
    event_type: goodlifetaiwan_package_arrived
  action:
    service: notify.discord_webhook
    data:
      message: |
        📦 New package for {{ trigger.event.data.recipient_name }}
        #{{ trigger.event.data.package_no }} at {{ trigger.event.data.placement }}
```

## Notify a specific person on their packages only

```yaml
- alias: GLT — notify Alice on her packages
  trigger:
    platform: event
    event_type: goodlifetaiwan_package_arrived
  condition:
    - "{{ trigger.event.data.recipient_phone == '0912345678' }}"
  action:
    service: notify.mobile_app_alice_iphone
    data:
      message: "包裹到了 — {{ trigger.event.data.placement }}"
```

## Re-auth prompt via Discord

```yaml
- alias: GLT — re-auth needed
  trigger:
    platform: event
    event_type: goodlifetaiwan_auth_required
  action:
    service: notify.discord_webhook
    data:
      message: |
        ⚠️ GoodLifeTaiwan re-login needed ({{ trigger.event.data.reason }})
        Run:  goodlifetaiwan.send_sms  (entry_id: {{ trigger.event.data.entry_id }})
```

## iOS Shortcut: request pickup code and show its QR

1. Shortcut → "Get contents of URL".
2. URL: `https://<your-ha>/api/services/goodlifetaiwan/request_pickup_code?return_response=true`
3. Method: POST.
4. Headers:
   - `Authorization: Bearer <HA_LONG_LIVED_TOKEN>`
   - `Content-Type: application/json`
5. Body: `{}` (or `{"entry_id": "..."}`).
6. "Get dictionary value" → `service_response` → `<your_entry_id>` → `image_b64`.
7. "Base64 decode" → "Get Image from Input" → "Show Result".

Alternatively, surface the image directly via `image.goodlifetaiwan_<slug>_qr_image` in a Lovelace picture card. `<slug>` is the community name slugified by HA from the device name.

## Lovelace card: QR with countdown

Replace `<slug>` below with your community's slug (check **Settings → Devices & Services → goodlifetaiwan → your community → entities** to see the exact entity IDs).

```yaml
type: vertical-stack
cards:
  - type: picture-entity
    entity: image.goodlifetaiwan_<slug>_qr_image
    show_name: false
    show_state: false
  - type: entities
    entities:
      - entity: sensor.goodlifetaiwan_<slug>_pickup_code
        name: Pickup code
      - entity: sensor.goodlifetaiwan_<slug>_pickup_code_expires
        name: Expires
```
