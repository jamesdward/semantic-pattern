"""Tests for the cross-grammar experiment (exp-002, Milestone 2 Phase 7).

Two parts:

  1. Rotated-sampler regression (SI-017 gap 2). The rotated crop must NEVER return
     a zero/negative-size window at any rotation 0-360, in particular the 85-95 deg
     band that used to degenerate under aspect jitter when the window was wider than
     its fitting margin. Tested at those rotations and under EXTREME aspect jitter
     (monkeypatched and via a very non-square surface).
  2. A ``--quick`` end-to-end ``battery.cross`` run into a tmp dir: the manifest,
     CSV and all four charts exist, the CSV has the declared columns and sane rows,
     the run is reproducible, and the headline disciplines hold in the smoke run
     (no impostor is ever 'identified'; cross-grammar scores stay clean).
"""

import csv
import numpy as np
import pytest

from pathlib import Path

from sheets import load_sheet
from generator import cascade
from generator import fragments
from generator.fragments import sample_fragment
from battery.cross import (
    CrossConfig, quick_config, run_cross, CSV_FIELDS,
    build_nine_ink_impostor,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET_001 = REPO_ROOT / "grammars" / "bar-cascade-001.yaml"
SHEET_002 = REPO_ROOT / "grammars" / "iso-002.yaml"
N_BANDS = 5
MODULE = 200


@pytest.fixture(scope="module")
def square_surface():
    sheet = load_sheet(SHEET_001)
    return cascade.render(sheet, n_bands=N_BANDS, module_px=MODULE, seed=0,
                          orientation_deg=0.0)


# =========================================================================
# 1. Rotated-sampler regression (SI-017 gap 2 / the negative-slice bug)
# =========================================================================

@pytest.mark.parametrize("rotation", [85, 88, 90, 92, 95])
def test_rotated_sampler_never_zero_size_near_90(square_surface, rotation):
    """No 85-95 deg fragment degenerates to a zero-size window (the old bug)."""
    n_ok = 0
    for k in range(120):
        rng = np.random.default_rng(k * 13 + rotation)
        try:
            frag, info = sample_fragment(
                square_surface, frac=0.5, rng=rng, rotation_deg=float(rotation),
                module_px=MODULE, n_bands=N_BANDS)
        except ValueError:
            # A large tilted window that genuinely cannot fit the surface: allowed
            # (a real geometric impossibility), but NOT the zero-size bug.
            continue
        n_ok += 1
        assert frag.ndim == 3
        assert frag.shape[0] > 0 and frag.shape[1] > 0
        assert frag.size > 0
        # FragmentInfo.size_wh must match the returned array exactly.
        assert frag.shape[:2] == (info.size_wh[1], info.size_wh[0])
    assert n_ok > 0  # some fragments at every tested rotation


@pytest.mark.parametrize("rotation", [85, 89, 90, 91, 95])
def test_rotated_sampler_extreme_aspect_jitter(square_surface, rotation, monkeypatch):
    """Under EXTREME aspect jitter (wide windows) near 90 deg, still never empty.

    Extreme jitter is exactly what surfaced the old bug: a window far wider than
    tall has margin_x ~= h/2 < w/2 near 90 deg, so the old crop's left edge went
    negative and sliced to empty. With the direct-warp fix the output is always
    the full (h, w) window.
    """
    monkeypatch.setattr(fragments, "_ASPECT_JITTER", 1.6)
    for k in range(200):
        rng = np.random.default_rng(k * 7 + rotation)
        try:
            frag, info = sample_fragment(
                square_surface, frac=0.3, rng=rng, rotation_deg=float(rotation))
        except ValueError:
            continue
        assert frag.size > 0 and 0 not in frag.shape
        assert frag.shape[:2] == (info.size_wh[1], info.size_wh[0])


def test_rotated_sampler_full_circle_no_empty(square_surface):
    """Sweep 0-360 deg: not a single zero-size window at any rotation."""
    for rot in range(0, 360, 5):
        for k in range(20):
            rng = np.random.default_rng(rot * 31 + k)
            try:
                frag, _ = sample_fragment(square_surface, frac=0.4, rng=rng,
                                          rotation_deg=float(rot))
            except ValueError:
                continue
            assert frag.size > 0 and 0 not in frag.shape


def test_rotated_sampler_wide_surface_near_90(monkeypatch):
    """A very non-square (wide) surface makes windows wide -> the old failure case."""
    sheet = load_sheet(SHEET_001)
    surf = cascade.render(sheet, n_bands=N_BANDS, module_px=MODULE, seed=0,
                          orientation_deg=0.0, size=(400, 1400))
    for rot in (88, 90, 92):
        for k in range(60):
            rng = np.random.default_rng(k * 5 + rot)
            try:
                frag, info = sample_fragment(surf, frac=0.5, rng=rng,
                                             rotation_deg=float(rot))
            except ValueError:
                continue
            assert frag.size > 0 and 0 not in frag.shape
            assert frag.shape[:2] == (info.size_wh[1], info.size_wh[0])


def test_rotated_fragment_at_90_has_no_border(square_surface):
    """A 90 deg fragment is sampled from inside the surface: real ink, no border."""
    sheet = load_sheet(SHEET_001)
    rng = np.random.default_rng(3)
    frag, _ = sample_fragment(square_surface, frac=0.2, rng=rng, rotation_deg=90.0)
    light, dark = cascade._ink(sheet, "light"), cascade._ink(sheet, "dark")
    flat = frag.reshape(-1, 3).astype(np.int32)
    near = np.minimum(np.abs(flat - light).sum(1), np.abs(flat - dark).sum(1))
    assert near.max() < 160   # no out-of-image (black) border


# =========================================================================
# 2. Nine-ink impostor construction
# =========================================================================

def test_nine_ink_impostor_is_far_from_002_inks():
    """The nine-ink impostor's inks sit well outside 002's delta-E tolerance."""
    sheet = load_sheet(SHEET_002)
    imp = build_nine_ink_impostor(sheet, tuple(range(40, 181, 5)))
    assert len(imp["inks"]) == 9
    # delta-E tolerance for 002's ink_set is 10; the impostor is comfortably past it.
    assert imp["min_delta_e"] > 15.0


# =========================================================================
# 3. End-to-end --quick cross-grammar run
# =========================================================================

def test_quick_cross_run_writes_all_artifacts(tmp_path):
    config = quick_config()
    summary = run_cross(config, tmp_path, timestamp="2026-01-01T00:00:00+00:00",
                        progress=False)

    assert (tmp_path / "manifest.yaml").exists()
    csv_path = tmp_path / "raw_results.csv"
    assert csv_path.exists()
    for name in ("curve_confidence_vs_frac_true_001.png",
                 "curve_confidence_vs_frac_true_002.png",
                 "confusion_summary.png",
                 "wb_robustness.png"):
        assert (tmp_path / name).exists(), name

    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == CSV_FIELDS
        rows = list(reader)
    assert len(rows) == summary["rows"] > 0
    # All four arms produced rows.
    arms = {r["arm"] for r in rows}
    assert {"surface_001", "surface_002", "wb_002", "impostor"} <= arms
    # true_grammar is recorded and both grammars appear.
    assert {"bar-cascade-001", "iso-002"} <= {r["true_grammar"] for r in rows}


def test_quick_cross_run_is_reproducible(tmp_path):
    ts = "2026-01-01T00:00:00+00:00"
    a, b = tmp_path / "a", tmp_path / "b"
    run_cross(quick_config(), a, timestamp=ts, progress=False)
    run_cross(quick_config(), b, timestamp=ts, progress=False)
    assert (a / "raw_results.csv").read_bytes() == (b / "raw_results.csv").read_bytes()
    assert ((a / "wb_robustness.png").read_bytes()
            == (b / "wb_robustness.png").read_bytes())


def test_quick_cross_no_impostor_identified(tmp_path):
    """False-positive discipline in the smoke run: no impostor fragment is ever
    'identified' against EITHER sheet, and the nine-ink grid impostor leaks
    nothing (structure is weight-0 in 002)."""
    run_cross(quick_config(), tmp_path, timestamp="2026-01-01T00:00:00+00:00",
              progress=False)
    with open(tmp_path / "raw_results.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    imp = [r for r in rows if r["arm"] == "impostor"]
    assert imp
    for r in imp:
        assert r["verdict_001"] != "identified"
        assert r["verdict_002"] != "identified"
    # The nine-ink grid impostor scores ~0 against 002 (no ink match, no structure).
    nine = [r for r in imp if r["impostor_id"] == "nine_ink_grid"]
    assert nine
    assert all(float(r["aggregate_002"]) < 0.4 for r in nine)


def test_quick_cross_grammar_separation(tmp_path):
    """A full 002 surface is 'identified' as 002 and scores ~0 as 001 (and the
    mirror for a 001 surface): the cross-grammar margin is real."""
    run_cross(quick_config(), tmp_path, timestamp="2026-01-01T00:00:00+00:00",
              progress=False)
    with open(tmp_path / "raw_results.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    full_002 = [r for r in rows if r["arm"] == "surface_002"
                and float(r["frac"]) == 1.0 and float(r["rotation_deg"]) == 0.0]
    assert full_002
    assert all(float(r["aggregate_001"]) < 0.4 for r in full_002)
    assert any(r["verdict_002"] == "identified" for r in full_002)
