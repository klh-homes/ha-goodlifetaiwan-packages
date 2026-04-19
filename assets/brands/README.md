# Brands-repo handoff (optional, likely deferred)

These files are staged here in case the brand icon for this integration is ever submitted to [`home-assistant/brands`](https://github.com/home-assistant/brands), the canonical location Home Assistant's frontend fetches icons from.

The current icon references the 中保好生活 app's brand elements (the "好" glyph + color palette). The brands repo's submission policy requires trademark ownership or authorization, which this project does not have — so realistically these files sit here as a regeneration source rather than an imminent PR. Without the submission, HA UI does not display a brand icon on integration cards, which is the status-quo.

## If submission ever becomes appropriate

Open a PR against `home-assistant/brands` adding these two files:

| Source file (here) | Destination path in `home-assistant/brands` |
| --- | --- |
| `assets/brands/icon.png` (256×256) | `custom_integrations/goodlifetaiwan/icon.png` |
| `assets/brands/icon@2x.png` (512×512) | `custom_integrations/goodlifetaiwan/icon@2x.png` |

No `logo.png` / `logo@2x.png` are submitted — the icon is already a wordmark-style glyph and a separate logo would be redundant for this integration.

## After the brands PR merges

In this repo, follow up with a one-line change:

- Remove the `ignore: brands` line from `.github/workflows/validate.yaml` (under the `hacs/action@main` step).

HA frontend pulls icons from `brands.home-assistant.io`. Users typically see the new icon after a browser refresh; HA restart not strictly required.

## Regenerating

The source is `assets/icon.png` (512×512, authored by the maintainer). To regenerate the 256×256 variant:

```python
from PIL import Image
Image.open("assets/icon.png").resize((256, 256), Image.LANCZOS).save(
    "assets/brands/icon.png", "PNG", optimize=True
)
```
