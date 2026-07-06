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

  * PRIMITIVE FREQUENCY MIX (audit s6 point 2; committed as an identification
    carrier at grammar_version 1.1.0, SI-008/SI-026). We classify inked regions
    into the five alphabet primitives (filled_cell, inscribed_circle, stripe_bar,
    staircase_diagonal, rounded_cap) by shape statistics in MODULE units and
    report the share of primitive INSTANCES by type (matching
    ``GroundTruth.primitive_counts`` semantics) plus n = instances observed.

    Separation is the hard part: overlapping primitives merge into one connected
    component. Three moves keep the estimate honest (SI-026):
      1. PER-INK LAYERING. Instead of one all-ink mask (where every adjacent or
         overlapping primitive fuses into a few giant blobs -- only ~3% of
         instances stay separable), we reconstruct each ink's footprint from the
         overprint arithmetic: a pixel "carries ink X" when it is exactly X (a
         depth-1 region) OR exactly multiply(X, Y) for some other extracted ink Y
         (a depth-2 overlap). Components then merge only with SAME-ink neighbours
         (rare: each instance draws a random ink from a 4-6 subset).
      2. INTERIOR-ONLY COUNTING. A component touching the image border may be a
         primitive clipped by the fragment boundary; its shape statistics are
         corrupted, so it is excluded (count reported) and n counts INTERIOR
         classified instances only. The scorer's n-dependent tolerance (score.py,
         SI-026) absorbs the sampling noise of the smaller n.
      3. HONEST UNCLASSIFIED SHARE. A component that does not match a single
         primitive's module footprint (a merged multi-primitive blob, or a shape
         off the alphabet) is counted UNCLASSIFIED, never forced into a bin. The
         mix is the share over CLASSIFIED instances; the unclassified share is
         reported so the caller sees how much was absorbed. Tolerance on the sheet
         (SI-026) covers the residual bias this leaves.

    The classifier never cross-confuses (measured recall 1.0 AND precision 1.0
    per type on isolated primitives -- single-bar stripe blocks are caught by the
    half-cell-bar rule, 4-cell stadiums by the widened extent band, and the
    disc-IoU gate keeps clipped-cell remnants out of the circle bin): on real
    compositions a merged or cap-clipped (SI-019) component becomes unclassified
    or a filled-cell remnant, never a wrong multi-cell label. The residual bias
    (filled_cell over-represented by survival and remnants) is consistent and
    baked into the committed expected vector, which genuine surfaces match and a
    same-ink impostor with a different composition does not (its measured L1
    floor sits at ~1.0, above the committed tolerance).

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

# --- primitive-mix classifier constants (SI-026) ----------------------------

# The five alphabet primitives, in the generator's PRIMITIVE_TYPES order. The
# committed mix vector (sheet ``expected``) and the measured value are lists in
# THIS order, so the scorer's L1 is index-aligned.
PRIMITIVE_MIX_ORDER = (
    "filled_cell",
    "inscribed_circle",
    "stripe_bar",
    "staircase_diagonal",
    "rounded_cap",
)

# A component whose area is below this fraction of a module cell is noise (a stray
# overprint sliver, a degraded edge) and is skipped before classification.
MIX_MIN_AREA_FRAC = 0.10

# A single primitive spans at most a few modules (staircase n<=5, stripe 3x3).
# A component wider/taller than this in modules is a merged multi-primitive blob
# and is counted unclassified, never binned.
MIX_MAX_FOOTPRINT_MODULES = 5

# A non-stripe component's bounding box must sit within this many modules of an
# integer cell footprint to be a clean single primitive (else unclassified).
MIX_FOOTPRINT_TOL_MODULES = 0.33

# Extent (filled area / bbox area) bands per primitive, in module-normalised
# shape space (all measured, not tuned to fit): a solid cell fills ~1.0, an
# inscribed circle ~pi/4, a stripe block ~0.5, a staircase ~1/n, a stadium
# ~0.85-0.93. Bands are set wide enough to absorb rasterisation yet never overlap
# between types (confusion matrix: precision 1.0, no cross-type errors).
MIX_STRIPE_EXTENT = (0.30, 0.72)
MIX_STAIRCASE_EXTENT_MAX = 0.62
MIX_CIRCLE_EXTENT = (0.64, 0.88)
# Stadium upper bound 0.955: an n-cell stadium fills (n-1+pi/4)/n of its bbox =
# 0.893 (n=2), 0.928 (n=3), 0.946 (n=4) -- the earlier 0.94 bound silently missed
# every 4-cell stadium (SI-026 recall fix).
MIX_STADIUM_EXTENT = (0.70, 0.955)
MIX_FILLED_EXTENT_MIN = 0.85
MIX_QUARTER_EXTENT_MAX = 0.985

