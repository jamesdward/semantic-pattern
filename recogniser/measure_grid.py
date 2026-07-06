"""Classical, deterministic measurers for GRID grammars (audit 002 / iso-002).

This is the grid-family sibling of ``recogniser/measure.py`` (which serves the
*band* family, grammar 001). The public pipeline (``recogniser/claim.py``)
dispatches on the sheet's ``structure.type``: ``band`` -> ``measure.py``,
``grid`` -> this module. Both return the same shape --
``{"measurements": {name: Measurement}, "working": {...}}`` with the same
``Measurement`` dataclass -- so the scorer consumes either uniformly.

Everything here is classical CV (numpy + OpenCV only, no learned models) and
deterministic: the same image yields byte-identical measurements (README
principle 4). A measurer NEVER looks at a grammar sheet -- it turns pixels into a
number plus an honest sample count -- so each measurer is unit-tested against
synthetic ground truth from ``generator/grid.py`` before the pipeline trusts it.

What we measure, and why (Pattern Grammar Audit 002 s2 / s5 / s6):

  * GRID MODULE (audit s2 method: edge-projection autocorrelation). Project the
    gradient magnitude onto x and onto y, autocorrelate each 1-D profile, and
    read the dominant common period. Autocorrelation is shift-invariant, so a
    crop that does not start on a module boundary recovers the same period
    (robust to the fragment not being module-aligned). Role: normalisation --
    anchors scale, identifies nothing (SI-006).

  * FLAT-INK EXTRACTION. The generated surfaces are exactly flat (no
    anti-aliasing, generator docstring), so the distinct ink colours are exact
    pixel values. We count unique colours (excluding the white ground) with a
    small Euclidean quantisation guard so a JPEG'd / degraded input still
    clusters to the same centroids, and keep colours holding a real area
    fraction.

  * PARENT / PRODUCT SEPARATION (audit s2 overprint arithmetic). A colour c3 is
    a PRODUCT (evidence of two-ink layering, not a member of the ink set) when
    some pair of *distinct* other extracted colours (c1, c2) satisfies
    ``multiply(c1, c2) ~= c3`` under the generator's exact rounding
    ``(c1*c2 + 127)//255``. Products are separated out; the candidate INK SET is
    the parents plus any colour not explainable as a product. Per product we
    record the residual ``|measured - multiply(parents)|`` -- that residual is
    the overprint-consistency verification measurement (audit s5: the arithmetic
    is a self-verifying colour check; ``c1*c2 != c3`` flags an unfaithful
    reproduction).

    Note on a palette coincidence: for the ISO master set red #C5111D equals
    multiply(#D52EB2, #EC5F2A) exactly. When both a lone red ink and a
    magenta*orange overprint occur they are the *same* pixel value and merge into
    one cluster, which frequency-ordered separation accepts as the (frequent)
    red ink -- so the coincidence is harmless. Every other master ink sits >= 43
    units from any distinct-pair product (measured), so the separation tolerance
    below never misclassifies a real ink as a product.

  * STRIPE RHYTHM (verification; audit s2: bar M/2, pitch 1M, duty 0.5). Where a
    striped region exists we run-length measure bar/gap in module units and
    report the duty; if no striped region is found we report it unobserved
    (n = 0), never a failure.

  * PRIMITIVE FREQUENCY MIX (audit s6 point 2; sheet ``status: unmeasured``,
    SI-008). A rough connected-component classifier bins components into
    {circle_like, rectangle_like, stripe_like, diagonal_like, other}. The sheet
    declares this feature unmeasured, so the scorer SKIPS it regardless (SI-008);
    we expose the measured mix in the working as "measured but uncommitted",
    with an honest caveat that overlapping primitives merge into one component so
    the classifier is coarse. We deliberately do not over-engineer it.

  * INK-SET matching is NOT done here. The two-path (absolute + relationship /
    white-balance-gain) match lives in ``recogniser/score.py`` because it needs
    the sheet's expected inks; this module only hands over the extracted ink set
    (BGR triples) and the products. Strict measurement/scoring separation holds:
    this module never sees a sheet.
"""

