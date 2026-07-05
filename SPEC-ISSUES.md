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
