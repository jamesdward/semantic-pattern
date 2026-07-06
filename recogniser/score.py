"""Scoring: measured features x a grammar sheet -> per-feature + aggregate (spec 8 step 3).

This is the *scoring* half of the recogniser, strictly separate from measurement
(``recogniser/measure.py`` never sees a sheet; this module never touches pixels).
It takes the ``Measurement`` objects a measurer produced and a loaded grammar
sheet, and reports, for every signature-locus feature, an agreement in [0, 1], a
sample-size-scaled confidence, and finally an aggregate that reflects BOTH how
well what we saw matched and how much of the locus we actually saw.

SCORING FORMULAS (published here so any conforming recogniser reproduces the
numbers -- spec 8 "reproducible"; SI-001 asks for exactly this):

  1. Per-feature agreement (SI-001), clipped-linear ("triangular"):

         agreement = max(0, 1 - |measured - expected| / tolerance)

     1 at an exact match, falling linearly to 0 at the tolerance boundary and
     staying 0 beyond it. Chosen over a Gaussian for one reason: the spec says
     agreement must reach 0 *at* the tolerance, and this hits exactly 0 there
     (a Gaussian only asymptotes). So a value two tolerances out scores a hard 0.

  2. Relational features (measure ``relation_*``, SI-001): the fingerprint is the
     relation between two *measured* operands, never their absolute values. For
     001's ``phase_step == duty_cycle`` we score

         agreement = max(0, 1 - |op1_measured - op2_measured| / tolerance)

     i.e. measured phase step vs measured duty, not vs any sheet constant.

  3. Colour (measure ``ink_set_match``): each observed ink is matched to the
     nearest sheet ink in CIE Lab; distance is delta-E76 (Euclidean Lab, the
     "delta_e" tolerance). Per-ink agreement is the same clipped-linear curve on
     delta-E / delta_e; the feature agreement is the mean over observed inks.

  3b. Primitive-frequency mix (measure ``primitive_frequency_mix``, SI-026): the
     measured and expected values are 5-bin instance-share vectors; agreement is
     the clipped-linear curve on their L1 distance over an n-DEPENDENT effective
     tolerance,

         tolerance_eff = tolerance * max(1, sqrt(N_REF_PRIMITIVES / n))

     because a share vector estimated from n interior instances of a 5-bin
     multinomial has L1 sampling deviation ~ sqrt(1/n): a small fragment's larger
     L1 is sampling noise, not disagreement (the n/(n+k) saturation below already
     handles "we saw little, so confidence is low"). At n >= N_REF_PRIMITIVES the
     committed tolerance applies unchanged, so full-measurement impostor
     rejection is never loosened. The claim's working carries n, tolerance_eff
     and the L1.

  4. Sample-size scaling (SI-002): a feature's confidence discounts its agreement
     by how many samples of its declared ``sample_unit`` were seen,

         confidence = agreement * saturation(n),   saturation(n) = n / (n + k)

     with a small per-unit k (SATURATION_K below): 0.5 for band boundaries, 2
     for periods, 0.5 for inks (two inks already complete the pair), 1 otherwise.
     Boundaries and inks saturate fast (a couple of transitions is already
     strong cascade evidence; two inks complete the colour pair); periods are
     cheap so they saturate slower.
     n = 0 means not observed: the feature is reported unobserved and its weight
     is renormalised away (it does NOT score 0, which would be a false negative).

  5. Aggregate (spec 8 step 3; audit s3 fragment-strength map). Two numbers,
     honestly separated:

         renormalised_score = sum over OBSERVED id-features of
                              (weight_i / sum observed weights) * confidence_i
             -- "of what we saw, how well did it match"

         coverage = (sum observed id-feature weights) / (sum measurable id-feature
                    weights)
             -- "how much of the locus did we see"

         aggregate_confidence = renormalised_score * coverage

     The coverage multiply is the honest cap the audit demands: a single-band
     fragment observes only duty + colour (weight 0.40 of 001's locus), so its
     coverage is 0.40 and its aggregate cannot exceed 0.40 however perfectly
     those two match -- it can never be "identified" (audit s3: part of one band
     is a weak sample). "Measurable" excludes features the *sheet* declares
     ``status: unmeasured`` (SI-008: reserved weight, renormalised away, not part
     of coverage); features the sheet expects but this recogniser cannot observe
     in the image (n = 0) DO count in the coverage denominator, so an unobserved
     structure feature honestly drags coverage down.

VERDICT THRESHOLDS are a v0 choice the spec does not fix (see SPEC-ISSUES
SI-013): ``identified`` at aggregate >= 0.70, ``candidate`` at >= 0.40, else
``not_recognised``. They are reported in every claim so a reader can re-bucket.
"""

from __future__ import annotations

import cv2
import numpy as np

# Verdict thresholds (v0 choice; SI-013). Reported in the claim.
IDENTIFIED_THRESHOLD = 0.70
CANDIDATE_THRESHOLD = 0.40

# White-balance relationship path (grid ink match, SI-020). A single per-channel
# diagonal correction gain must fall in these bounds to count as a plausible
# illuminant/white-balance shift; a gain outside them means the colours differ by
# more than a global cast, so the relationship path is rejected and only the
# absolute match stands.
GAIN_MIN = 0.5
GAIN_MAX = 2.0

# Channel-saturation guards for the relationship path (SI-020). A measured
# channel at >= CLIP_HIGH was (or may have been) clipped by the cast: its true
# pre-gain value is unknowable (only a lower bound survives), so it must not
# feed the gain estimate, and after correction it can only be scored one-sidedly.
# Symmetrically, a channel at <= CLIP_LOW is too small for a stable sheet/measured
# ratio (and may have been floored by a gain < 1). A channel's gain needs at
# least MIN_GAIN_OBS unclipped observations; with fewer it falls back NEUTRALLY
# to 1.0 with a caveat in the working (documented choice: neutral, not the
# cross-channel median, because white balance is per-channel by definition and
# borrowing another channel's cast would fabricate a correction).
CLIP_HIGH = 250.0
CLIP_LOW = 5.0
MIN_GAIN_OBS = 2

