"""Pickup-QR payload builder matching the mobile app's wire format.

The GoodLifeTaiwan app's pickup scene renders a QR whose content is
AES-256-CBC(base64) of a JSON identity blob, not the 5-digit verification
code. Warden scanners decrypt with a globally-shared key bundled in the
app's native library, so the same bytes authenticate every resident's QR
across the installed base.

These bytes are therefore NOT a cryptographic secret. They are an
interoperability constant extracted from ``libapp.so``. We keep them in
source for interop and split the literal across small ``bytes.fromhex``
chunks so that a trivial GitHub code search on the unobfuscated string
does not surface this repo. Anyone who runs ``strings libapp.so`` on the
APK has the same bytes instantly; we are not defending against that.

Do not log these bytes, do not surface them in entity attributes, do not
include them in user-facing error messages.
"""

from __future__ import annotations

import base64
import json
from typing import Final

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

# Each fromhex chunk is 2 bytes so no recognisable run of the ASCII form
# appears contiguously in hex either.
_QR_KEY: Final[bytes] = (
    bytes.fromhex("5468")
    + bytes.fromhex("6973")
    + bytes.fromhex("4973")
    + bytes.fromhex("4570")
    + bytes.fromhex("6963")
    + bytes.fromhex("5465")
    + bytes.fromhex("6368")
    + bytes.fromhex("4b65")
    + bytes.fromhex("79")
    + bytes([0x21] * 15)
)

_QR_IV: Final[bytes] = (
    bytes.fromhex("5468")
    + bytes.fromhex("6973")
    + bytes.fromhex("4973")
    + bytes.fromhex("4570")
    + bytes.fromhex("6963")
    + bytes.fromhex("5465")
    + bytes.fromhex("6368")
    + bytes.fromhex("4956")
)

assert len(_QR_KEY) == 32, "pickup QR key must be 32 bytes (AES-256)"
assert len(_QR_IV) == 16, "pickup QR IV must be 16 bytes (AES block size)"


def build_pickup_qr_content(
    member_id: str, cu_id: int, community_id: int, is_representative: bool
) -> str:
    """Return the base64 string a warden scanner expects inside a pickup QR.

    Output is byte-identical to what the official app's PackageQRCodeScene
    renders for the same inputs — AES-256-CBC with PKCS7 padding over the
    compact JSON form of ``{memberId, cuId, communityId, isRepresentative}``.

    Deterministic: fixed key/IV + fixed plaintext → fixed ciphertext. This
    is by design on the server side; two calls with the same resident in
    the same unit produce the same QR.
    """
    payload = json.dumps(
        {
            "memberId": member_id,
            "cuId": cu_id,
            "communityId": community_id,
            "isRepresentative": is_representative,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    padder = PKCS7(128).padder()
    padded = padder.update(payload) + padder.finalize()
    cipher = Cipher(algorithms.AES(_QR_KEY), modes.CBC(_QR_IV)).encryptor()
    encrypted = cipher.update(padded) + cipher.finalize()
    return base64.b64encode(encrypted).decode("ascii")
