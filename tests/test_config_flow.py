"""Tests for phone normalisation and community-label rendering."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.goodlifetaiwan.config_flow import (
    _community_label,
    normalize_phone,
)


def test_normalize_phone_taiwan_mobile():
    assert normalize_phone("0912345678") == "+886912345678"


def test_normalize_phone_full_e164():
    assert normalize_phone("+886912345678") == "+886912345678"


def test_normalize_phone_strips_separators():
    assert normalize_phone(" 0912-345-678 ") == "+886912345678"


def test_normalize_phone_886_prefix():
    assert normalize_phone("886912345678") == "+886912345678"


def test_normalize_phone_rejects_nonsense():
    with pytest.raises(vol.Invalid):
        normalize_phone("12345")
    with pytest.raises(vol.Invalid):
        normalize_phone("abc")


def test_community_label_with_short_address():
    unit = {
        "communityId": 1777,
        "communityUnitId": 110412,
        "communityName": "範例社區",
        "shortAddress": "1號1樓",
    }
    assert _community_label(unit) == "範例社區 — 1號1樓"


def test_community_label_without_short_address():
    unit = {"communityId": 1, "communityName": "社區A"}
    assert _community_label(unit) == "社區A"


def test_community_label_fallback_when_name_missing():
    unit = {"communityId": 42}
    assert _community_label(unit) == "community_42"
