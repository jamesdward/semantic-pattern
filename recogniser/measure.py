"""Classical, deterministic measurers: image -> locus features (spec 8 step 2).

This module is the *measurement* half of the recogniser. It is strictly
separate from scoring (``recogniser/score.py``): a measurer turns pixels into a
number plus an honest sample count, and never looks at a grammar sheet. That
separation is what lets every measurer be unit-tested against synthetic ground
truth on its own (project rule; see ``tests/test_recogniser.py``).

Everything here is classical CV -- OpenCV + numpy only, no learned models
(spec 8 conformance: "implementable ... with classical measurement"). Every
step is deterministic: Otsu, Sobel structure tensor and warpAffine all give the
same output for the same input, so the same image yields byte-identical
measurements (README principle 4).

What we measure, and why (Pattern Grammar Audit 001 s1-s3):

  * The surface is vertical *bands* of horizontal *bars*. Within a band the
    light/dark stripe pattern varies down y (the bars are horizontal); the band
    boundaries are vertical lines a module apart. Period *increases* left to
    right across bands (the cascade).
  * A fragment arrives at an unknown rotation. We estimate the stripe tilt
    (structure tensor), de-rotate so the bars are horizontal again, then do all
    run-length work on the de-rotated interior.
  * The identifying power is in the *transitions* (audit s3): one band gives a
    period + duty only; the frequency ratio and phase rule need >= 2 bands (>= 1
    boundary) in frame. Each measurer therefore reports ``n`` -- how many
    samples of its unit it actually saw -- so the scorer can scale confidence
    with sample size (SI-002) and refuse to over-claim from a single band.

HONESTY RULES enforced here (audit s3, SI-005):
  * A single-band fragment yields period + duty with n>0 but the cascade ratio
    and the anchored phase step come back n=0 (value None): there is no boundary
    to measure them across.
  * With no band boundary in frame, orientation is genuinely ambiguous -- a
    90-degree-rotated single band is indistinguishable from vertical stripes
    (audit s3). We flag ``orientation_ambiguous`` and record both candidate
    angles in the working; the run-length scalars (period, duty) are
    rotation-invariant so their *values* are unaffected, but the scorer reduces
    confidence because the orientation cannot be anchored (SI-005).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

# Below this stripe tilt (degrees) we treat the surface as already axis-aligned
# and skip the de-rotation warp, so a clean 0-degree render is measured without
# a needless resample. The enrolled 0.7-degree orientation is above this, so a
# natural surface is still de-rotated.
DEROTATE_MIN_DEG = 0.1

# A band must span at least this many measurable columns to be accepted; smaller
# runs are noise or partial edge bands and are merged into their neighbour.
MIN_BAND_COLS = 8

# Two adjacent column periods whose ratio exceeds this (either way) mark a band
# boundary. 1.4 sits comfortably between within-band noise (~1.0) and a cascade
# step (1.94); it is the same threshold the generator tests use.
BAND_STEP_RATIO = 1.4

# A colour class holding a smaller pixel fraction than this is treated as
# absent: the fragment sits inside a single ink and only one ink is observed.
SINGLE_INK_FRACTION = 0.02


@dataclass
class Measurement:
    """One measured locus feature: a value, how many samples backed it, detail.

    ``n`` is the count of the feature's *sample unit* actually observed (audit
    s3 / SI-002): band boundaries for a cascade ratio, periods for a duty cycle,
    inks for a colour set. ``n == 0`` means the feature was not observable in
    this image -- the scorer reports it unobserved and renormalises weight over
    what was seen. ``value`` is ``None`` exactly when ``n == 0``.
    """

    feature_measure_name: str
    value: object
    n: int
    detail: dict = field(default_factory=dict)


# --- two-colour classification ---------------------------------------------


def classify_two_colour(image: np.ndarray):
    """Split ``image`` (BGR uint8) into two ink classes by Otsu on luminance.

    Returns ``(light_mask, dark_bgr, light_bgr, inks_n, detail)``.

    Choice (documented per task): deterministic **Otsu threshold on the
    luminance channel**, then the per-class *mean* BGR as each ink's colour. We
    prefer Otsu + class means over k-means because it is fully deterministic (no
    seeded centroid init, no iteration-count sensitivity), needs no k guess for
    the two-ink case the grammar declares, and Otsu is the textbook optimum for
    a bimodal histogram -- which a two-ink pattern is. ``inks_n`` is 2 when both
    classes are populated, 1 when the fragment sits inside a single ink (audit
    s3: "a fragment inside one dark bar sees 1").
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be (H, W, 3) BGR")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    light_mask = otsu.astype(bool)
    light_frac = float(light_mask.mean())

    if light_frac < SINGLE_INK_FRACTION or light_frac > 1.0 - SINGLE_INK_FRACTION:
        # One class is essentially empty: the fragment sits inside a single ink.
        only = image.reshape(-1, 3).mean(0)
        detail = {"inks_n": 1, "light_fraction": light_frac, "otsu": True}
        return light_mask, only, only, 1, detail

    dark_bgr = image[~light_mask].mean(0)
    light_bgr = image[light_mask].mean(0)
    detail = {"inks_n": 2, "light_fraction": light_frac, "otsu": True}
    return light_mask, dark_bgr, light_bgr, 2, detail


