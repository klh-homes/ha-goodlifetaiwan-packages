# Installation

## HACS (recommended)

1. In HACS, open **Integrations** → three-dot menu → **Custom repositories**.
2. Add `https://github.com/klh-homes/ha-goodlifetaiwan-packages` with category **Integration**.
3. Search for **GoodLifeTaiwan Packages** and install.
4. Restart Home Assistant.
5. **Settings → Devices & Services → Add Integration**, then search for _GoodLifeTaiwan_.

## Manual

1. Copy the `custom_components/goodlifetaiwan/` directory into your HA `config/custom_components/` folder.
2. Restart Home Assistant.
3. Add the integration from **Settings → Devices & Services**.

## Requirements

- Home Assistant ≥ 2024.1.0
- Python ≥ 3.12 (bundled with HA)
- Python packages (installed automatically): `qrcode[pil]`

## First-run auth

On first setup the integration asks for the phone number registered with 中保好生活 and sends you an SMS verification code. Enter the 6-digit code within 3 minutes. Tokens are stored in `.storage/goodlifetaiwan_tokens_<entry_id>` and are covered by HA's built-in backup.

## Running two HA instances on the same account

Fine. Live-tested: the upstream server issues new refresh tokens on every `RefreshMemberToken` call but does not invalidate the old one. Each consumer keeps a valid token up to its 90-day expiry, so the mobile app, production HA, dev HA, and even two HA instances that happen to share a token file all coexist without kicking each other out.

What the integration still assumes: one entry per HA instance (i.e., don't add the same phone number twice on one HA). That's just HA hygiene, not a server restriction.
