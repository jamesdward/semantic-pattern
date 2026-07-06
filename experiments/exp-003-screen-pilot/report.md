# exp-003 pilot — first real-camera contact (screen arm)

**Status:** pilot, 24 photos, one surface (001-s0 on an Apple Studio Display), one phone.
Headline first, honestly: **0 of 24 photos recognised — the v0 pipeline fails completely on
real photographs.** That was a plausible outcome of first contact, and the point of a pilot
is the diagnosis, which is clean and mostly encouraging. Raw rows in
[`raw_results.csv`](raw_results.csv); the auto-summary is [`summary.md`](summary.md);
photos are the TrueTone × brightness sweep recorded in `photos/manifest.yaml` (raw images
kept out of git; ~102 MB).

## The failure, decomposed

Post-hoc diagnosis on straight-on frames (manual analysis, code inline in the session; the
locator prototype below is ~10 lines of classical CV):

**1. The dominant failure is missing scene localisation — not measurement.**
Every synthetic battery fragment was 100% pattern; every real photo is mostly *scene*
(black bezel, white page margin, desk — the pattern square is ~25% of the frame even in
the fills-frame shots). The recogniser's two-colour classification then splits the scene
into light-vs-dark instead of ink-vs-ink and every downstream number is garbage
(aggregate ≈ 0.02). With a trivial classical green-region locator (channel dominance +
morphological close + largest component) feeding the *unchanged* pipeline, measurements
land in the right neighbourhood immediately. Spec §8 step 1 lists normalisation
(perspective, scale, orientation) but never says "first find the surface in the scene" —
recorded as **SI-025**. This is the single highest-value fix and it is classical and small.

**2. Band segmentation is fragile on localised real crops.**
The same 5-band surface segmented into 6, 14 and 23 "bands" across three straight-on
shots (moiré, camera sharpening and residual perspective create false boundaries). The
cascade ratio comes out 1.87 / 1.28 / 1.70 against the committed 1.94 ± 0.03. Needs
perspective rectification (the page's white quad is right there to rectify against) and
boundary-merge robustification before the ratio is trustworthy from photos.

**3. Duty is biased upward by display bloom / camera exposure, past the tolerance.**
Measured 0.326–0.402 vs committed 0.31 ± 0.015 (worst at 25% display brightness, where
exposure noise is largest). The geometric signature *is* visible — but the ±0.015
tolerance was calibrated on synthetic renders and does not price real capture. Field
tolerances (or a bias model for emissive surfaces) are an open calibration task.

**4. Colour shifts warm and the 001 sheet has no relationship path to absorb it.**
Measured inks (BGR): dark ≈ (37–47, 101–109, 70–75) vs sheet (48, 91, 46); light ≈
(131–144, 208–216, 162–174) vs (129, 184, 115) — a strong warm/bright shift, remarkably
similar with TrueTone on and off, i.e. dominated by camera auto-WB and display gamut
rather than by TrueTone itself. The absolute ΔE gate (10) fails everywhere. exp-002's
answer to exactly this — colour as a diagonal-gain *relationship* (SI-020) — exists **only
for grid sheets**; 001's `colour_pair` scores absolute-only. Porting two-path matching to
band grammars is an obvious, already-designed fix.

**5. Phase stays origin-locked (known: SI-014)** — interior framings measure a meaningless
phase step, as predicted; nothing new, but the field data confirms it applies to every
real photo that doesn't capture the cascade origin.

## What the pilot answers (and doesn't)

- **Phase-4b question (a) — is real white balance diagonal?** Partially informative:
  the ink shift is broadly channel-wise (warm gain), but dark and light inks do not imply
  exactly the same gain (bloom brightens the light ink disproportionately) — a diagonal
  gain plus an additive bloom term fits better than gain alone. Real data for SI-020's
  model question, from n=1 display; print data still needed.
- **(b) clipping and (c) print gamut:** untouched — this was a screen, not print, and 002
  was not photographed. The print arm stays open (printer permitting).
- **L3:** emphatically still open. v0 does not recognise real photographs.

## Next (in value order)

1. **Localisation stage** in the recogniser proper (classical, ~small): find candidate
   pattern regions, rectify against the surrounding page quad where present, then run the
   existing pipeline per region. Re-run this pilot's photos unchanged as the acceptance test.
2. **Port two-path (relationship) colour matching to band sheets** — designed, tested
   machinery from exp-002.
3. Field-calibrated tolerances for duty/ratio under camera capture (needs the re-run's data).
4. Then the 002 sheets on screen (moiré vs the grid measurers, ink set under camera WB),
   and the print arm when hardware allows.
