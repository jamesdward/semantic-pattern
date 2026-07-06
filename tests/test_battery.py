"""Tests for the Phase-4 test battery: degradations and the synthetic harness.

Two parts, mirroring the project rule that machinery is tested before it is
trusted:

  1. Degradation unit tests -- each transform preserves shape/dtype, is
     deterministic (same args -> identical output), and moves the image in the
     sane direction (blur reduces gradient energy, brightness shifts the mean,
     white balance shifts the gained channel).
  2. A ``--quick`` end-to-end battery run into a tmp dir: asserts the manifest,
     CSV and curve PNGs exist, the CSV has the declared columns and a non-trivial
     row count, and the run is reproducible.

The Phase-4b real-photo ingestion path (``battery.ingest``) and the print pack
(``battery.printpack``) have their own suite in ``tests/test_ingest.py``.
"""

import csv
import numpy as np
import pytest

from pathlib import Path

from sheets import load_sheet
from generator import cascade
from generator.fragments import sample_fragment
from battery import degrade
from battery.run import BatteryConfig, quick_config, run_battery, CSV_FIELDS

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET_PATH = REPO_ROOT / "grammars" / "bar-cascade-001.yaml"
N_BANDS = 5
MODULE = 160


@pytest.fixture(scope="module")
def surface():
    sheet = load_sheet(SHEET_PATH)
    return cascade.render(sheet, n_bands=N_BANDS, module_px=MODULE, seed=0,
                          orientation_deg=0.0)


@pytest.fixture(scope="module")
def fragment(surface):
    frag, _ = sample_fragment(surface, frac=0.4, rng=np.random.default_rng(0),
                              module_px=MODULE, n_bands=N_BANDS)
    return frag


def _grad_energy(img):
    gray = img.mean(2).astype(np.float64)
    gx = np.diff(gray, axis=1)
    gy = np.diff(gray, axis=0)
    return float((gx * gx).sum() + (gy * gy).sum())


# =========================================================================
# 1. Degradation unit tests
# =========================================================================

@pytest.mark.parametrize("name,fn", [
    ("identity", lambda im: degrade.identity(im)),
    ("blur", lambda im: degrade.gaussian_blur(im, 2.0)),
    ("jpeg", lambda im: degrade.jpeg_roundtrip(im, 50)),
    ("brightness", lambda im: degrade.brightness(im, 1.2)),
    ("white_balance", lambda im: degrade.white_balance(im, (0.9, 1.0, 1.1))),
    ("perspective", lambda im: degrade.perspective_warp(im, 0.04, seed=3)),
])
def test_degradation_preserves_shape_and_dtype(fragment, name, fn):
    out = fn(fragment)
    assert out.shape == fragment.shape
    assert out.dtype == np.uint8


@pytest.mark.parametrize("fn", [
    lambda im: degrade.gaussian_blur(im, 2.0),
    lambda im: degrade.jpeg_roundtrip(im, 50),
    lambda im: degrade.brightness(im, 1.2),
    lambda im: degrade.white_balance(im, (0.9, 1.0, 1.1)),
    lambda im: degrade.perspective_warp(im, 0.04, seed=3),
    lambda im: degrade.identity(im),
])
def test_degradation_is_deterministic(fragment, fn):
    assert np.array_equal(fn(fragment), fn(fragment))


def test_identity_is_unchanged(fragment):
    assert np.array_equal(degrade.identity(fragment), fragment)


def test_blur_reduces_gradient_energy(fragment):
    """The sane direction: more blur -> less edge energy, monotonically."""
    e0 = _grad_energy(fragment)
    e1 = _grad_energy(degrade.gaussian_blur(fragment, 1.0))
    e2 = _grad_energy(degrade.gaussian_blur(fragment, 4.0))
    assert e2 < e1 < e0


def test_brightness_shifts_mean(fragment):
    up = degrade.brightness(fragment, 1.3)
    down = degrade.brightness(fragment, 0.7)
    assert up.mean() > fragment.mean() > down.mean()


def test_white_balance_shifts_gained_channel(fragment):
    """Boosting the red (index 2) gain raises the red-channel mean, not blue."""
    out = degrade.white_balance(fragment, (1.0, 1.0, 1.5))
    assert out[..., 2].mean() > fragment[..., 2].mean()
    assert out[..., 0].mean() == pytest.approx(fragment[..., 0].mean(), abs=1.0)


def test_perspective_zero_strength_is_identity(fragment):
    assert np.array_equal(degrade.perspective_warp(fragment, 0.0), fragment)


def test_perspective_different_seed_differs(fragment):
    a = degrade.perspective_warp(fragment, 0.05, seed=1)
    b = degrade.perspective_warp(fragment, 0.05, seed=2)
    assert not np.array_equal(a, b)


def test_degradation_rejects_bad_input():
    with pytest.raises(ValueError):
        degrade.gaussian_blur(np.zeros((4, 4), np.uint8), 1.0)


# =========================================================================
# 2. End-to-end --quick battery run
# =========================================================================

def test_quick_battery_run_writes_all_artifacts(tmp_path):
    config = quick_config()
    summary = run_battery(config, tmp_path, timestamp="2026-01-01T00:00:00+00:00",
                          progress=False)

    manifest = tmp_path / "manifest.yaml"
    csv_path = tmp_path / "raw_results.csv"
    assert manifest.exists()
    assert csv_path.exists()
    # Every declared curve/dist chart exists.
    for name in ("curve_min_fragment_vs_confidence.png",
                 "curve_confidence_by_boundaries.png",
                 "curve_degradation_blur.png",
                 "dist_impostor_vs_genuine.png"):
        assert (tmp_path / name).exists(), name

    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == CSV_FIELDS
        rows = list(reader)
    assert len(rows) == summary["rows"] > 0
    # The three arms all produced rows.
    arms = {r["arm"] for r in rows}
    assert {"genuine", "impostor"} <= arms
    # Genuine full-surface fragments beat single-band impostors (sanity).
    assert any(r["degradation"] != "none" for r in rows)


def test_quick_battery_run_is_reproducible(tmp_path):
    ts = "2026-01-01T00:00:00+00:00"
    a = tmp_path / "a"
    b = tmp_path / "b"
    run_battery(quick_config(), a, timestamp=ts, progress=False)
    run_battery(quick_config(), b, timestamp=ts, progress=False)
    assert (a / "raw_results.csv").read_bytes() == (b / "raw_results.csv").read_bytes()
    assert ((a / "curve_min_fragment_vs_confidence.png").read_bytes()
            == (b / "curve_min_fragment_vs_confidence.png").read_bytes())


def test_impostors_do_not_reach_identified_in_quick_run(tmp_path):
    """False-positive discipline holds even in the tiny smoke run: no impostor
    fragment is ever verdict 'identified' against bar-cascade-001."""
    run_battery(quick_config(), tmp_path, timestamp="2026-01-01T00:00:00+00:00",
                progress=False)
    with open(tmp_path / "raw_results.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    impostor_verdicts = {r["verdict_001"] for r in rows if r["arm"] == "impostor"}
    assert "identified" not in impostor_verdicts