# Single-bar stripe blocks: a 1-cell-tall stripe block renders as ONE solid bar,
# height M/2 -- no periodicity to vote on, but the alphabet contains no other
# half-cell-tall solid form, so a solid bar whose bbox height is ~M/2 and width a
# whole number of modules IS a stripe bar (SI-026 recall fix; previously ~1/3 of
# stripe placements were systematically missed).
MIX_HALF_BAR_HEIGHT = (0.35, 0.65)   # bbox height in modules
MIX_HALF_BAR_EXTENT_MIN = 0.90       # solid bar

# An inscribed-circle candidate must also BE a disc: IoU between the component
# and the ideal disc inscribed in its bounding box. Exact (1.0) on clean renders;
# the margin absorbs mild degradation. This gate exists because the extent band
# alone is spoofable -- a cell partially clipped by the depth-2 overprint cap
# (SI-019) can land in the circle extent band without being disc-shaped, and that
# leakage flattered a same-ink all-staircase impostor's mix (SI-026 / SI-022).
MIX_CIRCLE_DISC_IOU = 0.90


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


def _classify_primitive(comp: np.ndarray, module_px: int):
    """Classify one connected component mask into a primitive type, or ``None``.

    ``comp`` is a bool bounding-box mask of a single ink component; ``module_px``
    the measured module M. Returns one of ``PRIMITIVE_MIX_ORDER`` or ``None`` when
    the component is not a clean single primitive (a merged blob or off-alphabet
    shape -> unclassified). Decisions use only module-normalised shape statistics
    (footprint in cells, extent, internal stripe periodicity, a corner test) and
    are ordered so the distinctive stripe periodicity is tested before the
    footprint gate a half-integer stripe bbox would fail (SI-026).
    """
    bh, bw = comp.shape
    area = int(comp.sum())
    if area == 0:
        return None
    bw_m = bw / module_px
    bh_m = bh / module_px
    nw = int(round(bw_m))
    nh = int(round(bh_m))
    if nw < 1 or max(nw, nh) > MIX_MAX_FOOTPRINT_MODULES:
        return None
    extent = area / float(bw * bh)

    # (1) Stripe FIRST. Regular M/2 ink runs separated by ~M/2 gaps (period ~M) in
    # several columns -- a distinctive periodicity, tested before the footprint
    # gate because a multi-bar block's bbox height is the half-integer (h-0.5)M.
    if MIX_STRIPE_EXTENT[0] <= extent <= MIX_STRIPE_EXTENT[1] and _is_striped(comp, module_px):
        return "stripe_bar"

    # (1b) Single-bar stripe block: a 1-cell-tall block is ONE solid bar (height
    # M/2, width a whole number of modules) with no periodicity to vote on. No
    # other alphabet form is half-a-cell tall, so the shape alone identifies it.
    if MIX_HALF_BAR_HEIGHT[0] <= bh_m <= MIX_HALF_BAR_HEIGHT[1] \
       and abs(bw_m - nw) <= MIX_FOOTPRINT_TOL_MODULES \
       and extent >= MIX_HALF_BAR_EXTENT_MIN:
        return "stripe_bar"

    # Remaining types are single cells on the grid: bbox must be near-integer.
    if nh < 1 or abs(bw_m - nw) > MIX_FOOTPRINT_TOL_MODULES \
       or abs(bh_m - nh) > MIX_FOOTPRINT_TOL_MODULES:
        return None

    # (2) Staircase: square-ish multi-cell footprint left sparsely filled (~1/n).
    if nw >= 2 and nh >= 2 and abs(nw - nh) <= 1 and extent <= MIX_STAIRCASE_EXTENT_MAX:
        return "staircase_diagonal"

    # (3) Inscribed circle: square 1x1 or 2x2 footprint, extent ~pi/4, AND the
    # component actually IS the inscribed disc (IoU gate, MIX_CIRCLE_DISC_IOU):
    # the extent band alone is spoofable by depth-2-clipped cell remnants.
    if nw == nh and nw in (1, 2) and MIX_CIRCLE_EXTENT[0] <= extent <= MIX_CIRCLE_EXTENT[1]:
        if _disc_iou(comp) >= MIX_CIRCLE_DISC_IOU:
            return "inscribed_circle"
        return None  # circle-sized but not disc-shaped: unclassified, never binned

    # (4) Rounded cap -- stadium: elongated 1xn / nx1 footprint, high extent with
    # rounded (area-shaving) ends.
    if ((nw >= 2 and nh == 1) or (nh >= 2 and nw == 1)) \
       and MIX_STADIUM_EXTENT[0] <= extent <= MIX_STADIUM_EXTENT[1]:
        return "rounded_cap"

    # (5) 1x1 near-solid: filled_cell, unless exactly one corner is shaved -> the
    # rounded_cap quarter-round.
    if nw == 1 and nh == 1 and extent >= MIX_FILLED_EXTENT_MIN:
        q = max(2, module_px // 4)
        corners = [comp[:q, :q], comp[:q, -q:], comp[-q:, :q], comp[-q:, -q:]]
        empty = sum(1 for c in corners if float(c.mean()) < 0.55)
        if empty == 1 and extent < MIX_QUARTER_EXTENT_MAX:
            return "rounded_cap"
        return "filled_cell"
    return None


def _is_striped(comp: np.ndarray, module_px: int) -> bool:
    """True when a component's columns carry the M/2 bar / M/2 gap stripe rhythm.

    Samples several columns; a column votes when its ink runs are all ~M/2 and its
    interior gaps ~M/2 (audit s2 rhythm). Requires a majority of sampled columns
    to agree, so a lone bar (a 1-cell-tall block) or an incidental gap never reads
    as a stripe block.
    """
    bh, bw = comp.shape
    tol = 0.35 * module_px
    half = module_px / 2.0
    votes = 0
    checked = 0
    for frac in (0.2, 0.35, 0.5, 0.65, 0.8):
        cx = int(bw * frac)
        if cx >= bw:
            continue
        checked += 1
        runs = _run_lengths(comp[:, cx])
        interior = runs[1:-1] if len(runs) >= 3 else []
        on = [n for o, n in runs if o]
        gap = [n for o, n in interior if not o]
        if len(on) >= 2 and all(abs(n - half) <= tol for n in on) \
           and (not gap or all(abs(n - half) <= tol for n in gap)):
            votes += 1
    return checked >= 2 and votes >= max(2, checked - 1)


def _disc_iou(comp: np.ndarray) -> float:
    """IoU between a component and the ideal disc inscribed in its bounding box.

    The generator's circle mask is a pixel-centre distance test against the
    inscribed disc, so a true inscribed circle scores ~1.0; a clipped cell
    remnant that merely lands in the circle EXTENT band scores far lower.
    Deterministic, pure numpy.
    """
    bh, bw = comp.shape
    if bh == 0 or bw == 0:
        return 0.0
    ys = np.arange(bh, dtype=np.float64) + 0.5
    xs = np.arange(bw, dtype=np.float64) + 0.5
    X, Y = np.meshgrid(xs, ys)
    r = min(bw, bh) / 2.0
    disc = (X - bw / 2.0) ** 2 + (Y - bh / 2.0) ** 2 <= r ** 2
    inter = float(np.logical_and(comp, disc).sum())
    union = float(np.logical_or(comp, disc).sum())
    return inter / union if union else 0.0


def _ink_present_mask(surface_i64: np.ndarray, ink_bgr, other_inks) -> np.ndarray:
    """Reconstruct the footprint of one ink from the overprint arithmetic (SI-026).

    A pixel carries ink ``ink_bgr`` when it is exactly that colour (a depth-1
    region) or exactly ``multiply(ink_bgr, other)`` for some other extracted ink
    (a depth-2 overlap). Reconstructing the overlap regions keeps a primitive's
    footprint whole, so components merge only with SAME-ink neighbours.
    """
    x = np.asarray(ink_bgr, dtype=np.int64)
    present = np.all(surface_i64 == x, axis=2)
    for y in other_inks:
        if np.array_equal(x, y):
            continue
        present |= np.all(surface_i64 == _multiply(x, y), axis=2)
    return present.astype(np.uint8)


def measure_primitive_mix(surface: np.ndarray, module_px, ink_set):
    """Classify the primitive-frequency mix (audit s6 point 2; SI-026).

    ``ink_set`` is the extracted ink set ``[(bgr, fraction), ...]`` from
    ``separate_products``. Returns ``(mix, n_classified, detail)`` where ``mix`` is
    a list of five instance shares in ``PRIMITIVE_MIX_ORDER`` (summing to 1 over
    classified instances, or all-zero when none classified) and ``n_classified``
    the number of instances observed (the ``primitives_observed`` sample unit).

    Method (SI-026): per-ink footprint reconstruction (``_ink_present_mask``), a
    vertical morphological close to regroup a stripe block's separated bars into
    one component, connected components, then ``_classify_primitive`` on the
    ORIGINAL (un-closed) per-ink mask so the stripe rhythm survives. Components
    that are not a clean single primitive are counted unclassified. The detail
    carries the raw counts, the unclassified count/share and the ink layers used.
    """
    counts = {t: 0 for t in PRIMITIVE_MIX_ORDER}
    if not module_px or module_px < 4 or not ink_set:
        return [0.0] * len(PRIMITIVE_MIX_ORDER), 0, {
            "reason": "no module/ink set to anchor the primitive mix",
            "counts": counts, "n_unclassified": 0}

    surface_i64 = surface.astype(np.int64)
    ink_bgrs = [np.asarray(bgr, dtype=np.int64) for bgr, _ in ink_set]
    min_area = max(16, int(MIX_MIN_AREA_FRAC * module_px * module_px))
    # Close vertically enough to bridge the M/2 stripe gap but not a full-cell gap.
    kh = int(round(0.6 * module_px)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kh))

    H, W = surface.shape[:2]
    n_classified = 0
    n_unclassified = 0
    n_excluded_edge = 0
    for x in ink_bgrs:
        present = _ink_present_mask(surface_i64, x, ink_bgrs)
        grouped = cv2.morphologyEx(present, cv2.MORPH_CLOSE, kernel)
        n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
        present_bool = present.astype(bool)
        for lab in range(1, n_labels):
            if int(stats[lab, cv2.CC_STAT_AREA]) < min_area:
                continue
            top = int(stats[lab, cv2.CC_STAT_TOP])
            left = int(stats[lab, cv2.CC_STAT_LEFT])
            bh = int(stats[lab, cv2.CC_STAT_HEIGHT])
            bw = int(stats[lab, cv2.CC_STAT_WIDTH])
            # EDGE EXCLUSION (SI-026): a component touching the image border may be
            # a primitive clipped by the fragment boundary -- its shape statistics
            # are corrupted, so counting it biases the mix (standard practice in
            # counting problems: count interior events only). n therefore counts
            # INTERIOR classified instances; the exclusion count is reported. The
            # cost -- whole edge-cell primitives on a full surface are excluded
            # too, shrinking n -- is priced in consistently: the committed expected
            # vector is derived from interior-only measurements, and the scorer's
            # n-dependent tolerance (score.py) absorbs the sampling noise of a
            # smaller n instead of punishing it as disagreement.
            if top == 0 or left == 0 or top + bh >= H or left + bw >= W:
                n_excluded_edge += 1
                continue
            # Classify on the ORIGINAL mask (restricted to this grouped component)
            # so the stripe rhythm the close would fill in is preserved.
            comp = (present_bool[top:top + bh, left:left + bw]
                    & (labels[top:top + bh, left:left + bw] == lab))
            if int(comp.sum()) < min_area:
                continue
            t = _classify_primitive(comp, module_px)
            if t is None:
                n_unclassified += 1
            else:
                counts[t] += 1
                n_classified += 1

    if n_classified > 0:
        mix = [counts[t] / float(n_classified) for t in PRIMITIVE_MIX_ORDER]
    else:
        mix = [0.0] * len(PRIMITIVE_MIX_ORDER)
    total_components = n_classified + n_unclassified
    detail = {
        "order": list(PRIMITIVE_MIX_ORDER),
        "counts": counts,
        "n_classified": n_classified,
        "n_unclassified": n_unclassified,
        "n_excluded_edge": n_excluded_edge,
        "unclassified_share": (round(n_unclassified / total_components, 4)
                               if total_components else 0.0),
        "n_ink_layers": len(ink_bgrs),
        "note": "share of INTERIOR classified primitive INSTANCES by type; "
                "border-touching components excluded (clipped shapes bias the "
                "mix), merged/off-alphabet components counted unclassified "
                "(SI-026). This describes the reconstruction's composition "
                "model, not Studio.Build's original (SI-018).",
    }
    return mix, n_classified, detail


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

    # 5. Primitive mix (committed identification carrier -- SI-026, closing the
    #    same-ink half of SI-022). Classified from the extracted ink set via
    #    per-ink footprint reconstruction; None value when nothing classifiable.
    mix, n_comp, mix_detail = measure_primitive_mix(image, module_px, ink_set)
    working["primitive_mix"] = {"mix": mix, "n_classified": n_comp, **mix_detail}
    primitive_mix = Measurement(
        "primitive_frequency_mix",
        mix if n_comp > 0 else None,
        n_comp,
        mix_detail,
    )

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