# Consensus window for the per-channel gain estimate (SI-020). A genuine global
# cast makes every true-ink ratio agree to within ~1%; overprint colours that
# leak into the extracted ink set under the cast produce scattered junk ratios.
# The estimator takes the LARGEST cluster of ratios that mutually agree within
# this relative tolerance (deterministic; ties broken by tighter spread, then
# smaller ratio) -- robust up to half the observations being junk, where a plain
# median already breaks at ~1/3.
GAIN_CONSENSUS_REL_TOL = 0.05

# The relationship (white-balance-gain) path needs at least this many measured
# inks to be credible evidence of a GLOBAL colour cast (SI-020). With fewer, a
# free per-channel gain plus assignment freedom can overfit a handful of colours
# onto any palette -- so below this the GRID relationship path is disabled and only
# the absolute match stands, keeping cross-grammar discrimination honest (a two-ink
# band fragment cannot borrow the grid ink-set's leniency).
MIN_INKS_FOR_GAIN = 3

# --- band (2-ink) white-balance relationship path (SI-027) ------------------
#
# 001's band grammar has exactly TWO inks, below the grid path's MIN_INKS_FOR_GAIN
# credibility floor: a per-channel diagonal gain (3 free parameters) fit to a
# 2-ink / 6-observation system is nearly free to overfit -- it could map almost any
# dark/light colour pair onto the two greens. exp-003's pilot showed the real need
# anyway: photographed inks warm-shift 15-30 delta-E off enrolled, so the
# absolute-only band path fails on every real photo (0/24 colour agreement). SI-027
# ports two-path colour to band sheets HONESTLY, with two guards replacing the
# grid path's consensus-over-many-inks robustness that 2 inks cannot provide:
#
#   1. TIGHTER gain bounds than the grid path. A 2-ink system has no ratio
#      consensus to reject a bad gain, so the plausible-illuminant window is
#      narrowed (a global camera/display white balance is a modest cast, not a 2x
#      channel swing).
#   2. GAIN-INVARIANT corroboration from the sheet's OWN declared relationships
#      (colour_system.relationships, spec 3.5): the measured pair must preserve the
#      luminance ORDERING (light brighter than dark) AND the pair's HUE PROXIMITY
#      (the two inks are near-hue -- 001 is two greens). A positive diagonal gain
#      is order-preserving and, for two already-near-hue inks, proximity-preserving,
#      so these hold under a real cast but fail when the "inks" are two unrelated
#      colours a gain merely mapped close -- exactly the overfit the 2-ink system
#      is prone to. Both are read from the sheet's expected inks, so the rule
#      generalises to any 2-ink band sheet, not just 001.
#
# This phase fits DIAGONAL GAIN ONLY. The pilot's warm shift is roughly
# diagonal-plus-display-bloom (the light ink brightens disproportionately); a bloom
# model is deferred (SI-027 open corollary) and the post-gain residual is reported,
# not hidden.
BAND_GAIN_MIN = 0.6
BAND_GAIN_MAX = 1.7
# Extra hue slack (OpenCV hue units, 0..179) allowed on the measured pair beyond
# the expected pair's own hue distance before the proximity corroboration fails.
BAND_HUE_SLACK = 18

# Overprint-consistency verification tolerance (SI-020): Chebyshev distance (RGB
# units) between a measured overprint colour and the exact multiply of its
# parents. Clean renders sit at 0; a residual beyond this reads as broken
# arithmetic (audit s5 "c1*c2 != c3") and flags the claim's verification.
OVERPRINT_RESIDUAL_TOL = 12.0

# Reference sample size for the primitive-frequency-mix tolerance (SI-026): the
# typical INTERIOR classified-instance count of a full surface in the derivation
# corpus that produced the committed expected vector (grammars/iso-002.yaml,
# seeds x densities 0.35/0.45/0.55 x modules 40/48/64 on 14x10 grids, border
# components excluded). The mix feature's effective tolerance is
#
#     tolerance_eff = committed_tolerance * max(1, sqrt(N_REF_PRIMITIVES / n))
#
# -- a share vector from n instances of a 5-bin multinomial has L1 sampling
# deviation ~ sqrt(1/n), so a small-n fragment's larger L1 is sampling noise, not
# disagreement; at n >= N_REF the committed tolerance applies unchanged, so
# full-surface impostor rejection is never loosened by this rule.
N_REF_PRIMITIVES = 16

# Sample-size saturation constants per declared sample_unit (SI-002). Small
# integers: two boundaries already give real cascade evidence, a couple of inks
# complete the pair, but periods are cheap so they saturate slower.
SATURATION_K = {
    "band_boundaries": 0.5,
    "periods": 2.0,
    "inks_observed": 0.5,
    "bands": 1.0,
    "cells": 1.0,
    "striped_regions": 1.0,
    "diagonals": 1.0,
    "overlaps": 1.0,
    # 2.0 (not 1.0): the mix is a 5-BIN SHARE VECTOR, not a scalar -- a handful of
    # instances pins it far less than a handful of periods pins a period, and the
    # n-dependent tolerance (SI-026) simultaneously widens at small n, so the
    # saturation must discount harder to keep a noisy-but-lucky small-n mix from
    # pushing a same-ink impostor fragment over the identified line.
    "primitives_observed": 2.0,
}
DEFAULT_SATURATION_K = 1.0

# Operand names (in a relational feature) -> the measurement that carries them.
OPERAND_TO_MEASUREMENT = {
    "phase_step": "phase_step",
    "duty_cycle_light": "duty_cycle",
    "duty_cycle": "duty_cycle",
}


def agreement_linear(measured: float, expected: float, tolerance: float) -> float:
    """Clipped-linear agreement in [0, 1] (formula 1 above)."""
    if tolerance is None or tolerance <= 0:
        return 1.0 if measured == expected else 0.0
    return float(max(0.0, 1.0 - abs(measured - expected) / tolerance))


def saturation(n: int, sample_unit: str) -> float:
    """Sample-size scaling n / (n + k) (formula 4 above)."""
    if n <= 0:
        return 0.0
    k = SATURATION_K.get(sample_unit, DEFAULT_SATURATION_K)
    return float(n / (n + k))


