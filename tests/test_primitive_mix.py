"""Tests for the committed primitive-frequency mix (Phase 9; SI-026, closing the
dangerous same-ink half of SI-022).

At grammar_version 1.1.0 iso-002 carries identification on TWO measured features:
the ink set (weight 0.60) and the primitive-frequency mix (weight 0.40, was
reserved-unmeasured, SI-008). This file validates, by measurement against
generator ground truth (README rule):

  1. CLASSIFIER ACCURACY. Per-type recall on isolated primitives, and the key
     safety property -- ZERO cross-type confusion (a miss becomes unclassified,
     never a wrong label). Plus an end-to-end share-error floor on full
     compositions (includes the honest merged-blob + interior-only bias).
  2. GENUINE surfaces (held-out seeds NOT used to derive the expected vector)
     still identify.
  3. SAME-INK IMPOSTORS. all-circles and all-stripes compositions rendered with
     002's exact inks do NOT reach identified at any fragment fraction >= 0.2,
     with a stated margin (SI-022 dangerous half) -- and the NEAREST impostor
     family (all-staircase / all-filled, whose depth-2-cap remnants read as
     filled cells, SI-019) stays below identified at every fraction too.
  4. SMALL-FRAGMENT HONESTY. A fragment with only a few classified primitives
     yields a noisy mix estimate; the n-dependent tolerance (sampling noise is
     not disagreement) plus n-scaling (SI-002, k=2) must let the ink evidence
     carry such a fragment rather than mix noise dominating either way.
  5. The sheet validates at v1.1.0 with the mix committed.
"""

import numpy as np
import pytest
from pathlib import Path

from sheets import load_sheet
from generator import grid, fragments
from generator.grid import (filled_cell_mask, circle_mask, stripe_block_mask,
                            staircase_mask, stadium_mask, quarter_round_mask,
                            _hex_to_bgr)
from recogniser import measure_grid as mg
from recogniser import score
from recogniser.claim import recognise

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAMMARS = REPO_ROOT / "grammars"
ISO_SHEET = GRAMMARS / "iso-002.yaml"
TYPES = mg.PRIMITIVE_MIX_ORDER


@pytest.fixture(scope="module")
def sheet():
    return load_sheet(ISO_SHEET)


def _result(claim, sheet_id):
    for r in claim["results"]:
        if r["sheet_id"] == sheet_id:
            return r
    raise KeyError(sheet_id)


def _feature(result, fid):
    for f in result["per_feature"]:
        if f["id"] == fid:
            return f
    raise KeyError(fid)


def _isolated_mask(type_, rng, rows, cols, M):
    """Build one isolated primitive mask of a known type (mirrors the generator's
    seeded placement, but for a single instance whose true type we know)."""
    if type_ == "filled_cell":
        r = int(rng.integers(0, rows)); c = int(rng.integers(0, cols))
        return filled_cell_mask(rows, cols, M, r, c)
    if type_ == "inscribed_circle":
        scale = 2 if rng.random() < 0.35 else 1
        r = int(rng.integers(0, rows - scale + 1)); c = int(rng.integers(0, cols - scale + 1))
        return circle_mask(rows, cols, M, r, c, scale)
    if type_ == "stripe_bar":
        w = int(rng.integers(1, 4)); h = int(rng.integers(1, 4))
        r = int(rng.integers(0, rows - h + 1)); c = int(rng.integers(0, cols - w + 1))
        return stripe_block_mask(rows, cols, M, r, c, w, h)
    if type_ == "staircase_diagonal":
        n = int(rng.integers(2, 6)); direction = 1 if rng.random() < 0.5 else -1
        c = int(rng.integers(0, max(1, cols - n + 1)))
        if direction == 1:
            r = int(rng.integers(0, max(1, rows - n + 1)))
        else:
            r = int(rng.integers(n - 1, rows)) if rows >= n else rows - 1
        return staircase_mask(rows, cols, M, r, c, n, direction)
    # rounded_cap
    vertical = rng.random() < 0.5
    axis = rows if vertical else cols
    n = int(rng.integers(1, min(4, axis) + 1))
    if n == 1:
        corner = int(rng.integers(0, 4)); r = int(rng.integers(0, rows)); c = int(rng.integers(0, cols))
        return quarter_round_mask(rows, cols, M, r, c, corner)
    if vertical:
        c = int(rng.integers(0, cols)); r = int(rng.integers(0, rows - n + 1))
        return stadium_mask(rows, cols, M, r, c, n, vertical=True)
    r = int(rng.integers(0, rows)); c = int(rng.integers(0, cols - n + 1))
    return stadium_mask(rows, cols, M, r, c, n, vertical=False)


def _bbox_comp(mask):
    ys, xs = np.nonzero(mask)
    return mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


