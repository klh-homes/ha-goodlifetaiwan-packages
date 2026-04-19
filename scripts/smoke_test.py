#!/usr/bin/env python3
"""Live smoke tests against life-spi.glf.tw — no Home Assistant required.

Uses custom_components.goodlifetaiwan.api.GoodLifeApi directly, so what works
here is what the integration will see at runtime.

Token files default to ``../package-reverse-engineering/{token,refresh_token}.txt``
per HANDOFF.md. Refresh rotates on every use — the script writes the new refresh
token back to disk immediately, matching check_packages.sh behaviour.

Usage
-----
    python scripts/smoke_test.py refresh              # rotate + print exp
    python scripts/smoke_test.py unpicked             # list unpicked packages
    python scripts/smoke_test.py qr [OUT.png]         # generate QR, save PNG
    python scripts/smoke_test.py detail <packageId>   # single-package detail
    python scripts/smoke_test.py me                   # member info
    python scripts/smoke_test.py login +886912345678  # full SMS login

Overrides: COMMUNITY_ID, COMMUNITY_UNIT_ID env vars (defaults match RE tooling).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

# Add repo root to sys.path so we can import the integration without installing it.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.goodlifetaiwan.api import (  # noqa: E402
    ApiResponseError,
    AuthRejected,
    GoodLifeApi,
    NetworkError,
)
from custom_components.goodlifetaiwan.auth import jwt_exp, jwt_valid_structure  # noqa: E402

TOKEN_DIR = Path(
    os.environ.get(
        "GLT_TOKEN_DIR",
        str(REPO_ROOT.parent / "package-reverse-engineering"),
    )
)
TOKEN_FILE = TOKEN_DIR / "token.txt"
REFRESH_FILE = TOKEN_DIR / "refresh_token.txt"
# These defaults are placeholders — set COMMUNITY_ID and COMMUNITY_UNIT_ID
# environment variables to your actual values before running the smoke test
# (the v76/Package/UnpickedPackages call signs requests with both as headers).
COMMUNITY_ID = int(os.environ.get("COMMUNITY_ID", "101"))
COMMUNITY_UNIT_ID = int(os.environ.get("COMMUNITY_UNIT_ID", "1001"))

REFRESH_MARGIN_SEC = 30


def _read(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text().strip() or None


def _write(path: Path, token: str) -> None:
    path.write_text(token + "\n")
    path.chmod(0o600)


def _short(token: str) -> str:
    return token[:12] + "..." if token else "<empty>"


async def _ensure_access(api: GoodLifeApi) -> str:
    """Return a fresh access token, refreshing through the file store if needed."""
    access = _read(TOKEN_FILE)
    refresh = _read(REFRESH_FILE)

    if access and jwt_valid_structure(access):
        remaining = jwt_exp(access) - int(time.time())
        if remaining > REFRESH_MARGIN_SEC:
            return access
        print(f"[auto-refresh] access token has {remaining}s left, rotating", file=sys.stderr)

    if not refresh:
        raise SystemExit(f"no refresh token at {REFRESH_FILE}; run 'login <phone>' first")

    bundle = await api.refresh_token(refresh)
    _write(TOKEN_FILE, bundle.access_token)
    _write(REFRESH_FILE, bundle.refresh_token)
    print(f"[refresh] new access exp={bundle.expired}", file=sys.stderr)
    return bundle.access_token


async def cmd_refresh(api: GoodLifeApi) -> None:
    refresh = _read(REFRESH_FILE)
    if not refresh:
        raise SystemExit(f"no refresh token at {REFRESH_FILE}")
    bundle = await api.refresh_token(refresh)
    _write(TOKEN_FILE, bundle.access_token)
    _write(REFRESH_FILE, bundle.refresh_token)
    print(
        json.dumps(
            {
                "user_id": bundle.user_id,
                "expired": bundle.expired,
                "access": _short(bundle.access_token),
                "refresh": _short(bundle.refresh_token),
            },
            indent=2,
        )
    )


async def cmd_unpicked(api: GoodLifeApi) -> None:
    access = await _ensure_access(api)
    items = await api.unpicked_packages(access, COMMUNITY_ID, COMMUNITY_UNIT_ID)
    print(f"未領包裹: {len(items)} 筆 (community={COMMUNITY_ID} unit={COMMUNITY_UNIT_ID})")
    for item in items:
        placement = (item.get("packagePlacement") or {}).get("packagePlacementName", "-")
        contact = item.get("toContactInfo") or {}
        print(
            f"  #{item.get('packageNo', '?')} id={item['packageId']} "
            f"放={placement} 收={contact.get('name', '?')} "
            f"到={item.get('checkedInDate', '?')}"
        )


async def cmd_qr(api: GoodLifeApi, out_path: Path | None) -> None:
    access = await _ensure_access(api)
    data = await api.create_checkout_verification_code(access, COMMUNITY_ID, COMMUNITY_UNIT_ID)
    code = data.get("verificationCode")
    expires = data.get("expiredTime")
    print(f"code={code} expires={expires}")

    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    if out_path:
        out_path.write_bytes(png)
        print(f"wrote {out_path} ({len(png)} bytes)")
    else:
        print(f"image_b64[:60]={base64.b64encode(png).decode()[:60]}...")


async def cmd_detail(api: GoodLifeApi, package_id: int) -> None:
    access = await _ensure_access(api)
    data = await api.package_detail(access, COMMUNITY_ID, COMMUNITY_UNIT_ID, package_id)
    # Trim the dump to the fields detail adds on top of the list endpoint, plus
    # enough context to confirm we addressed the right package.
    summary = {
        "packageId": data.get("packageId"),
        "packageNo": data.get("packageNo"),
        "packageStatusId": data.get("packageStatusId"),
        "barcode": data.get("barcode"),
        "remark": data.get("remark"),
        "packageTags": data.get("packageTags"),
        "logistics": data.get("logistics"),
        "fileInfos": [
            {"fileNo": f.get("fileNo"), "url": f.get("fileUrl")}
            for f in (data.get("fileInfos") or [])
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


async def cmd_me(api: GoodLifeApi) -> None:
    access = await _ensure_access(api)
    info = await api.member_info(access)
    units = info.get("communityUnits") or []
    print(
        json.dumps(
            {
                "name": info.get("name"),
                "memberId": info.get("memberId"),
                "communityUnits": [
                    {
                        "communityId": u.get("communityId"),
                        "communityUnitId": u.get("communityUnitId"),
                        "communityName": u.get("communityName"),
                        "shortAddress": u.get("shortAddress"),
                    }
                    for u in units
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def cmd_login(api: GoodLifeApi, phone: str) -> None:
    """Full SMS login flow. Prompts for the 6-digit code on stdin."""
    if not phone.startswith("+"):
        phone = "+886" + phone.lstrip("0") if phone.startswith("0") else "+" + phone

    print(f"[1/3] SendVerifySms to {phone}", file=sys.stderr)
    verify_id = await api.send_verify_sms(phone)
    print(f"[1/3] verify_id={verify_id}", file=sys.stderr)

    code = input("輸入手機收到的 6 碼驗證碼: ").strip()
    if not code:
        raise SystemExit("no code entered")

    print("[2/3] verifySmsCode", file=sys.stderr)
    verify_token = await api.verify_sms_code(verify_id, code)

    print("[3/3] Member/Login", file=sys.stderr)
    bundle = await api.member_login(verify_token)
    _write(TOKEN_FILE, bundle.access_token)
    _write(REFRESH_FILE, bundle.refresh_token)
    print(
        json.dumps(
            {
                "user_id": bundle.user_id,
                "expired": bundle.expired,
                "tokens_written": [str(TOKEN_FILE), str(REFRESH_FILE)],
            },
            indent=2,
        )
    )


async def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__, file=sys.stderr)
        return 1
    cmd, *rest = argv

    async with aiohttp.ClientSession() as session:
        api = GoodLifeApi(session)
        try:
            if cmd == "refresh":
                await cmd_refresh(api)
            elif cmd == "unpicked":
                await cmd_unpicked(api)
            elif cmd == "qr":
                out = Path(rest[0]) if rest else Path("qr.png")
                await cmd_qr(api, out)
            elif cmd == "detail":
                if not rest:
                    raise SystemExit("detail requires a packageId")
                await cmd_detail(api, int(rest[0]))
            elif cmd == "me":
                await cmd_me(api)
            elif cmd == "login":
                if not rest:
                    raise SystemExit("login requires a phone number (e.g. +886912345678)")
                await cmd_login(api, rest[0])
            else:
                print(f"unknown command: {cmd}", file=sys.stderr)
                print(__doc__, file=sys.stderr)
                return 1
        except AuthRejected as err:
            print(f"auth rejected: {err}", file=sys.stderr)
            return 2
        except ApiResponseError as err:
            print(f"api error: {err} (code={err.code})", file=sys.stderr)
            return 3
        except NetworkError as err:
            print(f"network error: {err}", file=sys.stderr)
            return 4
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
