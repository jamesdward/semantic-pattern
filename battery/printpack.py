"""Print-pack generator for the print-and-photograph field battery (Phase 4b).

Spec s11 L3 ("Field-proven") depends on recognition "under the hostile-conditions
test battery (print, camera, light, angle, damage)". The synthetic battery
(``battery.run``) stands in for those axes deterministically; this module produces
the *fixed physical input* for the real thing: a small set of A4 sheets a person
prints at 100% and photographs. It is the experiment's committed input, so the
generated ``print-pack/`` is version-controlled, not gitignored.

What it emits (deterministically -- README principle 4):

  * six single-page PNGs sized EXACTLY for A4 at 300 dpi (2480 x 3508 px):
      - three bar-cascade-001 surfaces (seeds 0, 1, 2), ~170 mm square, 5 bands;
      - three iso-002 surfaces (seeds 0, 1, 2; each seed picks a different ink
        subset), a 12 x 8 cell grid ~170 mm wide.
    Each surface is centred with a white margin, and a small human-readable label
    is placed in the BOTTOM PAGE MARGIN, strictly outside the pattern rectangle.
  * ``print-pack/INSTRUCTIONS.md`` -- the print/photograph/manifest protocol.
  * ``photos/manifest.template.yaml`` (+ an empty ``photos/`` for the captures).

On the label vs the spec's "no marks inside the pattern" rule (spec s2, principle
2: "no payload encoding, no bounded marks, no fiducial code regions -- the whole
surface is the pattern"): the label is NOT part of the surface. It sits in the
page margin, never touching the pattern rectangle (``test_ingest`` asserts the two
bounding boxes are disjoint), and carries only human bookkeeping (which seed this
is, and "print at 100%"). Nothing about it is measured or fed to the recogniser --
the recogniser only ever sees a photograph of the pattern rectangle, cropped by
the photographer. It is exactly the "human-readable fallback" the spec calls a
conformance requirement for identity-bearing artefacts (spec s10 Accessibility),
kept off the signature-bearing surface on purpose.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from sheets import load_sheet
from generator import cascade
from generator import grid

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAMMARS = REPO_ROOT / "grammars"
SHEET_001 = GRAMMARS / "bar-cascade-001.yaml"
SHEET_002 = GRAMMARS / "iso-002.yaml"

# --- A4 at 300 dpi (portrait) ------------------------------------------------
DPI = 300
PAGE_W = 2480      # 210 mm * 300 / 25.4, rounded
PAGE_H = 3508      # 297 mm * 300 / 25.4, rounded
MM = DPI / 25.4    # pixels per millimetre

# Pattern target width ~170 mm at 300 dpi.
PATTERN_PX = round(170 * MM)   # 2008 px

# 001: 5 bands across ~170 mm -> a band module of ~402 px (5 * 402 = 2010 px).
BANDS_001 = 5
MODULE_001 = round(PATTERN_PX / BANDS_001)     # 402

# 002: 12 x 8 cells, ~170 mm across the 12 columns -> module ~167 px.
COLS_002, ROWS_002 = 12, 8
MODULE_002 = round(PATTERN_PX / COLS_002)      # 167

# A bottom band of the page reserved for the label (never overlaps the pattern).
LABEL_BAND_PX = 380
SEEDS = (0, 1, 2)

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _surface_specs():
    """Return the ordered list of surface specs (grammar x seed) to emit.

    Each spec is a plain dict; ``render`` is a zero-arg callable returning the
    (H, W, 3) BGR pattern array so page assembly is generator-agnostic.
    """
    sheet_001 = load_sheet(SHEET_001)
    sheet_002 = load_sheet(SHEET_002)
    specs = []
    for seed in SEEDS:
        specs.append({
            "surface_id": f"001-s{seed}",
            "grammar": "bar-cascade-001",
            "seed": seed,
            "render": (lambda s=seed: cascade.render(
                sheet_001, n_bands=BANDS_001, module_px=MODULE_001, seed=s)),
        })
    for seed in SEEDS:
        specs.append({
            "surface_id": f"002-s{seed}",
            "grammar": "iso-002",
            "seed": seed,
            "render": (lambda s=seed: grid.render(
                sheet_002, cols=COLS_002, rows=ROWS_002, module_px=MODULE_002, seed=s)),
        })
    return specs


def _label_lines(spec) -> list[str]:
    """The two human-readable label lines drawn in the bottom page margin."""
    return [
        f"{spec['surface_id']}   seed {spec['seed']}   grammar {spec['grammar']}",
        "PRINT AT 100% / ACTUAL SIZE  -  no fit-to-page, no scaling",
    ]


def build_page(spec) -> tuple[np.ndarray, dict]:
    """Compose one A4 page for ``spec``; return (page_bgr, meta).

    The pattern is centred horizontally and centred vertically in the region
    ABOVE a reserved bottom label band, so the label bounding box is always
    strictly below (disjoint from) the pattern rectangle. ``meta`` records both
    bounding boxes so the disjointness is machine-checkable (test requirement).
    """
    pattern = spec["render"]()
    ph, pw = pattern.shape[:2]

    page = np.full((PAGE_H, PAGE_W, 3), 255, np.uint8)

    avail_h = PAGE_H - LABEL_BAND_PX
    x0 = (PAGE_W - pw) // 2
    y0 = (avail_h - ph) // 2
    if x0 < 0 or y0 < 0:
        raise ValueError(
            f"pattern {pw}x{ph} does not fit A4 with the reserved label band")
    x1, y1 = x0 + pw, y0 + ph
    page[y0:y1, x0:x1] = pattern
    pattern_bbox = (x0, y0, x1, y1)

    # --- label in the bottom band, horizontally centred, disjoint from pattern
    lines = _label_lines(spec)
    scale, thickness = 1.4, 3
    sizes = [cv2.getTextSize(t, _FONT, scale, thickness) for t in lines]
    line_gap = 26
    text_h = sum(s[0][1] for s in sizes) + line_gap * (len(lines) - 1)
    band_top = avail_h
    block_top = band_top + (LABEL_BAND_PX - text_h) // 2

    ly = block_top
    label_x0, label_x1 = PAGE_W, 0
    label_y0 = block_top
    for text, ((tw, th), _base) in zip(lines, sizes):
        tx = (PAGE_W - tw) // 2
        ly += th
        cv2.putText(page, text, (tx, ly), _FONT, scale, (0, 0, 0), thickness,
                    cv2.LINE_AA)
        label_x0 = min(label_x0, tx)
        label_x1 = max(label_x1, tx + tw)
        ly += line_gap
    label_bbox = (label_x0, label_y0, label_x1, block_top + text_h)

    meta = {
        "surface_id": spec["surface_id"],
        "grammar": spec["grammar"],
        "seed": spec["seed"],
        "pattern_bbox": pattern_bbox,
        "label_bbox": label_bbox,
        "pattern_shape": [int(ph), int(pw)],
    }
    return page, meta


def _bboxes_disjoint(a, b) -> bool:
    """True if axis-aligned boxes (x0,y0,x1,y1) do not overlap."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0