from __future__ import annotations

import cv2
import numpy as np

from recogniser.measure import Measurement

# --- constants (v0 choices, documented) -------------------------------------

# Grid-module autocorrelation search window (px). The audit modules are 69-72 px;
# we search a generous band so smaller test renders and larger surfaces both
# resolve. The fundamental (not a harmonic 2M/3M) is chosen as the smallest lag
# that is a strong local autocorrelation peak.
MIN_MODULE_PX = 8
MAX_MODULE_PX = 500

# A colour is the white ground when every channel is at least this bright. The
# ground is exactly 255; the threshold tolerates JPEG softening without ever
# swallowing an ink (the lightest master inks keep a channel well below 245).
WHITE_MIN = 250

# Quantisation guard for flat-ink extraction: unique colours within this
# Euclidean BGR distance of an already-accepted cluster centroid are merged into
# it. 0 on a clean render (colours are exact); absorbs JPEG/degradation spread.
INK_MERGE_DIST = 8.0

# A colour cluster holding less than this fraction of the surface is treated as
# noise (JPEG ringing, thin AA-free edges are exact so this mostly drops
# compression artefacts) and dropped from the ink/product analysis.
MIN_INK_FRACTION = 0.003

# Parent/product separation is two-tier (Chebyshev per-channel on the exact
# multiply). A colour is a FAITHFUL product when it matches multiply of a pair of
# other inks -- SELF pairs (same ink overlapping itself, ink*ink) OR distinct
# pairs -- within PRODUCT_SEP_TOL. Clean products match at 0; every real master
# ink is >= 17 units from any self product and >= 43 from any distinct product
# (measured), so a tight 8 never misclassifies a real ink as a product (the sole
# 0-distance coincidence, red == magenta*orange, is the SAME pixel value and
# collapses by colour-merge). Faithful products are excluded from the ink set with
# a small residual.
PRODUCT_SEP_TOL = 8

# A colour that a DISTINCT-hue pair multiplies close-to-but-not-onto (residual in
# (PRODUCT_SEP_TOL, PRODUCT_BROKEN_TOL], contained, parents hue-distinct) is a
# BROKEN overprint: the arithmetic drifted (audit s5 "c1*c2 != c3"). It is excluded
# from the ink set and its (large) residual drives the overprint-consistency flag.
# The distinct-hue requirement stops a single-hue lightness ramp (a 001 band
# surface's anti-aliased greens, where a darker green ~= multiply of two lighter
# greens) from ever registering as a broken overprint. See SPEC-ISSUES SI-020.
PRODUCT_BROKEN_TOL = 40

# Multiply always darkens: an overprint colour is <= BOTH parents per channel.
# Requiring this containment (with a small slack for degradation) before a colour
# is called a product stops a noisy non-grid image (e.g. a 001 band surface's
# anti-aliased greens) from ever pairing into a spurious "broken" product and
# raising a false overprint-verification flag: two greens cannot be contained by
# two distinct other inks. A genuine (even mildly unfaithful) overprint stays
# darker than its parents and is still caught.
CONTAINMENT_SLACK = 4

# An overprint is a product of two DISTINCT inks (audit s2), so its parents must
# differ in HUE, not merely in lightness. This is the guard that keeps a
# single-hue lightness ramp -- e.g. a 001 band surface's anti-aliased green ramp,
# where a darker green really is ~multiply(two lighter greens) -- from ever being
# read as an overprint: all its colours share one hue. Threshold in OpenCV hue
# units (0..179, ~2 deg each); circular. Real ISO ink pairs that actually overlap
# differ by well over this; near-identical-hue inks (teal vs teal-grey) are
# conservatively skipped rather than risk a false flag (weight-0 verification).
MIN_PARENT_HUE = 15

# Stripe detection: a column is "striped" when its interior ink/gap runs are
# within this fraction of the module of the expected M/2 bar/gap.
STRIPE_RUN_TOL_FRAC = 0.30


