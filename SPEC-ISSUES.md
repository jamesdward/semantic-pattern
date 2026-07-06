# SPEC-ISSUES

Places where spec v0.1 (skeleton) was too vague to encode directly. Each entry records
the gap, the smallest reasonable choice made here, and the reasoning — so the spec can
be tightened deliberately rather than by implementation accident.

Format: `SI-NNN` · spec section · status (`open` = spec needs a decision, `decided-here`
= this implementation chose, spec should ratify or overrule).

---

## SI-001 · §5 · decided-here — Distance/scoring computation is named but not defined

§5 says distance computation "is defined by this spec so that clearance is reproducible",
but the skeleton contains no definition. **Choice:** v0 scores each locus feature as a
tolerance-normalised agreement in [0, 1] (1 at exact match, falling to 0 beyond the
declared tolerance), aggregates by declared weights, and publishes the exact formula in
the recogniser docs so any implementer reproduces the same numbers. The inter-sheet
*distance* used for enrolment clearance is deferred until there are enough sheets for it
to matter (Milestone 2+).

## SI-002 · §8 · decided-here — Confidence-vs-sample-size scaling has no functional form

§8 requires aggregate confidence that "scales with sample size" but gives no curve.
**Choice:** each locus feature declares a `sample_unit` (e.g. band boundaries observed,
periods observed, inks observed); per-feature confidence is scaled by n observations of
that unit before weighting. The exact scaling used is documented with the recogniser and
reported in every claim's working. The spec should eventually fix the form so two
conforming recognisers agree "within stated tolerance" (§8) — which is itself unstated.

## SI-003 · §3.7 · decided-here — Canonical peaks are non-exhaustive with no arbiter

The peak list ("0°/45°/90°, ratios 2.0/1.5/φ, duty 1/2 and 1/3, …") is explicitly
non-exhaustive, and nothing says who maintains it. **Choice:** each sheet declares, per
locus feature, the canonical peaks nearest its expected value; the schema validator
rejects any identification-weighted feature whose expected value sits within tolerance of
a declared peak. This makes peak-discounting mechanical but relies on honest declaration —
a shared peak registry is an open spec question.

## SI-004 · §4 · decided-here — JSON-LD canonical format has no @context

§4 mandates JSON-LD but the skeleton defines no vocabulary. **Choice:** sheets are
authored in YAML against a versioned JSON Schema (`schemas/grammar-sheet/v0/`), with keys
chosen to map 1:1 onto a future JSON-LD @context. Signature reference and publication at
`/.well-known/pattern-grammar` are represented as nullable fields (`sheet.issuer`,
`sheet.identity_endpoint`) until enrolment mechanics exist.

## SI-005 · §8/§3 · open — "Orientation-breaking features" have no declaration slot

§8 step 1 normalises orientation "via declared orientation-breaking features", but §3
provides no slot to declare them. For 001, orientation is anchored by the cascade
direction (periods increase one way) plus the tuned 0.7° offset; for a single-band
fragment it is genuinely ambiguous (audit 001 says so). **Choice pending:** v0 recogniser
treats orientation ambiguity as reduced confidence, honestly reported. The spec needs a
`structure.orientation_anchor` (or similar) slot.

## SI-006 · §3.7 · decided-here — Verification and normalisation features need a home

002's multiply-overprint rule identifies nothing (tool default) but *verifies* faithful
reproduction; 001's band module identifies nothing but anchors scale. The spec only
describes identification weights. **Choice:** the schema gives every locus feature a
`role: identification | verification | normalisation`; only identification features carry
weight > 0. Verification failures cap or flag a claim rather than contribute to it. The
spec should adopt this distinction or name a better one.

## SI-007 · §7 · open — Generation/provenance fields undefined for sheets

§7's instance/state/generation model implies sheet fields (validity windows, version
lineage) the skeleton doesn't specify. v0 sheets carry only `grammar_version`; the
family/variation encoding will be forced by Milestone 2 (grammar 002 dialects) and
recorded here when it is.

## SI-008 · §3.7 · decided-here — Audit 002 names an identification feature it never measured

