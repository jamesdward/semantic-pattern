"""Tests for the GRID recogniser family (Phase 6; grammar iso-002 / audit 002).

Project rule (README, tests row): every measurer is validated against synthetic
ground truth from the generator BY MEASUREMENT before the pipeline trusts it. So
this file measures the audit-002 quantities back out of ``generator.grid`` renders
where the ground truth (module, ink subset, exact multiply overprints) is known:

  1. Grid module via edge-projection autocorrelation -- on clean renders and on
     crops that do NOT start on a module boundary.
  2. Flat-ink extraction + parent/product separation -- the recovered ink set is
     the ground-truth subset (within small delta-E) and no multiply product leaks
     into it.
  3. Overprint consistency -- residual ~0 on a clean render; a drifted overprint
     (broken multiply arithmetic, audit s5) raises the claim's verification flag.
  4. WHITE-BALANCE ROBUSTNESS (the point of the phase, audit s6 / SI-020): under a
     per-channel gain the ABSOLUTE colour match collapses but the RELATIONSHIP
     path recovers -- agreement stays high, and the estimated (implied applied)
     gain matches the gain that was applied.
  5. Full pipeline -- a 002 render is top and candidate-or-better against iso-002,
     bar-cascade-001 scores below it, and the reverse direction still holds.
  6. Determinism -- same image -> byte-identical claim JSON.
"""

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from sheets import load_sheet
from generator import grid
from battery import degrade
from recogniser import measure_grid as mg
from recogniser import score
from recogniser.claim import recognise

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAMMARS = REPO_ROOT / "grammars"
ISO_SHEET = GRAMMARS / "iso-002.yaml"

COLS, ROWS, MODULE = 10, 7, 64
# A subset with genuine two-ink overlaps and no internal red coincidence.
SUBSET = ["#FAFF54", "#479F8C", "#D52EB2", "#75FB63", "#EC5F2A"]
# A subset whose inks do not clip a channel under moderate white balance, so the
# relationship path can fully recover (audit s6 print-fragility demonstration).
WB_SUBSET = ["#479F8C", "#D52EB2", "#75FB63", "#C5111D", "#96CBC4"]


@pytest.fixture(scope="module")
def sheet():
    return load_sheet(ISO_SHEET)


@pytest.fixture(scope="module")
def render(sheet):
    return grid.render_with_truth(sheet, cols=COLS, rows=ROWS, module_px=MODULE,
                                  seed=2, ink_subset=SUBSET, density=0.55)


def _feature(result, fid):
    for f in result["per_feature"]:
        if f["id"] == fid:
            return f
    raise KeyError(fid)


def _result(claim, sheet_id):
    for r in claim["results"]:
        if r["sheet_id"] == sheet_id:
            return r
    raise KeyError(sheet_id)


def _nearest_delta_e(bgr, hexes):
    lab = score._bgr_to_lab(np.array(bgr, dtype=np.float64))
    return min(score._delta_e76(lab, score._hex_to_lab(h)) for h in hexes)


# =========================================================================
# 1. Grid module (audit s2 edge-projection autocorrelation)
# =========================================================================


def test_grid_module_recovered_on_clean_render(render):
    surface, _ = render
    module, n_cells, _ = mg.detect_grid_module(surface)
    assert abs(module - MODULE) <= 2
    assert n_cells > 0


@pytest.mark.parametrize("dy,dx", [(29, 41), (13, 7), (50, 33)])
def test_grid_module_robust_to_non_aligned_crop(render, dy, dx):
    """Autocorrelation is shift-invariant, so a crop that does not start on a
    module boundary still recovers M (audit s2: robust to alignment)."""
    surface, _ = render
    crop = surface[dy:, dx:]
    module, _, _ = mg.detect_grid_module(crop)
    assert abs(module - MODULE) <= 2


# =========================================================================
# 2. Flat-ink extraction and parent/product separation
# =========================================================================


