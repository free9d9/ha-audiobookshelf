# home-assistant/brands submission

These are the assets for a PR to https://github.com/home-assistant/brands,
placed at `custom_integrations/audiobookshelf/`.

| File | Spec | Actual |
| --- | --- | --- |
| `icon.png` | 256x256, PNG, square, trimmed, transparency preferred | 256x256 RGBA |
| `icon@2x.png` | 512x512, PNG, square, trimmed | 512x512 RGBA |

Source: the official Audiobookshelf mark, `client/static/Logo.png` from
[advplyr/audiobookshelf](https://github.com/advplyr/audiobookshelf) (GPL-3.0). Downscaled
with Lanczos; alpha preserved; no recolouring or cropping beyond an alpha-bbox trim.

No `logo.png` is supplied: Audiobookshelf's mark is square, so Home Assistant falls back to
the icon, which is what we want. No `dark_` variants either — the mark is predominantly
orange rather than white, so it reads on light and dark backgrounds alike.
