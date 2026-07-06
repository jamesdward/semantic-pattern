"""Tests for scene localisation + rectification (Phase 10, SI-025 / SI-027).

Synthetic-first (project rule): every claim here is checked against composite
scenes built from GENERATED surfaces with known ground truth, never against the
real acceptance photos (those are an acceptance corpus, measured in the exp-003
re-run report, not a unit-test oracle). The scenes paste a generated surface onto
a larger background (black / white / textured) and, for the end-to-end case, warp
the whole composite with ``battery.degrade.perspective_warp`` -- so the locator
must find the surface inside a scene, and rectification must recover the cascade
measurement the way it does on a real off-axis photo.

Four things are asserted:
  1. LOCALISATION -- the locator's region overlaps the pasted surface (IoU >= 0.7)
     on black / white / textured backgrounds.
  2. RECTIFICATION -- a perspective-warped synthetic scene, end-to-end through
     ``recognise``, recovers the cascade ratio within tolerance (vs the garbage a
     full-res unrectified screen crop gives).
  3. SHORT-CIRCUIT -- a bare fragment (>=90% coverage) takes the byte-identical
     pre-SI-025 path: the claim is exactly the localised-path claim and carries no
     scene ``localisation`` block.
  4. BAND TWO-PATH COLOUR (SI-027) -- under a synthetic white-balance cast the
     relationship path recovers where the absolute path collapses (mirrors the
     iso-002 SI-020 test).
"""

import json
from pathlib import Path

import numpy as np
import pytest

from sheets import load_sheet, list_sheets
from generator import cascade, grid
from battery import degrade
from recogniser import locate, measure
from recogniser import claim as claim_mod
from recogniser.claim import recognise

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAMMARS = REPO_ROOT / "grammars"


@pytest.fixture(scope="module")
def band_sheet():
    return load_sheet(GRAMMARS / "bar-cascade-001.yaml")


@pytest.fixture(scope="module")
def grid_sheet():
    return load_sheet(GRAMMARS / "iso-002.yaml")


@pytest.fixture(scope="module")
def sheets():
    return list_sheets(str(GRAMMARS))


@pytest.fixture(scope="module")
def band_surface(band_sheet):
    # Axis-aligned so ground truth is exact (ratio 1.94, 5 bands).
    return cascade.render(band_sheet, n_bands=5, module_px=120, seed=0,
                          orientation_deg=0.0)


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    return inter / float(aw * ah + bw * bh - inter)


def _paste(background, surface, oy, ox):
    canvas = background.copy()
    sh, sw = surface.shape[:2]
    canvas[oy:oy + sh, ox:ox + sw] = surface
    return canvas, (ox, oy, sw, sh)


def _largest_region(regions):
    return max(regions, key=lambda r: r["bbox"][2] * r["bbox"][3])


# =========================================================================
# 1. Localisation -- the surface is found inside a scene (IoU >= 0.7)
# =========================================================================


def _textured_background(h, w):
    """A deterministic non-ink 'wood/desk' texture (a hue away from the greens)."""
    rng = np.random.default_rng(0)
    xx = np.arange(w)
    grain = (40 + 25 * np.sin(xx / 23.0)).astype(np.int32)
    base = np.zeros((h, w, 3), np.int32)
    base[:, :, 0] = np.clip(grain * 1.6, 0, 255)   # B high -> brown/blue, not green
    base[:, :, 1] = grain
    base[:, :, 2] = np.clip(grain * 1.3, 0, 255)
    base += rng.integers(-12, 12, base.shape)
    return np.clip(base, 0, 255).astype(np.uint8)


@pytest.mark.parametrize("bg_kind", ["black", "white", "textured"])
def test_locator_finds_surface_in_scene(band_surface, sheets, bg_kind):
    """On black / white / textured backgrounds the located region overlaps the
    pasted surface with IoU >= 0.7, and the scene is NOT short-circuited (it is a
    genuine scene, not a bare fragment)."""
    H, W = 1200, 1000
    if bg_kind == "black":
        bg = np.zeros((H, W, 3), np.uint8)
    elif bg_kind == "white":
        bg = np.full((H, W, 3), 255, np.uint8)
    else:
        bg = _textured_background(H, W)
    scene, truth = _paste(bg, band_surface, oy=250, ox=300)

    regions = locate.find_candidate_regions(scene, sheets)
    assert not regions[0]["working"].get("already_localised")   # a scene, not a fragment
    best = _largest_region(regions)
    assert _iou(truth, best["bbox"]) >= 0.7


# =========================================================================
# 2. Rectification -- a warped synthetic scene recovers the cascade ratio
# =========================================================================


