# Reverse-engineering notes

This integration talks to an undocumented mobile-app API. Everything we know about it was learned from decompiling the Android app and capturing live traffic — there is no vendor documentation, no stable versioning guarantee, and no public SDK.

## Source of truth

The canonical reverse-engineering write-up lives in a separate repository (kept out of this one because it ships the APK and captured traffic):

```
github.com/klh-homes/package-reverse-engineering  (private)
```

That repo contains:

- `README.md` — endpoint catalogue (175 endpoints) with request/response shapes.
- `check_packages.sh` — the working bash reference this integration ports from. Run this if you want to verify `life-spi.glf.tw` behaviour without touching Python.
- `contracts/` — the same specs this integration is built against.
- The APK + captured `.har` files.

## What the integration uses

Of the 175 known endpoints, v0.1 consumes **7**:

| Host                   | Endpoint                                                    | Used by                         |
| ---------------------- | ----------------------------------------------------------- | ------------------------------- |
| `auth.epictech.com.tw` | `POST /api/v2/smsVerify/SendVerifySms`                      | `send_sms`, config flow         |
| `auth.epictech.com.tw` | `POST /api/v2/smsVerify/verifySmsCode`                      | `submit_code`, config flow      |
| `auth.epictech.com.tw` | `POST /api/v2/Member/Login`                                 | SMS login chain                 |
| `auth.epictech.com.tw` | `POST /api/v2/Token/RefreshMemberToken`                     | AuthManager auto-refresh        |
| `life-spi.glf.tw`      | `GET /resident/api/Member/MemberInfo`                       | Config flow community selection |
| `life-spi.glf.tw`      | `GET /resident/api/v76/Package/UnpickedPackages`            | Coordinator poll                |
| `life-spi.glf.tw`      | `GET /resident/api/v76/Package/PackageDetail/{id}`          | On-demand, not polled           |
| `life-spi.glf.tw`      | `POST /resident/api/Package/CreateCheckOutVerificationCode` | `request_pickup_code`           |

## Headers that must match the app fingerprint

Every call replays the exact header shape the mobile app sends. Missing or altered headers cause some endpoints to fail with ambiguous errors.

```
app-info:      Android/14 Beer/1.2.44.2025080801
User-Agent:    Dart/3.8 (dart:io)
timestamp:     <epoch-millis-as-string>
traceparent:   <uuid-v4-lowercase>
Accept-Encoding: gzip
```

Life-spi host additionally requires:

```
Authorization:    Bearer <access_token>
communityid:      <int>
communityunitid:  <int>
```

Gotchas captured while porting from bash to Python:

- `timestamp` is epoch **milliseconds** as a string, not seconds and not ISO8601.
- `traceparent` is a plain lowercase UUID v4 — no `urn:` prefix, not the W3C traceparent format.
- `RefreshMemberToken` wraps the refresh token as a **plain string** in `data`: `{"data": "<jwt>"}`. All other auth endpoints wrap as an object. Easy to forget.
- Several endpoints return HTTP 200 with `code: "ERR9999"` on failure. HTTP status alone isn't enough to detect errors — always check the envelope `code` field.
- Azure Blob URLs for package photos are **public**. Adding `Authorization` actually causes a 403.

## Stability

The mobile app ships frequent updates; we haven't observed the app-info string changing in a way that breaks the API, but it's possible. If calls start failing after a vendor-side change, check:

1. Whether `app-info` / `User-Agent` need bumping.
2. Whether any endpoint path version suffix (`/v76/`) has advanced.
3. Whether request/response shapes drift (e.g., new required field in a body).

There is no vendor communication channel. A broken upstream is a feature-freeze event; file an issue describing the failure mode.