def _hex_to_lab(hex_value: str) -> np.ndarray:
    """'#RRGGBB' -> CIE Lab (L in [0,100], a,b in ~[-127,127]) via cv2 float path."""
    h = hex_value.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    bgr = np.array([[[b, g, r]]], dtype=np.float32) / 255.0
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)
    return lab[0, 0].astype(np.float64)


def _hex_to_bgr255(hex_value: str) -> np.ndarray:
    """'#RRGGBB' -> np.array([B, G, R], float 0..255) for gain estimation."""
    h = hex_value.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return np.array([b, g, r], dtype=np.float64)


def _bgr_to_lab(bgr) -> np.ndarray:
    """Mean BGR triple (0..255) -> CIE Lab, same float path as _hex_to_lab."""
    arr = np.array([[[float(bgr[0]), float(bgr[1]), float(bgr[2])]]],
                   dtype=np.float32) / 255.0
    lab = cv2.cvtColor(arr, cv2.COLOR_BGR2Lab)
    return lab[0, 0].astype(np.float64)


def _delta_e76(lab_a: np.ndarray, lab_b: np.ndarray) -> float:
    """delta-E76: plain Euclidean distance in CIE Lab (documented choice)."""
    return float(np.sqrt(((lab_a - lab_b) ** 2).sum()))


def _score_ink(feature: dict, measurement) -> dict:
    """Match observed inks to the sheet ink set in Lab; agreement = mean over inks."""
    expected_hexes = feature.get("expected") or []
    tol = feature.get("tolerance") or {}
    delta_e_tol = float(tol.get("delta_e", 10.0)) if isinstance(tol, dict) else 10.0
    expected_labs = [_hex_to_lab(h) for h in expected_hexes]

    observed = measurement.value or []
    per_ink = []
    for bgr in observed:
        lab = _bgr_to_lab(bgr)
        best = min((_delta_e76(lab, e) for e in expected_labs), default=float("inf"))
        per_ink.append({
            "measured_bgr": [round(float(c), 1) for c in bgr],
            "nearest_delta_e": round(best, 3),
            "agreement": round(agreement_linear(best, 0.0, delta_e_tol), 4),
        })
    agree = float(np.mean([p["agreement"] for p in per_ink])) if per_ink else 0.0
    return {
        "agreement": agree,
        "expected": expected_hexes,
        "measured": [p["measured_bgr"] for p in per_ink],
        "detail": {"delta_e_tolerance": delta_e_tol, "per_ink": per_ink},
    }


def _greedy_assign_matrix(dist):
    """Greedy min-distance assignment over a precomputed matrix (Hungarian approx).

    ``dist[mi][ei]`` is the distance between measured ink ``mi`` and expected ink
    ``ei``. For <= 9 inks a global-greedy assignment (repeatedly take the smallest
    available pair, each expected used once) is within a hair of optimal and fully
    deterministic. Returns ``[(mi, ei, distance), ...]`` covering every measured
    ink (sorted by ``mi``). If there are more measured inks than expected, the
    surplus fall back to their nearest expected (with reuse) so every measured
    ink is scored.
    """
    cand = []
    for mi, row in enumerate(dist):
        for ei, d in enumerate(row):
            cand.append((d, mi, ei))
    cand.sort()
    used_m, used_e = set(), set()
    pairs = []
    for d, mi, ei in cand:
        if mi in used_m or ei in used_e:
            continue
        pairs.append((mi, ei, d))
        used_m.add(mi)
        used_e.add(ei)
        if len(used_m) == len(dist):
            break
    for mi, row in enumerate(dist):
        if mi in used_m:
            continue
        ei = int(np.argmin(row))
        pairs.append((mi, ei, row[ei]))
    pairs.sort(key=lambda p: p[0])
    return pairs


def _greedy_assign(measured_labs, expected_labs):
    """Greedy assignment measured<->expected on plain delta-E76 in Lab."""
    dist = [[_delta_e76(ml, el) for el in expected_labs] for ml in measured_labs]
    return _greedy_assign_matrix(dist)


def _rank_corr(measured_vals, expected_vals):
    """Spearman rank correlation between two equal-length sequences.

    A single positive per-channel gain is monotonic, so it preserves the
    per-channel ORDERING of the inks -- a rank correlation ~1 corroborates that
    the ink relationships (orderings) survive the shift even where absolute values
    do not (audit s6). Ties are averaged; degenerate (constant) input scores 0.
    """
    m = np.asarray(measured_vals, dtype=np.float64)
    e = np.asarray(expected_vals, dtype=np.float64)
    if m.size < 2:
        return 0.0
    rm = _ranks(m)
    re = _ranks(e)
    if rm.std() == 0 or re.std() == 0:
        return 0.0
    return float(np.corrcoef(rm, re)[0, 1])


def _ranks(values: np.ndarray) -> np.ndarray:
    """Average ranks of ``values`` (ties share the mean rank). Deterministic."""
    order = np.argsort(values, kind="stable")
    ranks = np.empty(values.size, dtype=np.float64)
    ranks[order] = np.arange(values.size, dtype=np.float64)
    # Average ties so a gain that maps equal values to equal values scores 1.
    for v in np.unique(values):
        idx = values == v
        ranks[idx] = float(np.mean(ranks[idx]))
    return ranks


def _consensus_ratio(ratios):
    """Largest-consensus estimate of a shared ratio (SI-020 gain estimator).

    Returns the median of the biggest cluster of ``ratios`` that mutually agree
    within ``GAIN_CONSENSUS_REL_TOL`` (relative), or ``None`` if fewer than
    ``MIN_GAIN_OBS`` values are given. Deterministic: candidate clusters are
    anchored at each ratio in sorted order; ties on size break by tighter spread,
    then by smaller median. Chosen over a plain median because the junk fraction
    (overprint colours leaking into the ink set under a colour cast, then
    misassigned) can reach ~1/2, where a median is already dragged off; the true
    cast forms a tight cluster the junk cannot imitate.
    """
    if len(ratios) < MIN_GAIN_OBS:
        return None
    ordered = sorted(ratios)
    best = None  # (count, -spread, median) maximised
    for anchor in ordered:
        members = [r for r in ordered
                   if abs(r - anchor) <= GAIN_CONSENSUS_REL_TOL * anchor]
        if len(members) < MIN_GAIN_OBS:
            continue
        spread = members[-1] - members[0]
        med = float(np.median(members))
        key = (len(members), -spread, -med)
        if best is None or key > best[0]:
            best = (key, med)
    return best[1] if best is not None else None


