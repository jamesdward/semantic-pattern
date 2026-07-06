"""Tests for recogniser v0 (Phase 3).

Project rule (README, tests row): every measurer is validated against synthetic
ground truth BY MEASUREMENT *before* it is trusted in the pipeline. So this file
is in two halves:

  1. Measurer unit tests -- classify_two_colour, estimate_orientation and
     measure_surface are checked against surfaces/fragments rendered by the
     Phase 2 generator, where the audit-001 ground truth (ratio 1.94, duty 0.31,
     phase = duty, the two green inks) is known exactly.

  2. Scoring / claim tests -- the honesty behaviour the whole project exists to
     get right (spec 8, audit s3): a full surface is *identified*; a fragment
     that captures the transitions is a strong candidate/identified; a single
     band is capped low however well colour + duty match; and a canonical-peak
     impostor is refused even though it shares the phase relation and colour.

Determinism is asserted directly (same image -> byte-identical claim JSON).
"""

import copy
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from sheets import load_sheet
from generator import cascade
from generator.fragments import sample_fragment
from recogniser import measure, score
from recogniser.claim import recognise

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAMMARS = REPO_ROOT / "grammars"
SHEET_PATH = GRAMMARS / "bar-cascade-001.yaml"

N_BANDS = 5
MODULE = 200


@pytest.fixture(scope="module")
def sheet():
    return load_sheet(SHEET_PATH)


@pytest.fixture(scope="module")
def surface0(sheet):
    """Axis-aligned render (orientation forced to 0) for clean ground truth."""
    return cascade.render(
        sheet, n_bands=N_BANDS, module_px=MODULE, seed=0, orientation_deg=0.0
    )


def _feature(result, fid):
    """Pull one per-feature entry out of a score result by id."""
    for f in result["per_feature"]:
        if f["id"] == fid:
            return f
    raise KeyError(fid)


def _result(claim, sheet_id):
    for r in claim["results"]:
        if r["sheet_id"] == sheet_id:
            return r
    raise KeyError(sheet_id)


# =========================================================================
# 1. Measurer unit tests (against synthetic ground truth)
# =========================================================================


def test_classify_two_colour_recovers_inks(sheet, surface0):
    """Otsu + class means recover the two sheet inks within delta_e 10 (Lab)."""
    light_mask, dark_bgr, light_bgr, inks_n, _ = measure.classify_two_colour(surface0)
    assert inks_n == 2
    dark_hex, light_hex = "#2E5B30", "#73B881"
    d_dark = score._delta_e76(score._bgr_to_lab(dark_bgr), score._hex_to_lab(dark_hex))
    d_light = score._delta_e76(score._bgr_to_lab(light_bgr), score._hex_to_lab(light_hex))
    assert d_dark < 10.0
    assert d_light < 10.0


def test_estimate_orientation_recovers_tilt(surface0):
    """Structure-tensor tilt recovers a known rotation, mod the 90-degree stripe
    ambiguity (audit s3)."""
    # Axis-aligned surface: bars horizontal, tilt ~ 0.
    tilt0, _ = measure.estimate_orientation(surface0)
    assert abs(measure._wrap45(tilt0)) < 1.0
    # Rotated fragments: the recovered tilt, de-skewed to (-45, 45], matches the
    # applied angle mod 90 (a 90-degree turn maps onto the same stripe axis).
    for applied in (7.0, 30.0):
        frag, _ = sample_fragment(
            surface0, frac=0.4, rng=np.random.default_rng(0),
            rotation_deg=applied, module_px=MODULE, n_bands=N_BANDS,
        )
        tilt, _ = measure.estimate_orientation(frag)
        residual = measure._wrap45(tilt)
        # The generator rotates one way, the tensor measures the other; compare
        # magnitudes mod 90.
        assert min(abs(residual - applied), abs(residual + applied)) < 3.0


def test_measure_full_surface_ground_truth(surface0):
    """Full surface at 0 deg: cascade ~1.94, duty ~0.31, phase ~0.31, 5 bands."""
    m = measure.measure_surface(surface0)
    meas = m["measurements"]
    assert m["working"]["n_bands_detected"] == N_BANDS
    assert m["working"]["orientation_ambiguous"] is False

    cr = meas["period_cascade_ratio"]
    assert cr.n == N_BANDS - 1
    assert abs(cr.value - 1.94) < 0.03            # tuned frequency ratio (audit s4)

    duty = meas["duty_cycle"]
    assert abs(duty.value - 0.31) < 0.02          # tuned duty cycle (audit s4)
    assert duty.n > 50

    phase = meas["phase_step"]
    assert phase.n == N_BANDS - 1
    assert abs(phase.value - 0.31) < 0.03         # phase = duty (audit s3 identity)

    ink = meas["ink_set_match"]
    assert ink.n == 2


