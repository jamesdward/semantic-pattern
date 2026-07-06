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
    "primitives_observed": 1.0,
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
            ink = _score_ink(feature, m)
            agree = ink["agreement"]
            n = m.n
            entry["measured"] = ink["measured"]
            entry["detail"] = ink["detail"]
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
