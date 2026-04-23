# Current Mainline Bundle

This standalone bundle is aligned with the repository's current **mirror-view mainline**.

## Model

- Demo weight: `demo/modulecd_bsd_demo/weights/bsd_demo.pt`
- Source weight in main repo: `weights/stage_c_laneprojected_continue.pt`
- Summary JSON: `demo/modulecd_bsd_demo/weights/bsd_demo.json`

This model comes from continuing Stage C specialization on the mirror-view collection with lane-projected masks.

## Camera Profile

The current mainline assumes a mirror-like side/rear camera layout:

- Left camera:
  - `loc = [1.05, -1.02, 1.22]`
  - `rot = [-3.5, -148.0, 0.0]`
- Right camera:
  - `loc = [1.05, 1.02, 1.22]`
  - `rot = [-3.5, 148.0, 0.0]`
- `fov = 72`
- Runtime image size: `960x540`

These values are included in `demo/modulecd_bsd_demo/config.toml` as the current reference profile.

## Blind-Spot Template

The default fallback blind-spot templates are tuned for the mirror-view camera profile:

- Left:
  - `center_x = 0.24`
  - `top_y_base = 0.52`
  - `bot_half_w_base = 0.22`
  - `top_half_w_base = 0.09`
- Right:
  - `center_x = 0.76`
  - `top_y_base = 0.52`
  - `bot_half_w_base = 0.22`
  - `top_half_w_base = 0.09`

## Data Reference

The current mainline was switched to match the mirror-view dataset collected with refined lane-projected masks:

- Raw dataset: `bsd_remote_multimap_mirror_collect_v1`
- Processed dataset: `bsd_remote_multimap_mirror_collect_v1_caronly`

Key facts from that collection:

- `800` left images
- `800` right images
- `1583` lane-projected masks
- `17` adaptive fallback masks

## Why This Matters For The Demo

The standalone demo does not require CARLA, but it still depends on the model's training distribution. This bundle now matches the mirror-view mainline in:

- weight
- image size
- blind-spot template
- documented camera reference

If you later train a new compatible weight, you can replace `bsd_demo.pt` and `bsd_demo.json` directly.