def test_ink_set_recovers_ground_truth_subset(render):
    """The recovered ink set equals the ground-truth subset within small delta-E,
    and holds no more colours than were actually inked."""
    surface, gt = render
    inks, _ = mg.extract_flat_inks(surface)
    ink_set, products, _ = mg.separate_products(inks)
    assert len(ink_set) == len(gt.ink_subset)
    for bgr, _frac in ink_set:
        assert _nearest_delta_e(bgr, gt.ink_subset) < 5.0


def test_products_are_separated_from_parents(render):
    """No multiply product colour leaks into the ink set: every recorded overlap
    product is absent from the ink set (audit s2/s5 -- products are evidence of
    layering, not members of the ink set)."""
    surface, gt = render
    inks, _ = mg.extract_flat_inks(surface)
    ink_set, products, _ = mg.separate_products(inks)
    assert len(products) > 0
    ink_colours = {bgr for bgr, _ in ink_set}
    # Ground-truth two-ink (distinct) product colours must not appear as inks.
    for o in gt.overlaps:
        if o.inks[0] == o.inks[1]:
            continue
        assert tuple(int(v) for v in o.product) not in ink_colours


def test_extraction_deterministic(render):
    surface, _ = render
    a = mg.separate_products(mg.extract_flat_inks(surface)[0])[0]
    b = mg.separate_products(mg.extract_flat_inks(surface)[0])[0]
    assert a == b


# =========================================================================
# 3. Overprint consistency (verification; audit s5)
# =========================================================================


def test_overprint_residual_zero_on_clean_render(render):
    surface, _ = render
    m = mg.measure_grid_surface(surface)
    op = m["measurements"]["overprint_multiply_consistency"]
    assert op.n > 0                 # overlaps observed
    assert op.value == 0.0          # exact multiply everywhere (audit s2)


def _distinct_hue(a, b):
    return mg._hue_dist(mg._hue(grid._hex_to_bgr(a)),
                        mg._hue(grid._hex_to_bgr(b))) >= mg.MIN_PARENT_HUE


def _corrupt_one_overprint(surface, gt, delta=22):
    """Return a copy with one distinct-ink overlap's arithmetic drifted darker.

    Models an unfaithful reproduction (audit s5 "c1*c2 != c3"): the overlap stays
    darker than both parents (still a plausible overprint region) but no longer
    equals the exact multiply, so the residual jumps well past the tolerance."""
    corrupt = surface.copy().astype(np.int64)
    idx = {h: i for i, h in enumerate(gt.ink_subset)}
    two = gt.depth == 2
    lo = np.minimum(gt.label_first, gt.label_second)
    hi = np.maximum(gt.label_first, gt.label_second)
    for o in gt.overlaps:
        a, b = o.inks
        if a == b or not _distinct_hue(a, b):
            continue
        sel = two & (lo == min(idx[a], idx[b])) & (hi == max(idx[a], idx[b]))
        if not sel.any():
            continue
        corrupt[sel] = np.clip(np.array(o.product) - delta, 0, 255)
        return corrupt.astype(np.uint8), o
    pytest.skip("no distinct-hue overlap to corrupt in this render")


def test_broken_overprint_flags_verification(render, sheet):
    """A drifted overprint gives a large residual, and the claim's verification
    flags fire for iso-002 (audit s5). A clean render does NOT flag."""
    surface, gt = render
    clean = _result(recognise(surface, str(GRAMMARS)), "iso-002")
    assert "overprint_consistency" not in clean["verification_failures"]

    corrupt, _o = _corrupt_one_overprint(surface, gt)
    m = mg.measure_grid_surface(corrupt)
    op = m["measurements"]["overprint_multiply_consistency"]
    assert op.value > score.OVERPRINT_RESIDUAL_TOL       # residual is large

    r = _result(recognise(corrupt, str(GRAMMARS)), "iso-002")
    assert "overprint_consistency" in r["verification_failures"]
    assert _feature(r, "overprint_consistency")["agreement"] == 0.0


# =========================================================================
# 4. WHITE BALANCE -- the point of the phase (audit s6, SI-020)
# =========================================================================