@pytest.mark.parametrize("applied", [7.0, 30.0, 90.0])
def test_measure_derotation_recovers_measurements(surface0, applied):
    """A fragment rotated 7/30/90 deg is de-rotated and measured correctly.

    The 90-degree case is the stripe ambiguity (audit s3): the measurer tries
    both axes and reads the cascade off the row axis instead of the columns.
    """
    frag, info = sample_fragment(
        surface0, frac=0.4, rng=np.random.default_rng(0),
        rotation_deg=applied, module_px=MODULE, n_bands=N_BANDS,
    )
    m = measure.measure_surface(frag)
    cr = m["measurements"]["period_cascade_ratio"]
    duty = m["measurements"]["duty_cycle"]
    assert cr.value is not None and cr.n >= 1
    assert abs(cr.value - 1.94) < 0.06            # cascade recovered up the right way
    assert abs(duty.value - 0.31) < 0.03
    if applied == 90.0:
        assert m["working"]["stripe_axis"] == "rows"


def test_single_region_reports_cascade_and_phase_unobserved(surface0):
    """A fragment inside one band yields period + duty but NO cascade, NO phase
    (audit s3 honesty rule)."""
    inside_band2 = surface0[:, 420:560]           # x fully within band 2 (400-600)
    m = measure.measure_surface(inside_band2)
    assert m["working"]["n_bands_detected"] == 1
    assert m["working"]["orientation_ambiguous"] is True
    assert m["measurements"]["period_cascade_ratio"].n == 0
    assert m["measurements"]["period_cascade_ratio"].value is None
    assert m["measurements"]["phase_step"].n == 0
    # ...but duty and inks ARE observed.
    assert m["measurements"]["duty_cycle"].value is not None
    assert abs(m["measurements"]["duty_cycle"].value - 0.31) < 0.03
    assert m["measurements"]["ink_set_match"].n == 2


# =========================================================================
# 2. Scoring / claim behaviour (spec 8, audit s3)
# =========================================================================


def test_full_surface_is_identified(surface0):
    """The canonical 5-band surface scores against 001 as identified (>= 0.70)."""
    claim = recognise(surface0, str(GRAMMARS))
    top = claim["results"][0]
    assert top["sheet_id"] == "bar-cascade-001"
    assert top["verdict"] == "identified"
    assert top["aggregate_confidence"] >= score.IDENTIFIED_THRESHOLD
    assert top["coverage"] == 1.0
    assert claim["note"].startswith("identity claim")


def test_two_boundary_fragment_high_confidence(surface0):
    """A 2+ boundary fragment that captures the phase origin reaches high
    confidence (>= 0.70) -- the transitions carry the signature (audit s3).

    A full-height, three-band vertical slice is a genuine fragment (60% of the
    width) that includes the phase origin at y = 0, so the phase = duty identity
    is confirmable; interior crops that miss the origin are candidates instead
    (see test_interior_fragment_is_candidate)."""
    strip = surface0[:, 0:600]                    # bands 0..2 -> 2 boundaries
    result = _result(recognise(strip, str(GRAMMARS)), "bar-cascade-001")
    assert result["aggregate_confidence"] >= 0.70
    assert result["verdict"] in ("identified", "candidate")
    assert result["coverage"] == 1.0
    # The signature really was measured across the transitions:
    assert _feature(result, "cascade_ratio")["agreement"] > 0.6
    assert _feature(result, "phase_duty_identity")["agreement"] > 0.5


def test_interior_fragment_is_at_least_candidate(surface0):
    """An interior multi-band fragment (missing the phase origin) is recognised
    as at least a candidate, and clearly beats a single band."""
    frag, info = sample_fragment(
        surface0, frac=0.4, rng=np.random.default_rng(3),
        module_px=MODULE, n_bands=N_BANDS,
    )
    assert info.band_boundaries_spanned >= 2
    result = _result(recognise(frag, str(GRAMMARS)), "bar-cascade-001")
    assert result["verdict"] in ("candidate", "identified")
    assert result["aggregate_confidence"] >= score.CANDIDATE_THRESHOLD
    # cascade + colour are strong even when phase (origin-dependent) is not:
    assert _feature(result, "cascade_ratio")["agreement"] > 0.6
    assert _feature(result, "colour_pair")["agreement"] > 0.6