def _clip_aware_delta_e(corrected_bgr, clipped_high, expected_bgr, expected_lab):
    """Delta-E76 of a gain-corrected ink vs a sheet ink, one-sided on clipped channels.

    A channel measured at >= CLIP_HIGH was saturated by the cast, so after
    correction it is only a LOWER BOUND on the true ink channel (true post-gain
    value >= 255, hence true ink >= 255 * correction_gain). A clipped channel
    therefore cannot *match* -- that is measurement loss, not identity mismatch
    (SI-020) -- so its contribution is capped one-sidedly: if the sheet channel is
    at or above the bound, the observation is CONSISTENT with the sheet ink and
    the channel is scored as a match (substituted by the sheet value); if the
    sheet channel is BELOW the bound, even the most charitable reading exceeds it
    and the (real) mismatch is kept. Unclipped channels score normally.
    """
    if not any(clipped_high):
        return _delta_e76(_bgr_to_lab(corrected_bgr), expected_lab)
    adjusted = np.array(corrected_bgr, dtype=np.float64)
    for ch in range(3):
        if clipped_high[ch] and expected_bgr[ch] >= adjusted[ch]:
            adjusted[ch] = expected_bgr[ch]
    return _delta_e76(_bgr_to_lab(adjusted), expected_lab)