INSTRUCTIONS = """\
# Print pack -- Phase 4b (print-and-photograph field battery)

These six sheets are the fixed physical input for the L3 "field-proven" test
(spec s11). You print them, photograph them under a spread of real conditions,
and the recogniser is run on the photos UNCHANGED. The point is to find out what
real print + camera + light + angle do to recognition -- so photograph honestly,
including the awkward conditions. A photo that fails to recognise is a RESULT, not
a mistake; do not retake it to "make it work".

## 1. Print (do this once, note what you used)

- Print every PNG in this folder at **100% / actual size**. In the print dialog:
  turn **OFF** "fit to page", "shrink to fit" and any scaling -- it MUST say 100%.
- Use **plain white paper** for the first pass. (If you want a second pass on
  matte photo paper later, do it -- just record `paper:` in the manifest.)
- Write down your **printer model** -- you will put it in the manifest.
- The label in the bottom margin of each sheet says which surface it is
  (e.g. `001-s0`) and "print at 100%". That label is NOT part of the pattern; it
  is only there so you can tell the sheets apart. Do not photograph it as if it
  were the pattern -- frame the pattern square/rectangle itself.

## 2. Photograph (the condition matrix)

For **each** of the six printed sheets, take a spread of photos covering:

- **3 lightings:** `daylight` (near a window, no direct sun on the paper) ·
  `warm_indoor` (a warm bulb / tungsten-ish room light) ·
  `cool_led_or_shade` (cool white LED, or open shade outdoors).
- **3 angles:** `0` (straight on, phone parallel to paper) · `30` (~30 deg
  tilt) · `60` (~60 deg tilt -- a steep, hostile angle).
- **2 distances:** `fills_frame` (the pattern fills most of the frame) ·
  `far_2m` (stand back ~2 m; the pattern is small in the frame).

That is 3 x 3 x 2 = 18 photos per sheet if you do the full matrix. If time is
short, prioritise: all three lightings straight-on and filling the frame first,
then add angles and distance. Partial coverage is fine -- the summary marks any
question it lacks data for as "insufficient data" rather than guessing.

Phone camera: use the **default** camera app, **auto** everything (auto WB, auto
exposure, HDR as it comes). Do NOT use a "document scan" mode -- that flattens
lighting and defeats the point. Hold steady; a blurred shot is a blurred shot.

## 3. File naming

Names carry NO meaning -- the manifest does. Let your phone name them
(`IMG_0001.jpg` ...), or use anything you like. Just make each filename unique and
put every file in the `photos/` folder.

## 4. Fill the manifest (this is the important part)

Copy `photos/manifest.template.yaml` to `photos/manifest.yaml` and add **one
entry per photo**. Each entry ties a filename to the surface it shows and the
conditions you shot it under:

```yaml
photos:
  - file: IMG_0001.jpg          # exact filename in photos/
    surface_id: 001-s0          # the id printed on the sheet's bottom label
    grammar: bar-cascade-001    # bar-cascade-001 for 001-* sheets, iso-002 for 002-*
    conditions:
      lighting: daylight        # daylight | warm_indoor | cool_led_or_shade
      angle_deg: 0              # 0 | 30 | 60
      distance: fills_frame     # fills_frame | far_2m
      printer: "Brand Model 123"  # your printer
      paper: plain              # plain | matte_photo
    notes: ""                   # anything worth remembering; free-form
```

`surface_id` MUST match the printed label exactly, and `grammar` MUST be
`bar-cascade-001` or `iso-002`. Everything else is logged verbatim.

## 5. Run the recogniser over the photos

From the repo root:

```sh
uv run python -m battery.ingest photos/ photos/manifest.yaml --out experiments/exp-003-print-photo/
```

That reads the manifest, runs the **identical** recognise pipeline on each photo
(no photo-special preprocessing), and writes `raw_results.csv` (one row per photo,
with the recogniser's verdict and, for the 002 sheets, the white-balance two-path
detail) and `summary.md` (per-condition tables plus the three Phase-4b questions,
each answered from the data or marked "insufficient data"). A missing file, a file
not in the manifest, or an unreadable image is recorded as a row status -- the run
never crashes.
"""