def test_single_band_fragment_confidence_capped_low(surface0):
    """A single-band fragment cannot be identified however well colour + duty
    match: coverage is 0.40, so the aggregate is capped there (audit s3)."""
    inside_band2 = surface0[:, 420:560]
    result = _result(recognise(inside_band2, str(GRAMMARS)), "bar-cascade-001")
    # duty and colour match essentially perfectly...
    assert _feature(result, "duty")["agreement"] > 0.5
    assert _feature(result, "colour_pair")["agreement"] > 0.8
    # ...but cascade + phase are unobserved and coverage caps the claim.
    assert "cascade_ratio" in result["unobserved_identification_features"]
    assert "phase_duty_identity" in result["unobserved_identification_features"]
    assert result["coverage"] == pytest.approx(0.40, abs=1e-6)
    assert result["aggregate_confidence"] < score.IDENTIFIED_THRESHOLD
    assert result["aggregate_confidence"] <= 0.40   # the cap really bites
    assert result["verdict"] != "identified"


def test_impostor_cascade_and_duty_agreement_near_zero(sheet, surface0):
    """The false-positive discipline: a canonical-peak surface (ratio 2.0, duty
    1/3, phase 1/3) is refused against 001. cascade + duty agreements collapse to
    ~0 (2.0 and 0.333 are >1 tolerance off 1.94 / 0.31), so despite sharing the
    phase = duty relation and the colour pair, the aggregate stays well below
    identification. THIS is the point of the project."""
    impostor_sheet = copy.deepcopy(sheet)
    impostor_sheet["combination_rules"]["frequency_ratio"] = 2.0
    impostor_sheet["combination_rules"]["duty_cycle_light"] = 1.0 / 3.0
    impostor_sheet["combination_rules"]["phase_step"] = 1.0 / 3.0
    surf_imp = cascade.render(
        impostor_sheet, n_bands=N_BANDS, module_px=MODULE, seed=0, orientation_deg=0.0
    )
    result = _result(recognise(surf_imp, str(GRAMMARS)), "bar-cascade-001")

    assert _feature(result, "cascade_ratio")["agreement"] < 0.05
    assert _feature(result, "duty")["agreement"] < 0.05
    assert result["verdict"] != "identified"
    assert result["aggregate_confidence"] < 0.50    # well below identification


def test_determinism_claim_byte_identical(surface0):
    """Same image -> byte-identical claim JSON (dumped sort_keys)."""
    a = json.dumps(recognise(surface0, str(GRAMMARS)), sort_keys=True)
    b = json.dumps(recognise(surface0, str(GRAMMARS)), sort_keys=True)
    assert a == b


# =========================================================================
# 3. Multi-sheet robustness (iso-002 must not crash; graceful unknowns)
# =========================================================================


def test_iso002_does_not_crash_and_scores_below_001(surface0):
    """002 scores what it can (colour) on a 001 cascade and reports its structure
    features unobserved -- and comes out below 001 (task requirement)."""
    claim = recognise(surface0, str(GRAMMARS))
    r001 = _result(claim, "bar-cascade-001")
    r002 = _result(claim, "iso-002")
    assert r002["aggregate_confidence"] < r001["aggregate_confidence"]
    assert r002["verdict"] != "identified"


def test_iso002_unknown_measures_reported_unobserved_gracefully(surface0):
    """Measures 002 names but recogniser v0 has no measurer for
    (grid_module_detect, stripe_duty, staircase_step_angle,
    overprint_multiply_consistency) are reported unobserved with a note -- never
    a crash. primitive_frequency_mix is skipped as sheet-unmeasured (SI-008)."""
    r002 = _result(recognise(surface0, str(GRAMMARS)), "iso-002")
    grid = _feature(r002, "grid_module")
    assert grid["observed"] is False
    assert "no measurer" in grid.get("note", "")

    prim = _feature(r002, "primitive_frequency_mix")
    assert prim["observed"] is False
    assert "SI-008" in prim.get("note", "")

    # Its one real measurer (ink_set) did run: colour is scored, structure is not.
    assert _feature(r002, "ink_set")["observed"] is True


# =========================================================================
# 4. CLI smoke
# =========================================================================


def test_cli_prints_claim_json(surface0, tmp_path):
    """`python -m recogniser <image>` prints a parseable identity claim."""
    png = tmp_path / "surf.png"
    cascade.render_png(
        load_sheet(SHEET_PATH), png, n_bands=N_BANDS, module_px=MODULE,
        seed=0, orientation_deg=0.0,
    )
    proc = subprocess.run(
        [sys.executable, "-m", "recogniser", str(png), "--grammars", str(GRAMMARS)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), check=True,
    )
    claim = json.loads(proc.stdout)
    assert claim["note"].startswith("identity claim")
    assert claim["results"][0]["sheet_id"] == "bar-cascade-001"