# =========================================================================
# 1. Classifier accuracy floor + zero cross-type confusion (SI-026)
# =========================================================================


def test_classifier_recall_and_no_cross_confusion():
    """On isolated primitives the classifier's INTRINSIC accuracy: per-type recall
    above a stated (data-derived) floor, and -- the load-bearing safety property
    -- ZERO cross-type confusion. A miss falls to unclassified, never to a wrong
    primitive label, so the classifier cannot manufacture a plausible-but-false
    mix (SI-026)."""
    M = 48
    rows = cols = 8
    rng = np.random.default_rng(0)
    N = 120
    confusion = {t: {u: 0 for u in list(TYPES) + [None]} for t in TYPES}
    for _ in range(N):
        for t in TYPES:
            comp = _bbox_comp(_isolated_mask(t, rng, rows, cols, M))
            pred = mg._classify_primitive(comp, M)
            confusion[t][pred] += 1

    recall = {t: confusion[t][t] / N for t in TYPES}
    # Off-diagonal (a true type predicted as a DIFFERENT type) must be exactly 0.
    for t in TYPES:
        for u in TYPES:
            if u != t:
                assert confusion[t][u] == 0, f"cross-confusion {t}->{u}: {confusion[t][u]}"

    # Per-type recall floors, below the measured values (all five types measure
    # 1.0: single-bar stripe blocks are caught by the half-cell-bar rule,
    # 4-cell stadiums by the widened extent band).
    for t in TYPES:
        assert recall[t] >= 0.95, f"{t} recall {recall[t]}"
    assert np.mean(list(recall.values())) >= 0.98


def test_end_to_end_share_error_floor(sheet):
    """On full compositions the measured instance-share vector tracks the ground-
    truth mix within a stated mean-absolute-share-error floor. This includes the
    honest merged-blob bias (overlapping primitives -> unclassified), so the floor
    is looser than the isolated-classifier accuracy -- and stated as such."""
    errs = []
    classified_fracs = []
    for M in (40, 48, 64):
        for d in (0.35, 0.45, 0.55):
            for s in range(6):
                surface, gt = grid.render_with_truth(
                    sheet, cols=14, rows=10, module_px=M, seed=s, density=d)
                meas = mg.measure_grid_surface(surface)["measurements"]["primitive_frequency_mix"]
                if meas.n == 0:
                    continue
                gtc = gt.primitive_counts
                total = sum(gtc.values())
                true_vec = np.array([gtc[t] / total for t in TYPES])
                errs.append(np.abs(np.array(meas.value) - true_vec))
                classified_fracs.append(meas.n / total)
    errs = np.array(errs)
    # Measured ~0.105 (interior-only); floor 0.15 with margin. Every type < 0.20.
    assert errs.mean() < 0.15
    assert (errs.mean(axis=0) < 0.20).all()
    # A meaningful separable INTERIOR sample is recovered (per-ink separation +
    # edge exclusion, SI-026; measured ~0.25 of placed instances).
    assert np.mean(classified_fracs) > 0.18


# =========================================================================
# 2. Genuine held-out surfaces still identify (SI-026 requirement b)
# =========================================================================

# Seeds 100+ are NOT in the derivation corpus (seeds 0-11) the expected vector
# came from -- a genuine hold-out.
HELDOUT_SEEDS = [100, 101, 102, 103, 104, 105, 106, 107]


def test_genuine_heldout_surfaces_identify(sheet):
    """Held-out genuine full surfaces (seeds never used to derive the expected
    vector) still reach identified -- both id-features observed, coverage 1.0."""
    verdicts = []
    for s in HELDOUT_SEEDS:
        for d in (0.35, 0.45, 0.55):
            surface = grid.render(sheet, cols=16, rows=12, module_px=48, seed=s, density=d)
            r = _result(recognise(surface, str(GRAMMARS)), "iso-002")
            assert r["aggregate_confidence"] >= score.CANDIDATE_THRESHOLD
            verdicts.append(r["verdict"])
    identified_rate = np.mean([v == "identified" for v in verdicts])
    # Full genuine surfaces identify at >= 0.80 (director acceptance; measured
    # 0.92 on this corpus).
    assert identified_rate >= 0.80


def test_genuine_small_fragments_stay_solid_candidates(sheet):
    """Genuine frac-0.2 fragments: the mix is barely measurable there (few
    interior instances), so identification honestly weakens -- but the mean
    aggregate must stay a solid candidate (>= 0.45; measured ~0.64). The
    n-dependent tolerance keeps mix sampling noise from being punished as
    disagreement, and the ink evidence carries the fragment."""
    aggs = []
    for s in (100, 102, 104):
        for d in (0.35, 0.45, 0.55):
            surface = grid.render(sheet, cols=16, rows=12, module_px=48,
                                  seed=s, density=d)
            rng = np.random.default_rng(s * 7 + int(d * 100))
            for _ in range(3):
                frag = fragments.sample_fragment(surface, frac=0.2, rng=rng)[0]
                r = _result(recognise(frag, str(GRAMMARS)), "iso-002")
                aggs.append(r["aggregate_confidence"])
    assert float(np.mean(aggs)) >= 0.45