# --- multiply (matches generator.grid exactly) ------------------------------


def _multiply(a, b):
    """Channel-wise multiply overprint ``(a*b + 127)//255`` (generator.grid).

    Re-implemented here (rather than imported from ``generator``) so the
    recogniser stays independent of the generator, but the arithmetic is
    byte-identical to ``generator.grid.multiply`` -- the audit s2 rule verified to
    the integer.
    """
    a = np.asarray(a, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
    return (a * b + 127) // 255


def _hue(bgr) -> int:
    """OpenCV HSV hue (0..179) of a single BGR colour (for distinct-ink test)."""
    px = np.array([[[int(bgr[0]), int(bgr[1]), int(bgr[2])]]], dtype=np.uint8)
    return int(cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0, 0])


def _hue_dist(h1: int, h2: int) -> int:
    """Circular distance between two OpenCV hues (period 180)."""
    d = abs(h1 - h2) % 180
    return min(d, 180 - d)


# --- grid module via edge-projection autocorrelation (audit s2) -------------


def _axis_autocorr_period(profile: np.ndarray):
    """Dominant period of a 1-D edge projection via autocorrelation.

    Returns ``(period_px, strength)`` where strength is the peak autocorrelation
    normalised by lag-0 (in [0, 1]); ``(None, 0.0)`` if nothing resolves. The
    fundamental is the SMALLEST lag that is a strong local maximum, so a lattice
    with structure at 2M/4M (whose autocorrelation also peaks there) still
    reports M, not a multiple.
    """
    profile = profile.astype(np.float64)
    profile = profile - profile.mean()
    if profile.size < 2 * MIN_MODULE_PX or not np.any(profile):
        return None, 0.0
    full = np.correlate(profile, profile, mode="full")
    ac = full[full.size // 2 :]  # non-negative lags
    zero = ac[0] if ac[0] != 0 else 1.0
    lo = MIN_MODULE_PX
    hi = min(ac.size - 2, MAX_MODULE_PX)
    if hi <= lo:
        return None, 0.0
    window = ac[lo : hi + 1]
    peak = float(window.max())
    if peak <= 0:
        return None, 0.0
    # Local maxima at >= 40% of the window peak; take the smallest lag (the
    # fundamental) among them.
    thresh = 0.4 * peak
    for lag in range(lo, hi + 1):
        if ac[lag] < thresh:
            continue
        if ac[lag] >= ac[lag - 1] and ac[lag] >= ac[lag + 1]:
            return lag, float(ac[lag] / zero)
    lag = lo + int(np.argmax(window))
    return lag, float(ac[lag] / zero)


def detect_grid_module(surface: np.ndarray):
    """Detect the square module M (px) by edge-projection autocorrelation.

    Returns ``(module_px, n_cells, detail)``. ``module_px`` is ``None`` when no
    lattice resolves. Method (audit s2): sum the absolute x-gradient over rows to
    get a vertical-edge profile and the absolute y-gradient over columns for a
    horizontal-edge profile, autocorrelate each, and reconcile the two axis
    periods (a square lattice shares one M). Shift-invariant, so an off-grid crop
    recovers the same M.
    """
    grey = surface.astype(np.float64).sum(axis=2)
    dx = np.abs(np.diff(grey, axis=1)).sum(axis=0)  # vertical edges, length W-1
    dy = np.abs(np.diff(grey, axis=0)).sum(axis=1)  # horizontal edges, length H-1
    px, sx = _axis_autocorr_period(dx)
    py, sy = _axis_autocorr_period(dy)

    if px is None and py is None:
        return None, 0, {"x_period": None, "y_period": None,
                         "reason": "no lattice resolved"}
    # Reconcile: if both axes resolved and agree within 2 px, average; otherwise
    # take the stronger axis (a striped fragment shows edges on one axis only).
    if px is not None and py is not None and abs(px - py) <= 2:
        module = int(round((px + py) / 2.0))
    elif px is not None and py is not None:
        module = px if sx >= sy else py
    else:
        module = px if px is not None else py

    h, w = surface.shape[:2]
    n_cells = max(1, (h // module) * (w // module)) if module else 0
    detail = {"x_period": px, "y_period": py,
              "x_strength": round(sx, 4), "y_strength": round(sy, 4),
              "module_px": module}
    return module, n_cells, detail


# --- flat-ink extraction ----------------------------------------------------


def extract_flat_inks(surface: np.ndarray):
    """Extract distinct flat ink colours (excluding white) with area fractions.

    Returns ``(inks, detail)`` where ``inks`` is a list of ``(bgr_tuple,
    fraction)`` sorted by fraction desc then colour (deterministic). Method:
    exclude near-white ground pixels, take unique colours with counts, then a
    frequency-ordered greedy clustering with an ``INK_MERGE_DIST`` Euclidean
    guard so degraded inputs cluster to the same centroids a clean render gives
    exactly. Colours below ``MIN_INK_FRACTION`` of the surface are dropped as
    noise.
    """
    h, w = surface.shape[:2]
    total = float(h * w)
    flat = surface.reshape(-1, 3)
    ground = np.all(flat >= WHITE_MIN, axis=1)
    body = flat[~ground]
    if body.shape[0] == 0:
        return [], {"total_px": total, "n_clusters": 0, "note": "all-white surface"}

    colours, counts = np.unique(body, axis=0, return_counts=True)
    colours = colours.astype(np.int64)
    # Deterministic order: most frequent first, ties by BGR value.
    order = np.lexsort((colours[:, 2], colours[:, 1], colours[:, 0], -counts))

    centroids = []   # float BGR
    weights = []     # pixel counts
    sums = []        # weighted BGR sums (for centroid update)
    for idx in order:
        c = colours[idx].astype(np.float64)
        n = int(counts[idx])
        assigned = None
        best = INK_MERGE_DIST
        for k, cen in enumerate(centroids):
            d = float(np.sqrt(((cen - c) ** 2).sum()))
            if d <= best:
                best = d
                assigned = k
        if assigned is None:
            centroids.append(c.copy())
            sums.append(c * n)
            weights.append(n)
        else:
            sums[assigned] = sums[assigned] + c * n
            weights[assigned] += n
            centroids[assigned] = sums[assigned] / weights[assigned]

    inks = []
    for cen, wgt in zip(centroids, weights):
        frac = wgt / total
        if frac < MIN_INK_FRACTION:
            continue
        bgr = tuple(int(round(v)) for v in cen)
        inks.append((bgr, frac))
    inks.sort(key=lambda t: (-t[1], t[0]))
    detail = {"total_px": total, "n_clusters": len(inks)}
    return inks, detail


# --- parent / product separation (audit s2 overprint arithmetic) ------------


def separate_products(inks):
    """Split extracted inks into a candidate ink set and multiply products.

    ``inks`` is ``[(bgr, fraction), ...]`` (frequency-ordered). Returns
    ``(ink_set, products, detail)``:

      * ``ink_set``  -- ``[(bgr, fraction), ...]`` parents + colours not
        explainable as a product (the identification-bearing set).
      * ``products`` -- list of dicts ``{colour, parents, predicted, residual,
        broken}`` where ``residual`` is the Chebyshev distance between the measured
        colour and ``multiply(parents)`` (audit s5 overprint-consistency signal),
        and ``broken`` marks a drifted (unfaithful) overprint.

    Two-tier (see the ``PRODUCT_SEP_TOL`` / ``PRODUCT_BROKEN_TOL`` constants),
    both requiring containment (the product is darker than both parents, since
    multiply darkens):

      * FAITHFUL -- a self pair (ink*ink) OR a distinct pair whose multiply lands
        within ``PRODUCT_SEP_TOL`` of the colour (residual ~0 on a clean render).
      * BROKEN   -- a DISTINCT-HUE pair whose multiply lands within
        ``PRODUCT_BROKEN_TOL`` but beyond ``PRODUCT_SEP_TOL``: the arithmetic
        drifted (audit s5). The distinct-hue requirement stops a single-hue
        lightness ramp from ever registering as a broken overprint.

    Both kinds are removed from the ink set. Frequency order matters: parents
    (frequent lone-ink regions) are considered before their (rarer) products, so a
    product is only ever explained by colours already accepted as inks.
    """
    colours = [np.array(bgr, dtype=np.int64) for bgr, _ in inks]
    hues = [_hue(bgr) for bgr, _ in inks]
    n = len(colours)
    is_product = [False] * n
    products = []

    for k in range(n):
        best = None  # (residual, i, j, broken)
        for i in range(n):
            if i == k or is_product[i]:
                continue
            for j in range(i, n):  # j == i allows self-overlap products (ink*ink)
                if j == k or is_product[j]:
                    continue
                # Containment: c_k must be darker than both parents (multiply
                # darkens), else it is a lone ink / artefact, not an overprint.
                if np.any(colours[k] > colours[i] + CONTAINMENT_SLACK) or \
                   np.any(colours[k] > colours[j] + CONTAINMENT_SLACK):
                    continue
                d = int(np.abs(_multiply(colours[i], colours[j]) - colours[k]).max())
                distinct_hue = _hue_dist(hues[i], hues[j]) >= MIN_PARENT_HUE
                if d <= PRODUCT_SEP_TOL:
                    broken = False
                elif d <= PRODUCT_BROKEN_TOL and distinct_hue:
                    broken = True   # drifted arithmetic between two distinct inks
                else:
                    continue
                if best is None or d < best[0]:
                    best = (d, i, j, broken)
        if best is not None:
            d, i, j, broken = best
            is_product[k] = True
            products.append({
                "colour": tuple(int(v) for v in colours[k]),
                "parents": (tuple(int(v) for v in colours[i]),
                            tuple(int(v) for v in colours[j])),
                "predicted": tuple(int(v) for v in _multiply(colours[i], colours[j])),
                "residual": int(d),
                "broken": broken,
            })

    ink_set = [inks[k] for k in range(n) if not is_product[k]]
    detail = {"n_inks": len(ink_set), "n_products": len(products),
              "n_broken": sum(1 for p in products if p["broken"])}
    return ink_set, products, detail


# --- stripe rhythm (verification; audit s2) ---------------------------------


def measure_stripe_duty(surface: np.ndarray, module_px):
    """Measure the stripe duty over any horizontally-striped region.

    Returns ``(duty, n_regions, detail)``. A column is "striped" when its
    interior ink/gap runs are all within ``STRIPE_RUN_TOL_FRAC`` of the expected
    M/2 bar/gap and it holds >= 2 bars; contiguous striped columns are grouped
    into regions. ``duty`` is the mean ink fraction over striped columns; when no
    striped region is found ``n_regions == 0`` and ``duty`` is ``None`` (reported
    unobserved, never a failure -- audit s5).
    """
    if not module_px or module_px < 4:
        return None, 0, {"reason": "no module to anchor stripe rhythm"}
    half = module_px / 2.0
    tol = STRIPE_RUN_TOL_FRAC * module_px
    ink = (surface != 255).any(axis=2)  # any ink (incl. overprints)
    h, w = ink.shape

    striped_cols = []
    duties = []
    for x in range(w):
        col = ink[:, x]
        runs = _run_lengths(col)
        if len(runs) < 4:
            continue
        interior = runs[1:-1]  # drop partial leading/trailing runs
        bar_runs = [n for on, n in interior if on]
        gap_runs = [n for on, n in interior if not on]
        if len(bar_runs) < 2:
            continue
        if all(abs(n - half) <= tol for n in bar_runs) and \
           all(abs(n - half) <= tol for n in gap_runs):
            striped_cols.append(x)
            duties.append(float(col.mean()))

    if not striped_cols:
        return None, 0, {"reason": "no striped region found"}

    # Group contiguous striped columns into regions (sample_unit striped_regions).
    n_regions = 1
    for a, b in zip(striped_cols, striped_cols[1:]):
        if b - a > 1:
            n_regions += 1
    duty = float(np.mean(duties))
    detail = {"n_striped_columns": len(striped_cols), "n_regions": n_regions,
              "expected_duty": 0.5, "measured_duty": round(duty, 4)}
    return duty, n_regions, detail


def _run_lengths(mask_1d):
    """Lengths of consecutive True/False runs down a bool vector: [(on, n), ...]."""
    runs = []
    if mask_1d.size == 0:
        return runs
    cur = bool(mask_1d[0])
    length = 1
    for v in mask_1d[1:]:
        if bool(v) == cur:
            length += 1
        else:
            runs.append((cur, length))
            cur = bool(v)
            length = 1
    runs.append((cur, length))
    return runs


# --- primitive frequency mix (SI-008: measured but uncommitted) -------------


def measure_primitive_mix(surface: np.ndarray, module_px):
    """Rough connected-component classification of the primitive mix.

    Returns ``(mix, n_components, detail)``. Bins each ink component into
    {circle_like, rectangle_like, stripe_like, diagonal_like, other} by simple
    shape statistics (extent, aspect, circularity, and internal stripe periodicity).
    This is deliberately coarse: overlapping primitives merge into one component,
    so the counts are indicative, not exact -- honesty note carried in the detail.
    The sheet declares ``primitive_frequency_mix`` unmeasured (SI-008), so the
    scorer skips this regardless; it is exposed only as working evidence.
    """
    ink = (surface != 255).any(axis=2).astype(np.uint8)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    bins = {"circle_like": 0, "rectangle_like": 0, "stripe_like": 0,
            "diagonal_like": 0, "other": 0}
    min_area = max(16, (module_px * module_px) // 8) if module_px else 16
    n_components = 0
    for lab in range(1, n_labels):
        area = int(stats[lab, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        n_components += 1
        bw = int(stats[lab, cv2.CC_STAT_WIDTH])
        bh = int(stats[lab, cv2.CC_STAT_HEIGHT])
        extent = area / float(bw * bh) if bw * bh else 0.0
        aspect = bw / float(bh) if bh else 0.0
        comp = (labels[stats[lab, cv2.CC_STAT_TOP]:stats[lab, cv2.CC_STAT_TOP] + bh,
                       stats[lab, cv2.CC_STAT_LEFT]:stats[lab, cv2.CC_STAT_LEFT] + bw]
                == lab)
        # Internal stripe periodicity: a stripe block's centre column alternates.
        mid = comp[:, bw // 2] if bw else np.array([], dtype=bool)
        runs = [n for on, n in _run_lengths(mid) if on]
        striped = (module_px and len(runs) >= 2
                   and all(abs(n - module_px / 2.0) <= 0.4 * module_px for n in runs))
        if striped:
            bins["stripe_like"] += 1
        elif extent >= 0.95 and 0.8 <= aspect <= 1.25:
            bins["rectangle_like"] += 1
        elif 0.70 <= extent <= 0.85 and 0.8 <= aspect <= 1.25:
            bins["circle_like"] += 1          # inscribed circle fills ~pi/4 of its box
        elif extent <= 0.6:
            bins["diagonal_like"] += 1        # staircase leaves the box sparsely filled
        else:
            bins["other"] += 1
    detail = {"caveat": "overlapping primitives merge into one component; counts "
                        "are coarse and this feature is uncommitted (SI-008)",
              "min_component_area": min_area}
    return bins, n_components, detail


# --- top-level grid-surface measurement -------------------------------------


def measure_grid_surface(image: np.ndarray) -> dict:
    """Measure every iso-002 (grid) locus feature present in ``image`` (BGR uint8).

    Returns ``{"measurements": {name: Measurement}, "working": {...}}`` keyed by
    the sheet ``measure`` names the scorer consumes:
    ``grid_module_detect``, ``ink_set_match`` (the extracted ink SET; the
    two-path colour match is done in the scorer), ``overprint_multiply_consistency``,
    ``stripe_duty``, ``primitive_frequency_mix``, and ``staircase_step_angle``.
    Features not observable come back ``n == 0`` / ``value None`` (honesty rule).
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be (H, W, 3) BGR uint8")

    working = {"family": "grid"}
    steps = []

    # 1. Grid module (audit s2 edge-projection autocorrelation).
    module_px, n_cells, grid_detail = detect_grid_module(image)
    working["grid"] = grid_detail
    steps.append(f"grid module = {module_px} px (edge-projection autocorrelation)"
                 if module_px else "grid module: no lattice resolved")
    grid_m = Measurement("grid_module_detect", module_px, n_cells if module_px else 0,
                         grid_detail)

    # 2. Flat inks -> parent/product separation.
    inks_all, ink_detail = extract_flat_inks(image)
    ink_set, products, sep_detail = separate_products(inks_all)
    steps.append(f"extracted {len(inks_all)} flat colours -> {len(ink_set)} inks, "
                 f"{len(products)} products")
    working["inks"] = {
        "extracted": [{"bgr": list(bgr), "fraction": round(f, 5)} for bgr, f in inks_all],
        "ink_set": [{"bgr": list(bgr), "fraction": round(f, 5)} for bgr, f in ink_set],
        "products": products,
        **ink_detail, **sep_detail,
    }
    ink_measurement = Measurement(
        feature_measure_name="ink_set_match",
        value=[list(bgr) for bgr, _ in ink_set],
        n=len(ink_set),
        detail={"order": "BGR ink-set centroids (products excluded)",
                "fractions": [round(f, 5) for _, f in ink_set],
                "products": products,
                # Full extracted palette (inks + products) with fractions, so the
                # scorer's relationship path can undo an estimated white-balance
                # gain and re-separate products in the corrected space where the
                # multiply arithmetic holds again (SI-020).
                "extracted": [[list(bgr), round(f, 5)] for bgr, f in inks_all]},
    )

    # 3. Overprint consistency (verification): the WORST product residual. Clean
    #    renders give 0 (exact multiply); a broken overprint gives a large
    #    residual that the scorer turns into a verification flag (audit s5).
    if products:
        residuals = [p["residual"] for p in products]
        overprint = Measurement(
            "overprint_multiply_consistency",
            float(max(residuals)),
            len(products),
            {"per_product_residual": residuals,
             "mean_residual": round(float(np.mean(residuals)), 3),
             "max_residual": int(max(residuals))},
        )
    else:
        overprint = Measurement("overprint_multiply_consistency", None, 0,
                                {"reason": "no two-ink overlaps observed"})

    # 4. Stripe rhythm (verification).
    duty, n_stripe, stripe_detail = measure_stripe_duty(image, module_px)
    working["stripe"] = stripe_detail
    stripe = Measurement("stripe_duty", duty, n_stripe, stripe_detail)

    # 5. Primitive mix (measured but uncommitted -- SI-008).
    mix, n_comp, mix_detail = measure_primitive_mix(image, module_px)
    working["primitive_mix"] = {"mix": mix, "n_components": n_comp, **mix_detail}
    primitive_mix = Measurement("primitive_frequency_mix", mix, n_comp,
                                {"note": "measured but uncommitted (SI-008)", **mix_detail})

    # 6. Staircase angle: not separately measured in the v0 grid family (weight-0
    #    verification; robustly recovering 45deg from a cell staircase inside a
    #    free composition is not worth fake precision). Reported unobserved with
    #    an honest reason; the primitive classifier's diagonal_like count is in
    #    the working as coarse evidence.
    staircase = Measurement("staircase_step_angle", None, 0,
                            {"reason": "staircase angle not measured in v0 grid "
                                       "family; see primitive_mix.diagonal_like"})

    working["normalisation_steps"] = steps
    return {
        "measurements": {
            "grid_module_detect": grid_m,
            "ink_set_match": ink_measurement,
            "overprint_multiply_consistency": overprint,
            "stripe_duty": stripe,
            "primitive_frequency_mix": primitive_mix,
            "staircase_step_angle": staircase,
        },
        "working": working,
    }
