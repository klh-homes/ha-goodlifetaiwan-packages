"""Constants for GoodLifeTaiwan integration."""

from __future__ import annotations

DOMAIN = "goodlifetaiwan"
PLATFORMS: tuple[str, ...] = ("sensor", "image", "button", "number", "switch")

# API hosts
BASE_URL_API = "https://life-spi.glf.tw"
BASE_URL_AUTH = "https://auth.epictech.com.tw"

# Request header fingerprint (match the mobile app)
APP_INFO = "Android/14 Beer/1.2.44.2025080801"
USER_AGENT = "Dart/3.8 (dart:io)"

# Event types (public API surface — see contracts/events.md)
EVENT_PACKAGE_ARRIVED = f"{DOMAIN}_package_arrived"
EVENT_PACKAGE_PICKED = f"{DOMAIN}_package_picked"
EVENT_AUTH_REQUIRED = f"{DOMAIN}_auth_required"
EVENT_AUTH_SMS_SENT = f"{DOMAIN}_auth_sms_sent"
EVENT_AUTH_SUCCESS = f"{DOMAIN}_auth_success"
EVENT_AUTH_FAILED = f"{DOMAIN}_auth_failed"

# Service names
SERVICE_REQUEST_PICKUP_CODE = "request_pickup_code"
SERVICE_SEND_SMS = "send_sms"
SERVICE_SUBMIT_CODE = "submit_code"

# Config entry data keys
CONF_PHONE_NUMBER = "phone_number"
CONF_COMMUNITY_UNIT_IDS = "community_unit_ids"
CONF_MEMBER_INFO = "member_info"
CONF_SCAN_INTERVAL = "scan_interval_seconds"
CONF_AUTO_REGENERATE_PICKUP_CODE = "auto_regenerate_pickup_code"

# Storage
STORAGE_VERSION = 1
STORAGE_KEY_FMT = f"{DOMAIN}_tokens_{{entry_id}}"

# Defaults
DEFAULT_SCAN_INTERVAL_SEC = 600
MIN_SCAN_INTERVAL_SEC = 60
MAX_SCAN_INTERVAL_SEC = 3600

ACCESS_TOKEN_REFRESH_MARGIN_SEC = 30
SEND_SMS_RATE_LIMIT_SEC = 60
VERIFY_ID_TTL_SEC = 180  # 3 min

# Auth states
AUTH_STATE_OK = "ok"
AUTH_STATE_REFRESHING = "refreshing"
AUTH_STATE_AUTH_NEEDED = "auth_needed"
AUTH_STATE_ERROR = "error"

AUTH_STATES: tuple[str, ...] = (
    AUTH_STATE_OK,
    AUTH_STATE_REFRESHING,
    AUTH_STATE_AUTH_NEEDED,
    AUTH_STATE_ERROR,
)

# life-spi response codes
RESPONSE_CODE_SUCCESS = "COM00001"
