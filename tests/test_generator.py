"""Tests for the bar-cascade generator and fragment sampler.

Project rule (README, tests row): every generator is validated against synthetic
ground truth BY MEASUREMENT. These tests render surfaces from the enrolled sheet
and measure the audit-001 quantities back out of the pixels -- period ratios,
duty cycle, band width, phase offsets, orientation -- then check determinism and
the fragment sampler's ground truth.
"""

import numpy as np
import pytest

from pathlib import Path

from sheets import load_sheet
from generator import cascade
from generator.fragments import (
    FragmentInfo,
    band_boundaries_spanned,
    sample_fragment,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET_PATH = REPO_ROOT / "grammars" / "bar-cascade-001.yaml"

N_BANDS = 5
MODULE = 200


@pytest.fixture(scope="module")
def sheet():
    return load_sheet(SHEET_PATH)


@pytest.fixture(scope="module")
def surface0(sheet):
    """Axis-aligned render (orientation forced to 0) for clean measurement."""
    return cascade.render(
        sheet, n_bands=N_BANDS, module_px=MODULE, seed=0, orientation_deg=0.0
    )


# --- measurement helpers ----------------------------------------------------


def _classify_column(surface, x, light, dark):
    """Boolean 'is light' per pixel down column x (nearest-ink classification)."""
    col = surface[:, x, :].astype(np.int32)
    d_light = np.abs(col - light).sum(1)
    d_dark = np.abs(col - dark).sum(1)
    return d_light < d_dark


def _rising_edges(is_light):
    """Indices where the column goes dark -> light (start of a light bar)."""
    return np.where(np.diff(is_light.astype(int)) == 1)[0] + 1


def _measure_period(surface, x, light, dark):
    """Mean full-cycle period (px) at column x, or None if too few edges."""
    rises = _rising_edges(_classify_column(surface, x, light, dark))
    if len(rises) < 2:
        return None
    return float(np.mean(np.diff(rises)))


def _measure_light_fraction(surface, x, light, dark):
    return float(_classify_column(surface, x, light, dark).mean())


def _measure_phase(surface, x, period, light, dark):
    """Vertical phase (first rising-edge y, mod period)."""
    rises = _rising_edges(_classify_column(surface, x, light, dark))
    return float(rises[0] % period)


# --- band structure ---------------------------------------------------------


def test_render_shape_and_dtype(surface0):
    assert surface0.shape == (N_BANDS * MODULE, N_BANDS * MODULE, 3)
    assert surface0.dtype == np.uint8


def test_band_periods_ratio_is_frequency_ratio(sheet):
    periods = cascade.band_periods(sheet, N_BANDS, MODULE)
    ratios = periods[1:] / periods[:-1]
    assert np.allclose(ratios, 1.94, atol=1e-6)


def test_measured_period_ratios(sheet, surface0):
    light, dark = cascade._ink(sheet, "light"), cascade._ink(sheet, "dark")
    periods = [
        _measure_period(surface0, b * MODULE + MODULE // 2, light, dark)
        for b in range(N_BANDS)
    ]
    assert all(p is not None for p in periods)
    ratios = [periods[b + 1] / periods[b] for b in range(N_BANDS - 1)]
    # Tuned frequency ratio (audit s4). A few percent measurement tolerance.
    for r in ratios:
        assert abs(r - 1.94) < 1.94 * 0.03


def test_measured_light_fraction(sheet, surface0):
    light, dark = cascade._ink(sheet, "light"), cascade._ink(sheet, "dark")
    for b in range(N_BANDS):
        lf = _measure_light_fraction(surface0, b * MODULE + MODULE // 2, light, dark)
        # Tuned duty cycle 0.31 (audit s4), within a couple of percent.
        assert abs(lf - 0.31) < 0.03


def test_band_width_equals_module(sheet, surface0):
    """Bands are module_px wide: the column period jumps at x = k*module."""
    light, dark = cascade._ink(sheet, "light"), cascade._ink(sheet, "dark")
    W = surface0.shape[1]
    xs = list(range(5, W - 5, 5))
    periods = [_measure_period(surface0, x, light, dark) for x in xs]
    boundaries = []
    for i in range(1, len(periods)):
        if periods[i] is None or periods[i - 1] is None:
            continue
        ratio = periods[i] / periods[i - 1]
        if ratio > 1.4 or ratio < 1 / 1.4:  # a cascade step, not within-band noise
            boundaries.append(xs[i])
    assert len(boundaries) == N_BANDS - 1
    for k, bx in enumerate(boundaries, start=1):
        assert abs(bx - k * MODULE) <= 10


def test_phase_offset_is_phase_step_times_finer_period(sheet, surface0):
    """Adjacent bands offset by phase_step x the finer band's period (audit s1)."""
    light, dark = cascade._ink(sheet, "light"), cascade._ink(sheet, "dark")
    periods = cascade.band_periods(sheet, N_BANDS, MODULE)
    phase_step = float(sheet["combination_rules"]["phase_step"])
    phases = [
        _measure_phase(surface0, b * MODULE + MODULE // 2, periods[b], light, dark)
        for b in range(N_BANDS)
    ]
    for b in range(N_BANDS - 1):
        offset = phases[b + 1] - phases[b]
        expected = phase_step * periods[b]  # finer = lower-indexed band
        assert abs(offset - expected) <= max(1.5, 0.06 * periods[b])


# --- orientation ------------------------------------------------------------


def test_orientation_angle_measures_tuned_value(sheet):
    """Real 0.7deg render: dominant stripe angle ~= 0.7deg (audit s4)."""
    surface = cascade.render(sheet, n_bands=N_BANDS, module_px=MODULE, seed=0)
    light, dark = cascade._ink(sheet, "light"), cascade._ink(sheet, "dark")

    # Track the first light-bar edge across band 0's width and regress its
    # sub-pixel y against x; the slope is the stripe tilt. (Gradient-histogram
    # angle is swamped by uint8 quantisation at sub-degree tilts.)
    xs, ys = [], []
    for x in range(5, MODULE - 5):
        col = surface[:, x, :].astype(np.float64)
        signed = np.abs(col - dark).sum(1) - np.abs(col - light).sum(1)  # >0 => light
        edge = None
        for y in range(0, 40):
            if signed[y] <= 0 < signed[y + 1]:
                edge = y + (0 - signed[y]) / (signed[y + 1] - signed[y])
                break
        if edge is not None:
            xs.append(x)
            ys.append(edge)
    assert len(xs) > 50
    slope = np.polyfit(np.array(xs), np.array(ys), 1)[0]
    angle = np.rad2deg(np.arctan(slope))
    assert abs(angle - 0.7) < 0.3


def test_orientation_default_comes_from_sheet(sheet):
    """No override -> the sheet's 0.7deg is used, so it differs from 0deg."""
    default = cascade.render(sheet, n_bands=N_BANDS, module_px=MODULE, seed=0)
    flat = cascade.render(
        sheet, n_bands=N_BANDS, module_px=MODULE, seed=0, orientation_deg=0.0
    )
    assert not np.array_equal(default, flat)


# --- determinism (README principle 4) ---------------------------------------


def test_determinism_array(sheet):
    a = cascade.render(sheet, n_bands=N_BANDS, module_px=MODULE, seed=7)
    b = cascade.render(sheet, n_bands=N_BANDS, module_px=MODULE, seed=7)
    assert np.array_equal(a, b)


def test_determinism_png_byte_identical(sheet, tmp_path):
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    cascade.render_png(sheet, p1, n_bands=N_BANDS, module_px=MODULE, seed=3)
    cascade.render_png(sheet, p2, n_bands=N_BANDS, module_px=MODULE, seed=3)
    assert p1.read_bytes() == p2.read_bytes()


# --- fragment sampler -------------------------------------------------------


def test_fragment_area_matches_frac(surface0):
    rng = np.random.default_rng(11)
    H, W = surface0.shape[:2]
    for frac in (0.1, 0.25, 0.5):
        frag, info = sample_fragment(surface0, frac=frac, rng=rng)
        realised = (frag.shape[0] * frag.shape[1]) / (H * W)
        # Only integer rounding + clipping move area off frac.
        assert abs(realised - frac) < 0.02
        assert info.area_frac_actual == pytest.approx(realised)


def test_band_boundaries_spanned_known_windows():
    # Window x in [150, 450): crosses boundaries at 200 and 400 -> 2, bands 0..2.
    count, span = band_boundaries_spanned(150, 450, MODULE, N_BANDS)
    assert count == 2
    assert span == (0, 2)
    # Window entirely inside band 1 -> no boundary crossed.
    count, span = band_boundaries_spanned(210, 390, MODULE, N_BANDS)
    assert count == 0
    assert span == (1, 1)
    # Full-width window: all interior boundaries.
    count, span = band_boundaries_spanned(0, N_BANDS * MODULE, MODULE, N_BANDS)
    assert count == N_BANDS - 1
    assert span == (0, N_BANDS - 1)


def test_fragment_info_records_band_ground_truth(surface0):
    rng = np.random.default_rng(5)
    frag, info = sample_fragment(
        surface0, frac=0.3, rng=rng, module_px=MODULE, n_bands=N_BANDS
    )
    assert isinstance(info, FragmentInfo)
    x0, _ = info.origin_xy
    w, _ = info.size_wh
    expected_count, expected_span = band_boundaries_spanned(
        x0, x0 + w, MODULE, N_BANDS
    )
    assert info.band_boundaries_spanned == expected_count
    assert info.band_span == expected_span
    # to_dict is JSON-manifest friendly.
    d = info.to_dict()
    assert d["frac"] == 0.3
    assert d["module_px"] == MODULE


def test_fragment_reproducible_same_seed(surface0):
    f1, i1 = sample_fragment(surface0, frac=0.2, rng=np.random.default_rng(99))
    f2, i2 = sample_fragment(surface0, frac=0.2, rng=np.random.default_rng(99))
    assert np.array_equal(f1, f2)
    assert i1.to_dict() == i2.to_dict()


def test_fragment_rotated_has_no_border(sheet, surface0):
    """A rotated fragment is sampled from inside the surface: no ground border."""
    rng = np.random.default_rng(2)
    frag, info = sample_fragment(
        surface0, frac=0.1, rng=rng, rotation_deg=8.0, module_px=MODULE, n_bands=N_BANDS
    )
    assert info.rotation_deg == 8.0
    # Every pixel is one of the two inks (no black border from out-of-image warp).
    light, dark = cascade._ink(sheet, "light"), cascade._ink(sheet, "dark")
    flat = frag.reshape(-1, 3).astype(np.int32)
    d_light = np.abs(flat - light).sum(1)
    d_dark = np.abs(flat - dark).sum(1)
    near_ink = np.minimum(d_light, d_dark)
    # Anti-aliased/warp-blended edges sit up to ~125 from the nearest ink (a 50%
    # light/dark blend); a black border (BGR 0,0,0) is ~185 from dark, so this
    # threshold passes real fragments and fails an out-of-image border.
    assert near_ink.max() < 160


def test_fragment_frac_out_of_range_rejected(surface0):
    with pytest.raises(ValueError):
        sample_fragment(surface0, frac=0.0, rng=np.random.default_rng(0))
    with pytest.raises(ValueError):
        sample_fragment(surface0, frac=1.5, rng=np.random.default_rng(0))


# --- SVG (nice-to-have) -----------------------------------------------------


def test_render_svg_smoke(sheet):
    svg = cascade.render_svg(sheet, n_bands=N_BANDS, module_px=MODULE)
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert "rotate(0.7" in svg  # sheet orientation applied
    assert svg.count("<rect") > N_BANDS  # many light bars drawn