Audit 002 §6 point 2 names "the primitive mix and composition statistics — the
proportions in which the alphabet is used" as one of the two things that carry ISO's
identification load, but the audit publishes **no measured proportions** for it (unlike
the ink set in point 1, which lists nine exact values). The sheet cannot declare an
`expected` it does not have. **Choice:** `iso-002.yaml` declares the
`primitive_frequency_mix` locus feature with `role: identification`,
`weight: 0.25`, `expected: null` and `status: unmeasured` — the weight is *reserved*, not
spent. The validator counts unmeasured weight in the locus sum (so the reservation is
explicit and the sheet still sums to 1.0), and the recogniser skips unmeasured features at
runtime and renormalises the remaining identification weights. The measurement is deferred
to Milestone 2, when primitive-frequency extraction exists; committing the value then is a
`grammar_version` bump, not a schema change. The spec should say how an audit records an
identification dimension it has named but not yet quantified — reserving weight under an
explicit `unmeasured` status is the mechanism proposed here.

## SI-009 · §3.7 · decided-here — Canonical-peak rule only bites on numeric expected values

Spec §3.7 lists canonical peaks as numbers (0°/45°/90°, ratios 2.0/1.5/φ, duty 1/2·1/3).
The mechanical rejection ("expected within tolerance of a peak") is therefore only
well-defined when a feature's `expected` is a scalar number and its `tolerance` is a scalar
number. For non-numeric identification features — an ink *set* (002's `ink_set`, expected =
list of hexes, tolerance = `{delta_e: 10}`) or a colour *pair* (001's `colour_pair`) — the
declared peaks are numeric and do not apply, so the validator skips the peak check for
them. This is correct for the shipped sheets (a specific nine-ink address is the opposite of
a crowded default), but it means the spec has **no defined notion of a canonical peak in
colour space** — e.g. "primary red / pure #FF0000" as a crowded ink value. Colour-space
peaks are left for a later draft; until then the validator does not police them and the
guard rests on honest ink choice (compare SI-003).

## SI-010 · §3.4/§3.7 · decided-here — Relational operands resolve against combination_rules keys

§3.7's relational fingerprint (001's `phase_step == duty_cycle`) needs its operands to name
real declared values, but the spec does not say where a relational feature's operands are
allowed to point. **Choice:** `relation_*` feature operands must resolve to either the id of
another locus feature or a **key defined anywhere in `combination_rules`** (the validator
collects those keys recursively). So 001's `phase_duty_identity` operands
`[phase_step, duty_cycle_light]` resolve to the two constants declared in
`combination_rules`. Operands pointing at `colour_system` inks or `structure` dimensions are
**not** currently resolvable — no shipped feature needs that, and widening the namespace
without a case risks masking typos. The spec should fix the operand namespace explicitly.

## SI-011 · §3.2/§6.1 · decided-here — The finest stripe period is not a declared grammar value

The sheet declares the module (band width, `module_width_relative`) and the cascade
*ratios* (`frequency_ratio`, `duty_cycle_light`, `phase_step`), all scale-invariant. It does
**not** declare the finest band's absolute stripe period — audit 001 measured band 0 at
14.9 px against a 191 px band, but that 14.9/191 ≈ 0.078 is a free choice of the original
maker, not a committed ratio, and nothing in §3 has a slot for it. A generator nonetheless
needs a number to place the first period. **Choice:** the generator carries a single
constant `BASE_PERIOD_RELATIVE = 0.078` (module-relative, from the audit measurement) and
sets `base_period_px = round(BASE_PERIOD_RELATIVE * module_px)` so the finest band lands on a
clean integer pixel count. Every coarser band follows from the declared `frequency_ratio`.
This is a rendering choice, not a signature value (the recogniser scores ratios, never the
absolute finest period), but the spec should decide whether a grammar of this shape ought to
commit a `structure.base_rhythm_relative` (or similar) so two conforming generators produce
identically-scaled surfaces from the same module. Until then, `BASE_PERIOD_RELATIVE` lives in
`generator/cascade.py` and is documented there.

## SI-012 · §8 · decided-here — Band-coordinate ground truth needs the module, absent from a bare surface

