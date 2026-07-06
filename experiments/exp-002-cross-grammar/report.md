# Experiment 002 — Cross-grammar recognition & white-balance robustness (Phase 7)

**Status:** L2 evidence, two grammar families (band 001 + grid 002), scored side by side.
L3 remains open — all conditions are synthetic (see SI-017 and the closing commentary). Every
number below is recomputed from [`raw_results.csv`](raw_results.csv); nothing is estimated.

## What was run

The harness (`battery/cross.py`) drives the identical production pipeline the recogniser uses,
now over **two** families and scoring every fragment against **all** sheets in `grammars/`:

> render a 001 (band) or 002 (grid) surface → sample a ground-truthed fragment → optionally
> degrade → `recogniser.claim.recognise` against `bar-cascade-001` **and** `iso-002` → record
> BOTH sheets' aggregate/coverage/verdict (and 002's two-path ink detail) as one CSV row.

Exact reproduction is pinned in [`manifest.yaml`](manifest.yaml):

| | |
|---|---|
| git commit | `d751043b3ca7b228e123b833048c3a479ff84d62` (working tree: Phase-7 changes — the sampler fix + `battery/cross.py` — are uncommitted at run time) |
| command | `python -m battery.cross --out experiments/exp-002-cross-grammar` |
| surface seeds | `[0, 1, 2]`; harness base seed `20260706` |
| 001 surface | 5 bands, module 200 px (1000×1000), orientation 0° |
| 002 surface | 10×10 cells, module 100 px (1000×1000), density 0.45, a **different seeded 4–6 ink subset per seed** (seed 0/1/2 subsets in the manifest) |
| recogniser | v0; verdict thresholds `identified ≥ 0.70`, `candidate ≥ 0.40` (SI-013) |
| rows | **1269** (443 surface_001, 442 surface_002, 96 wb_002, 288 impostor); **123 skipped** for large-tilted-window geometry |

Four arms: (A) 001 surfaces × fracs `[0.05…1.0]` × rotations `[0, 30, 90]`; (B) 002 surfaces,
same sweep; (C) white-balance sweep on 002 fragments at frac 0.5; (D) two impostors. Every cell
where a statistic is read holds ≥ 20 fragments (8 frags × 3 seeds = 24). The run is deterministic:
re-running from the same config produces a byte-identical CSV and byte-identical chart PNGs
(asserted in `tests/test_cross.py`).

**The 123 skips are all the genuine geometric impossibility of fitting a large tilted window in a
square surface** (a 30°-rotated full square does not fit inside the square — a `ValueError`, honestly
tallied), **not** the zero-size-window bug that thinned exp-001's 90° arm. That bug (SI-017 gap 2) is
**fixed** this phase (`generator/fragments.py`; see §5), so 90° now has full coverage
(surface_002: 168 / 118 / 156 fragments at rot 0 / 30 / 90).

---

## 1. Cross-grammar discrimination — the headline

*(`curve_confidence_vs_frac_true_001.png`, `curve_confidence_vs_frac_true_002.png`,
`confusion_summary.png`)*

Mean aggregate confidence, genuine fragments, rotation 0°, against the **true** sheet and against
the **cross** sheet (the other family), by fragment fraction:

| | frac 0.05 | 0.10 | 0.20 | 0.35 | 0.50 | 0.75 | 1.00 |
|---|---|---|---|---|---|---|---|
| **001 fragments** → 001 (true) | 0.352 | 0.378 | 0.467 | 0.563 | 0.569 | 0.566 | **0.709** |
| **001 fragments** → 002 (cross) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| **002 fragments** → 002 (true) | 0.599 | 0.683 | 0.804 | 0.861 | 0.885 | 0.879 | 0.879 |
| **002 fragments** → 001 (cross) | 0.004 | 0.006 | 0.010 | 0.011 | 0.009 | 0.020 | 0.000 |

**Does any 001 fragment ever reach candidate against 002, or vice versa? No — not one of 885.**
Across every fraction and rotation:

- All **443** true-001 fragments score `not_recognised` against 002 (max cross aggregate **0.158**).
- All **442** true-002 fragments score `not_recognised` against 001 (max cross aggregate **0.268**).

The cross-sheet distribution never touches the candidate line (0.40); the true-sheet distribution
sits **0.35–0.88**. The margin between the two — the false-positive property spec §5 asks to be
published — is the full width of the confidence axis. Confusion counts (all fracs × rotations):

| true → scored | not_recognised | candidate | identified |
|---|---|---|---|
| 001 → **001** | 143 | 279 | 21 |
| 001 → 002 | **443** | 0 | 0 |
| 002 → 001 | **442** | 0 | 0 |
| 002 → **002** | 11 | 100 | 331 |

