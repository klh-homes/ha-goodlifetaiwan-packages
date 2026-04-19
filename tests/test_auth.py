"""Tests for JWT helpers and auth state transitions."""

from __future__ import annotations

import time

from custom_components.goodlifetaiwan.auth import (
    jwt_exp,
    jwt_valid_structure,
    mask_phone,
)


def test_jwt_valid_structure_accepts_well_formed(fresh_access_token):
    assert jwt_valid_structure(fresh_access_token) is True


def test_jwt_valid_structure_rejects_short_or_missing():
    assert jwt_valid_structure(None) is False
    assert jwt_valid_structure("") is False
    assert jwt_valid_structure("not.a.jwt.too.many") is False
    assert jwt_valid_structure("abc.def") is False


def test_jwt_exp_reads_epoch(fresh_access_token):
    exp = jwt_exp(fresh_access_token)
    assert exp > time.time()


def test_mask_phone_last_four():
    assert mask_phone("+886912345678") == "***5678"
    assert mask_phone("0912") == "***0912"
    assert mask_phone("12") == "***12"
