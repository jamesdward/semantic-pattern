# exp-003 re-run — localisation + rectification + band colour, same 24 photos (Phase 10)

**Status:** the exp-003 pilot's own 24 screen photos, re-run through a recogniser that now
**locates** the surface in the scene, **rectifies** its perspective, and scores band colour by
the SI-020 two-path (white-balance-relationship) method ported to the 2-ink band grammar
(SI-027). One surface (`001-s0` on an Apple Studio Display), one phone, the TrueTone x brightness
x angle sweep of [`../exp-003-screen-pilot`](../exp-003-screen-pilot). Every number below is
recomputed from [`raw_results.csv`](raw_results.csv) (recognition summary / per-condition) and,
for the measured cascade ratio and duty, from the recogniser's per-photo **claim working**
(the run is deterministic, so the two agree); nothing is estimated.

The headline, honestly: **localisation + rectification turns 0/24 into 4/24 candidates and lifts
the mean target aggregate 10x (0.020 -> 0.201)** — the pilot's dominant failure (no scene
localisation, SI-025) is fixed. But **no photo reaches `identified`**, and the *residual* field
error on the two committed constants is **~2x tolerance at best and 5-16x at worst**: the cascade
ratio is recovered to within tolerance whenever band segmentation survives capture, and duty is
**systematically biased high by display bloom** past the synthetic +/-0.015 tolerance on every
frame. The thesis reading is in section 3.

## What changed since the pilot (recogniser, not the photos)

The 24 JPEGs and `photos/manifest.yaml` are byte-for-byte the pilot's. The pipeline gained, all
classical + deterministic (no new deps):

1. **Scene localisation** (`recogniser/locate.py`, SI-025). Colour-coherence segmentation keeps
   connected regions whose colour is compatible (generous Lab dE, chromatic — the neutral page is
   excluded by a chroma floor) with an enrolled sheet's inks and that carry interior edge
   structure. A bare fragment (>=90% ink-compatible span) short-circuits to the byte-identical
   pre-SI-025 path, so **all synthetic behaviour is unchanged**.
2. **Perspective rectification** (`locate.rectify`). The pattern square's quad is traced
   (contour + `approxPolyDP`, min-area-rect fallback) on a downscaled copy and the full-res crop
   is warped fronto-parallel; a capture-moire low-pass (area-downscale to 800 px) precedes
   measurement — a photograph of a *screen* carries display-grid/sensor moire that otherwise
   explodes the band segmenter (measured: a full-res crop segments into 12-200 "bands", ratio
   garbage; the same crop at 800 px gives the true 5).
3. **Two-path band colour** (`recogniser/score.py`, SI-027). The 001 `colour_pair` now scores
   `max(absolute, relationship)`: a per-channel diagonal white-balance gain (tight bounds,
   corroborated by the sheet's own luminance-order + hue-proximity relationships, to stop a 2-ink
   gain overfitting) — diagonal only, **no bloom model this phase**; the residual is reported.

The ingest command is unchanged (`recognise()` handles scenes internally):

```
uv run python -m battery.ingest photos/ photos/manifest.yaml --out experiments/exp-003-screen-rerun/
```

## 1. Side-by-side vs the pilot

Recognition of `bar-cascade-001` (the target sheet), 24 rows, all `ok`:

| metric | pilot (v0, whole frame) | re-run (locate -> rectify -> 2-path) |
|---|---|---|
| identified | 0 / 24 | 0 / 24 |
| **candidate or better** | **0 / 24** | **4 / 24** |
| mean target aggregate | **0.020** | **0.201** (10x) |
| photos giving exactly 5 bands | 0 / 24 | **11 / 24** |

**By angle** (mean target aggregate; pilot -> re-run):

| angle | n | pilot | re-run | re-run verdicts |
|---|---|---|---|---|
| 0 deg | 6 | 0.030 | **0.276** | 4 not_recognised, 2 candidate |
| 30 deg | 12 | 0.021 | **0.195** | 10 not_recognised, 2 candidate |
| 60 deg | 6 | 0.009 | **0.140** | 6 not_recognised |

