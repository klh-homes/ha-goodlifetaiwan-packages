"""Tests for coordinator helpers: package summarisation."""

from __future__ import annotations

from custom_components.goodlifetaiwan.coordinator import CommunityState, _summarize


def test_summarize_extracts_fields():
    state = CommunityState(
        community_id=101,
        community_unit_id=1001,
        community_name="範例社區",
        slug="",
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
    assert pkg.community_name == "範例社區"