@pytest.mark.parametrize("gains", [(1.15, 1.0, 0.85), (0.85, 1.0, 1.15)])
def test_white_balance_relationship_path_recovers(sheet, gains):
    """Under a per-channel white-balance gain the ABSOLUTE colour match collapses
    but the RELATIONSHIP path recovers: agreement stays high (>= 0.7), the
    absolute-only match would fail, and the estimated (implied applied) gain
    matches the gain that was applied. THIS is the milestone requirement."""
    surface = grid.render(sheet, cols=COLS, rows=ROWS, module_px=MODULE,
                          seed=1, ink_subset=WB_SUBSET, density=0.5)
    shifted = degrade.white_balance(surface, gains)

    r = _result(recognise(shifted, str(GRAMMARS)), "iso-002")
    ink = _feature(r, "ink_set")
    d = ink["detail"]

    # Absolute path is print-fragile: a global cast pushes every delta-E out.
    assert d["agreement_absolute"] < 0.2
    # Relationship path recovers the ink relationships under the cast.
    assert d["agreement_relationship"] >= 0.7
    assert ink["agreement"] >= 0.7                       # feature = max(abs, rel)
    # The estimated gain recovers the applied white balance (BGR order).
    implied = d["implied_applied_gain_bgr"]
    for got, applied in zip(implied, gains):
        assert abs(got - applied) < 0.1
    assert d["gain_in_bounds"] is True
    # Ordering (rank) relationships survive a positive gain (cheap corroboration;
    # a couple of overprint colours leak into the raw ink set under the cast, so
    # this is a soft positive-correlation check, not the load-bearing signal).
    assert d["rank_correlation"] > 0.4
    # And iso-002 is still recognised as at least a candidate under the cast.
    assert r["aggregate_confidence"] >= score.CANDIDATE_THRESHOLD


def test_white_balance_with_channel_clipping_recovers(sheet):
    """Director smoke case (regression): seed 3, cols 10, rows 7, module 64 --
    the seeded subset holds bright-channel inks (#FAFF54, #75FB63, #95C3F9 ...)
    whose saturated channels the warm (1.15, 1.0, 0.85) cast clips. Clipped
    observations must not bias the gain estimate (saturation-aware consensus,
    SI-020) and clipped channels are scored one-sidedly after correction
    (measurement loss, not identity mismatch). Before that rule this exact case
    scored 0.372 / not_recognised."""
    surface = grid.render(sheet, cols=COLS, rows=ROWS, module_px=MODULE, seed=3)
    shifted = degrade.white_balance(surface, (1.15, 1.0, 0.85))

    claim = recognise(shifted, str(GRAMMARS))
    assert claim["results"][0]["sheet_id"] == "iso-002"      # still the top sheet

    r = _result(claim, "iso-002")
    # Comfortably above the candidate line (requirement is >= 0.40).
    assert r["aggregate_confidence"] >= 0.55
    assert r["verdict"] in ("candidate", "identified")

    ink = _feature(r, "ink_set")
    d = ink["detail"]
    assert d["agreement_absolute"] < 0.2          # absolute path still collapses
    assert d["agreement_relationship"] >= 0.6     # relationship path recovers
    # The estimated gain recovers the applied cast despite the clipped channels.
    for got, applied in zip(d["implied_applied_gain_bgr"], (1.15, 1.0, 0.85)):
        assert abs(got - applied) < 0.05
    # Clipped-ink handling is reported in the working.
    clip = d["clipping"]
    assert clip["n_clipped_inks"] > 0
    assert clip["gain_fallback_channels"] == []   # enough unclipped observations


def test_absolute_only_match_would_fail_under_white_balance(sheet):
    """Corroborates the above: with the relationship path removed, the same shift
    drops the colour agreement below the candidate line -- showing the recovery is
    the relationship path's doing, not luck."""
    surface = grid.render(sheet, cols=COLS, rows=ROWS, module_px=MODULE,
                          seed=1, ink_subset=WB_SUBSET, density=0.5)
    shifted = degrade.white_balance(surface, (1.15, 1.0, 0.85))
    r = _result(recognise(shifted, str(GRAMMARS)), "iso-002")
    assert _feature(r, "ink_set")["detail"]["agreement_absolute"] < 0.2