MANIFEST_TEMPLATE = """\
# Manifest for the print-and-photograph field battery (Phase 4b).
#
# Copy this file to photos/manifest.yaml and add one entry per photo you take.
# - file:       the exact filename in the photos/ folder (names carry no meaning).
# - surface_id: MUST match the label printed in the sheet's bottom margin
#               (001-s0..001-s2 for the bar-cascade-001 sheets, 002-s0..002-s2
#               for the iso-002 sheets).
# - grammar:    bar-cascade-001 (for the 001-* sheets) or iso-002 (for the 002-*).
# - conditions: how you shot it; logged verbatim into raw_results.csv.
#     lighting:  daylight | warm_indoor | cool_led_or_shade
#     angle_deg: 0 | 30 | 60         (straight-on / ~30 deg / ~60 deg)
#     distance:  fills_frame | far_2m
#     printer:   your printer model (free text)
#     paper:     plain | matte_photo | ...
# - notes:      free-form.
#
# The two entries below are EXAMPLES -- delete them and add your own.

surface_id: null        # optional default surface_id for entries that omit their own
photos:
  - file: IMG_0001.jpg
    surface_id: 001-s0
    grammar: bar-cascade-001
    conditions:
      lighting: daylight
      angle_deg: 0
      distance: fills_frame
      printer: "<your printer model>"
      paper: plain
    notes: ""
  - file: IMG_0002.jpg
    surface_id: 002-s0
    grammar: iso-002
    conditions:
      lighting: warm_indoor
      angle_deg: 30
      distance: fills_frame
      printer: "<your printer model>"
      paper: plain
    notes: "example second entry"
"""


def generate(out_dir, photos_dir=None) -> dict:
    """Generate the whole print pack. Returns a small summary dict.

    Writes the six page PNGs and INSTRUCTIONS.md under ``out_dir`` (default
    ``print-pack/``) and the manifest template + an empty capture folder under
    ``photos_dir`` (default ``<repo>/photos``). Deterministic: identical bytes on
    every run for identical grammars/generators.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    photos_dir = Path(photos_dir) if photos_dir else (REPO_ROOT / "photos")
    photos_dir.mkdir(parents=True, exist_ok=True)

    pages = []
    for spec in _surface_specs():
        page, meta = build_page(spec)
        # Cheap invariant: the label never touches the pattern (spec s2).
        if not _bboxes_disjoint(meta["pattern_bbox"], meta["label_bbox"]):
            raise AssertionError(
                f"label overlaps pattern for {meta['surface_id']}")
        path = out_dir / f"{spec['surface_id']}.png"
        if not cv2.imwrite(str(path), page):
            raise IOError(f"failed to write {path}")
        meta["png"] = str(path)
        pages.append(meta)

    (out_dir / "INSTRUCTIONS.md").write_text(INSTRUCTIONS)
    (photos_dir / "manifest.template.yaml").write_text(MANIFEST_TEMPLATE)
    # Keep the (otherwise empty) capture folder in version control.
    (photos_dir / ".gitkeep").write_text("")

    return {
        "out_dir": str(out_dir),
        "photos_dir": str(photos_dir),
        "pages": pages,
        "page_size": [PAGE_W, PAGE_H],
    }


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the print-and-photograph print pack (Phase 4b).")
    parser.add_argument("--out", default="print-pack",
                        help="output directory for the page PNGs + INSTRUCTIONS")
    parser.add_argument("--photos", default=None,
                        help="folder for the manifest template + captures "
                             "(default: <repo>/photos)")
    args = parser.parse_args(argv)
    summary = generate(args.out, args.photos)
    print(f"wrote {len(summary['pages'])} pages to {summary['out_dir']} "
          f"(A4 {summary['page_size'][0]}x{summary['page_size'][1]} px)")
    for p in summary["pages"]:
        print(f"  {p['surface_id']}: {p['png']}")
    print(f"manifest template + captures: {summary['photos_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