**By distance:** fills_frame (n=18) 0.013 -> **0.227** (4 candidate); far_2m (n=6) 0.041 ->
**0.123** (0 candidate — the pattern is smaller and softer at 2 m, so segmentation collapses
more often).

The 4 candidates are exactly the **TrueTone-off, 75-100% brightness** frames (IMG_2279, 2280,
2283, 2284): the conditions with the least warm cast and least bloom, where colour recovers
(agreement 0.37-0.74, see section 4) enough to push the aggregate over the 0.40 line. Recognition
appears exactly where the physics is kindest — a coherent signal, not noise.

## 2. THE DECISIVE TABLE — residual field error on the committed constants

Per **straight-on** (angle 0 deg) photo, the measured cascade ratio and duty from the claim
working vs committed `frequency_ratio = 1.94 +/- 0.03` and `duty_cycle_light = 0.31 +/- 0.015`:

| photo | TrueTone | bright | n_bands | ratio | ratio resid | duty | duty resid | colour | verdict |
|---|---|---|---|---|---|---|---|---|---|
| IMG_2263 | on | 100% | 5 | 1.941 | **+0.001** | 0.333 | +0.023 | 0.331 | not_recognised |
| IMG_2267 | on | 75% | 5 | 1.964 | +0.024 | 0.527 | **+0.217** | 0.000 | not_recognised |
| IMG_2271 | on | 25% | 10 | 1.447 | **-0.493** | 0.371 | +0.061 | 0.000 | not_recognised |
| IMG_2275 | off | 25% | 7 | 1.663 | -0.277 | 0.366 | +0.056 | 0.317 | not_recognised |
| IMG_2279 | off | 75% | 5 | 1.945 | +0.005 | 0.343 | +0.033 | 0.365 | **candidate** |
| IMG_2283 | off | 100% | 5 | 1.945 | +0.005 | 0.331 | +0.021 | 0.567 | **candidate** |

**Straight-on residual distribution (n=6):**

| constant | mean resid | mean \|resid\| | max \|resid\| | mean x tol | max x tol |
|---|---|---|---|---|---|
| cascade ratio (tol 0.03) | -0.123 | 0.134 | 0.493 | **4.5x** | 16.4x |
| duty (tol 0.015) | +0.069 | 0.069 | 0.217 | **4.6x** | 14.5x |

Across **all 24** photos: ratio mean\|resid\| 0.236 (**7.9x** tol, max 26x), duty mean\|resid\|
0.065 (**4.4x** tol, max 23x, and the mean is a one-sided **+0.065 high**).

## 3. The thesis reading — viable calibration gap, or must margins widen?

The two constants answer differently, and the split is the finding.

**Cascade ratio — recoverable to within tolerance, *conditional on capture quality*.** The
aggregate residual (4.5x straight-on) is **bimodal, not a calibration offset**: when band
segmentation survives capture (`n_bands == 5`, on 11/24 frames — IMG_2263, 2279, 2283 straight-on)
the ratio lands at **+0.001...+0.024, i.e. within ~1x the committed +/-0.03**. The large residuals
are all **segmentation collapse** — the 25%-brightness straight frames (2271->10 bands, 2275->7)
and every steep/2 m frame explode to 46-58 "bands" (moire + noise + residual perspective
manufacture false boundaries) and the ratio is meaningless. So the ratio has **no inherent
field-calibration gap**; the residual is the **partially-fixed (2)** — rectification + the moire
low-pass fixed the clean captures, but boundary-merge robustification (not done this phase) is
still needed before low-brightness/steep frames are trustworthy.

**Duty — a real, one-sided field bias past tolerance; margins must widen (or a bloom model).**
Duty is biased **high on every frame** (+0.02 to +0.22, mean +0.069), and the bias **survives
correct segmentation**: IMG_2263 and 2283 give exactly 5 bands and clean ratios yet still read
duty 0.333 and 0.331 (+0.02, ~1.5x tol), and IMG_2267 — also 5 bands — reads 0.527 (+0.22) because
at 75% brightness the light bars **bloom and swell**, inflating the light-pixel fraction. This is
exactly the pilot's diagnosis (3): the +/-0.015 tolerance was calibrated on synthetic renders and
does not price emissive-capture bloom. **Verdict: duty sits at ~1.5-4.6x tolerance and is
systematically biased, not scattered — the field tolerance must widen (or an emissive-surface
bloom/bias model is needed) before duty is a trustworthy discriminator from a screen photo.**
Diagonal gain does not touch it (bloom is geometric, not a colour cast).