def test_relationship_path_disabled_below_min_inks(sheet):
    """A global-gain claim from too few inks is not credible (SI-020): with fewer
    than MIN_INKS_FOR_GAIN measured inks the relationship path is disabled, so a
    tiny palette cannot borrow the grid ink-set's leniency."""
    two_ink = grid.render(sheet, cols=COLS, rows=ROWS, module_px=MODULE, seed=4,
                          ink_subset=["#D52EB2", "#75FB63"], density=0.25)
    shifted = degrade.white_balance(two_ink, (1.15, 1.0, 0.85))
    r = _result(recognise(shifted, str(GRAMMARS)), "iso-002")
    d = _feature(r, "ink_set")["detail"]
    if d["n_inks"] < score.MIN_INKS_FOR_GAIN:
        assert d["relationship_applicable"] is False
        assert d["agreement_relationship"] == 0.0


# =========================================================================
# 5. Full pipeline and cross-direction discrimination
# =========================================================================


def test_grid_render_top_is_iso002(render):
    """A clean 002 render is the top verdict against iso-002 (candidate-or-better).
    At grammar_version 1.1.0 the primitive mix is a MEASURED identification carrier
    (SI-026), so on a full render both id-features (ink set + mix) are observed and
    coverage is 1.0."""
    surface, _ = render
    claim = recognise(surface, str(GRAMMARS))
    top = claim["results"][0]
    assert top["sheet_id"] == "iso-002"
    assert top["aggregate_confidence"] >= score.CANDIDATE_THRESHOLD
    assert top["verdict"] in ("candidate", "identified")
    # SI-026: the primitive mix is now measured and observed on a full render.
    prim = _feature(top, "primitive_frequency_mix")
    assert prim["observed"] is True and prim["n"] > 0
    assert top["coverage"] == pytest.approx(1.0, abs=1e-6)


def test_bar_cascade_001_scores_below_iso002_on_grid_render(render):
    """Cross-discrimination one way: on a 002 grid render, bar-cascade-001 scores
    below iso-002 and is not identified (Phase 7 depends on honest degradation)."""
    surface, _ = render
    claim = recognise(surface, str(GRAMMARS))
    r001 = _result(claim, "bar-cascade-001")
    r002 = _result(claim, "iso-002")
    assert r001["aggregate_confidence"] < r002["aggregate_confidence"]
    assert r001["verdict"] != "identified"


def test_cross_direction_band_render_still_top_001():
    """Cross-discrimination the other way (existing behaviour preserved with the
    real grid measurers now present): a 001 band render is still top as
    bar-cascade-001 and iso-002 stays well below it."""
    from generator import cascade
    band_sheet = load_sheet(GRAMMARS / "bar-cascade-001.yaml")
    surf = cascade.render(band_sheet, n_bands=5, module_px=200, seed=0,
                          orientation_deg=0.0)
    claim = recognise(surf, str(GRAMMARS))
    top = claim["results"][0]
    assert top["sheet_id"] == "bar-cascade-001"
    assert top["verdict"] == "identified"
    r002 = _result(claim, "iso-002")
    assert r002["aggregate_confidence"] < top["aggregate_confidence"]
    assert r002["verdict"] != "identified"


# =========================================================================
# 6. Determinism
# =========================================================================


def test_determinism_claim_byte_identical(render):
    surface, _ = render
    a = json.dumps(recognise(surface, str(GRAMMARS)), sort_keys=True)
    b = json.dumps(recognise(surface, str(GRAMMARS)), sort_keys=True)
    assert a == b


def test_measure_grid_surface_shape():
    """Every grid measurer returns a Measurement with the v0 shape (name, value,
    n, detail)."""
    sheet = load_sheet(ISO_SHEET)
    surface = grid.render(sheet, cols=COLS, rows=ROWS, module_px=MODULE, seed=0)
    m = mg.measure_grid_surface(surface)
    for name in ("grid_module_detect", "ink_set_match",
                 "overprint_multiply_consistency", "stripe_duty",
                 "primitive_frequency_mix", "staircase_step_angle"):
        meas = m["measurements"][name]
        assert meas.feature_measure_name == name
        assert isinstance(meas.n, int)
        assert isinstance(meas.detail, dict)