# --- orientation and de-rotation -------------------------------------------


def _wrap90(angle_deg: float) -> float:
    """Wrap an angle into (-90, 90] (bar tilt is only defined mod 180)."""
    while angle_deg <= -90.0:
        angle_deg += 180.0
    while angle_deg > 90.0:
        angle_deg -= 180.0
    return angle_deg


def _wrap45(angle_deg: float) -> float:
    """Wrap an angle into (-45, 45] -- the residual de-skew mod a quarter turn.

    Stripes are aligned to *an* axis by a rotation of at most 45 degrees; whether
    that axis is horizontal or vertical (the 90-degree stripe ambiguity, audit
    s3) is then resolved by trying both measurement axes. Wrapping to (-45, 45]
    keeps the de-rotation small, so we never warp by ~90 degrees (which would
    crop a non-square fragment to a thin sliver)."""
    while angle_deg <= -45.0:
        angle_deg += 90.0
    while angle_deg > 45.0:
        angle_deg -= 90.0
    return angle_deg


def estimate_orientation(image: np.ndarray):
    """Estimate the dominant stripe/bar tilt via the gradient structure tensor.

    Returns ``(bar_tilt_deg, detail)``. ``bar_tilt_deg`` is how far the bars are
    tilted from horizontal, in (-90, 90]; de-rotating by this angle makes the
    bars horizontal again. Method: the structure tensor
    ``J = [[sum gx^2, sum gx gy], [sum gx gy, sum gy^2]]`` (Sobel gradients);
    the dominant gradient direction is ``0.5 * atan2(2 Jxy, Jxx - Jyy)``. For
    horizontal bars the gradient is vertical (~90 degrees); the bar tilt is that
    direction minus 90. Chosen over an FFT peak because it is a couple of
    reductions, needs no windowing choice, and is naturally magnitude-weighted.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float64)
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    jxx = float((gx * gx).sum())
    jyy = float((gy * gy).sum())
    jxy = float((gx * gy).sum())
    grad_dir_deg = np.rad2deg(0.5 * np.arctan2(2.0 * jxy, jxx - jyy))
    bar_tilt = _wrap90(grad_dir_deg - 90.0)
    detail = {"gradient_dir_deg": grad_dir_deg, "bar_tilt_deg": bar_tilt}
    return bar_tilt, detail


def _rotated_rect_max_area(w: float, h: float, angle_rad: float):
    """Largest axis-aligned rectangle inside a ``w x h`` rect rotated by angle.

    Standard result (used to crop away the warp border after de-rotation so no
    replicated-edge pixels reach the run-length measurers).
    """
    if w <= 0 or h <= 0:
        return 0.0, 0.0
    width_is_longer = w >= h
    side_long, side_short = (w, h) if width_is_longer else (h, w)
    sin_a = abs(np.sin(angle_rad))
    cos_a = abs(np.cos(angle_rad))
    if side_short <= 2.0 * sin_a * cos_a * side_long or abs(sin_a - cos_a) < 1e-10:
        x = 0.5 * side_short
        wr, hr = (x / sin_a, x / cos_a) if width_is_longer else (x / cos_a, x / sin_a)
    else:
        cos_2a = cos_a * cos_a - sin_a * sin_a
        wr = (w * cos_a - h * sin_a) / cos_2a
        hr = (h * cos_a - w * sin_a) / cos_2a
    return wr, hr


def derotate(image: np.ndarray, bar_tilt_deg: float):
    """Rotate ``image`` so the bars are horizontal, then crop the warp border.

    Returns ``(aligned, detail)``. If the estimated tilt is below
    ``DEROTATE_MIN_DEG`` the image is returned unchanged (no needless resample of
    an already-aligned surface). Otherwise the image is warp-rotated by the tilt
    and cropped to the largest inscribed axis-aligned rectangle, so the interior
    handed to the run-length measurers contains only real pattern -- never the
    replicated edge pixels a rotation leaves in the corners.
    """
    if abs(bar_tilt_deg) < DEROTATE_MIN_DEG:
        return image, {"derotated": False, "applied_deg": 0.0}
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), bar_tilt_deg, 1.0)
    rotated = cv2.warpAffine(
        image, matrix, (w, h),
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
    )
    inner_w, inner_h = _rotated_rect_max_area(w, h, np.deg2rad(bar_tilt_deg))
    inner_w = int(max(1, np.floor(inner_w)))
    inner_h = int(max(1, np.floor(inner_h)))
    x0 = (w - inner_w) // 2
    y0 = (h - inner_h) // 2
    aligned = rotated[y0 : y0 + inner_h, x0 : x0 + inner_w].copy()
    detail = {"derotated": True, "applied_deg": float(bar_tilt_deg),
              "inner_wh": (inner_w, inner_h)}
    return aligned, detail


# --- run-length structure --------------------------------------------------


def _column_rises(light_mask: np.ndarray, x: int):
    """Y indices where column ``x`` goes dark -> light (start of a light bar)."""
    col = light_mask[:, x].astype(np.int8)
    return np.where(np.diff(col) == 1)[0] + 1


def _column_period(light_mask: np.ndarray, x: int):
    """Median full-cycle period (px) at column ``x``, or None if < 2 light bars."""
    rises = _column_rises(light_mask, x)
    if len(rises) < 2:
        return None
    return float(np.median(np.diff(rises)))


def _axis_profile(light_mask: np.ndarray):
    """Per-column period profile down ``light_mask``: (xs, periods) of valid columns.

    A column is valid when it holds >= 2 light bars (a measurable stripe period).
    Called on both the mask and its transpose to test the two candidate stripe
    axes (the 90-degree ambiguity): the true stripe axis yields many valid lines
    with fine periods, the perpendicular (band) axis yields few.
    """
    w = light_mask.shape[1]
    xs_all = np.arange(1, w - 1)
    periods_all = [_column_period(light_mask, int(x)) for x in xs_all]
    valid = [p is not None for p in periods_all]
    xs = xs_all[valid]
    periods = np.array([float(p) for p, ok in zip(periods_all, valid) if ok],
                       dtype=np.float64)
    return xs, periods


def _segment_bands(xs: np.ndarray, periods: np.ndarray):
    """Group columns into bands by detecting period jumps (audit s1 boundaries).

    ``xs`` are the columns that had a measurable period and ``periods`` their
    values. A new band starts where the column period jumps by more than
    ``BAND_STEP_RATIO`` relative to the running median of the current band; runs
    shorter than ``MIN_BAND_COLS`` are merged back so a few noisy columns do not
    manufacture a boundary. Returns a list of index-lists into ``xs``.
    """
    if len(xs) == 0:
        return []
    bands = []
    current = [0]
    for i in range(1, len(periods)):
        rep = float(np.median(periods[current]))
        if periods[i] > BAND_STEP_RATIO * rep or periods[i] < rep / BAND_STEP_RATIO:
            if len(current) >= MIN_BAND_COLS:
                bands.append(current)
                current = [i]
            else:
                # Too short to be a real band: absorb and keep going.
                current.append(i)
        else:
            current.append(i)
    if len(current) >= MIN_BAND_COLS:
        bands.append(current)
    elif bands:
        bands[-1].extend(current)
    elif current:
        bands.append(current)
    return bands


# --- top-level surface measurement -----------------------------------------


def measure_surface(image: np.ndarray) -> dict:
    """Measure every 001 locus feature present in ``image`` (BGR uint8).

    Returns ``{"measurements": {name: Measurement}, "working": {...}}`` where the
    measurement names are the sheet ``measure`` names the scorer consumes:
    ``period_cascade_ratio``, ``duty_cycle``, ``phase_step``, ``ink_set_match``,
    ``band_period``. Features not observable in this image come back with
    ``n == 0`` and ``value == None`` (honesty rule). The working records the
    normalisation applied, the orientation estimate and any ambiguity (spec 8:
    "the working is part of the output").
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be (H, W, 3) BGR uint8")

    steps = []
    ambiguities = []

    # 1. Normalise orientation on the raw (border-free) fragment. Estimate the
    #    stripe tilt, de-skew by the residual (<= 45 deg) so the stripes align to
    #    an axis without a near-90 sliver crop.
    bar_tilt, ori_detail = estimate_orientation(image)
    deskew = _wrap45(bar_tilt)
    aligned, derot_detail = derotate(image, deskew)
    steps.append(
        f"estimated bar tilt {bar_tilt:.2f} deg (structure tensor); de-skewed by "
        f"{deskew:.2f} deg "
        + ("(cropped to inscribed interior)" if derot_detail["derotated"]
           else "(below threshold, measured as-is)")
    )

    # 2. Two-colour classification on the aligned interior (axis-independent).
    light_mask, dark_bgr, light_bgr, inks_n, ink_detail = classify_two_colour(aligned)
    steps.append(
        f"Otsu two-colour classification: {inks_n} ink(s), "
        f"light fraction {ink_detail['light_fraction']:.3f}"
    )

    ink = Measurement(
        feature_measure_name="ink_set_match",
        value=[list(np.round(dark_bgr, 3)), list(np.round(light_bgr, 3))]
        if inks_n == 2
        else [list(np.round(dark_bgr, 3))],
        n=inks_n,
        detail={"order": "BGR mean per class", **ink_detail},
    )

    # 3. Resolve the 90-degree stripe ambiguity by trying both axes (audit s3,
    #    SI-005): measure the period profile down the columns and down the rows
    #    (transpose), and keep whichever axis has more measurable stripe lines --
    #    the true stripe axis crosses many bars, the band axis almost none.
    xs_c, per_c = _axis_profile(light_mask)
    xs_r, per_r = _axis_profile(light_mask.T)
    if len(xs_c) >= len(xs_r):
        axis = "columns"
        xs, periods = xs_c, per_c
    else:
        axis = "rows"
        light_mask = light_mask.T  # measure the same way, transposed
        xs, periods = xs_r, per_r
    steps.append(
        f"stripe axis = {axis} ({len(xs_c)} column vs {len(xs_r)} row stripe lines)"
    )

    working = {
        "orientation_bar_tilt_deg": round(float(bar_tilt), 3),
        "orientation_deskew_deg": round(float(deskew), 3),
        "stripe_axis": axis,
        "orientation_candidates_deg": [round(float(deskew), 3),
                                       round(float(_wrap90(deskew + 90.0)), 3)],
        "orientation_detail": ori_detail,
        "derotation": derot_detail,
        "normalisation_steps": steps,
        "ambiguities": ambiguities,
    }

    if len(xs) < MIN_BAND_COLS:
        # Not even one band's worth of measurable columns: report duty/inks only.
        duty_val = float(light_mask.mean()) if len(xs) else None
        n_periods = int(sum(len(_column_rises(light_mask, int(x))) - 1 for x in xs))
        working["n_bands_detected"] = 1 if len(xs) else 0
        working["orientation_ambiguous"] = True
        ambiguities.append(
            "no band boundary in frame: orientation cannot be anchored; a "
            "90-degree rotation of a single band reads identically (audit s3, SI-005)"
        )
        return {
            "measurements": {
                "ink_set_match": ink,
                "duty_cycle": Measurement("duty_cycle", duty_val, max(n_periods, 0)
                                          if duty_val is not None else 0,
                                          {"single_region": True}),
                "band_period": Measurement("band_period",
                                           float(np.median(periods)) if len(periods) else None,
                                           1 if len(periods) else 0, {}),
                "period_cascade_ratio": Measurement("period_cascade_ratio", None, 0,
                                                    {"reason": "no band boundary in frame"}),
                "phase_step": Measurement("phase_step", None, 0,
                                          {"reason": "phase unanchored without a boundary"}),
            },
            "working": working,
        }

    bands = _segment_bands(xs, periods)
    n_bands = len(bands)
    working["n_bands_detected"] = n_bands

    # Orientation anchor (spec 8 step 1): the grammar declares the cascade runs
    # increasing_period (audit s1). If the detected bands run coarse-to-fine we
    # are looking at the pattern flipped (e.g. a 180/90-degree rotation), so we
    # reverse the band order to the declared sense before measuring ratios and
    # the phase step. This resolves the flip ambiguity the moment a boundary is
    # in frame; a single band cannot be anchored this way (audit s3, SI-005).
    if n_bands >= 2:
        first_p = float(np.median(periods[bands[0]]))
        last_p = float(np.median(periods[bands[-1]]))
        if last_p < first_p:
            bands = bands[::-1]
            steps.append("band order reversed to declared cascade direction "
                         "increasing_period (orientation anchor, audit s1)")

    # Per-band representative period, duty and phase.
    band_period = []
    band_duty = []
    band_phase = []      # (finer_period, first_rise_y) for the band's centre column
    band_period_n = []   # periods observed in that band's centre column
    for idx_list in bands:
        cols = xs[idx_list]
        band_period.append(float(np.median(periods[idx_list])))
        band_duty.append(float(light_mask[:, cols].mean()))
        xc = int(cols[len(cols) // 2])
        rises = _column_rises(light_mask, xc)
        p = _column_period(light_mask, xc)
        band_phase.append((p, float(rises[0]) if len(rises) else None))
        band_period_n.append(max(len(rises) - 1, 0))

    # duty_cycle: mean over bands, n = total periods observed (SI-002 sample unit).
    duty_val = float(np.mean(band_duty))
    n_periods = int(sum(band_period_n))
    duty = Measurement("duty_cycle", duty_val, n_periods,
                       {"per_band_duty": [round(d, 4) for d in band_duty]})

    # period_cascade_ratio: ratios of adjacent band periods, n = boundaries.
    ratios = [band_period[i + 1] / band_period[i] for i in range(n_bands - 1)]
    cascade = Measurement(
        "period_cascade_ratio",
        float(np.mean(ratios)) if ratios else None,
        len(ratios),
        {"ratios": [round(r, 4) for r in ratios],
         "band_periods": [round(p, 2) for p in band_period]},
    )

    # phase_step: offset between adjacent bands / finer (lower-index) period,
    # as a fraction in [0, 1); n = boundaries (audit s1 phase rule).
    phase_fracs = []
    for i in range(n_bands - 1):
        p0, ph0 = band_phase[i]
        _, ph1 = band_phase[i + 1]
        if p0 is None or ph0 is None or ph1 is None:
            continue
        offset = (ph1 - ph0) % p0
        phase_fracs.append(offset / p0)
    phase = Measurement(
        "phase_step",
        float(np.mean(phase_fracs)) if phase_fracs else None,
        len(phase_fracs),
        {"per_boundary_step": [round(f, 4) for f in phase_fracs]},
    )

    # band_period: normalisation anchor, n = bands.
    band_period_m = Measurement("band_period", float(np.median(band_period)), n_bands,
                                {"band_periods": [round(p, 2) for p in band_period]})

    if n_bands < 2:
        working["orientation_ambiguous"] = True
        ambiguities.append(
            "single band in frame: orientation cannot be anchored (audit s3, SI-005)"
        )
    else:
        working["orientation_ambiguous"] = False

    return {
        "measurements": {
            "ink_set_match": ink,
            "duty_cycle": duty,
            "period_cascade_ratio": cascade,
            "phase_step": phase,
            "band_period": band_period_m,
        },
        "working": working,
    }


def load_image(image_or_path) -> np.ndarray:
    """Return a BGR uint8 array from a path or pass an array straight through."""
    if isinstance(image_or_path, np.ndarray):
        return image_or_path
    path = str(image_or_path)
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"could not read image: {path}")
    return img