Audit 001 s3 makes band-boundary count a first-class ground-truth quantity (a fragment's
identifying power tracks how many band boundaries it spans). The fragment sampler
(`generator/fragments.py`) records this in `FragmentInfo`, but a bare `(H, W, 3)` surface
array carries no record of where its band boundaries are — that needs `module_px` and
`n_bands`, which are generation parameters, not recoverable from pixels without running a
measurement. **Choice:** `sample_fragment` takes optional keyword-only `module_px` / `n_bands`
(defaulting to `None`); when supplied it populates the band-coordinate fields of
`FragmentInfo`, and when absent those fields stay `None`. The battery, which generates the
surface, always has these to hand and passes them. This keeps the sampler usable on any
surface while making the band ground truth exact when the generation params are known. The
spec has no notion of a surface carrying its own generation provenance; if one is added
(compare SI-007), the module would travel with the surface and this parameter could drop.

## SI-013 · §8 · decided-here — Verdict thresholds (identified / candidate) are undefined

§8 step 4 requires a recogniser to report "high-confidence identification · candidate-with-
probability · or honest non-recognition" but fixes no numeric boundary between those three
buckets. **Choice:** recogniser v0 calls an aggregate confidence `identified` at `>= 0.70`,
`candidate` at `>= 0.40`, else `not_recognised`, and publishes both thresholds in every
claim's `working` so a reader can re-bucket. The numbers are calibrated against the
sample-size saturation of `score.py` (SI-002): with those `k` constants the canonical
5-band surface lands ~0.77 and a single band is capped at 0.40, so 0.70/0.40 place the
boundaries where the audit-s3 fragment-strength map wants them. They are **not** universal:
the spec should either fix thresholds (so "two conforming recognisers agree", §8) or, better,
require the recogniser to publish its own thresholds + scoring constants and compare claims
by the raw numbers rather than the verdict word. Until then the words are advisory and the
three published numbers (renormalised score, coverage, aggregate) are the real output.

## SI-014 · §8/§3.7 · open — The phase = duty fingerprint needs the phase *origin*, not just a boundary