**Net:** localisation (1) is *solved* — the reason 0/24 became measurable. The residual that keeps
identification at 0/24 is **not** the calibration of the constants themselves but (a) band
**segmentation robustness** under capture (ratio) and (b) an **unpriced emissive bloom bias**
(duty), plus (c) bloom-limited colour (section 4). None is fatal to the thesis; all are concrete,
scoped next fixes the honest field data now names.

## 4. What localisation/rectification fixed, and what remains (quantified)

- **Localisation (SI-025) — fixed.** 22/24 frames localise to the ~2500 px green pattern square
  (not the page/bezel) via the scene path; the 2 exceptions (IMG_2274, 2282 — both 60 deg
  fills-frame, where the foreshortened pattern already spans >=90% of the frame) short-circuit as
  bare fragments and are measured whole-frame without rectification (and over-segment — an honest
  edge of the >=90% short-circuit that keeps synthetic fragments on the old path). The lift from
  0.020 -> 0.201 is this stage: the two-colour classifier now splits ink-vs-ink, not page-white vs
  bezel-dark.
- **Rectification + moire low-pass — fixed for clean captures, partial otherwise.** 11/24 frames
  recover the true 5 bands; the rest (low brightness, 30-60 deg, 2 m) still over-segment. This is
  fix **(2) done only partially**, as scoped — perspective is corrected but boundary-merge
  robustification is not.
- **Duty bloom bias — unfixed by design (measured).** Mean +0.069 high (4.6x tol), present even on
  cleanly-segmented frames. No bloom model this phase; the residual is the open task.
- **SI-014 phase origin — unchanged (confirmed).** No interior framing captures the cascade
  origin, so `phase_duty_identity` is scored on a locally-recovered inter-band offset; nothing new,
  the known limitation persists on every real frame.
- **Two-path band colour (SI-027) — helps, but bloom-limited.** Mean absolute-path agreement
  **0.011** (the absolute dE gate collapses everywhere, as the pilot predicted); mean
  relationship-path agreement **0.205** — a real lift, and 0.37-0.74 on the 4 TrueTone-off bright
  frames. But the **mean post-diagonal-gain residual is dE 11.5**, just past the committed dE-10
  tolerance: the pilot's "diagonal-plus-bloom" shift is confirmed — a single diagonal gain recovers
  the warm cast (gain in-bounds on 15/24; relationship path applicable on 12/24 where both inks are
  seen) but **cannot** undo the bloom that desaturates the light ink toward white (measured light
  ink [198, 222, 224] BGR on IMG_2263 — near-neutral). Diagonal gain is the right first move and it
  is honestly not enough; a bloom term (SI-027 open corollary) is the next step.

## 5. Scope and honesty notes

- **Constants are not tuned to these photos.** The empirical locator constants (localisation dE 34,
  chroma floor 8, measurement resolution 800 px) are set from the ground/ink physics and flagged
  with generalisation risk in the code and SI-025; nothing is fit to the 24-frame outcome. The 24
  photos are an **acceptance corpus, not a training set**.
- **One display, one phone, one surface, screen only.** The print arm stays open. Duty/colour bloom
  is a *screen* phenomenon; print will bias differently (SI-024).
- **`recogniser/measure_grid` is slow on real photos** (~5 s/region: `extract_flat_inks` clusters
  ~34,000 unique JPEG colours where a synthetic render has ~30) — noted, not fixed here (it does
  not affect the band result and iso-002 is still correctly rejected as top sheet on the green
  frames).
- **L3 stays open.** v0 now *measures* real photographs and produces honest candidates, but
  identifies none; the scoping findings above (segmentation robustness, field duty tolerance /
  bloom model, print arm) are what stand between here and L3.