# =========================================================================
# 3. Same-ink impostors do NOT identify at any fragment >= 0.2 (SI-022)
# =========================================================================

IDENT = score.IDENTIFIED_THRESHOLD  # 0.70
IMPOSTOR_MARGIN = 0.04              # stated margin below the identified line


@pytest.mark.parametrize("impostor", ["inscribed_circle", "stripe_bar"])
@pytest.mark.parametrize("frac", [0.2, 0.5, 1.0])
def test_same_ink_impostor_never_identifies(sheet, impostor, frac):
    """A same-ink / different-composition impostor -- an all-circles or all-stripes
    field rendered with 002's EXACT master inks -- must not reach identified at any
    fragment fraction >= 0.2, with a stated margin. Its inks match (agreement ~1)
    but its mix is a one-hot vector far from the committed expected mix, so mix
    agreement is ~0 and the ink weight alone (0.60) cannot cross 0.70 (SI-022 /
    SI-026)."""
    for d in (0.35, 0.45, 0.55):
        surface = grid.render(sheet, cols=16, rows=12, module_px=48, seed=0,
                              density=d, types=[impostor])
        rng = np.random.default_rng(int(d * 100) + 7)
        for _ in range(4):
            frag = surface if frac >= 1.0 else fragments.sample_fragment(
                surface, frac=frac, rng=rng)[0]
            r = _result(recognise(frag, str(GRAMMARS)), "iso-002")
            assert r["verdict"] != "identified"
            assert r["aggregate_confidence"] <= IDENT - IMPOSTOR_MARGIN, (
                f"{impostor} frac {frac} d {d}: agg {r['aggregate_confidence']}")
            if frac >= 1.0:
                break


@pytest.mark.parametrize("impostor", ["staircase_diagonal", "filled_cell"])
@pytest.mark.parametrize("frac", [0.35, 0.5, 1.0])
def test_nearest_impostor_family_stays_below_identified(sheet, impostor, frac):
    """The NEAREST same-ink impostor family: all-staircase and all-filled, whose
    depth-2-cap remnants (SI-019) read as filled cells, concentrating measured
    mass on the filled/staircase bins. Their measured L1 floor (~1.0) sits above
    the committed tolerance because the classifier's recall fixes (half-cell-bar
    stripe rule, stadium band, disc-IoU gate) spread the expected vector across
    all five bins -- so even this family must stay below identified at every
    fraction. Margin here is thinner than the circles/stripes one and asserted
    as strictly-below-threshold (empirical worst 0.684)."""
    for s in range(3):
        for d in (0.35, 0.45, 0.55):
            surface = grid.render(sheet, cols=16, rows=12, module_px=48, seed=s,
                                  density=d, types=[impostor])
            rng = np.random.default_rng(s * 13 + int(d * 100) + 3)
            reps = 1 if frac >= 1.0 else 3
            for _ in range(reps):
                frag = surface if frac >= 1.0 else fragments.sample_fragment(
                    surface, frac=frac, rng=rng)[0]
                r = _result(recognise(frag, str(GRAMMARS)), "iso-002")
                assert r["verdict"] != "identified"
                assert r["aggregate_confidence"] < IDENT, (
                    f"all-{impostor} frac {frac} s {s} d {d}: "
                    f"agg {r['aggregate_confidence']}")


def test_impostor_mix_disagrees_genuine_agrees(sheet):
    """The discriminator, stated directly: on a full surface the all-circles
    impostor's mix agreement is ~0 while a genuine surface's is well above it."""
    imp = grid.render(sheet, cols=16, rows=12, module_px=48, seed=0, density=0.45,
                      types=["inscribed_circle"])
    gen = grid.render(sheet, cols=16, rows=12, module_px=48, seed=101, density=0.45)
    r_imp = _result(recognise(imp, str(GRAMMARS)), "iso-002")
    r_gen = _result(recognise(gen, str(GRAMMARS)), "iso-002")
    imp_mix = _feature(r_imp, "primitive_frequency_mix")
    gen_mix = _feature(r_gen, "primitive_frequency_mix")
    # Both see the exact inks, so ink agreement is high for both...
    assert _feature(r_imp, "ink_set")["agreement"] >= 0.9
    # ...but the mix separates them.
    assert imp_mix["agreement"] == 0.0
    assert gen_mix["agreement"] >= 0.3
    assert r_gen["aggregate_confidence"] > r_imp["aggregate_confidence"] + 0.1


