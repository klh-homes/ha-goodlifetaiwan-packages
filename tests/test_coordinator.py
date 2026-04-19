"""Tests for coordinator helpers: slug derivation and package summarisation."""

from __future__ import annotations

from custom_components.goodlifetaiwan.coordinator import CommunityState, _summarize
from custom_components.goodlifetaiwan.entity import community_slug


def test_community_slug_ascii_name():
    slug = community_slug("Sunrise Building", 110412, 1777)
    assert slug == "sunrise_building_0412"


def test_community_slug_chinese_name_falls_back():
    slug = community_slug("日健滙社區", 110412, 1777)
    assert slug == "c1777_0412"


def test_community_slug_mixed_name_keeps_ascii():
    slug = community_slug("日健滙 Alpha", 110412, 1777)
    assert slug == "alpha_0412"


def test_summarize_extracts_fields():
    state = CommunityState(
        community_id=1777,
        community_unit_id=110412,
        community_name="日健滙",
        slug="c1777_0412",
    )
    raw = {
        "packageId": 123,
        "packageNo": "0095",
        "toContactInfo": {"name": "Alice", "phone": "0912"},
        "packagePlacement": {"packagePlacementName": "櫃台"},
        "checkedInDate": "2026-04-16T16:21:56",
        "isPackageOwner": True,
        "fileInfos": [{}],
    }
    pkg = _summarize(raw, state)
    assert pkg.package_id == 123
    assert pkg.recipient_name == "Alice"
    assert pkg.placement == "櫃台"
    assert pkg.is_owner is True
    assert pkg.has_photo is True
    assert pkg.community_name == "日健滙"