Audit 001 s3's fragment-strength map says one band boundary already yields "one phase
observation", implying the phase step is measurable wherever a boundary is in frame. In
practice it is **not**: the per-band phase accumulates from the pattern's origin, and the
offset between two adjacent bands is only pinned relative to that origin. Because the cascade
ratio (1.94) is irrational-ish, the visible offset between a coarser stripe and the nearest
finer stripe takes the values `(phase_step + (ratio-1)·j) mod 1` over successive coarse
stripes `j` — an equidistributed set carrying no single recoverable number. An interior
fragment that does not contain the phase origin therefore cannot confirm `phase == duty`; a
fragment that *does* (e.g. a full-height slice reaching the origin row, or the whole surface)
measures it cleanly at 0.31. **Consequence in v0:** the recogniser measures the phase step
best-effort with `n = boundaries`, but on origin-free fragments its agreement is honestly
low, so such fragments top out as *candidates* while origin-capturing captures reach
*identified*. This is arguably the true shape of audit s3's "unevenly distributed signature":
the phase component of the genome lives not merely in the transitions but at the origin of the
cascade. The spec/audit should either (a) refine the fragment-strength map to say the phase
observation needs the origin, or (b) add a structural anchor (a detectable origin marker, cf.
the audit's optional "micro-rhythm") that distributes the phase reference into every band.

Sharper corollary, stated plainly: **this cost was created by the tune.** At the canonical
ratio 2.00 the adjacent periods are commensurate — every second fine stripe realigns with a
coarse stripe, so the inter-band offset is a single locally measurable number at *any*
boundary. Tuning 2.00 → 1.94 (audit s4) bought distinctiveness in the ratio dimension by
spending local measurability in the phase dimension. Tuning is not free: moving a value off
a canonical peak can change *which fragments can measure which locus features*. The spec's
tuning guidance (§6.2 step 3) should require re-deriving the fragment-strength map after
any tune.

## SI-015 · §8 · decided-here — "Aggregate confidence" must carry coverage as well as match quality

§8 step 3 asks for "an aggregate confidence that scales with sample size" but a single scalar
cannot honestly express two independent facts: *how well what we saw matched* and *how much of
the signature locus we actually saw*. A fragment that observes only duty + colour and matches
them perfectly would, on a naive weighted-mean-over-observed-features scheme, report ~1.0 —
hiding that 60% of the locus (cascade, phase) was never seen. **Choice:** the recogniser
publishes **three** numbers — `renormalised_score` (weighted confidence over *observed*
identification features: "of what we saw, how well did it match"), `coverage` (observed
identification weight / measurable identification weight: "how much did we see"), and their
product `aggregate_confidence`. The product is the honest cap the audit-s3 map demands: a
single-band fragment has coverage 0.40, so its aggregate cannot exceed 0.40 however perfect
the match. Weight of features the *sheet* marks `status: unmeasured` (SI-008) is excluded from
the coverage denominator (reserved, not owed); weight of features the sheet expects but this
recogniser could not observe (n = 0) stays in the denominator, so it honestly drags coverage
down. The spec should adopt the three-number output (or an equivalent that keeps match-quality
and coverage separable) rather than a single conflated confidence.

## SI-016 · §8/§11 · open — A canonical-peak impostor leaks the phase = duty relation *because* the genome was tuned off the peak

The Phase-4 battery (`experiments/exp-001-synthetic-battery`) confirms SI-014 empirically and
turns up its mirror image on the impostor side. The three relational-fingerprint features of 001
are ratio (weight 0.30), duty (0.20) and the `phase == duty` relation (0.30); the tune moved the
ratio 2.00 → 1.94 to leave the crowded power-of-two peak. **Consequence measured here:** a
canonical-peak impostor rendered at ratio **2.00** (with phase = duty = 1/3, and 001's inks by
construction) can *locally* measure its own `phase == duty` relation at any boundary — because at
the commensurate ratio 2.00 every second fine stripe realigns with a coarse stripe, so the
inter-band offset is a single locally recoverable number (SI-014's corollary, run forwards). The
genuine tuned grammar at 1.94 *spent* that local measurability, so its interior fragments score
the relation ≈ 0.02 while the ratio-2.00 impostor scores it ≈ 0.88. Net effect in the data: the
impostor's ratio and duty agreements both collapse to ≈ 0 (2.00 and 0.333 are each more than a
tolerance off 1.94 / 0.31), but colour (shared by construction, weight 0.20) plus the leaked
relation (weight 0.30) lift 13/168 of its fragments just over the **candidate** line (max
aggregate 0.418) at mid fractions — **never `identified`** (the two collapsed features cap it).
The headline discipline holds (no impostor reaches identification), but the near-miss is real and
diagnosable. **Spec consequence:** a relational fingerprint whose measurability depends on the
operands' *commensurability* is more measurable for an on-peak impostor than for the off-peak
genuine grammar — a perverse incentive the tuning guidance (§6.2) does not currently price in.
The spec should either (a) require relational features to be scored only when an origin/anchor
pins the relation for the enrolled grammar too (so genuine and impostor are measured on equal
terms), or (b) weight a relation feature by how *distinctively* its value sits (phase = duty = 1/3
is itself a crowded coordinate; phase = duty = 0.31 is not), so a relation shared at a canonical
value carries less than one shared at a tuned value. Colour is not a discriminator in this arm at
all (impostors carry 001's exact inks on purpose), which is why the entire false-positive margin
rests on the structural features — as the audit intended.

## SI-017 · §11 · decided-here — The synthetic battery is not the field battery; L3 stays open

Spec §11 makes L3 ("Field-proven") depend on recognition "under the hostile-conditions test
battery (print, camera, light, angle, damage)" and says the L3 criteria are "deliberately
unfinished until that data exists". This phase produces the *synthetic* half of that data:
deterministic, parameterised degradations (`battery/degrade.py`) standing in for each field axis,
swept against the fragment/rotation grid, with a real-photo ingestion seam (`battery/ingest/`)
defined and tested but **not yet exercised on real captures** (print-and-photograph is out of
scope this session). **Choice:** the battery reports L2-style evidence (an open recogniser
identifying generated surfaces from fragments at stated confidence under stated synthetic
conditions) and leaves L3 explicitly unmet until `battery.ingest` is run on
printed-and-photographed surfaces with a published manifest. The spec should say whether synthetic
degradation evidence counts toward L3 at all, or only as L2 corroboration; this implementation
treats it as the latter. Two v0 gaps the field battery must also close, surfaced by this run and
not silently capped: (1) the genuine fragment sweep uses fractions ≥ 0.05 at module 200 px, so a
window is always ≥ ~223 px against a 200 px module and therefore always spans ≥ 1 boundary — the
audit-s3 "part of one band" (single-band, coverage-0.40) row is characterised here only by the
uniform-stripe impostor and the recogniser unit tests, not by the genuine frac sweep; and (2) the
Phase-2 rotated fragment sampler degenerates to a zero-size window at ≈ 90° when aspect jitter
makes the window far wider than its fitting margin, so those fragments are honestly skipped and
tallied (`skipped_geometry` in the manifest) rather than measured — a sampler bug to fix before
90° coverage is complete, not a spec gap.

## SI-018 · §3 · decided-here — The audit names a "density"/composition dialect but publishes no composition model, so the grid generator supplies one

Audit 002 (§3, §4 `variation.per_variant: [ink subset, composition, density]`) lists *composition*
and *density* as things that vary per variant, and §3 states plainly that "the compositions are
figurative and free — the genome is the construction system, not the picture." It therefore
publishes **no** placement model: no primitive-frequency proportions, no density scale, no rule
for how instances are laid on the grid. That is deliberate — the genome is the alphabet + grid +
rhythm + overprint + ground, and the picture is dialect freedom — but the generator
(`generator/grid.py`) must still *choose* a concrete composition to emit pixels. **Choice made:**
seeded free placement of `round(density × cols × rows)` primitive instances, each of a
uniformly-chosen type from the five-primitive alphabet, at seeded grid positions/sizes/orientations,
drawn in a seeded 4–6 ink subset of the master set. The `density` parameter (default 0.45) maps to
audit §3 "density" as *instances per cell*: it scales only how crowded the free composition is, and
touches nothing in the genome (grid, alphabet, stripe rhythm, overprint, ground are all invariant to
it). The uniform type distribution is an explicit non-commitment: the sheet's `primitive_frequency_mix`
feature (weight reserved, `status: unmeasured`, SI-008) is exactly the proportions the audit §6.2 names
but never publishes, so the generator records the realised counts as ground truth (`GroundTruth.primitive_counts`)
without claiming any distribution is *the* identity's. **Spec consequence:** the meta-grammar should
say whether a composition/density model is (a) purely a generator concern outside the enrolled genome
(this implementation's assumption), or (b) itself an enrollable dialect feature with committed
proportions — and if (b), it must define the units of "density" and the reference for a
primitive-frequency-mix distribution so a recogniser can score it. Until then this generator treats
composition as free (a), and its density/mix parameters as reproducibility knobs, not signature.

## SI-019 · §2 · decided-here — Exact multiply overprint is not associative under rounding, so overprint depth is capped at 2

Audit 002 §2 verifies the overprint rule to the integer: an overlap colour equals the channel-wise
multiply of its parents, `c = round(c1·c2 / 255)`, confirmed exact on three measured pairs
(green×yellow → (115,251,33), etc.). The audit only ever measures **two-ink** overlaps — every row in
its overprint table is a single pair — and never states what a *third* ink crossing a two-ink overlap
should yield. This matters because the integer-rounded multiply is **not associative**:
`round(round(a·b)/·c)` can differ from `round(a·round(b·c))` by a unit, and more importantly a
free composition with deep pile-ups would populate the output with triple-, quadruple- … product
colours, none of which the audit sanctions or a recogniser's pairwise self-verification
(audit §5: "a fragment where inks cross contains parents and product together") can check. **Choice made:**
the generator caps overprint at 2 inks deep — a third ink does not print where two already overlap
(`generator/grid.py`, `MAX_DEPTH = 2`). White is an exact multiply identity
(`round(255·c/255) == c`), so the ground never counts toward depth. This keeps the strong whole-image
invariant *every output colour is white, a lone ink, or a pairwise ink product* exactly true and
pixel-checkable, at the cost that dense pile-ups clip the third ink rather than deepening. **Spec
consequence:** the meta-grammar should state, for a multiply-overprint colour model, either (a) that
overprint is defined only pairwise and deeper stacks are out of grammar (this implementation), or
(b) a committed n-ink compositing order + rounding so deep overlaps are themselves derivable and
verifiable — since without one, "overlap == multiply(parents)" is ambiguous the moment three inks meet.