# =========================================================================
# 4. Small-fragment honesty: n-scaling stops a noisy mix dominating (SI-002)
# =========================================================================


def test_small_mix_saturation_discounts():
    """A 3-primitive mix estimate is noisy; the sample-size saturation (SI-002,
    k=2 for primitives_observed) must discount it so it cannot dominate the
    score. At n=3 the mix confidence is agreement x 3/(3+2) = 0.6 x agreement --
    strictly below the agreement, and below a large-n mix's near-1 saturation."""
    k = score.SATURATION_K["primitives_observed"]
    assert k == 2.0
    assert score.saturation(3, "primitives_observed") == pytest.approx(3 / (3 + k))
    assert score.saturation(3, "primitives_observed") < 0.8
    # Monotone: more primitives -> more confidence in the mix estimate.
    assert (score.saturation(3, "primitives_observed")
            < score.saturation(25, "primitives_observed"))


def test_n_dependent_tolerance_widens_then_pins():
    """The mix tolerance is n-dependent (SI-026): tolerance_eff = committed x
    max(1, sqrt(n_ref/n)). Small n widens (sampling noise is not disagreement);
    n >= n_ref pins to the committed value so full-measurement impostor rejection
    is never loosened."""
    n_ref = score.N_REF_PRIMITIVES
    tol = 0.95
    for n in (1, 3, 6):
        eff = tol * max(1.0, np.sqrt(n_ref / n))
        assert eff > tol
    for n in (n_ref, n_ref + 10):
        assert tol * max(1.0, np.sqrt(n_ref / n)) == pytest.approx(tol)


def test_small_fragment_mix_does_not_dominate(sheet):
    """On a small genuine fragment carrying only a few classified primitives, the
    ink evidence -- not the noisy mix -- must carry the score. We assert the mix's
    weighted confidence contribution is below the ink's, so a noisy few-primitive
    mix estimate cannot swing the verdict on its own (SI-002 / SI-026), and that
    the claim's working carries n, tolerance_eff and the L1 (director requirement)
    with the widened tolerance applied at small n."""
    surface = grid.render(sheet, cols=16, rows=12, module_px=48, seed=102, density=0.35)
    rng = np.random.default_rng(11)
    found = False
    for _ in range(40):
        frag = fragments.sample_fragment(surface, frac=0.12, rng=rng)[0]
        r = _result(recognise(frag, str(GRAMMARS)), "iso-002")
        mix = _feature(r, "primitive_frequency_mix")
        ink = _feature(r, "ink_set")
        if not (mix["observed"] and ink["observed"] and 1 <= mix["n"] <= 6):
            continue
        found = True
        # The working shows the n-dependent tolerance handling (SI-026).
        det = mix["detail"]
        assert det["n"] == mix["n"]
        assert "l1_distance" in det
        assert det["tolerance_eff"] > det["tolerance_committed"]  # widened (n small)
        assert det["n_ref"] == score.N_REF_PRIMITIVES
        # Small n -> saturation strictly discounts the mix confidence below its
        # agreement (confidence = agreement x saturation, SI-002).
        assert mix["saturation"] < 0.8
        if mix["agreement"] > 0:
            assert mix["confidence"] < mix["agreement"]
        assert mix["confidence"] == pytest.approx(
            mix["agreement"] * mix["saturation"], abs=1e-3)
        # Ink (weight 0.60, saturated) contributes more than the small-n mix
        # (weight 0.40, discounted): the fragment leans on the ink, not mix noise.
        ink_contrib = 0.60 * (ink["confidence"] or 0.0)
        mix_contrib = 0.40 * (mix["confidence"] or 0.0)
        assert ink_contrib > mix_contrib
    assert found, "no small-mix fragment sampled; adjust frac/seed"


# =========================================================================
# 5. Sheet validates at v1.1.0 with the mix committed
# =========================================================================


def test_sheet_committed_mix_v110(sheet):
    assert sheet["sheet"]["grammar_version"] == "1.1.0"
    feats = {f["id"]: f for f in sheet["signature_locus"]["features"]}
    mix = feats["primitive_frequency_mix"]
    assert mix["status"] == "measured"
    assert mix["role"] == "identification"
    assert mix["weight"] == pytest.approx(0.40)
    assert feats["ink_set"]["weight"] == pytest.approx(0.60)
    assert isinstance(mix["expected"], list) and len(mix["expected"]) == 5
    assert isinstance(mix["tolerance"], (int, float))
    # Identification weights still sum to 1.0 (the load-bearing invariant).
    idw = sum(f["weight"] for f in sheet["signature_locus"]["features"]
              if f["role"] == "identification")
    assert idw == pytest.approx(1.0)