An honest qualification, stated up front: **this separation is easy, and for a structural reason.**
The two families do not share a measurer — 001 is scored by the *band* measurer, 002 by the *grid*
measurer (`claim.py` dispatches on `structure.type`). A 001 fragment fed to the 002 sheet has its
grid/ink features come back mostly unobserved, so 002's *coverage* collapses and the aggregate is
floored near 0 by construction. The discrimination is real and published, but it is a between-family
result. The hard distinctiveness test — two grammars in the **same** family, competing on the same
measurements — is not run here because there is no second band or second grid grammar yet (see §6).

---

## 2. White-balance robustness — the Milestone 2 core claim

*(`wb_robustness.png`)* 002's identification is carried entirely by its ink set (audit 002 §6), and
colour-led signatures are print-fragile. The Milestone 2 claim is that the **relationship** path
(colour as a single global white-balance gain, SI-020) survives a cast where the **absolute** path
(ΔE against declared inks) does not. Mean 002 ink agreement over the frac-0.5 sweep, 24 fragments
per gain cell:

| gain (B,G,R) | sense | absolute | **relationship** | feature (max) | implied applied R-gain | clipped inks (of ~9) |
|---|---|---|---|---|---|---|
| (1.0, 1.0, 1.0) | neutral | 0.965 | 0.965 | 0.965 | 1.00 | 2.0 |
| (0.85, 1.0, 1.15) | warm | **0.125** | **0.690** | 0.690 | 1.145 | 5.1 |
| (1.15, 1.0, 0.85) | cool | **0.035** | **0.806** | 0.806 | 0.847 | 3.0 |
| (0.7, 1.0, 1.3) | strong-warm | **0.016** | **0.588** | 0.588 | 1.298 | 8.8 |

Reading:

- **The absolute path collapses under any cast** — from 0.965 (neutral) to ≤ 0.125 (warm), 0.035
  (cool), 0.016 (strong-warm). On its own, colour recognition of 002 would be lost the moment a
  camera white-balances.
- **The relationship path holds** — 0.690 / 0.806 / 0.588, all **above the candidate line (0.40)**,
  so the feature agreement (the max of the two paths, SI-020) tracks the relationship path and the
  identity survives the cast. The estimated correction gain is **in bounds for 100 %** of fragments
  in every cell, and its **implied applied gain recovers the white balance that was applied** to two
  significant figures (warm 1.145 ≈ 1.15, cool 0.847 ≈ 0.85, strong-warm 1.298 ≈ 1.30) — the claim
  reports not just *that* colour matched but *what cast* it corrected for.
- **Where it breaks — strong-warm clipping (SI-020), quantified.** Pushing red to 1.30 saturates
  bright inks: the clipped-ink count climbs 2 → 5.1 → 8.8 of ~9, and the relationship path drops from
  0.806 (cool, 3 clipped) to **0.588** (strong-warm, 8.8 clipped). The one-sided clip-aware scoring
  (a clipped channel is a lower bound, not a mismatch) is what keeps it above candidate rather than
  collapsing with the absolute path — but it is *degraded*, exactly as SI-020 predicts: once most
  inks clip, a lower bound is most of what survives. Strong-warm is the edge of the model, and the
  data shows the edge.

This is the Milestone 2 result with numbers: **colour-as-relationship converts a total loss (0.016)
into a retained candidate-or-better identity (0.588–0.806) across a plausible cast range, and reports
the cast it removed** — degrading gracefully into the clipping regime rather than failing silently.

---

## 3. Impostors — mutual, both scored against both sheets

*(288 fragments, 144 per impostor.)*

**(a) The exp-001 canonical band impostor** — ratio 2.0, duty ⅓, phase ⅓, carrying 001's exact inks
(colour cannot discriminate it; the margin rests on structure):

| scored against | not_recognised | candidate | identified | mean | max |
|---|---|---|---|---|---|
| **001** | 136 | 8 | **0** | 0.317 | 0.412 |
| 002 | 144 | 0 | **0** | 0.000 | 0.000 |

Reproduces exp-001's SI-016 finding exactly: 8 fragments (fracs 0.35/0.50) reach *candidate* — colour
(shared by construction) plus the `phase == duty` relation, which the commensurate ratio-2.00 impostor
can measure locally where the tuned 1.94 grammar cannot — but **never identification**, because ratio
and duty (0.50 of the weight) are dead off-peak. Against 002 it scores a flat 0.

**(b) The nine-ink grid impostor** — 002's structure (grid, alphabet, exact multiply overprint)
rendered in **nine inks hue-rotated 165° in Lab**, min ΔE **24.4** from 002's master set (well past
the ΔE-10 tolerance):

