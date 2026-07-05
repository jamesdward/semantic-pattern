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
