# Pattern Grammar Audit — Worked Example 001

**Subject:** uploaded two-colour bar cascade pattern (735 × 697 px sample)
**Method:** pixel-level measurement — two-colour classification, run-length analysis per band, phase correlation
**Status:** audit complete · commit proposed · tune proposed

---

## 1. What the audit found

This pattern was made with rules. None of them were written down. All of them are recoverable by measurement

### Structure

Vertical bands of purely horizontal bars. Within every band, every pixel column is identical — the bars are exactly 0.0°, the band boundaries exactly vertical. Five bands visible in the sample; the outer two are cropped. Interior band width is constant at **191–192 px**: a fixed module

### The frequency cascade

Each band's stripe period doubles, left to right:

| Band | Period (px) | Light bar (px) | Dark bar (px) |
|---|---|---|---|
| 0 | 14.9 | 5.1 | 9.9 |
| 1 | 29.6 | 10.1 | 19.5 |
| 2 | 58.4 | 20.0 | 38.4 |
| 3 | 113.5 | 40.0 | 73.5 |
| 4 | 213.7 | 81.0 | 132.7 |

The light-bar widths double *exactly*: 5 → 10 → 20 → 40 → 80. Measured period ratios: 1.99, 1.97, 1.94, 1.88 — the drift at the coarse end is a sample artefact (band 4 contains only three runs and is edge-cropped). **Implied design rule: frequency ratio = 2.00 per band**

### Duty cycle

Light fraction per band: 0.336, 0.334, 0.339, 0.346, 0.350

**Implied design rule: duty cycle = 1/3 light, 2/3 dark** — held within ±1.6% across a 14× range of scale. This is a committed value in all but name

### The phase rule (the hidden elegance)

Adjacent bands are offset vertically. Measured offsets, as a fraction of the finer band's period: **0.34, 0.35, 0.32, 0.35**

Each band steps by one-third of the finer period — that is, **the phase step equals the duty cycle.** One number, 1/3, governs both the bar proportion and the stagger. Whoever made this may not have articulated that; the pattern did

### Colour

Dark: **#2E5B30** (46, 91, 48) · Light: **#73B881** (115, 184, 129)
Area ratio ≈ 2:1 dark:light — the duty cycle expressed as colour mass

---

## 2. The recovered grammar (commit candidate, as-is)

```yaml
grammar:
  name: bar-cascade/example-001
  structure:
    element: horizontal bar
    arrangement: vertical bands, fixed module width
    orientation_deg: 0.0
  cascade:
    frequency_ratio: 2.00        # period doubles per band
    duty_cycle_light: 0.333      # light = 1/3 of period
    phase_step: 0.333            # per-band offset, fraction of finer period
    rule_note: phase_step == duty_cycle (single governing constant)
  colour:
    dark: "#2E5B30"
    light: "#73B881"
    area_ratio_dark_light: 2.0   # follows from duty cycle
  module:
    band_width_relative: constant (measured 191 px at sample scale)
```

Six numbers and one structural sentence reproduce this pattern completely. That is a sett

---

## 3. Signature assessment — how identifiable is it, honestly

### Strengths

- **Ratios everywhere.** Frequency ratio, duty cycle, phase step and colour-area ratio are all scale-invariant — they survive any zoom, distance or reproduction size
- **The phase/duty identity (1/3 = 1/3)** is a genuinely distinctive relational fingerprint — a second-order property casual imitations won't reproduce
- **Topological simplicity** makes measurement trivial and confident: run lengths and transitions, no segmentation ambiguity

### Weaknesses

- **Canonical-peak exposure.** Horizontal bars, ratio 2.00, duty 1/3 — these sit on the most crowded coordinates in generative design. Powers of two and simple fractions are where everyone lands. As committed, the false-positive risk against the world's stripe patterns is real
- **Unevenly distributed signature.** Within a single band, a fragment yields only one period, one duty cycle, and an unanchorable phase — a weak, ambiguous sample. The identifying power concentrates in the *cascade*: a fragment must span **two or more band boundaries** to capture the frequency ratio and phase step. The genome is not in every cell; it is in the transitions
- **Rotation ambiguity.** A 90°-rotated crop of a single band is indistinguishable from vertical stripes. Orientation only anchors once a band boundary is in frame

### Fragment-strength map

| Fragment captures | Identification strength |
|---|---|
| Part of one band | Weak — period + duty only, phase unanchored |
| One band boundary | Moderate — one ratio observation + one phase observation |
| Two+ band boundaries | Strong — cascade ratio, phase rule and duty confirmed jointly |
| Any fragment + colour | Each adds the colour-pair and area-ratio check |

---

## 4. Tune proposal — moving off the crowded peaks

Micro-adjustments below human perceptual thresholds that relocate the grammar into ownable coordinate space. The pattern still reads as itself; the measurements become unmistakable

| Parameter | As-is | Tuned | Why |
|---|---|---|---|
| Frequency ratio | 2.00 | **1.94** | Off the power-of-two peak; imperceptible across a 5-band cascade |
| Duty cycle | 0.333 | **0.31** | Off the 1/3 peak; bars fractionally lighter in mass |
| Phase step | 0.333 | **0.31** | Preserves the elegant phase = duty identity at the new value |
| Orientation | 0.00° | **0.7°** *(optional)* | Kills rotation ambiguity and stripe-world collisions; visible only against a hard reference edge — trade-off to art-direct |
| Colour ratio | 2.00 | follows duty → **2.23** | Keeps colour mass slaved to the governing constant |

The tuned grammar keeps the pattern's one-number elegance — a single constant (0.31) still governs proportion, stagger and colour mass — but at a value nobody arrives at by accident

**Distinctiveness after tune:** the conjunction (ratio 1.94 ∧ duty 0.31 ∧ phase = duty ∧ colour pair) is a four-way coincidence; accidental collision becomes negligible even before a control-corpus test

### Optional: evening out the genome

If fragment-level identification matters for this identity, one structural addition distributes signature into single-band fragments: a **micro-rhythm within the dark runs** — e.g. every dark bar carries a hairline at 0.31 of its own height. Self-similarity puts the governing constant inside every cell instead of only at the transitions. This changes the design's appearance slightly and is an art-direction decision, not an audit finding

---

## 5. What enrolment would look like

1. Commit the tuned values in a published grammar sheet at the brand's domain (`/.well-known/pattern-grammar`)
2. Publish the recogniser checks: colour-pair match → run-length ratios → cascade ratio across boundaries → phase/duty identity → confidence scaled by bands captured
3. Regenerate production assets from the committed grammar (the tune is invisible; the files become canonical)
4. Family variation space, if wanted: per-product duty values within a declared range; per-era ratio revisions — measurable dialects of one language

---

## 6. The finding, in one sentence

**This pattern already had a grammar — six numbers and a sentence — and the only thing standing between "a nice pattern" and "a machine-recognisable identity" was writing them down and nudging three values off the crowded peaks**