| scored against | not_recognised | candidate | identified | mean | max |
|---|---|---|---|---|---|
| 001 | 144 | 0 | **0** | 0.009 | 0.276 |
| **002** | 144 | 0 | **0** | 0.003 | 0.084 |

**Does structure-only similarity leak? No — as expected, ~0.** This is the sharpest confirmation of
audit §6's peak-discounting: a surface that shares *everything* structural with 002 (same square grid,
same duty-½ stripe rhythm, same 45° staircases, same exact multiply overprint) scores **0.003** against
002, because every one of those features is weight-0 verification and the only identification-weighted
feature — the ink set — is deliberately wrong. And the WB **relationship path does not rescue it**: a
Lab hue rotation is not a diagonal per-channel gain, so no in-bounds consensus gain maps the shifted
inks onto 002's set (this is SI-020's diagonal-only design working as intended — it accepts a global
cast, it refuses a hue rotation). Exact ≠ distinctive; the address is the inks, and only the inks.

---

## 4. 002 fragment strength vs audit 002 §5

*(rotation 0; fragment fraction as an approximate proxy for "how much composition is in frame" — the
mapping is by area, not by counting cells/circles/stripes, so it is indicative, not exact.)*

| frac | ≈ audit §5 content | mean agg. (002) | ink agr. | verdicts (of 24) |
|---|---|---|---|---|
| 0.05 | a few cells | 0.599 | 0.679 | 7 identified · 14 candidate · 3 not_recognised |
| 0.10 | a small cluster | 0.683 | 0.755 | 13 · 11 · 0 |
| 0.20 | a circle / striped block | 0.804 | 0.879 | 18 · 6 · 0 |
| 0.35 | several primitives | 0.861 | 0.939 | 22 · 2 · 0 |
| 0.50 | quarter surface | 0.885 | 0.965 | 24 · 0 · 0 |
| 0.75–1.00 | most / whole | 0.879 | 0.958 | 24 · 0 · 0 |

**Audit §5's headline — "dramatically better than Example 001" — is confirmed.** 002 reaches
`identified` from a fragment as small as **5 % of the surface area** (7 of 24 at frac 0.05; a clear
majority `identified` by frac 0.10), where 001 reaches identification only on the **whole** surface
(exp-001 §1, and §1 above: 001 frac 1.0 = 0.709). The reason is structural and matches the audit: 001's
signature is localised in band transitions and, worse, at the cascade origin (SI-014), so a small
interior fragment is weak; 002's genome — the ink set — is present in *almost every* fragment (audit §5
"almost anywhere → ink values against the master set"), so a few cells already carry the address. The
weight-0 verification rows of audit §5 (one circle → Ø/M vocabulary; a striped area → M/2 rhythm; a
two-ink overlap → the multiply check) are measured but carry no identification weight, exactly as the
audit intends; the confidence above is the ink set doing the work at every fraction.

The 3 not_recognised at frac 0.05 are honest: a ~5 % window sometimes catches only one or two inks —
below `MIN_INKS_FOR_GAIN = 3` (SI-020), which disables the relationship path and, if the fragment is
also colour-thin, leaves too little to cross candidate. 002's identifying power is high but not
unconditional at the smallest scale.

**Rotation** (all fracs, surface_002): mean aggregate 0.799 / 0.793 / 0.775 at 0° / 30° / 90°, with
110 of 156 fragments still `identified` at 90°. The audit §5 "90° grid ambiguity" is broken by stripe
orientation and composition as the audit predicted — and 90° is now *fully sampled* thanks to the
sampler fix (§5), where exp-001 could only sample it thinly.

---

## 5. The sampler fix (SI-017 gap 2)

The Phase-2 rotated-fragment sampler degenerated to a **zero-size window** near 90° under aspect
jitter: it warped the whole surface to a full W×H canvas and then cropped an axis-aligned w×h box at
`(cx − w/2, cy − h/2)`; for a window wider than tall the fitting margin near 90° is `≈ h/2 < w/2`, so
the crop's left edge went **negative**, and Python's negative-index slicing read `x0` as `W + x0`,
producing an empty slice. Measured before the fix: **93 of 1000** frac-0.5 fragments at rotations
85–95° were zero-size; exp-001 tallied 121 such skips and its 90° arm was thin as a result.

