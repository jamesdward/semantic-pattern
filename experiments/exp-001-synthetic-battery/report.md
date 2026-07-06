# Experiment 001 — Synthetic recognition battery (Phase 4a)

**Status:** L2 evidence (open recogniser identifies generated surfaces from fragments under
stated *synthetic* conditions). L3 remains open — see SI-017 and the closing commentary. Every
number below is recomputed from `raw_results.csv`; nothing is estimated.

## What was run

The battery drives the identical production pipeline the recogniser uses —

> render surface from `grammars/bar-cascade-001.yaml` → sample a ground-truthed fragment →
> degrade it → `recogniser.claim.recognise` against **all** sheets in `grammars/` → record one CSV
> row per fragment.

Exact reproduction is pinned in [`manifest.yaml`](manifest.yaml):

| | |
|---|---|
| git commit | `1c70d92d6472807a837719907004854a28ba951b` |
| command | `python -m battery.run --out experiments/exp-001-synthetic-battery` |
| surface seeds | `[0, 1, 2]`; harness base seed `20260705` |
| surface | 5 bands, module 200 px (1000×1000 px), orientation forced to 0° for clean ground truth |
| recogniser | v0; verdict thresholds `identified ≥ 0.70`, `candidate ≥ 0.40` (SI-013) |
| rows | **3671** fragments (2999 genuine, 672 impostor); **121** skipped for impossible rotated geometry |

Three arms, each swept against the fragment-fraction axis so the grid stays tractable (each
degradation axis is swept **separately** at rotation 0, not crossed with every other axis; the
rotation axis is swept at no degradation). Every non-degenerate cell holds ≥ 20 fragments
(`frags_per_cell` 8 × 3 seeds = 24), so the per-cell means below are stable. The run is
deterministic: re-running from the same config produces a byte-identical CSV and byte-identical
curve PNGs (asserted in `tests/test_battery.py`).

---

## 1. Minimum fragment vs confidence

*(`curve_min_fragment_vs_confidence.png`, `curve_confidence_by_boundaries.png`)*

Mean aggregate confidence against 001, genuine fragments, no degradation, rotation 0°:

| fragment fraction | 0.05 | 0.10 | 0.20 | 0.35 | 0.50 | 0.75 | 1.00 |
|---|---|---|---|---|---|---|---|
| mean aggregate | 0.355 | 0.379 | 0.460 | 0.484 | 0.559 | 0.545 | **0.704** |

- **Candidate (0.40) is crossed at fraction ≈ 0.20** — a fragment covering a fifth of the surface
  area (≈ two band boundaries) is already an honest candidate.
- **Identified (0.70) is reached only at fraction 1.00** — the whole surface (mean 0.704, 58 % of
  those fragments verdict `identified`). No smaller genuine fraction reaches identification on
  average, and only 32 of 2999 genuine fragments with fraction < 1.0 *ever* reach `identified`
  across all rotations and degradations.

Rotation costs confidence but not the candidate crossing: candidate is crossed at fraction ≈ 0.20
for 0° / 7° / 90° and ≈ 0.35 for 30°; identification is not reached at any rotation < whole
surface. (The 30° and 90° arms lose their largest-fraction cells to the sampler's rotated-geometry
skips — see SI-017 gap 2.)

### Mapping to audit 001 §3's fragment-strength table

Splitting rotation-0 genuine fragments by the band boundaries they span (`curve_confidence_by_boundaries.png`):

| audit §3 row | boundaries spanned | n | mean aggregate | cascade agr. | phase=duty agr. | reaches identified |
|---|---|---|---|---|---|---|
| part of one band — *weak* | 0 (single band) | — | — | — | — | — |
| one boundary — *moderate* | 1 | 31 | 0.348 | 0.796 | 0.045 | 0 % |
| two+ boundaries — *strong* | 2+ | 137 | 0.532 | 0.935 | 0.109 | 12 % |

The table reproduces the audit's ordering exactly — more boundaries, more confidence — with one
sharp qualification the audit did not have the data to state:

- **The single-band "weak" row is not sampled by the genuine sweep.** At module 200 px the
  smallest fraction (0.05) is a ≈ 223 px window, always wider than the 200 px module, so it always
  spans ≥ 1 boundary. The single-band case (coverage capped at 0.40) is instead demonstrated by the
  uniform-stripe impostor (flat aggregate 0.160, below) and the recogniser unit tests. This is a
  battery gap, logged honestly as SI-017 gap 1, not a property of the grammar.
- **"Two+ boundaries → strong, phase rule confirmed" is only half true on the tuned grammar.**
  Cascade ratio and duty *are* confirmed jointly (cascade agreement 0.935), but the `phase == duty`
  relation is **not** — its agreement is 0.109 across interior two-boundary fragments, versus 0.530
  on the whole surface. This is the SI-014 caveat, and the data shows it cleanly:

| | n | phase=duty agr. | mean aggregate | identified |
|---|---|---|---|---|
| whole surface (fraction 1.0, **origin captured**) | 24 | 0.530 | 0.704 | 58 % |
| interior 2+ boundary (fraction < 1.0, **origin-free**) | 113 | 0.019 | 0.496 | 2 % |

Only **2.65 %** of interior fragments happen to measure the phase relation above 0.3. The phase
component of 001's genome does not live in the transitions — it lives at the cascade **origin**
(SI-014). So the practical ceiling for an origin-free fragment sits at **candidate ≈ 0.50**,
however many boundaries it spans; identification needs the origin, which in practice means the
whole surface (or a full-height slice reaching row 0). The battery makes the audit's
"unevenly distributed signature" quantitative: the unevenness is not just across bands, it is
concentrated at one point.

---

## 2. Degradation tolerances

*(`curve_degradation_*.png`)* Candidate-or-better retention rate at **fraction 0.50**, rotation 0°,
24 fragments per cell. Identification is *not* the yardstick here — an origin-free 0.50 fragment
cannot be `identified` on the tuned grammar regardless of degradation (§1) — so tolerance is read
as *retention of a correct candidate call*:

| axis | parameter → retention |
|---|---|
| **Gaussian blur** (σ px) | 0: 1.00 · **1: 0.92** · 2: 0.54 · 4: 0.46 |
| **JPEG** (quality) | 95: 1.00 · 75: 1.00 · 50: 1.00 · **30: 1.00** |
| **Brightness** (×) | 1.0: 1.00 · 0.7: 0.75 · 1.3: 0.58 |
| **White balance** | neutral: 1.00 · cool: 0.79 · warm: 0.75 |
| **Perspective** (corner jitter) | 0: 1.00 · **0.02: 0.88** · 0.05: 0.42 |

Reading, holding at fraction 0.50:

- **JPEG is a non-event.** Recognition holds at 100 % all the way down to quality 30 — the cascade
  is carried by run-length ratios, which survive block artefacts and chroma subsampling.
- **Blur holds to σ ≈ 1 px** (92 %) and breaks by σ = 2 (54 %). At module 200 the finest band's
  period is ≈ 16 px, so σ = 2 already erases the fine end of the cascade. Tolerance scales with
  resolution: a higher-resolution capture buys blur headroom.
- **Perspective holds to strength ≈ 0.02** (88 %) and breaks by 0.05 (42 %) — an off-axis camera is
  tolerable up to a few degrees of keystone, beyond which the per-band period profile skews enough
  to break the boundary segmentation.
- **Brightness and white balance degrade colour, not structure.** Under a 0.7×/1.3× exposure the
  colour-pair agreement collapses to 0.000 (the inks leave the ΔE-10 tolerance), yet candidate
  retention stays 0.58–0.75 because the structural features (cascade, duty) are exposure-invariant.
  White balance is the same story: colour agreement ≈ 0.25 under a warm/cool cast, structure
  carries the call. This is the three-number output (SI-015) doing its job — colour drops out of
  coverage, the structural weight still matches, and the claim degrades gracefully rather than
  flipping to non-recognition.

---

## 3. False positives (the impostor arm)

*(`dist_impostor_vs_genuine.png`)* Four non-enrolled, plausible look-alikes, rendered from in-memory
**modified copies** of the 001 sheet (validation deliberately bypassed — they are impostors) and
**carrying 001's exact inks by construction**, so colour never discriminates them and the whole
false-positive margin rests on structure. Verdict counts against `bar-cascade-001`, all fractions,
168 fragments each:

| impostor | ratio · duty · phase | not_recognised | **candidate** | **identified** | mean agg. | max agg. |
|---|---|---|---|---|---|---|
| imp1 canonical_ratio2_duty33_phase33 | 2.00 · ⅓ · ⅓ | 155 | 13 | **0** | 0.308 | 0.418 |
| imp2 ratio2_duty50_phase50 | 2.00 · ½ · ½ | 144 | 24 | **0** | 0.318 | 0.423 |
| imp3 ratio15_duty33 | 1.50 · ⅓ · ⅓ | 150 | 18 | **0** | 0.208 | 0.415 |
| imp4 uniform_stripe_no_cascade | single period, no cascade | 168 | 0 | **0** | 0.160 | 0.160 |

**Headline claim — confirmed: no impostor fragment reaches `identified`.** Zero of 672. The genuine
distribution has a tail crossing 0.70 (max 0.824); no impostor exceeds **0.423**. At the
identification threshold the separation is clean.

**But the claim is not clean at the *candidate* line, and this is worth stating honestly.** 55 of
672 impostor fragments (8 %) reach `candidate` — all at mid fractions (0.5–0.75), none at the
extremes. Diagnosis, feature by feature:

- **Colour leaks fully, by construction** (agreement ≈ 1.0): impostors carry 001's inks on purpose,
  to force the test onto structure. In the field a colour mismatch would help; here it is disabled.
- **Cascade ratio and duty collapse to ≈ 0** for every impostor (2.00 and 1.50 are each > tolerance
  off 1.94; duty ⅓ = 0.333 and ½ are off 0.31) — the two features carrying 0.50 of the
  identification weight give the impostor nothing. This is exactly why the aggregate caps at ≈ 0.42
  and cannot climb to identification.
- **The `phase == duty` relation leaks for the ratio-2.00 impostors** — and this is the surprise
  worth the section. imp1 shares 001's phase = duty relation *and* sits at the commensurate ratio
  2.00. Per SI-014's corollary run forwards, at ratio 2.00 the inter-band offset is locally
  measurable at *any* boundary (every second fine stripe realigns), so imp1 scores the relation
  ≈ 0.88 even off-origin — where the genuine tuned grammar (ratio 1.94) scores it ≈ 0.02. The tune
  that made 001's phase origin-dependent handed the on-peak impostor a relation-measurement the
  genuine grammar spent away. Colour (0.20) + leaked relation (0.30) is what lifts those 13
  fragments to candidate. It is still not enough for identification, because ratio and duty (0.50 of
  the weight) are dead. This asymmetry is logged as **SI-016** — a perverse incentive in the tuning
  guidance the spec should price in.
- **The uniform stripe is refused outright** — no cascade, so coverage caps at 0.40 and the flat
  0.160 aggregate never approaches candidate.

Net: the false-positive discipline holds where it must (identification), and where it frays
(candidate, 8 % of a colour-and-relation-sharing impostor set) the cause is fully diagnosed and
attributable to two deliberate choices — sharing 001's inks, and the SI-014/SI-016 phase asymmetry.

---

## 4. Honest commentary — what v0 cannot do

- **Origin-free phase is unmeasurable on the tuned grammar.** The single biggest finding: an
  interior fragment, however large, tops out at candidate because `phase == duty` needs the cascade
  origin (SI-014). Identification of a fragment that is not the whole surface is, for 001 as tuned,
  largely out of reach. Milestone 2 should either add a detectable origin/anchor that distributes
  the phase reference into every band (audit 001's optional micro-rhythm), or the recogniser should
  stop weighting a relation it usually cannot measure.
- **90° single-band ambiguity and rotated-sampling gaps.** The measurer resolves the 90° stripe
  ambiguity once a boundary is in frame, but the Phase-2 rotated sampler degenerates to a zero-size
  window near 90° under aspect jitter (121 fragments skipped, tallied in the manifest). 90° coverage
  is therefore thinner than 0°/7°/30°. A sampler fix is needed before 90° tolerance is fully
  characterised (SI-017 gap 2).
- **Colour was deliberately neutralised in the impostor arm**, so this experiment says nothing about
  how much colour *would* discriminate a real-world look-alike. That is the point of the arm (test
  structure), but it means the reported false-positive margin is a worst case for colour and a best
  case for the impostor.
- **002's identification features are unmeasured.** The battery scores every sheet in `grammars/`,
  and `iso-002`'s structural measures have no v0 measurer (reported unobserved, never a crash); its
  reserved `primitive_frequency_mix` weight (SI-008) is still unspent. Nothing here validates 002.
- **This is L2, not L3.** All degradation is synthetic. The real-photo path (`battery/ingest/`) is
  built and tested against a generated stand-in but has not seen a printed-and-photographed surface.
  L3 stays open until it does (SI-017).

**What surprised.** Two things. First, JPEG down to quality 30 costs *nothing* — the run-length
signature is genuinely compression-proof, more so than expected. Second, the SI-014 phase cost is
not merely a genuine-fragment limitation; it is *symmetric* — it simultaneously weakens the genuine
grammar's interior fragments and strengthens the on-peak impostor's, because measurability of a
relation depends on commensurability, which the canonical peak has and the tune removes (SI-016).
The tune bought ratio distinctiveness by spending phase measurability on *both* sides of the ledger.

**What Milestone 2 should fix.** (1) Distribute the phase reference into every band (origin
marker / micro-rhythm) so interior fragments can be identified, closing the gap this experiment
opened. (2) Reweight or gate relational features by distinctiveness (SI-016). (3) Fix the rotated
sampler and add a genuine single-band arm so the audit-§3 "weak" row is measured directly. (4) Run
`battery.ingest` on real captures to open the L3 question.

Graceful uncertainty is the product, and the data bears it out: the recogniser says `identified`
only for the whole surface, `candidate` for honest partial evidence, and `not_recognised` for the
uniform-stripe impostor — and it never once said `identified` to an impostor.