def _score_ink_grid(feature: dict, measurement) -> dict:
    """Two-path ink-set match for GRID sheets: absolute vs relationship (SI-020).

    The milestone requirement: ink-set matching with colour measured as
    relationships/orderings, not absolutes, so it survives a white-balance shift
    (audit s6). Two paths, and the feature agreement is their MAX:

      (a) ABSOLUTE. Greedy-assign measured inks to sheet inks in Lab; per-ink
          agreement is the clipped-linear curve on delta-E76 / delta_e; agreement
          is the mean over measured inks. This is the print-fragile path -- a
          global colour cast pushes every delta-E up and it degrades.

      (b) RELATIONSHIP. Estimate a single per-channel diagonal correction gain
          ``g`` (sRGB BGR, "map measured onto sheet") as the largest CONSENSUS
          cluster of per-channel sheet/measured ratios over the assignment
          (``_consensus_ratio``), SATURATION-AWARE: channel observations measured
          at >= CLIP_HIGH (clipped by the cast; true value unknowable) or
          <= CLIP_LOW (floored/unstable) are excluded, and a channel with fewer
          than MIN_GAIN_OBS unclipped observations falls back neutrally to 1.0
          with a caveat. Apply ``g`` to the full extracted palette, re-separate
          products in the corrected space, re-assign and re-measure delta-E76 --
          scoring the clipped channels of clipped inks ONE-SIDEDLY
          (``_clip_aware_delta_e``: a clipped channel is a lower bound, so
          consistency with the sheet ink counts as a match; measurement loss is
          not identity mismatch). If a single global gain within
          [GAIN_MIN, GAIN_MAX] explains the shift, the ink RELATIONSHIPS
          (ratios/orderings) hold, so this path stays high where (a) fell. The
          IMPLIED APPLIED gain (1/g) is reported -- it recovers the white balance
          that was applied.

    A per-channel rank correlation corroborates the ordering survives (a positive
    gain is rank-preserving). The working carries both paths, the gain, the
    implied applied gain, the in-bounds flag, the rank correlation and the
    clipped-ink handling.
    """
    expected_hexes = feature.get("expected") or []
    tol = feature.get("tolerance") or {}
    delta_e_tol = float(tol.get("delta_e", 10.0)) if isinstance(tol, dict) else 10.0
    expected_labs = [_hex_to_lab(h) for h in expected_hexes]
    expected_bgr = [_hex_to_bgr255(h) for h in expected_hexes]

    observed = measurement.value or []
    measured_bgr = [np.array([float(c) for c in bgr], dtype=np.float64) for bgr in observed]
    measured_labs = [_bgr_to_lab(bgr) for bgr in measured_bgr]
    if not measured_bgr or not expected_labs:
        return {"agreement": 0.0, "expected": expected_hexes, "measured": [],
                "detail": {"reason": "no inks to match"}}

    # (a) absolute path.
    assign_abs = _greedy_assign(measured_labs, expected_labs)
    abs_deltas = [d for _, _, d in assign_abs]
    per_ink_abs = [agreement_linear(d, 0.0, delta_e_tol) for d in abs_deltas]
    agreement_abs = float(np.mean(per_ink_abs))

    # (b) relationship path: a single per-channel diagonal correction gain that
    # maps measured -> sheet, estimated as the largest CONSENSUS cluster of
    # per-channel sheet/measured ratios over the absolute assignment
    # (_consensus_ratio). Consensus (not least squares, not a plain median)
    # because a global colour cast breaks the multiply overprint arithmetic, so
    # overprint colours leak into the extracted ink set under white balance and
    # get misassigned -- up to ~half the ratios can be junk, which drags a median
    # but cannot imitate the tight cluster the true inks form. The leaked colours
    # are re-separated in corrected space below; any that remain score ~0 in the
    # agreement mean (they are not sheet inks), so the path is not flattered.
    # Saturation-aware (SI-020): a channel observation at >= CLIP_HIGH is clipped
    # (true pre-gain value unknowable) and one at <= CLIP_LOW is unstable/floored,
    # so neither feeds the ratio; a channel with fewer than MIN_GAIN_OBS unclipped
    # observations falls back neutrally to gain 1.0 with a caveat.
    m_stack = np.array([measured_bgr[mi] for mi, _, _ in assign_abs])   # (k,3)
    s_stack = np.array([expected_bgr[ei] for _, ei, _ in assign_abs])   # (k,3)
    gain = np.ones(3, dtype=np.float64)
    gain_fallback_channels = []
    for ch in range(3):
        ratios = [s_stack[i, ch] / m_stack[i, ch]
                  for i in range(len(m_stack))
                  if CLIP_LOW < m_stack[i, ch] < CLIP_HIGH]
        estimate = _consensus_ratio(ratios)
        if estimate is not None:
            gain[ch] = estimate
        else:
            gain_fallback_channels.append("BGR"[ch])
    in_bounds = bool(np.all((gain >= GAIN_MIN) & (gain <= GAIN_MAX)))
    enough_inks = len(measured_bgr) >= MIN_INKS_FOR_GAIN
    relationship_ok = in_bounds and enough_inks

    # Undo the gain on the FULL extracted palette (inks + products) and re-separate
    # products in the corrected space -- there the multiply overprint arithmetic
    # holds again, so the couple of overprint colours that leaked into the ink set
    # under white balance are correctly removed before the corrected ink set is
    # matched (SI-020). Falls back to the ink set alone if the full palette was not
    # carried in the measurement. Each corrected colour carries a clip mask
    # (which channels were measured saturated), so the re-match can score clipped
    # channels one-sidedly (see _clip_aware_delta_e).
    from recogniser import measure_grid as _mg
    extracted = getattr(measurement, "detail", {}).get("extracted")
    if extracted:
        raw_palette = [np.array(bgr, dtype=np.float64) for bgr, _ in extracted]
        corrected_palette = [(tuple(np.clip(r * gain, 0.0, 255.0)), f)
                             for r, (_, f) in zip(raw_palette, extracted)]
        # Clip mask per corrected colour. The gain map is injective on unclipped
        # values, so keying by the corrected tuple is safe; on the rare collision
        # (two clipped raws collapsing) the masks are OR-ed.
        clip_by_tuple = {}
        for r, (ct, _) in zip(raw_palette, corrected_palette):
            mask = tuple(bool(v >= CLIP_HIGH) for v in r)
            prev = clip_by_tuple.get(ct)
            clip_by_tuple[ct] = mask if prev is None else \
                tuple(a or b for a, b in zip(prev, mask))
        corrected_set, _, _ = _mg.separate_products(corrected_palette)
        corrected_ink_bgr = [np.array(bgr, dtype=np.float64) for bgr, _ in corrected_set]
        clip_masks = [clip_by_tuple[bgr] for bgr, _ in corrected_set]
    else:
        corrected_ink_bgr = [np.clip(bgr * gain, 0.0, 255.0) for bgr in measured_bgr]
        clip_masks = [tuple(bool(v >= CLIP_HIGH) for v in bgr) for bgr in measured_bgr]

    dist_rel = [[_clip_aware_delta_e(c, mask, expected_bgr[ei], expected_labs[ei])
                 for ei in range(len(expected_labs))]
                for c, mask in zip(corrected_ink_bgr, clip_masks)]
    assign_rel = _greedy_assign_matrix(dist_rel)
    rel_deltas = [d for _, _, d in assign_rel]
    per_ink_rel = [agreement_linear(d, 0.0, delta_e_tol) for d in rel_deltas]
    agreement_rel = float(np.mean(per_ink_rel)) if relationship_ok else 0.0
    n_clipped_inks = sum(1 for mask in clip_masks if any(mask))

    # Rank-order corroboration (per channel, over the absolute assignment).
    rank_corrs = []
    for ch in range(3):
        rank_corrs.append(_rank_corr([m_stack[i, ch] for i in range(len(m_stack))],
                                     [s_stack[i, ch] for i in range(len(s_stack))]))
    rank_corr = float(np.mean(rank_corrs))

    implied_applied_gain = [round(float(1.0 / g), 4) if g != 0 else None for g in gain]
    agreement = max(agreement_abs, agreement_rel)

    # Per-ink detail: the absolute assignment (measured ink -> nearest sheet ink
    # and its delta-E). The relationship path re-separates in corrected space so
    # its ink set differs; it is summarised in aggregate below, not paired here.
    per_ink = [{
        "measured_bgr": [round(float(measured_bgr[mi][c]), 1) for c in range(3)],
        "matched_ink": expected_hexes[ei],
        "delta_e_absolute": round(dabs, 3),
    } for mi, ei, dabs in assign_abs]
    return {
        "agreement": agreement,
        "expected": expected_hexes,
        "measured": [p["measured_bgr"] for p in per_ink],
        "detail": {
            "delta_e_tolerance": delta_e_tol,
            "path": "max(absolute, relationship)  (SI-020)",
            "agreement_absolute": round(agreement_abs, 4),
            "agreement_relationship": round(agreement_rel, 4),
            "mean_delta_e_absolute": round(float(np.mean(abs_deltas)), 3),
            "mean_delta_e_relationship": round(float(np.mean(rel_deltas)), 3),
            "correction_gain_bgr": [round(float(g), 4) for g in gain],
            "implied_applied_gain_bgr": implied_applied_gain,
            "gain_in_bounds": in_bounds,
            "relationship_applicable": relationship_ok,
            "n_inks": len(measured_bgr),
            # Saturation handling (SI-020): channels measured >= CLIP_HIGH are
            # excluded from the gain estimate and scored one-sidedly after
            # correction (consistent-with-sheet counts as match; a bound already
            # past the sheet value keeps its real mismatch).
            "clipping": {
                "clip_high": CLIP_HIGH,
                "clip_low": CLIP_LOW,
                "n_clipped_inks": n_clipped_inks,
                "gain_fallback_channels": gain_fallback_channels,
                "note": "clipped channels excluded from gain estimate; "
                        "one-sided (lower-bound) scoring after correction; "
                        "fallback channels use neutral gain 1.0",
            },
            "rank_correlation_bgr": [round(c, 4) for c in rank_corrs],
            "rank_correlation": round(rank_corr, 4),
            "per_ink": per_ink,
        },
    }


def _hue_of_bgr(bgr) -> float:
    """OpenCV HSV hue (0..179) of a mean BGR triple (0..255)."""
    px = np.array([[[int(round(bgr[0])), int(round(bgr[1])), int(round(bgr[2]))]]],
                  dtype=np.uint8)
    return float(cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0, 0])


