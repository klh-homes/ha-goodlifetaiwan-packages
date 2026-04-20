"""v0.3.5 regression tests for the pickup-QR encrypter.

The warden scanner accepts only the exact byte pattern the mobile app
produces (AES-256-CBC, PKCS7, fixed key+IV, compact-JSON plaintext). If
any of those parameters change, real-world pickup scanning breaks
silently — server returns no error, warden just says "not recognised".
These tests lock the wire format down against regression.

Test vectors baked in by hand: inputs deliberately chosen to not reveal
the maintainer's real account. Values below are completely synthetic.
"""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from custom_components.goodlifetaiwan._qr_crypto import (
    _QR_IV,
    _QR_KEY,
    build_pickup_qr_content,
)


def test_key_and_iv_sizes():
    assert len(_QR_KEY) == 32
    assert len(_QR_IV) == 16


def test_deterministic_same_inputs():
    """Fixed key + fixed IV + fixed plaintext → fixed ciphertext. Two calls
    with the same inputs must return byte-identical output. (If someone
    accidentally introduces a random IV or nonce, this test catches it.)"""
    a = build_pickup_qr_content("0900000000", 1, 1, False)
    b = build_pickup_qr_content("0900000000", 1, 1, False)
    assert a == b


def test_different_member_id_different_output():
    a = build_pickup_qr_content("0900000001", 1, 1, False)
    b = build_pickup_qr_content("0900000002", 1, 1, False)
    assert a != b


def test_different_is_representative_different_output():
    a = build_pickup_qr_content("0900000000", 1, 1, False)
    b = build_pickup_qr_content("0900000000", 1, 1, True)
    assert a != b


def test_round_trip_decrypts_to_expected_plaintext():
    """Sanity: encrypt then decrypt with the same key/IV and confirm the
    plaintext is the JSON shape the warden scanner expects. This also
    implicitly verifies the JSON separator style (compact, no spaces).
    """
    ct = build_pickup_qr_content("0912345678", 1001, 101, True)
    raw = base64.b64decode(ct)
    cipher = Cipher(algorithms.AES(_QR_KEY), modes.CBC(_QR_IV)).decryptor()
    padded = cipher.update(raw) + cipher.finalize()
    unpadder = PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    assert json.loads(plain) == {
        "memberId": "0912345678",
        "cuId": 1001,
        "communityId": 101,
        "isRepresentative": True,
    }
    # Also confirm the serialised form used on the wire has no whitespace —
    # if someone switches json.dumps to default separators, this breaks.
    assert b": " not in plain
    assert b", " not in plain


def test_known_ciphertexts_do_not_drift():
    """Known-good vectors. If any of these change, the warden scanner
    will reject all pickup QRs the integration produces — meaning either
    (a) an intentional wire-format change (regen vectors + document why)
    or (b) a bug. Either way, this test firing is a hard stop.
    """
    cases = [
        (
            ("0900000000", 1, 1, False),
            "tP7u5ZJ1WYqYToBmRQ8SPW7g8oDgwHbkAy/fRUFl/FSjOjKmNDpYVHuVQq2UWcCGGXCC2lVbC56jtlFx/ICuSrsr8BDFxNvM8136HD5axb0=",
        ),
        (
            ("0912345678", 1001, 101, True),
            "m8f9DHUn19VyG3xJ3g5OVP8/ktqKSPwze4wDcg/j/agDHEUu3YPrS9e7n8fVh6K6q1Csvcmm4m4jKeLziqN6ZHoXgmLAAW9aUoSSOAoprho=",
        ),
        (
            ("0999999999", 9999, 9999, False),
            "UGoIRc2ibFGbj2Jm5F5r80yzWkkvt61PtvuhThEHYmjVlAAoupToYPUZMtNyp7VP6hxUdlgy5ZY2gUpE7NHdGhS1FeiiSYp3c1PLVNHwv9vspX1dtlvSv6hFjp5iOddA",
        ),
    ]
    for inputs, expected in cases:
        got = build_pickup_qr_content(*inputs)
        assert got == expected, (
            f"ciphertext drift for inputs {inputs!r}: got {got!r}, expected {expected!r}. "
            "If this is intentional, update the vectors AND verify against a real "
            "app-generated QR before shipping — the warden scanner will reject "
            "anything that doesn't match the app exactly."
        )