def test_rectification_recovers_ratio_end_to_end(band_surface):
    """A perspective-warped page-in-scene, run end-to-end through recognise(),
    is localised, rectified, and its measured cascade ratio lands within
    tolerance of 1.94 -- where the SAME crop measured unrectified at full screen
    resolution gives a garbage ratio (>10) from moire-driven false bands."""
    sh, sw = band_surface.shape[:2]
    page = np.full((1400, 1100, 3), 255, np.uint8)
    page[300:300 + sh, 350:350 + sw] = band_surface
    big = np.zeros((1600, 1300, 3), np.uint8)
    big[100:1500, 100:1200] = page
    warped = degrade.perspective_warp(big, 0.05, seed=1)

    claim = recognise(warped, str(GRAMMARS))
    assert "localisation" in claim                       # took the scene path
    r = next(x for x in claim["results"] if x["sheet_id"] == "bar-cascade-001")
    assert r["region"]["rectified"] is True

    cr = next(f for f in r["per_feature"] if f["id"] == "cascade_ratio")
    assert cr["measured"] is not None
    assert abs(float(cr["measured"]) - 1.94) <= 0.08     # recovered near the truth
    # And the true 5-band structure is recovered (not moire-exploded).
    assert claim["measurement_working"]["band"]["n_bands_detected"] == 5


def test_scene_claim_is_deterministic(band_surface):
    """Same warped scene -> byte-identical claim JSON (README principle 4)."""
    sh, sw = band_surface.shape[:2]
    big = np.zeros((1200, 1000, 3), np.uint8)
    big[250:250 + sh, 300:300 + sw] = band_surface
    warped = degrade.perspective_warp(big, 0.04, seed=2)
    a = json.dumps(recognise(warped, str(GRAMMARS)), sort_keys=True)
    b = json.dumps(recognise(warped, str(GRAMMARS)), sort_keys=True)
    assert a == b


# =========================================================================
# 3. Short-circuit -- a bare fragment is byte-identical to the pre-SI-025 path
# =========================================================================


def test_bare_fragment_short_circuits_to_localised_path(band_surface, sheets):
    """A full surface (>=90% ink-compatible coverage) short-circuits: the claim is
    EXACTLY the pre-SI-025 localised-path claim and carries no scene block."""
    regions = locate.find_candidate_regions(band_surface, sheets)
    assert len(regions) == 1
    assert regions[0]["working"].get("already_localised") is True

    got = recognise(band_surface, str(GRAMMARS))
    assert "localisation" not in got                     # no scene block added
    # Byte-identical to running the pre-SI-025 path directly.
    ref = claim_mod._recognise_localised(band_surface, "<array>", list_sheets(str(GRAMMARS)))
    assert got == ref


def test_grid_surface_also_short_circuits(grid_sheet, sheets):
    """A grid fragment is sparse ink on white, but its inks span corner to corner,
    so it short-circuits too -- keeping iso-002 synthetic claims byte-identical."""
    surface = grid.render(grid_sheet, cols=10, rows=7, module_px=64, seed=3)
    regions = locate.find_candidate_regions(surface, sheets)
    assert regions[0]["working"].get("already_localised") is True
    assert "localisation" not in recognise(surface, str(GRAMMARS))


# =========================================================================
# 4. Band two-path colour (SI-027) -- relationship recovers, absolute collapses
# =========================================================================


@pytest.mark.parametrize("gains", [(1.25, 1.0, 0.78), (0.78, 1.0, 1.25)])
def test_band_white_balance_relationship_recovers(band_surface, gains):
    """Under a per-channel white-balance cast the 001 ABSOLUTE colour match
    collapses but the RELATIONSHIP path (SI-027) recovers: agreement stays high,
    the absolute-only match fails, and the implied applied gain matches the cast.
    Mirrors the iso-002 SI-020 test for the 2-ink band grammar."""
    shifted = degrade.white_balance(band_surface, gains)
    claim = recognise(shifted, str(GRAMMARS))
    r = next(x for x in claim["results"] if x["sheet_id"] == "bar-cascade-001")
    cp = next(f for f in r["per_feature"] if f["id"] == "colour_pair")
    d = cp["detail"]

    assert d["agreement_absolute"] < 0.2                 # absolute path collapses
    assert d["agreement_relationship"] >= 0.7            # relationship recovers
    assert cp["agreement"] >= 0.7                        # feature = max(abs, rel)
    assert d["gain_in_bounds"] is True
    for got, applied in zip(d["implied_applied_gain_bgr"], gains):
        assert abs(got - applied) < 0.1
    # The sheet-derived corroboration (luminance order + hue proximity) holds for
    # a genuine cast on the two greens.
    assert d["corroboration"]["luminance_order_preserved"] is True
    assert d["corroboration"]["hue_proximity_preserved"] is True


def test_band_relationship_rejects_wrong_colour_pair(band_sheet):
    """A two-ink pair that is NOT the sheet greens (a red/blue pair a gain could
    map close) must fail the gain-invariant corroboration, so the relationship
    path does NOT rescue it -- the guard against 2-ink overfitting (SI-027)."""
    from recogniser import score, measure as _m
    # Build a fake 2-ink measurement: saturated red (dark) + saturated blue (light).
    fake = _m.Measurement("ink_set_match",
                          value=[[40.0, 40.0, 200.0], [200.0, 60.0, 40.0]], n=2,
                          detail={"order": "BGR mean per class"})
    feature = next(f for f in band_sheet["signature_locus"]["features"]
                   if f["id"] == "colour_pair")
    out = score._score_ink_band(feature, fake)
    # Hues are far apart -> proximity corroboration fails -> relationship disabled.
    assert out["detail"]["corroboration"]["hue_proximity_preserved"] is False
    assert out["detail"]["agreement_relationship"] == 0.0