def _hue_dist(h1: float, h2: float) -> float:
    """Circular distance between two OpenCV hues (period 180)."""
    d = abs(h1 - h2) % 180.0
    return min(d, 180.0 - d)


def _score_ink_band(feature: dict, measurement) -> dict:
    """Two-path ink match for BAND (2-ink) sheets: absolute vs relationship (SI-027).

    Feature agreement is ``max(absolute, relationship)``:

      (a) ABSOLUTE -- the byte-identical pre-SI-027 band match (``_score_ink``):
          each observed ink to its nearest sheet ink in Lab, mean clipped-linear
          agreement on delta-E76 / delta_e. A global cast pushes every delta-E up
          and this collapses (pilot: 0/24).

      (b) RELATIONSHIP -- runs ONLY when both inks are observed (n == 2). Estimate
          a per-channel diagonal correction gain (sheet/measured ratios, clipped
          channels excluded), then, GATED on tighter bounds (BAND_GAIN_MIN/MAX)
          AND sheet-derived gain-invariant corroboration (luminance ordering + hue
          proximity of the pair, from the expected inks), apply it and re-score
          delta-E clip-aware. If a modest single diagonal gain explains the shift
          and the pair relationships survive, this path holds where (a) fell.
          Diagonal gain ONLY (no bloom model this phase); residual reported.

    A single-ink fragment (n == 1) has no pair to corroborate, so the relationship
    path is disabled and the result is byte-identical to the old absolute-only
    band scoring. See the SI-027 constants block.
    """
    absolute = _score_ink(feature, measurement)   # byte-identical old path

    expected_hexes = feature.get("expected") or []
    tol = feature.get("tolerance") or {}
    delta_e_tol = float(tol.get("delta_e", 10.0)) if isinstance(tol, dict) else 10.0
    expected_labs = [_hex_to_lab(h) for h in expected_hexes]
    expected_bgr = [_hex_to_bgr255(h) for h in expected_hexes]
    observed = measurement.value or []
    measured_bgr = [np.array([float(c) for c in bgr], dtype=np.float64) for bgr in observed]

    # Relationship path only for a full 2-ink observation against a 2-ink sheet.
    relationship_ok = len(measured_bgr) == 2 and len(expected_bgr) == 2
    gain = np.ones(3, dtype=np.float64)
    gain_fallback_channels = []
    agreement_rel = 0.0
    rel_deltas = []
    corroboration = {"applicable": relationship_ok}
    if relationship_ok:
        measured_labs = [_bgr_to_lab(b) for b in measured_bgr]
        assign = _greedy_assign(measured_labs, expected_labs)   # [(mi, ei, d), ...]
        m_stack = np.array([measured_bgr[mi] for mi, _, _ in assign])
        s_stack = np.array([expected_bgr[ei] for _, ei, _ in assign])
        for ch in range(3):
            ratios = [s_stack[i, ch] / m_stack[i, ch]
                      for i in range(len(m_stack))
                      if CLIP_LOW < m_stack[i, ch] < CLIP_HIGH]
            if ratios:
                gain[ch] = float(np.median(ratios))
            else:
                gain_fallback_channels.append("BGR"[ch])
        in_bounds = bool(np.all((gain >= BAND_GAIN_MIN) & (gain <= BAND_GAIN_MAX)))

        # Gain-invariant corroboration from the sheet's own expected inks.
        # ei -> mi map (which measured ink was assigned to each expected ink).
        m_for_e = {ei: mi for mi, ei, _ in assign}
        exp_L = [float(expected_labs[ei][0]) for ei in range(2)]
        # Expected luminance ordering (which expected ink is the lighter one).
        e_light, e_dark = (0, 1) if exp_L[0] >= exp_L[1] else (1, 0)
        meas_L_light = float(_bgr_to_lab(measured_bgr[m_for_e[e_light]])[0])
        meas_L_dark = float(_bgr_to_lab(measured_bgr[m_for_e[e_dark]])[0])
        lum_ok = meas_L_light >= meas_L_dark
        # Hue proximity of the measured pair vs the expected pair (+ slack).
        exp_hue_dist = _hue_dist(_hue_of_bgr(expected_bgr[0]), _hue_of_bgr(expected_bgr[1]))
        meas_hue_dist = _hue_dist(_hue_of_bgr(measured_bgr[0]), _hue_of_bgr(measured_bgr[1]))
        hue_ok = meas_hue_dist <= exp_hue_dist + BAND_HUE_SLACK
        corroboration = {"applicable": True, "luminance_order_preserved": lum_ok,
                         "hue_proximity_preserved": hue_ok,
                         "expected_pair_hue_dist": round(exp_hue_dist, 2),
                         "measured_pair_hue_dist": round(meas_hue_dist, 2)}

        relationship_ok = in_bounds and lum_ok and hue_ok
        # Apply the gain and re-score delta-E clip-aware (a clipped bright channel
        # is a lower bound; consistency with the sheet ink counts as a match).
        clip_masks = [tuple(bool(v >= CLIP_HIGH) for v in b) for b in measured_bgr]
        corrected = [np.clip(b * gain, 0.0, 255.0) for b in measured_bgr]
        dist_rel = [[_clip_aware_delta_e(c, mask, expected_bgr[ei], expected_labs[ei])
                     for ei in range(len(expected_labs))]
                    for c, mask in zip(corrected, clip_masks)]
        assign_rel = _greedy_assign_matrix(dist_rel)
        rel_deltas = [d for _, _, d in assign_rel]
        per_ink_rel = [agreement_linear(d, 0.0, delta_e_tol) for d in rel_deltas]
        agreement_rel = float(np.mean(per_ink_rel)) if relationship_ok else 0.0
    else:
        in_bounds = False

    agreement = max(absolute["agreement"], agreement_rel)
    implied_applied_gain = [round(float(1.0 / g), 4) if g != 0 else None for g in gain]
    n_clipped = sum(1 for b in measured_bgr if any(v >= CLIP_HIGH for v in b))
    detail = dict(absolute["detail"])
    detail.update({
        "path": "max(absolute, relationship)  (SI-027, band two-path)",
        "agreement_absolute": round(absolute["agreement"], 4),
        "agreement_relationship": round(agreement_rel, 4),
        "mean_delta_e_relationship": (round(float(np.mean(rel_deltas)), 3)
                                      if rel_deltas else None),
        "correction_gain_bgr": [round(float(g), 4) for g in gain],
        "implied_applied_gain_bgr": implied_applied_gain,
        "gain_in_bounds": in_bounds,
        "relationship_applicable": relationship_ok,
        "n_inks": len(measured_bgr),
        "corroboration": corroboration,
        "clipping": {"clip_high": CLIP_HIGH, "clip_low": CLIP_LOW,
                     "n_clipped_inks": n_clipped,
                     "gain_fallback_channels": gain_fallback_channels,
                     "note": "diagonal gain only this phase (no bloom model, SI-027); "
                             "clipped bright channels scored one-sidedly after correction"},
        "rank_correlation": None,   # not meaningful for a 2-ink system; reported None
    })
    return {"agreement": agreement, "expected": expected_hexes,
            "measured": absolute["measured"], "detail": detail}


