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

**Don't.** Refresh tokens rotate on every use; whichever HA refreshes second will be kicked into the `auth_needed` state.