**Fix (`generator/fragments.py`):** frame the warp *directly onto the window* — translate the rotation
matrix so the window centre maps to `(w/2, h/2)` of a **w×h** output and `warpAffine` renders the whole
window in one pass. The returned fragment is now **always exactly (h, w)**; a negative slice is
impossible at any rotation 0–360°. For a window that already fit under the old code the output is
pixel-equivalent (same rotation, same centre, just framed to the window), so all existing tests pass
unchanged — none had encoded the bug. Verified: **0 zero-size windows** across a full 0–360° sweep;
the only remaining skips are genuine large-tilted-window non-fits (the `ValueError`, tallied). The
battery-level zero-size guard in `battery/run.py` is now dead code; it is kept as a never-firing
invariant (commented) so the frozen exp-001 harness stays byte-reproducible. New regression tests at
85–95° and under extreme aspect jitter are in `tests/test_cross.py`.

---

## 6. Honest commentary — what still fails

- **002 is a colour monoculture, and this experiment makes that concrete.** Its *only*
  identification-weighted feature is the ink set (weight 0.75); `primitive_frequency_mix`'s 0.25 is
  reserved-unmeasured (SI-008). So 002's entire identity rests on colour fidelity — precisely the
  print-fragility audit §6 named. The WB relationship path is what makes that survivable, but it is a
  **single point of failure**: a cast that clips most inks drags even the relationship path to 0.588
  (strong-warm, §2), and a fragment with < 3 inks cannot use it at all (§4). 001 spreads its identity
  over four structural features; 002 stakes everything on one. Both are honest to their audits, but
  they fail differently, and 002 fails toward the camera.

- **`primitive_frequency_mix` is still unmeasured (SI-008).** Until it is, two grid *dialects* that
  share an ink set are indistinguishable to this recogniser, and 002's coverage denominator is a
  single feature. The nine-ink impostor confirms the *good* half of this (different inks → 0), but the
  untested half is the dangerous one: same inks, different composition. Milestone 3 must implement
  primitive-frequency extraction and let 002 commit its proportions (a `grammar_version` bump).

- **The control corpus is trivial, and grid-vs-grid distance is undefined (new: SI-022).** Spec §5
  wants the false-positive rate published against a "control corpus of non-enrolled pattern work". Here
  it is two enrolled grammars plus two impostors, and — as §1 admits — the two grammars are in
  *different measurer families*, so their separation is structural rather than a close numeric call.
  The published margin (no fragment crosses; no impostor identified) is real but it is a between-family,
  small-corpus number. The real distinctiveness test is same-family (two band grammars, or two grid
  grammars competing on the same measurements), and there is no second same-family grammar to run it
  against yet. The same gap seen from the grid side: spec §5's minimum-distance-over-signature-locus
  cannot presently be computed between two grid grammars, because the only non-zero-weight grid feature
  is the ink set (SI-008).

- **This is L2, not L3.** All conditions are synthetic. The WB robustness uses `degrade.white_balance`
  — a clean per-channel gain with clipping. A real camera adds sensor noise, demosaic, a possibly
  *non-diagonal* colour transform and non-uniform illumination. The nine-ink result shows the
  relationship path correctly **refuses** a non-diagonal transform (a hue rotation) — which is right
  for an impostor, but means a real illuminant that is even mildly non-diagonal would read as a
  *genuine* mismatch. **Phase 4b (`battery/ingest`, print-and-photograph) must check:** (a) whether
  real white balance stays inside the diagonal-gain model the relationship path assumes; (b) the
  clipping regime on real bright inks (§2's strong-warm edge); and (c) whether 002's ink set survives a
  real print's gamut at all. L3 stays open until it runs (SI-017).

**What surprised.** The cross-grammar separation is *cleaner than the recogniser deserves credit for* —
zero of 885 fragments reached even candidate on the wrong sheet — but the cleanness is the family split
doing the work, not a hard-won numeric margin. The genuinely earned result of this phase is §2: colour
measured as a global-cast relationship turns a 0.016 absolute collapse into a 0.59–0.81 retained
identity **and names the cast it removed**, holding above candidate across warm/cool and degrading
predictably (not catastrophically) into the strong-warm clipping regime. That, and closing the 90°
sampler gap that thinned exp-001, are the two things Phase 7 actually moves.

---

## SPEC-ISSUES added this phase

- **SI-022** (§5) — the cross-grammar false-positive margin is published against a two-grammar corpus
  in different measurer families, not a control corpus of non-enrolled pattern work; and grid-vs-grid
  minimum distance is presently carried entirely by the ink set (every 002 structural feature is
  weight-0 and `primitive_frequency_mix` is unmeasured, SI-008), so two grid dialects sharing an ink
  set are indistinguishable.

SI-017 gap 2 (the rotated-sampler zero-size bug) is **resolved** this phase (§5); its entry is
annotated accordingly.