def _resolve_relation(feature: dict, measurements: dict):
    """Return (measured_diff, expected, tolerance, n, detail) for a relation_* feature.

    The operands are two *measured* quantities; agreement is on |op1 - op2| vs the
    declared tolerance (SI-001). Returns ``None`` for measured_diff when either
    operand was not observed.
    """
    operands = feature.get("operands", [])
    tol = feature.get("tolerance")
    values = []
    ns = []
    resolved = {}
    for name in operands:
        meas_name = OPERAND_TO_MEASUREMENT.get(name)
        m = measurements.get(meas_name) if meas_name else None
        if m is None or m.n <= 0 or m.value is None:
            resolved[name] = None
            values.append(None)
            ns.append(0)
        else:
            resolved[name] = float(m.value)
            values.append(float(m.value))
            ns.append(m.n)
    if any(v is None for v in values):
        return None, 0.0, tol, 0, {"operands": resolved, "reason": "operand unobserved"}
    diff = abs(values[0] - values[1])
    n = min(ns)  # the relation is only as sampled as its scarcer operand
    return diff, 0.0, tol, n, {"operands": resolved, "measured_abs_diff": round(diff, 4)}


def score_sheet(sheet: dict, measured: dict) -> dict:
    """Score one grammar ``sheet`` against a ``measure_surface`` result.

    Returns a dict with ``per_feature`` (one entry per locus feature), the three
    headline numbers (``renormalised_score``, ``coverage``, ``aggregate_confidence``),
    a ``verdict`` and a ``working`` block. All numbers follow the formulas in the
    module docstring.
    """
    measurements = measured["measurements"]
    features = sheet.get("signature_locus", {}).get("features", [])
    structure_type = sheet.get("structure", {}).get("type")

    per_feature = []
    observed_weight = 0.0     # id-feature weight we actually scored
    measurable_weight = 0.0   # id-feature weight we *could* score (SI-008 aware)
    weighted_conf = 0.0       # sum weight_i * confidence_i over observed
    verification_failures = []

    for feature in features:
        fid = feature.get("id")
        role = feature.get("role", "identification")
        weight = float(feature.get("weight", 0.0))
        measure_name = feature.get("measure", "")
        sample_unit = feature.get("sample_unit", "")
        status = feature.get("status", "measured")

        entry = {
            "id": fid,
            "measure": measure_name,
            "role": role,
            "weight": weight,
            "sample_unit": sample_unit,
            "observed": False,
            "agreement": None,
            "n": 0,
            "confidence": None,
            "measured": None,
            "expected": feature.get("expected"),
        }

        # SI-008: a feature the sheet itself declares unmeasured is reserved
        # weight, skipped and renormalised away -- not part of coverage.
        if status == "unmeasured":
            entry["note"] = ("sheet declares this feature unmeasured; weight "
                             "reserved and renormalised away (SI-008)")
            per_feature.append(entry)
            continue

        # A measurable identification feature contributes to the coverage base.
        if role == "identification":
            measurable_weight += weight

        # --- resolve the measurement for this feature ---
        if measure_name.startswith("relation_"):
            diff, expected, tol, n, rdetail = _resolve_relation(feature, measurements)
            entry["detail"] = rdetail
            if diff is None:
                entry["note"] = "relation operand not observed in fragment"
                per_feature.append(entry)
                continue
            agree = agreement_linear(diff, expected, tol)
            entry["measured"] = {"abs_diff": round(diff, 4),
                                 "operands": rdetail["operands"]}
            entry["expected"] = f"|{ ' - '.join(feature.get('operands', [])) }| ~ 0"
        elif measure_name == "ink_set_match":
            m = measurements.get("ink_set_match")
            if m is None or m.n <= 0:
                entry["note"] = "no inks observed"
                per_feature.append(entry)
                continue
            # Grid sheets use the many-ink two-path match (SI-020); band sheets use
            # the 2-ink two-path match (SI-027). Both reduce to the byte-identical
            # absolute path when the relationship path is inapplicable (grid: < 3
            # inks; band: != 2 inks), so single-ink fragments are unchanged.
            ink = (_score_ink_grid(feature, m) if structure_type == "grid"
                   else _score_ink_band(feature, m))
            agree = ink["agreement"]
            n = m.n
            entry["measured"] = ink["measured"]
            entry["detail"] = ink["detail"]
        elif measure_name == "primitive_frequency_mix":
            # Primitive-mix identification carrier (SI-026, closing the same-ink
            # half of SI-022). Agreement is clipped-linear on the L1 distance
            # between the measured and expected instance-share vectors, over the
            # declared scalar tolerance (SI-001 convention). n = classified
            # instances scales confidence (SI-002): a fragment with a handful of
            # primitives yields a noisy mix, so its small n discounts it rather
            # than letting the estimate dominate. n = 0 -> unobserved (skipped and
            # renormalised away, never scored 0).
            m = measurements.get("primitive_frequency_mix")
            expected_vec = feature.get("expected")
            tol = feature.get("tolerance")
            if m is None or m.n <= 0 or m.value is None:
                entry["note"] = "no classifiable primitives observed in this fragment"
                per_feature.append(entry)
                continue
            if not isinstance(expected_vec, (list, tuple)) or not isinstance(tol, (int, float)):
                entry["note"] = ("primitive_frequency_mix expects a vector expected "
                                 "and scalar tolerance; skipped")
                per_feature.append(entry)
                continue
            measured_vec = list(m.value)
            l1 = float(np.abs(np.asarray(measured_vec, dtype=float)
                              - np.asarray(expected_vec, dtype=float)).sum())
            n = m.n
            # n-DEPENDENT EFFECTIVE TOLERANCE (SI-026). The mix is a share vector
            # estimated from n interior instances of a 5-bin multinomial: an
            # HONEST measurement's expected L1 sampling deviation scales
            # ~ sqrt(1/n), so a small-n fragment's larger L1 is sampling noise,
            # not disagreement, and must not be punished as such (the existing
            # n/(n+k) saturation already handles "we saw little -> low
            # confidence"). The committed tolerance is calibrated at the typical
            # full-surface interior instance count N_REF_PRIMITIVES (from the
            # derivation corpus); below it the tolerance widens by sqrt(n_ref/n),
            # at or above it tolerance_eff == the committed value, so full-surface
            # impostor rejection is never loosened.
            tol_eff = float(tol) * max(1.0, np.sqrt(N_REF_PRIMITIVES / n))
            agree = agreement_linear(l1, 0.0, tol_eff)
            entry["measured"] = [round(float(v), 4) for v in measured_vec]
            entry["detail"] = {"l1_distance": round(l1, 4),
                               "tolerance_committed": float(tol),
                               "tolerance_eff": round(tol_eff, 4),
                               "n_ref": N_REF_PRIMITIVES,
                               "n": int(n),
                               "order": getattr(m, "detail", {}).get("order"),
                               **getattr(m, "detail", {})}
        elif measure_name == "overprint_multiply_consistency":
            # Verification (audit s5): agreement falls with the worst product
            # residual; a large residual (broken multiply arithmetic) flags the
            # claim. No overlaps observed -> unobserved, NOT a failure.
            m = measurements.get("overprint_multiply_consistency")
            if m is None or m.n <= 0 or m.value is None:
                entry["note"] = "no two-ink overlaps observed (overprint unverified)"
                per_feature.append(entry)
                continue
            residual = float(m.value)
            agree = agreement_linear(residual, 0.0, OVERPRINT_RESIDUAL_TOL)
            n = m.n
            entry["measured"] = {"max_product_residual": round(residual, 3)}
            entry["detail"] = getattr(m, "detail", {})
        elif measure_name in measurements:
            m = measurements[measure_name]
            if m.n <= 0 or m.value is None:
                entry["note"] = f"{measure_name} not observable in this fragment"
                per_feature.append(entry)
                continue
            expected = feature.get("expected")
            tol = feature.get("tolerance")
            if not isinstance(expected, (int, float)):
                # A measured feature with no numeric expected (e.g. band_period
                # normalisation): report the value, no agreement to score.
                entry["observed"] = True
                entry["n"] = m.n
                entry["measured"] = round(float(m.value), 4)
                entry["note"] = "no expected value declared (normalisation anchor)"
                per_feature.append(entry)
                continue
            agree = agreement_linear(float(m.value), float(expected), float(tol))
            n = m.n
            entry["measured"] = round(float(m.value), 4)
            entry["detail"] = getattr(m, "detail", {})
        else:
            # No measurer exists for this sheet's measure (e.g. 002's
            # primitive/grid/overprint measures). Treat as unobserved, and say so
            # (graceful, never a crash -- task requirement).
            entry["note"] = (f"no measurer for '{measure_name}' in recogniser v0; "
                             "reported unobserved")
            per_feature.append(entry)
            continue

        # --- sample-size scaling and bookkeeping ---
        sat = saturation(n, sample_unit)
        confidence = agree * sat
        entry.update({
            "observed": True,
            "agreement": round(float(agree), 4),
            "n": int(n),
            "saturation": round(sat, 4),
            "confidence": round(float(confidence), 4),
        })

        if role == "identification" and weight > 0:
            observed_weight += weight
            weighted_conf += weight * confidence
        elif role == "verification":
            # Verification features flag rather than score (SI-006). A failing
            # verification (agreement 0 with samples) caps/flags the claim.
            if agree <= 0.0 and n > 0:
                verification_failures.append(fid)

        per_feature.append(entry)

    renormalised_score = (weighted_conf / observed_weight) if observed_weight > 0 else 0.0
    coverage = (observed_weight / measurable_weight) if measurable_weight > 0 else 0.0
    aggregate = renormalised_score * coverage

    if aggregate >= IDENTIFIED_THRESHOLD:
        verdict = "identified"
    elif aggregate >= CANDIDATE_THRESHOLD:
        verdict = "candidate"
    else:
        verdict = "not_recognised"

    unobserved = [e["id"] for e in per_feature
                  if e["role"] == "identification"
                  and e.get("weight", 0) > 0
                  and not e["observed"]
                  and e.get("note", "").find("SI-008") < 0]

    return {
        "sheet_id": sheet.get("sheet", {}).get("id"),
        "grammar_version": sheet.get("sheet", {}).get("grammar_version"),
        "aggregate_confidence": round(float(aggregate), 4),
        "renormalised_score": round(float(renormalised_score), 4),
        "coverage": round(float(coverage), 4),
        "verdict": verdict,
        "per_feature": per_feature,
        "unobserved_identification_features": unobserved,
        "verification_failures": verification_failures,
        "working": {
            "score_formula": "agreement=max(0,1-|m-e|/tol); "
                             "confidence=agreement*n/(n+k); "
                             "aggregate=renormalised_score*coverage",
            "saturation_k": SATURATION_K,
            "identified_threshold": IDENTIFIED_THRESHOLD,
            "candidate_threshold": CANDIDATE_THRESHOLD,
            "observed_id_weight": round(observed_weight, 4),
            "measurable_id_weight": round(measurable_weight, 4),
        },
    }
