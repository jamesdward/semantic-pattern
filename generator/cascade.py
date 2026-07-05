"""Bar-cascade surface generator (grammar 001).

Renders a *bar cascade* surface from a loaded grammar sheet
(``grammars/bar-cascade-001.yaml``). Every rule below is read FROM the sheet's
``combination_rules`` / ``colour_system`` / ``structure`` slots -- nothing about
the cascade is hardcoded here except the one value the sheet does not carry
(the finest stripe period; see BASE_PERIOD_RELATIVE and SPEC-ISSUES SI-011).

Geometry (Pattern Grammar Audit 001, section 1):

  * Vertical bands of horizontal bars. Bands are vertical strips arranged
    left-to-right; each band is ``module_px`` pixels wide (the module IS the
    band width -- audit s1 "fixed module", sheet ``module_width_relative``).
  * Within a band every pixel column is identical; the stripe pattern varies
    down the vertical (y) axis. Band boundaries are vertical lines at
    x = k * module_px.
  * Band b (b = 0 .. n_bands-1) has stripe period
        period_b = base_period * frequency_ratio ** b
    so the period increases left-to-right (sheet
    ``structure.band.cascade_direction: increasing_period``; audit s1 cascade).
  * Within each period the first ``duty_cycle_light`` fraction is the light ink,
    the remainder the dark ink (audit s1 duty cycle; sheet ``duty_cycle_light``).
  * Adjacent bands are offset vertically by ``phase_step`` x the FINER band's
    period (audit s1 "phase rule"). Finer = smaller period = the lower-indexed
    band, so the phase accumulates:
        phase_0 = 0 ;  phase_b = phase_{b-1} + phase_step * period_{b-1}
  * The whole surface is rotated by ``orientation_deg`` (audit s4 tuned 0.7 deg).

Rendering choices (documented per task):

  * COLOUR ORDER: arrays are BGR uint8 -- the project-wide convention, because
    the recogniser reads surfaces with cv2.imread (which returns BGR) and this
    generator writes them with cv2.imwrite. ``render`` returns (H, W, 3) BGR.
  * ROTATION: rendered ANALYTICALLY, not by warping a finished canvas. For each
    (supersampled) output pixel we inverse-rotate its coordinate about the image
    centre and evaluate the band/stripe function at that point. This keeps the
    bars filling the whole frame at any angle (the audit sample is a tilted
    *field*, not a tilted tile leaving ground corners). Coordinates that fall
    outside the finite band range are clamped to the nearest band so the frame
    stays filled rather than showing ground triangles at the corners.
  * ANTI-ALIASING: the pattern is evaluated on a grid ``supersample`` times
    denser in each axis, then area-averaged down (cv2.INTER_AREA). Integer
    factor + fixed inputs => deterministic, byte-identical output.

Determinism (README principle 4, spec s8): same sheet + params + seed produce a
byte-identical array and PNG. The v1 cascade is fully deterministic; ``seed`` is
threaded and reserved for future per-instance variation (variation_model).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

# The finest band's stripe period as a fraction of the module (band width).
# The sheet declares the module (band width) and the cascade *ratios*, but not
# the absolute finest period -- that is a free scale choice (SPEC-ISSUES SI-011).
# Value from audit 001 s1: band 0 period 14.9 px / band width 191 px = 0.078.
BASE_PERIOD_RELATIVE = 0.078


def _hex_to_bgr(value: str) -> np.ndarray:
    """'#RRGGBB' -> np.array([B, G, R], uint8). Colour space per sheet (sRGB-hex)."""
    value = value.lstrip("#")
    r = int(value[0:2], 16)
    g = int(value[2:4], 16)
    b = int(value[4:6], 16)
    return np.array([b, g, r], dtype=np.uint8)


def _ink(sheet: dict, ink_id: str) -> np.ndarray:
    """Look up an ink by id in colour_system.inks and return its BGR triple."""
    for ink in sheet["colour_system"]["inks"]:
        if ink["id"] == ink_id:
            return _hex_to_bgr(ink["value"])
    raise KeyError(f"ink '{ink_id}' not found in sheet colour_system.inks")


def band_periods(sheet: dict, n_bands: int, module_px: int) -> np.ndarray:
    """Per-band stripe periods in pixels (audit s1 cascade).

    period_b = base_period * frequency_ratio ** b, base_period integer-rounded
    from BASE_PERIOD_RELATIVE * module_px so the finest band lands on a clean
    pixel count for measurement.
    """
    ratio = float(sheet["combination_rules"]["frequency_ratio"])
    base_period = max(2.0, round(BASE_PERIOD_RELATIVE * module_px))
    return base_period * ratio ** np.arange(n_bands, dtype=np.float64)


def band_phases(sheet: dict, periods: np.ndarray) -> np.ndarray:
    """Per-band vertical phase offsets in pixels (audit s1 phase rule).

    phase_0 = 0 ; phase_b = phase_{b-1} + phase_step * period_{b-1}. The offset
    uses the finer (lower-indexed, shorter-period) band's period.
    """
    phase_step = float(sheet["combination_rules"]["phase_step"])
    phases = np.zeros_like(periods)
    for b in range(1, len(periods)):
        phases[b] = phases[b - 1] + phase_step * periods[b - 1]
    return phases


def render(
    sheet: dict,
    *,
    n_bands: int,
    module_px: int,
    seed: int,
    size=None,
    supersample: int = 3,
    orientation_deg=None,
) -> np.ndarray:
    """Render a bar-cascade surface from ``sheet``; return (H, W, 3) BGR uint8.

    Parameters
      sheet         a sheet dict as returned by sheets.load_sheet.
      n_bands       number of vertical bands to render.
      module_px     band width in pixels (the module; audit s1).
      seed          reserved for instance variation; threaded, deterministic v1.
      size          optional (H, W) output shape. Default: square with
                    W = n_bands * module_px so each band is exactly module_px
                    wide. If W differs from n_bands*module_px, x outside the band
                    range is clamped to the nearest band.
      supersample   anti-alias factor (>=1); pattern evaluated S x denser then
                    area-averaged down.
      orientation_deg  override the sheet's orientation (audit s4 0.7 deg). Pass
                    0.0 to render axis-aligned (used by measurement tests).
    """
    if supersample < 1:
        raise ValueError("supersample must be >= 1")

    # Reserved for future stochastic instance variation (variation_model); the
    # v1 cascade is deterministic, but we thread the seed as the spec requires.
    _rng = np.random.default_rng(seed)

    if size is None:
        width = n_bands * module_px
        height = width
    else:
        height, width = int(size[0]), int(size[1])

    rules = sheet["combination_rules"]
    duty = float(rules["duty_cycle_light"])
    if orientation_deg is None:
        orientation_deg = float(rules["orientation_deg"])
    theta = np.deg2rad(float(orientation_deg))

    periods = band_periods(sheet, n_bands, module_px)
    phases = band_phases(sheet, periods)

    light = _ink(sheet, "light")
    dark = _ink(sheet, "dark")

    s = supersample
    # Supersampled pixel-centre coordinates, expressed in final-image units.
    xs = (np.arange(width * s, dtype=np.float64) + 0.5) / s
    ys = (np.arange(height * s, dtype=np.float64) + 0.5) / s
    gx, gy = np.meshgrid(xs, ys)  # both (H*s, W*s)

    # Inverse-rotate each output coordinate about the image centre to find the
    # point in the un-rotated pattern frame (displayed = pattern rotated by
    # +theta, so pattern coord = R(-theta) * (out - centre) + centre).
    cx, cy = width / 2.0, height / 2.0
    dx = gx - cx
    dy = gy - cy
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    px = cos_t * dx + sin_t * dy + cx
    py = -sin_t * dx + cos_t * dy + cy

    # Band index from x; clamp to keep the frame filled at tilted corners.
    band = np.floor(px / module_px).astype(np.int64)
    np.clip(band, 0, n_bands - 1, out=band)

    period_at = periods[band]
    phase_at = phases[band]

    # Fractional position within the stripe period; light where < duty.
    frac = np.mod(py - phase_at, period_at) / period_at
    light_mask = frac < duty

    hi = np.where(light_mask[..., None], light, dark).astype(np.uint8)

    if s == 1:
        return hi
    # Area-average down: deterministic, byte-identical for fixed inputs.
    return cv2.resize(hi, (width, height), interpolation=cv2.INTER_AREA)


def render_png(sheet: dict, path, **params) -> np.ndarray:
    """Render and write a PNG to ``path``. Returns the rendered BGR array.

    Byte-identical for identical sheet + params (README principle 4).
    """
    surface = render(sheet, **params)
    path = str(path)
    ok = cv2.imwrite(path, surface)
    if not ok:
        raise IOError(f"failed to write PNG to {path}")
    return surface


def render_svg(
    sheet: dict,
    *,
    n_bands: int,
    module_px: int,
    size=None,
    orientation_deg=None,
) -> str:
    """Minimal stdlib-only SVG of the same cascade (nice-to-have).

    Emits one <rect> per light bar per band inside a group rotated by
    orientation_deg about the centre, over a ground-coloured background. The
    analytic PNG path is the canonical renderer; this is for scalable eyeballing.
    """
    if size is None:
        width = n_bands * module_px
        height = width
    else:
        height, width = int(size[0]), int(size[1])

    rules = sheet["combination_rules"]
    duty = float(rules["duty_cycle_light"])
    if orientation_deg is None:
        orientation_deg = float(rules["orientation_deg"])

    periods = band_periods(sheet, n_bands, module_px)
    phases = band_phases(sheet, periods)

    def hexval(ink_id):
        for ink in sheet["colour_system"]["inks"]:
            if ink["id"] == ink_id:
                return ink["value"]
        raise KeyError(ink_id)

    light_hex = hexval("light")
    dark_hex = hexval("dark")

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        # Ground = dark ink (2/3 mass); light bars painted over it.
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{dark_hex}"/>',
        f'<g transform="rotate({orientation_deg} {width / 2} {height / 2})">',
    ]
    # Over-draw margin so tilted bars still cover the frame edges.
    y0, y1 = -height, 2 * height
    for b in range(n_bands):
        x = b * module_px
        p = periods[b]
        light_h = duty * p
        phase = phases[b]
        # First light bar start at or below y0.
        k0 = int(np.floor((y0 - phase) / p))
        k1 = int(np.ceil((y1 - phase) / p))
        for k in range(k0, k1 + 1):
            ry = phase + k * p
            parts.append(
                f'<rect x="{x:.3f}" y="{ry:.3f}" width="{module_px}" '
                f'height="{light_h:.3f}" fill="{light_hex}"/>'
            )
    parts.append("</g></svg>")
    return "\n".join(parts)


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a bar-cascade surface from a grammar sheet."
    )
    parser.add_argument("sheet", help="path to a grammar sheet YAML")
    parser.add_argument("out", help="output PNG path")
    parser.add_argument("--bands", type=int, default=5, help="number of bands")
    parser.add_argument("--module", type=int, default=200, help="band width in px")
    parser.add_argument("--seed", type=int, default=0, help="instance seed")
    parser.add_argument(
        "--orientation",
        type=float,
        default=None,
        help="override orientation in degrees (default: sheet value)",
    )
    parser.add_argument("--supersample", type=int, default=3)
    args = parser.parse_args(argv)

    # Imported here so the module has no import-time dependency on the loader.
    from sheets import load_sheet

    sheet = load_sheet(args.sheet)
    render_png(
        sheet,
        args.out,
        n_bands=args.bands,
        module_px=args.module,
        seed=args.seed,
        orientation_deg=args.orientation,
        supersample=args.supersample,
    )
    print(f"wrote {args.out} ({args.bands} bands, module {args.module}px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
