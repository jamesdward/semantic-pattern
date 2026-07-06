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
